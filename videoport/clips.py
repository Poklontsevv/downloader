"""Local clip workspace and vertical MP4 exports."""

import json
import logging
import math
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor
from tempfile import TemporaryDirectory, TemporaryFile
from threading import Lock
from uuid import uuid4

import requests
from flask import abort, jsonify, request, send_file

from .clip_analysis import FLAGS, semantic_rank, suggest_clips, transcribe, visual_cues
from .montage import MAX_SEGMENTS, montage_actions, speech_blocks, trim_candidate_pauses
from .media import save_file

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v", ".ts"}
LOGGER = logging.getLogger(__name__)


def probe(path):
    response = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,crypto", "-show_entries",
         "format=duration:stream=codec_type", "-of", "json", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, creationflags=FLAGS, timeout=35, check=True,
    )
    info = json.loads(response.stdout)
    duration = float(info["format"]["duration"])
    streams = {stream.get("codec_type") for stream in info.get("streams", [])}
    if not math.isfinite(duration) or duration <= 0 or "video" not in streams:
        raise ValueError("Не удалось определить длительность исходного видео.")
    return duration, "audio" in streams


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
    if write_subtitles(directory / "captions.srt", clip, transcript):
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
    target = directory / "clip.mp4"
    if len(segments) == 1:
        segment = dict(segments[0], layout=clip["layout"])
        render_segment(source, directory, target.name, segment, transcript, progress, preview, False)
        return target

    durations = [segment["end"] - segment["start"] for segment in segments]
    duration = sum(durations)
    elapsed = 0
    parts = []
    for index, (segment, length) in enumerate(zip(segments, durations)):
        name = f"segment-{index}.mp4"
        render_segment(
            source, directory, name, dict(segment, layout=clip["layout"]), transcript,
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


class ClipStudio:
    def __init__(self, cache: Path, tools: Path, output: Path):
        self.cache, self.tools, self.output = cache, tools, output
        self.lock = Lock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clip")
        self.storage = TemporaryDirectory(prefix="videoport-clips-")
        self.temp = Path(self.storage.name)
        self.source = None
        self.transcript = []
        self.preview = None
        self.status = {"busy": False, "stage": "", "percent": None, "clips": [], "files": [], "folder": str(output)}

    def snapshot(self):
        with self.lock:
            return self.status.copy()

    def update(self, **changes):
        with self.lock:
            self.status.update(changes)

    def progress(self, stage, percent):
        self.update(stage=stage, percent=percent)

    def launch(self, operation, data):
        with self.lock:
            if self.status["busy"]:
                return False
            self.status.update(busy=True, error=None, stage="Запуск…", percent=None)
            if operation == "analyse":
                self.status.update(clips=[], files=[], preview=None, warning=None, name=None, duration=None)
            elif operation == "preview":
                self.status["preview"] = None
            elif operation == "export":
                self.status.update(folder=str(data["folder"]), files=[])
            self.worker.submit(self.run, operation, data)
            return True

    def run(self, operation, data):
        try:
            if operation == "analyse":
                self.analyse(**data)
            else:
                self.make_clips(operation, **data)
        except requests.RequestException:
            self.update(error="Не удалось загрузить модель или получить ответ от локальной модели.")
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            message = str(error) if isinstance(error, ValueError) else "Не удалось открыть или обработать видео."
            self.update(error=message)
        finally:
            self.update(busy=False)

    def analyse(self, source, length, number, language, semantic):
        self.source = None
        self.transcript = []
        duration, audio = probe(source)
        motion, cuts = visual_cues(source, duration, self.progress)
        warning = None
        with TemporaryDirectory(dir=self.temp) as folder:
            if audio:
                try:
                    self.transcript = transcribe(source, Path(folder), self.cache, self.tools, language, self.progress)
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
            clip["captions"] = "\n".join(caption_lines(self.transcript, clip["start"], clip["end"]))
            if "segments" not in clip:
                clip["segments"] = [{"start": clip["start"], "end": clip["end"]}]
            for segment in clip["segments"]:
                segment["captions"] = "\n".join(caption_lines(self.transcript, segment["start"], segment["end"]))
            clip.pop("text", None)
        self.source = source
        self.update(name=source.name, duration=duration, clips=selected, warning=warning,
                    stage="Варианты готовы", percent=100)

    def make_clips(self, operation, clips, folder):
        if not self.source:
            raise ValueError("Сначала проанализируйте видео.")
        if operation == "export":
            folder.mkdir(parents=True, exist_ok=True)
        saved = []
        for index, clip in enumerate(clips):
            preview = operation == "preview"
            stage = "Сборка превью" if preview else f"Экспорт клипа {index + 1} из {len(clips)}"
            self.progress(stage, 0)
            with TemporaryDirectory(dir=self.temp if preview else folder, prefix=".videoport-clip-") as temp:
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
                    target = save_file(result, folder, clip["title"], f"clip-{index + 1}")
                    saved.append(str(target))
                    self.update(files=saved.copy())
        self.progress("Превью готово" if operation == "preview" else "Экспорт завершён", 100)


def attach_clips(app, workspace, cache, tools):
    studio = ClipStudio(cache, tools, workspace.output / "Нарезка")

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

    @app.get("/api/clip/transcript")
    def clip_transcript():
        return jsonify(studio.transcript if not studio.snapshot()["busy"] else [])

    @app.post("/api/clip/<operation>")
    def clip_action(operation):
        if operation not in {"analyse", "preview", "export"}:
            abort(404)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        try:
            if operation == "analyse":
                if data.get("id"):
                    item = workspace.file_for(data["id"])
                    source = item[0] / item[1] if item else None
                else:
                    source = Path(data["file"])
                if not source or not source.is_absolute() or not source.is_file() or source.suffix.lower() not in VIDEO_EXTENSIONS:
                    raise ValueError("Выберите видеофайл на компьютере.")
                length, number = int(data["length"]), int(data["number"])
                language = data["language"]
                if length not in {30, 45, 60} or not 1 <= number <= 10 or language not in {"auto", "ru", "en"}:
                    raise ValueError("Проверьте параметры анализа.")
                task = dict(source=source, length=length, number=number, language=language, semantic=data.get("semantic") is True)
            else:
                state = studio.snapshot()
                duration = state.get("duration")
                if not duration:
                    raise ValueError("Сначала проанализируйте видео.")
                items = data["clips"]
                if not isinstance(items, list) or not 1 <= len(items) <= (1 if operation == "preview" else 10):
                    raise ValueError("Выберите клипы для обработки.")
                clips = []
                for item in items:
                    ranges = item.get("segments", [item])
                    if not isinstance(ranges, list) or not 1 <= len(ranges) <= MAX_SEGMENTS:
                        raise ValueError(f"Добавьте от 1 до {MAX_SEGMENTS} отрезков на клип.")
                    segments = []
                    for part in ranges:
                        start, end = float(part["start"]), float(part["end"])
                        if not (0 <= start < end <= duration + 0.05 and end - start >= 1 - 1e-6):
                            raise ValueError("Отрезок должен быть в пределах исходного видео и длиться от одной секунды.")
                        end = min(end, duration)
                        text = ("\n".join(caption_lines(studio.transcript, start, end))
                                if part.get("autoCaptions") is True else str(part.get("captions", "")))
                        segments.append(dict(start=start, end=end, captions=text[:10000]))
                    if sum(part["end"] - part["start"] for part in segments) > 180 + 1e-6:
                        raise ValueError("Общая длительность клипа должна быть не больше 180 секунд.")
                    clips.append(dict(segments=segments, title=str(item["title"]).strip()[:120] or "Клип",
                                      layout="fit" if item.get("layout") == "fit" else "crop"))
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
