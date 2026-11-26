"""Local clip workspace and vertical MP4 exports."""

import json
import logging
import math
from pathlib import Path
from shutil import copyfile
import subprocess
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory, TemporaryFile
from threading import Lock
from uuid import uuid4

import requests
from flask import abort, jsonify, request, send_file

from .clip_analysis import FLAGS, SPEECH_DEVICES, SPEECH_MODELS, replace_transcript, semantic_rank, suggest_clips, transcribe, visual_cues
from .montage import MAX_SEGMENTS, montage_actions, speech_blocks, trim_candidate_pauses
from .media import save_file
from .projects import Projects
from .subtitles import CaptionFonts, normalize_cues, normalize_style, write_styled_subtitles

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}
LOGGER = logging.getLogger(__name__)


def probe(path):
    response = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,crypto", "-show_entries",
         "format=duration:stream=codec_type,codec_name,pix_fmt:stream_disposition=attached_pic", "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=FLAGS, timeout=35, check=True,
    )
    info = json.loads(response.stdout)
    duration = float(info["format"]["duration"])
    streams = [stream for stream in info.get("streams", []) if not stream.get("disposition", {}).get("attached_pic")]
    video = next((stream for stream in streams if stream["codec_type"] == "video"), None)
    audio = next((stream["codec_name"] for stream in streams if stream["codec_type"] == "audio"), None)
    if not math.isfinite(duration) or duration <= 0 or not video:
        raise ValueError("Не удалось определить длительность исходного видео.")
    suffix = path.suffix.lower()
    if (suffix in {".mp4", ".m4v", ".mov"} and video["codec_name"] == "h264"
            and video.get("pix_fmt") in {"yuv420p", "yuvj420p"} and audio in {None, "aac", "mp3"}):
        mime = "video/mp4"
    elif suffix == ".webm" and video["codec_name"] in {"vp8", "vp9", "av1"} and audio in {None, "opus", "vorbis"}:
        mime = "video/webm"
    else:
        mime = None
    return duration, bool(audio), mime


def caption_lines(transcript, start, end):
    return [row["text"] for row in transcript if row["start"] < end and row["end"] > start]


def srt_time(seconds):
    milliseconds = max(0, round(seconds * 1000))
    minutes, milliseconds = divmod(milliseconds, 60000)
    hours, minutes = divmod(minutes, 60)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def write_subtitles(path, clip, transcript):
    start, end = clip["start"], clip["end"]
    lines = [line.strip() for line in clip["captions"].splitlines() if line.strip()]
    if not lines:
        return False
    cues = [row for row in transcript if row["start"] < end and row["end"] > start]
    if len(lines) == len(cues):
        times = [(max(0, row["start"] - start), min(end, row["end"]) - start) for row in cues]
    else:
        step = (end - start) / len(lines)
        times = [(i * step, (i + 1) * step) for i in range(len(lines))]
    blocks = []
    for index, ((begin, finish), line) in enumerate(zip(times, lines), 1):
        clean = line.replace("<", "‹").replace(">", "›").replace("{", "(").replace("}", ")").replace("\\", "/")
        blocks.append(f"{index}\n{srt_time(begin)} --> {srt_time(finish)}\n{clean}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return True


def run_render(args, directory, progress, duration):
    args = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-progress", "pipe:1", "-nostats", *args]
    with TemporaryFile(dir=directory) as diagnostics:
        with subprocess.Popen(args, cwd=directory, creationflags=FLAGS, stdout=subprocess.PIPE,
                              stderr=diagnostics, text=True) as process:
            for line in process.stdout:
                if line.startswith("out_time_us="):
                    try:
                        elapsed = int(line.split("=", 1)[1]) / 1_000_000
                        progress(max(0, min(99, round(100 * elapsed / duration))))
                    except ValueError:
                        continue
            returncode = process.wait()
        if returncode:
            size = diagnostics.seek(0, 2)
            diagnostics.seek(max(0, size - 4096))
            LOGGER.error("FFmpeg clip rendering failed (exit %s): %s",
                         returncode, diagnostics.read().decode("utf-8", errors="replace").strip())
            raise ValueError("FFmpeg не смог собрать клип. Подробности записаны в журнал Videoport.")


def render_segment(source, directory, target, clip, transcript, progress, preview, montage):
    start, end = clip["start"], clip["end"]
    width, height = (360, 640) if preview else (1080, 1920)
    fraction = f"{width}:{height}"
    if clip["layout"] == "fit":
        picture = f"scale={fraction}:force_original_aspect_ratio=decrease:force_divisible_by=2,pad={fraction}:(ow-iw)/2:(oh-ih)/2"
    else:
        picture = f"scale={fraction}:force_original_aspect_ratio=increase,crop={fraction}"
    picture += ",setsar=1"
    if "style" in clip:
        if write_styled_subtitles(directory, clip, transcript):
            picture += ",subtitles=captions.ass:fontsdir=fonts"
    elif write_subtitles(directory / "captions.srt", clip, transcript):
        picture += ",subtitles=captions.srt:force_style='Fontname=Arial,Fontsize=19,Outline=2,Alignment=2,MarginV=36'"
    if montage:
        # All parts need the same video time base for the concat demuxer.
        picture += ",fps=30"
    args = ["-ss", str(start), "-protocol_whitelist", "file,crypto",
            "-i", str(source), "-t", str(end-start), "-map", "0:V:0", "-map", "0:a:0?",
            "-vf", picture, "-c:v", "libx264", "-preset", "ultrafast" if preview else "fast", "-crf", "23" if preview else "18",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", target]
    run_render(args, directory, progress, end - start)


def render_clip(source, directory, clip, transcript, progress, preview=False):
    segments = clip.get("segments") or [clip]
    settings = {"layout": clip["layout"]}
    if "style" in clip:
        settings["style"] = clip["style"]
        settings["font_asset"] = clip.get("font_asset")
    target = directory / "clip.mp4"
    if len(segments) == 1:
        segment = dict(segments[0], **settings)
        render_segment(source, directory, target.name, segment, transcript, progress, preview, False)
        return target

    durations = [segment["end"] - segment["start"] for segment in segments]
    duration = sum(durations)
    elapsed = 0
    parts = []
    for index, (segment, length) in enumerate(zip(segments, durations)):
        name = f"segment-{index}.mp4"
        render_segment(
            source, directory, name, dict(segment, **settings), transcript,
            lambda percent: progress(round(95 * (elapsed + length * percent / 100) / duration)),
            preview, True,
        )
        elapsed += length
        parts.append(f"file {name}\n")
    (directory / "montage.ffconcat").write_text("".join(parts), encoding="ascii")
    progress(95)
    run_render(["-f", "concat", "-safe", "1", "-protocol_whitelist", "file,crypto",
                "-i", "montage.ffconcat", "-map", "0:v:0", "-map", "0:a:0?",
                "-c", "copy", "-movflags", "+faststart", target.name],
               directory, lambda percent: progress(95 + percent * 4 // 100), duration)
    return target


def parse_clip(item, duration, transcript, imported=()):
    ranges = item.get("segments", [item])
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= MAX_SEGMENTS:
        raise ValueError(f"Добавьте от 1 до {MAX_SEGMENTS} отрезков на клип.")
    segments = []
    style = normalize_style(item["style"], imported) if "style" in item else None
    for part in ranges:
        start, end = float(part["start"]), float(part["end"])
        if not (0 <= start < end <= duration + 0.05 and end - start >= 1 - 1e-6):
            raise ValueError("Отрезок должен быть в пределах исходного видео и длиться от одной секунды.")
        segment = dict(start=start, end=min(end, duration))
        if style is not None:
            cues = part.get("cues")
            segment["cues"] = normalize_cues(cues, duration) if cues is not None else None
        else:
            text = ("\n".join(caption_lines(transcript, start, end))
                    if part.get("autoCaptions") is True else str(part.get("captions", "")))
            segment["captions"] = text[:10000]
        segments.append(segment)
    if sum(part["end"] - part["start"] for part in segments) > 180 + 1e-6:
        raise ValueError("Общая длительность клипа должна быть не больше 180 секунд.")
    clip = dict(segments=segments, title=str(item["title"]).strip()[:120] or "Клип",
                layout="fit" if item.get("layout") == "fit" else "crop")
    if style is not None:
        clip["style"] = style
    return clip


class ClipStudio:
    def __init__(self, cache: Path, tools: Path, output: Path, project_folder: Path):
        self.cache, self.tools, self.output = cache, tools, output
        self.projects = Projects(project_folder)
        self.fonts = CaptionFonts(project_folder / "Fonts")
        self.lock = Lock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip")
        self.storage = TemporaryDirectory(prefix="videoport-clips-")
        self.temp = Path(self.storage.name)
        self.project = None
        self.source = None
        self.transcript = []
        self.preview = None
        self.status = {"busy": False, "stage": "", "percent": None, "clips": [], "files": [], "folder": str(output)}
        for entry in self.projects.projects():
            try:
                self.activate(self.projects.read(entry["id"]))
                break
            except (KeyError, ValueError, OSError):
                LOGGER.warning("Could not restore project %s", entry["id"])

    def activate(self, project):
        self.project = project
        self.source = Path(project["source"])
        self.transcript = self.projects.transcript(project["id"]) if project["status"] == "ready" else []
        self.preview = None
        self.status = {"busy": False, "percent": None, "preview": None}
        for field in ("stage", "clips", "files", "folder", "name", "duration", "warning", "error", "mime", "transcriptRevision", "speech"):
            self.status[field] = project.get(field)
        if not self.source.is_file():
            self.status["error"] = "Исходное видео не найдено. Верните его по указанному пути."

    def snapshot(self):
        with self.lock:
            return dict(self.status, projects=self.projects.projects(), fonts=self.fonts.list(),
                        project=self.project["id"] if self.project else None,
                        projectFolder=str(self.projects.path(self.project["id"])) if self.project else None,
                        projectStatus=self.project["status"] if self.project else None,
                        speechOptions={key: self.project["options"].get(key)
                                       for key in ("quality", "device")} if self.project else {},
                        source=str(self.source) if self.source else None)

    def update(self, **changes):
        with self.lock:
            self.status.update(changes)

    def progress(self, stage, percent):
        self.update(stage=stage, percent=percent)

    def open(self, project_id):
        with self.lock:
            if self.status["busy"]:
                return False
            self.activate(self.projects.read(project_id))
            return True

    def launch(self, operation, data):
        with self.lock:
            if self.status["busy"]:
                return False
            if operation == "captions" and (not self.project or self.project["id"] != data.pop("project_id")):
                raise ValueError("Откройте нужный проект.")
            if operation == "analyse":
                source = data["source"]
                project_id = data.pop("project_id", None)
                options = {key: data[key] for key in ("length", "number", "language", "semantic", "quality", "device")}
                if project_id:
                    project = self.projects.read(project_id)
                    if project["status"] not in {"interrupted", "error"}:
                        raise ValueError("Этот проект уже проанализирован.")
                    project.update(status="analysis", stage="Запуск анализа…", error=None)
                    self.projects.save(project)
                else:
                    project = self.projects.create(source, options)
                self.activate(project)
            elif operation == "preview":
                self.status["preview"] = None
            if operation == "export":
                self.status.update(folder=str(data["folder"]))
            if operation in {"analyse", "captions"}:
                self.status["speech"] = None
            self.status.update(busy=True, error=None, stage="Запуск…", percent=None)
            self.worker.submit(self.run, operation, data)
            return True

    def save_draft(self, project_id, clip_id, draft):
        with self.lock:
            if not self.project or self.project["id"] != project_id or self.project["status"] != "ready":
                raise ValueError("Откройте проект перед редактированием.")
            clips = self.project["clips"]
            if not any(clip["id"] == clip_id for clip in clips):
                raise ValueError("Клип не найден.")
            updated = [dict(clip, **draft) if clip["id"] == clip_id else clip for clip in clips]
            project = dict(self.project, clips=updated)
            self.projects.save(project)
            self.project = project
            self.status["clips"] = updated

    def record(self, **changes):
        with self.lock:
            project = dict(self.project, **changes)
            self.projects.save(project)
            self.project = project
            self.status.update(changes)

    def run(self, operation, data):
        error = None
        try:
            if operation == "analyse":
                self.analyse(**data)
            elif operation == "captions":
                self.refresh_transcript(**data)
            else:
                self.make_clips(operation, **data)
        except requests.RequestException:
            error = "Не удалось загрузить модель или получить ответ от локальной модели."
        except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as cause:
            LOGGER.warning("Clip task failed", exc_info=True)
            error = str(cause) if isinstance(cause, ValueError) else "Не удалось открыть или обработать видео."
        finally:
            if error:
                if operation == "analyse":
                    try:
                        self.record(status="error", error=error, stage="Анализ остановлен")
                    except OSError:
                        LOGGER.exception("Could not persist analysis failure")
                self.update(error=error)
            self.update(busy=False)

    def refresh_transcript(self, segments, quality, device):
        if not self.source or not self.source.is_file():
            raise ValueError("Исходное видео не найдено.")
        intervals = []
        for part in sorted(segments, key=lambda part: part["start"]):
            start, end = part["start"], part["end"]
            if intervals and start <= intervals[-1][1]:
                intervals[-1][1] = max(end, intervals[-1][1])
            else:
                intervals.append([start, end])
        options = self.project["options"]
        result = self.transcript
        found = False
        reports = []

        def report(update):
            reports.append(update)
            self.update(speech={"label": update["label"] if len({run["label"] for run in reports}) == 1
                               else "NVIDIA и процессор",
                               "seconds": round(sum(run["seconds"] for run in reports), 1)})

        selected_device = device
        for index, (start, end) in enumerate(intervals, 1):
            def progress(stage, percent):
                self.progress(f"{stage} · {index} из {len(intervals)}", percent)

            fresh = transcribe(self.source, self.cache, self.tools, options.get("language", "ru"),
                              progress, quality, start=start, end=end, device=device, report=report)
            if reports and reports[-1]["backend"] == "cpu":
                device = "cpu"
            if fresh:
                found = True
                result = replace_transcript(result, fresh, start, end)
        if not found:
            raise ValueError("На этих отрезках речь не распознана. Текущие субтитры сохранены.")
        project_id = self.project["id"]
        with self.lock:
            self.projects.save_transcript(project_id, result)
            self.transcript = result
            project = dict(self.project, transcriptRevision=(self.project.get("transcriptRevision") or 0) + 1,
                           options=dict(options, quality=quality, device=selected_device), speech=self.status.get("speech"))
            self.projects.save(project)
            self.project = project
            self.status.update(transcriptRevision=project["transcriptRevision"], stage="Автосубтитры обновлены", percent=100)

    def preview_source(self):
        project = self.project
        if not project or project["status"] != "ready" or not project.get("mime"):
            return None
        if project.get("proxy"):
            return self.projects.path(project["id"]) / "preview.mp4"
        return self.source

    def prepare_source(self, source, duration):
        folder = self.projects.path(self.project["id"])
        self.progress("Подготовка просмотра исходника", 0)
        with TemporaryDirectory(dir=folder, prefix=".videoport-proxy-") as name:
            directory = Path(name)
            run_render(["-protocol_whitelist", "file,crypto", "-i", str(source),
                        "-map", "0:V:0", "-map", "0:a:0?",
                        "-vf", "scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=24",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", "preview.mp4"],
                       directory, lambda value: self.progress("Подготовка просмотра исходника", value), duration)
            (directory / "preview.mp4").replace(folder / "preview.mp4")

    def analyse(self, source, length, number, language, semantic, quality, device):
        self.transcript = []
        duration, audio, mime = probe(source)
        motion, cuts = visual_cues(source, duration, self.progress)
        warning = None
        if audio:
            try:
                self.transcript = transcribe(source, self.cache, self.tools, language, self.progress, quality,
                                             device=device, report=lambda result: self.update(speech=result))
            except (requests.RequestException, ValueError, OSError, subprocess.SubprocessError):
                warning = "Распознавание речи недоступно. Варианты подобраны по кадрам."
        candidates = suggest_clips(self.transcript, motion, cuts, duration, length)
        blocks = speech_blocks(self.transcript, motion, cuts, duration)
        trim_candidate_pauses(candidates, blocks, motion)
        montage_actions(candidates, motion, cuts)
        if semantic and any(candidate["text"] for candidate in candidates):
            try:
                if not semantic_rank(candidates, blocks, length, self.cache, self.progress):
                    warning = "Модель не вернула оценки. Показаны варианты по речи и движению."
            except (requests.RequestException, OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                warning = "Смысловая модель недоступна. Показаны варианты по речи и движению."
        elif semantic and warning is None:
            warning = "Речь для смысловой оценки не найдена. Показаны варианты по кадрам."
        candidates.sort(key=lambda item: item["score"], reverse=True)
        selected = candidates[:number]
        for clip in selected:
            clip.update(id=uuid4().hex, layout="crop", selected=True, style=normalize_style(None))
            if "segments" not in clip:
                clip["segments"] = [{"start": clip["start"], "end": clip["end"]}]
            for segment in clip["segments"]:
                segment["cues"] = None
            clip.pop("text", None)
        proxy = mime is None
        if proxy:
            self.prepare_source(source, duration)
            mime = "video/mp4"
        self.projects.save_transcript(self.project["id"], self.transcript)
        self.record(status="ready", duration=duration, clips=selected, warning=warning,
                    stage="Варианты готовы", mime=mime, proxy=proxy, speech=self.snapshot().get("speech"))
        self.update(percent=100)

    def make_clips(self, operation, clips, folder):
        if not self.source:
            raise ValueError("Сначала проанализируйте видео.")
        project_output = self.projects.path(self.project["id"]) / "Clips"
        if operation == "export":
            project_output.mkdir(parents=True, exist_ok=True)
        saved = list(self.project["files"])
        for index, clip in enumerate(clips):
            preview = operation == "preview"
            stage = "Сборка превью" if preview else f"Экспорт клипа {index + 1} из {len(clips)}"
            self.progress(stage, 0)
            with TemporaryDirectory(dir=self.temp if preview else project_output, prefix=".videoport-clip-") as temp:
                if "style" in clip:
                    clip["font_asset"] = self.fonts.resolve(clip["style"]["font"])
                result = render_clip(self.source, Path(temp), clip, self.transcript,
                                     lambda value: self.progress(stage, value), preview)
                if preview:
                    key = uuid4().hex
                    target = self.temp / (key + ".mp4")
                    result.replace(target)
                    with self.lock:
                        if self.preview:
                            try:
                                self.preview[1].unlink(missing_ok=True)
                            except PermissionError:
                                pass
                        self.preview = (key, target)
                        self.status["preview"] = key
                else:
                    target = save_file(result, project_output, clip["title"], f"clip-{index + 1}")
                    saved.append(str(target))
                    self.record(files=saved.copy(), folder=str(folder))
                    if folder.resolve() != project_output.resolve():
                        folder.mkdir(parents=True, exist_ok=True)
                        with TemporaryDirectory(dir=folder, prefix=".videoport-export-") as copy:
                            staged = Path(copy) / "clip.mp4"
                            copyfile(target, staged)
                            save_file(staged, folder, clip["title"], f"clip-{index + 1}")
        self.progress("Превью готово" if operation == "preview" else "Экспорт завершён", 100)


def attach_clips(app, workspace, cache, tools, projects):
    studio = ClipStudio(cache, tools, workspace.output / "Нарезка", projects)

    @app.get("/api/clip/state")
    def clip_state():
        state = studio.snapshot()
        with workspace.lock:
            state["downloads"] = [{"id": job["id"], "name": job["file"]} for job in workspace.jobs.values()
                                  if job["state"] == "done" and Path(job["file"]).suffix.lower() in VIDEO_EXTENSIONS]
        return jsonify(state)

    @app.get("/clip-video/<key>")
    def clip_preview(key):
        with studio.lock:
            if not studio.preview or studio.preview[0] != key:
                abort(404)
            response = send_file(studio.preview[1], mimetype="video/mp4", conditional=True)
            response.headers["Cache-Control"] = "no-store"
            return response

    @app.get("/clip-source/<key>")
    def clip_source(key):
        with studio.lock:
            if not studio.project or studio.project["id"] != key:
                abort(404)
            path = studio.preview_source()
            if not path or not path.is_file():
                abort(404)
            response = send_file(path, mimetype=studio.project["mime"], conditional=True)
            response.headers["Cache-Control"] = "no-store"
            return response

    @app.get("/api/clip/transcript")
    def clip_transcript():
        with studio.lock:
            return jsonify(studio.transcript if studio.project and studio.project["status"] == "ready" else [])

    @app.post("/api/clip/project")
    def clip_project():
        data = request.get_json(silent=True) or {}
        try:
            if not studio.open(data.get("project")):
                return jsonify(error="Дождитесь текущей обработки."), 409
        except (AttributeError, KeyError, OSError, ValueError):
            return jsonify(error="Не удалось открыть проект."), 404
        return "", 204

    @app.post("/api/clip/draft")
    def clip_draft():
        data = request.get_json(silent=True) or {}
        try:
            duration = studio.snapshot()["duration"]
            draft = parse_clip(data["clip"], duration, studio.transcript, studio.fonts.imported)
            draft["selected"] = data["clip"].get("selected") is not False
            studio.save_draft(data["project"], data["clip"]["id"], draft)
        except (AttributeError, KeyError, OSError, TypeError, ValueError) as error:
            return jsonify(error=str(error) if isinstance(error, ValueError) else "Не удалось сохранить изменения."), 400
        return "", 204

    @app.post("/api/clip/font")
    def add_caption_font():
        request.max_content_length = 6_000_000
        uploaded = request.files.get("font")
        if not uploaded:
            return jsonify(error="Выберите файл шрифта."), 400
        try:
            with studio.lock:
                entry = studio.fonts.add(uploaded)
        except (OSError, ValueError) as error:
            return jsonify(error=str(error) if isinstance(error, ValueError) else "Не удалось сохранить шрифт."), 400
        return jsonify(entry), 201

    @app.get("/clip-font/<key>")
    def caption_font(key):
        with studio.lock:
            if key not in studio.fonts.imported:
                abort(404)
            _, path = studio.fonts.resolve(key)
            return send_file(path, mimetype="font/otf" if path.suffix == ".otf" else "font/ttf")

    @app.post("/api/clip/<operation>")
    def clip_action(operation):
        if operation not in {"analyse", "captions", "preview", "export"}:
            abort(404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        try:
            if operation == "analyse":
                if data.get("project"):
                    project = studio.projects.read(data["project"])
                    source = Path(project["source"])
                    data.update(project["options"])
                elif data.get("id"):
                    item = workspace.file_for(data["id"])
                    source = item[0] / item[1] if item else None
                else:
                    source = Path(data["file"])
                if not source or not source.is_absolute() or not source.is_file() or source.suffix.lower() not in VIDEO_EXTENSIONS:
                    raise ValueError("Выберите видеофайл на компьютере.")
                length, number = int(data["length"]), int(data["number"])
                language, quality, device = data["language"], data.get("quality", "medium"), data.get("device", "auto")
                if (length not in {30, 45, 60} or not 1 <= number <= 10 or language not in {"auto", "ru", "en"}
                        or quality not in SPEECH_MODELS or device not in SPEECH_DEVICES):
                    raise ValueError("Проверьте параметры анализа.")
                task = dict(source=source, length=length, number=number, language=language,
                            semantic=data.get("semantic") is True, quality=quality, device=device, project_id=data.get("project"))
            elif operation == "captions":
                state = studio.snapshot()
                if state.get("projectStatus") != "ready":
                    raise ValueError("Сначала проанализируйте видео.")
                clip = parse_clip(data["clip"], state["duration"], studio.transcript, studio.fonts.imported)
                options = state["speechOptions"]
                quality = data.get("quality", options.get("quality") or "medium")
                device = data.get("device", options.get("device") or "auto")
                if quality not in SPEECH_MODELS or device not in SPEECH_DEVICES:
                    raise ValueError("Выберите модель и устройство распознавания.")
                task = dict(segments=clip["segments"], project_id=data["project"], quality=quality, device=device)
            else:
                state = studio.snapshot()
                duration = state.get("duration")
                if not duration:
                    raise ValueError("Сначала проанализируйте видео.")
                items = data["clips"]
                if not isinstance(items, list) or not 1 <= len(items) <= (1 if operation == "preview" else 10):
                    raise ValueError("Выберите клипы для обработки.")
                clips = [parse_clip(item, duration, studio.transcript, studio.fonts.imported) for item in items]
                folder = Path(data.get("folder") or state["folder"])
                if not folder.is_absolute():
                    raise ValueError("Выберите папку экспорта.")
                task = dict(clips=clips, folder=folder)
            if not studio.launch(operation, task):
                return jsonify(error="Дождитесь текущей обработки."), 409
        except (AttributeError, KeyError, TypeError, OSError, ValueError) as error:
            return jsonify(error=str(error) if isinstance(error, ValueError) else "Проверьте параметры."), 400
        return "", 202

    return studio
