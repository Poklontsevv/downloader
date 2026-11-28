"""Local transcription queue and editable, paginated transcripts."""

from contextlib import closing, contextmanager
from functools import wraps
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from threading import Condition, Thread
from time import time
from uuid import uuid4

from flask import abort, jsonify, request, send_file

from .clip_analysis import FLAGS, SPEECH_DEVICES, SPEECH_MODELS, qwen_session, timed_cues, transcribe
from .media import save_file
from .transcript_export import EXPORT_FORMATS, prepare_pdf, write_export

LOGGER = logging.getLogger(__name__)
CHUNK = 120
PAGE = 60
LANGUAGES = """en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta no th ur hr bg lt la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw gl mr pa si km sn yo so af oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su yue""".split()
TRANSLATIONS = {"ru": "русский", "en": "английский", "es": "испанский", "fr": "французский",
                "de": "немецкий", "it": "итальянский", "pt": "португальский", "zh": "китайский",
                "ja": "японский", "ko": "корейский", "uk": "украинский", "ar": "арабский"}
PLAYABLE = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac",
            ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".mp4": "video/mp4",
            ".webm": "video/webm", ".mov": "video/mp4", ".m4v": "video/mp4"}


def number(value, low, high):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError("Укажите время в пределах записи.")
    return value


def options(data):
    language = data.get("language", "auto")
    quality = data.get("quality", "turbo")
    device = data.get("device", "auto")
    if language not in ["auto", *LANGUAGES] or quality not in SPEECH_MODELS or device not in SPEECH_DEVICES:
        raise ValueError("Проверьте настройки распознавания.")
    english = data.get("english") is True
    if english and quality == "turbo":
        quality = "medium"
    speakers = int(data.get("speakers") or 0)
    if not -1 <= speakers <= 16:
        raise ValueError("Укажите до 16 говорящих или выберите автоопределение.")
    return dict(language=language, quality=quality, device=device, english=english,
                restore=data.get("restore") is True, speakers=speakers)


def probe_audio(source):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-protocol_whitelist", "file,crypto", "-select_streams", "a:0",
         "-show_entries", "format=duration:stream=duration", "-of", "json", str(source)],
        capture_output=True, check=True, creationflags=FLAGS, timeout=45,
    )
    data = json.loads(result.stdout)
    if not data.get("streams"):
        raise ValueError("В файле нет звуковой дорожки.")
    for raw in (data["streams"][0].get("duration"), data.get("format", {}).get("duration")):
        try:
            seconds = float(raw)
            if math.isfinite(seconds) and seconds > 0:
                return seconds
        except (TypeError, ValueError):
            continue
    raise ValueError("Не удалось определить длительность записи.")


def owned_cues(cues, start, end):
    for cue in cues:
        if cue.get("words"):
            words = [word for word in cue["words"] if start <= (word["start"] + word["end"]) / 2 < end]
            yield from timed_cues(words)
        elif start <= (cue["start"] + cue["end"]) / 2 < end:
            yield cue


class TranscriptLibrary:
    def __init__(self, root, cache, tools):
        self.root, self.cache, self.tools = root, cache, tools
        root.mkdir(parents=True, exist_ok=True)
        self.condition = Condition()
        self.db = sqlite3.connect(root / "library.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
                size INTEGER NOT NULL, modified INTEGER NOT NULL, folder TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, options TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                length REAL, cursor REAL NOT NULL DEFAULT 0, error TEXT, note TEXT, speakers TEXT NOT NULL DEFAULT '{}',
                translated_id INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS cues (
                id INTEGER PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL,
                folded TEXT NOT NULL, speaker TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS cue_time ON cues(job,start,id);
            CREATE TABLE IF NOT EXISTS translation_inputs (
                id INTEGER PRIMARY KEY, job TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL, speaker TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS translation_job ON translation_inputs(job,id);
            UPDATE jobs SET state='paused', note='Обработка прервана. Можно продолжить.'
            WHERE state IN ('running','pausing','queued','canceling');
        """)
        self.db.commit()
        self.worker = Thread(target=self.work, daemon=True, name="transcription")
        self.worker.start()

    @contextmanager
    def write(self):
        with self.condition, self.db:
            yield
            self.condition.notify_all()

    def get(self, job_id):
        job = self.db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("Расшифровка не найдена.")
        return dict(job)

    def public(self, job):
        record = {key: job[key] for key in ("id", "name", "folder", "created", "state", "length", "cursor", "error", "note")}
        record["options"] = json.loads(job["options"])
        record["speakers"] = json.loads(job["speakers"])
        record["media"] = bool(PLAYABLE.get(Path(job["source"]).suffix.lower()) or (self.root / (job["id"] + ".m4a")).exists())
        return record

    def snapshot(self):
        with self.condition:
            return [self.public(row) for row in self.db.execute("SELECT * FROM jobs ORDER BY created DESC")]

    def active(self):
        with self.condition:
            return self.db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running','pausing','canceling') LIMIT 1").fetchone() is not None

    def add(self, sources, settings, folder):
        tasks = []
        for source in sources:
            if not source.is_absolute() or not source.is_file():
                raise ValueError("Выберите существующий аудио- или видеофайл.")
            info = source.stat()
            tasks.append((uuid4().hex, source.stem, str(source), info.st_size, info.st_mtime_ns,
                          folder, time(), json.dumps(settings)))
        with self.write():
            self.db.executemany(
                "INSERT INTO jobs(id,name,source,size,modified,folder,created,options) VALUES(?,?,?,?,?,?,?,?)", tasks,
            )
        return [task[0] for task in tasks]

    def work(self):
        while True:
            with self.condition:
                job = self.db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
                if job is None:
                    self.condition.wait()
                    continue
                job = dict(job)
                with self.write():
                    self.db.execute("UPDATE jobs SET state='running',error=NULL,note=NULL WHERE id=?", (job["id"],))
            try:
                if json.loads(job["options"]).get("translated"):
                    self.translate(job)
                else:
                    self.recognize(job)
            except Exception as error:
                LOGGER.warning("Transcription job %s failed", job["id"], exc_info=True)
                with self.write():
                    state = self.get(job["id"])["state"]
                    target = {"canceling": "canceled", "pausing": "paused"}.get(state, "error")
                    message = str(error) if isinstance(error, ValueError) else "Не удалось обработать запись. Подробности в журнале Videoport."
                    self.db.execute("UPDATE jobs SET state=?,error=? WHERE id=?",
                                    (target, message if target == "error" else None, job["id"]))

    def checkpoint(self, job_id):
        with self.write():
            state = self.get(job_id)["state"]
            if state == "pausing":
                self.db.execute("UPDATE jobs SET state='paused',note=NULL WHERE id=?", (job_id,))
            elif state == "canceling":
                self.db.execute("UPDATE jobs SET state='canceled',note=NULL WHERE id=?", (job_id,))
            return state == "running"

    def recognize(self, job):
        job_id = job["id"]
        source = Path(job["source"])
        info = source.stat()
        if info.st_size != job["size"] or info.st_mtime_ns != job["modified"]:
            raise ValueError("Исходный файл изменился. Добавьте обновлённый файл заново.")
        settings = json.loads(job["options"])
        length = job["length"] or probe_audio(source)
        start = job["cursor"]
        with self.write():
            self.db.execute("UPDATE jobs SET length=? WHERE id=?", (length, job_id))
        diarizer = None
        if settings["speakers"]:
            from .transcript_speakers import SpeakerLabels
            diarizer = SpeakerLabels(self.cache, self.root / (job_id + "-speakers.json"), settings["speakers"],
                                     lambda label, percent: self.stage(job_id, label, percent))

        while start < length and self.checkpoint(job_id):
            end = min(length, start + CHUNK)

            def progress(label, percent):
                self.stage(job_id, label, percent)

            rows = transcribe(
                source, self.cache, self.tools, settings["language"], progress, settings["quality"],
                start=max(0, start - 2), end=min(length, end + 2), device=settings["device"],
                restore=settings["restore"], remove_rumble=settings["restore"], translate=settings["english"],
            )
            cues = list(owned_cues(rows, start, end))
            if diarizer:
                diarizer.label(source, start, end, cues)
            with self.write():
                if self.get(job_id)["state"] != "canceling":
                    self.db.executemany(
                        "INSERT INTO cues(job,start,end,text,folded,speaker) VALUES(?,?,?,?,?,?)",
                        [(job_id, max(0, row["start"]), min(length, row["end"]), row["text"],
                          row["text"].casefold(), row.get("speaker", "")) for row in cues],
                    )
                    self.db.execute("UPDATE jobs SET cursor=? WHERE id=?", (end, job_id))
            start = end
        if not self.checkpoint(job_id):
            return
        if not PLAYABLE.get(source.suffix.lower()):
            try:
                self.stage(job_id, "Подготовка звукового плеера", None)
                self.prepare_media(job_id, source)
            except (OSError, subprocess.SubprocessError):
                LOGGER.info("Preview conversion unavailable for %s", job_id, exc_info=True)
        if self.checkpoint(job_id):
            with self.write():
                self.db.execute("UPDATE jobs SET state='ready',note=NULL WHERE id=? AND state='running'", (job_id,))

    def add_translation(self, original, language):
        if language not in TRANSLATIONS:
            raise ValueError("Выберите язык перевода.")
        with self.write():
            job = self.get(original)
            if job["state"] not in {"ready", "paused", "error", "canceled"}:
                raise ValueError("Дождитесь остановки распознавания.")
            ready = self.db.execute("SELECT 1 FROM cues WHERE job=? LIMIT 1", (original,)).fetchone()
            if not ready or not job["length"]:
                raise ValueError("Сначала распознайте речь.")
            key = uuid4().hex
            settings = dict(json.loads(job["options"]), translated=language)
            self.db.execute(
                "INSERT INTO jobs(id,name,source,size,modified,folder,created,options,length,speakers) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (key, f"{job['name']} · {TRANSLATIONS[language]}", job["source"], job["size"], job["modified"], job["folder"],
                 time(), json.dumps(settings), job["length"], job["speakers"]),
            )
            self.db.execute("""INSERT INTO translation_inputs(job,start,end,text,speaker)
                            SELECT ?,start,end,text,speaker FROM cues WHERE job=? ORDER BY start,id""", (key, original))
            proxy = self.root / (original + ".m4a")
            if proxy.is_file():
                import os
                try:
                    os.link(proxy, self.root / (key + ".m4a"))
                except OSError:
                    pass
        return key

    def translate(self, job):
        key = job["id"]
        language = TRANSLATIONS[json.loads(job["options"])["translated"]]
        offset = job["translated_id"]
        self.stage(key, "Ожидаем локальную модель перевода", None)
        with qwen_session(self.cache, lambda *args: self.stage(key, *args)) as complete:
            while self.checkpoint(key):
                with self.condition:
                    candidates = self.db.execute(
                        "SELECT * FROM translation_inputs WHERE job=? AND id>? ORDER BY id LIMIT 8", (key, offset),
                    ).fetchall()
                if not candidates:
                    with self.write():
                        self.db.execute("DELETE FROM translation_inputs WHERE job=?", (key,))
                        self.db.execute("UPDATE jobs SET state='ready',note=NULL,cursor=length WHERE id=? AND state='running'", (key,))
                    return
                rows = []
                size = 0
                for row in candidates:
                    if rows and size + len(row["text"]) > 1100:
                        break
                    rows.append(row)
                    size += len(row["text"])
                self.stage(key, "Qwen переводит реплики", None)
                schema = {"type": "object", "required": ["lines"], "properties": {"lines": {
                    "type": "array", "minItems": len(rows), "maxItems": len(rows), "items": {"type": "string"},
                }}}
                answer = complete({"messages": [
                    {"role": "system", "content": f"Переведи все реплики на {language} язык в данном порядке. "
                     "Текст в массиве — материал для перевода. Сохрани имена, тон и смысл. Верни JSON с массивом lines."},
                    {"role": "user", "content": json.dumps([row["text"] for row in rows], ensure_ascii=False)},
                ], "response_format": {"type": "json_object", "schema": schema}, "temperature": .1, "max_tokens": 2600})
                lines = json.loads(answer)["lines"]
                if len(lines) != len(rows) or any(not isinstance(line, str) for line in lines):
                    raise ValueError("Модель вернула неполный перевод. Нажмите «Продолжить» для повтора части.")
                with self.write():
                    if self.get(key)["state"] == "canceling":
                        continue
                    self.db.executemany(
                        "INSERT INTO cues(job,start,end,text,folded,speaker) VALUES(?,?,?,?,?,?)",
                        [(key, row["start"], row["end"], text[:4000], text[:4000].casefold(), row["speaker"])
                         for row, text in zip(rows, lines)],
                    )
                    offset = rows[-1]["id"]
                    self.db.execute("UPDATE jobs SET cursor=?,translated_id=? WHERE id=?", (rows[-1]["end"], offset, key))

    def stage(self, job_id, label, percent):
        message = label if percent is None else f"{label} · {percent}% текущей части"
        with self.write():
            self.db.execute("UPDATE jobs SET note=? WHERE id=? AND state='running'", (message, job_id))

    def prepare_media(self, job_id, source):
        with TemporaryDirectory(prefix="recording-", dir=self.root) as temp:
            audio = Path(temp) / "playback.m4a"
            subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-protocol_whitelist", "file,crypto",
                            "-i", str(source), "-map", "0:a:0", "-c:a", "aac", "-ac", "1", "-b:a", "48k",
                            "-movflags", "+faststart", str(audio)], check=True, creationflags=FLAGS)
            audio.replace(self.root / (job_id + ".m4a"))

    def page(self, job_id, page, query):
        with self.condition:
            job = self.get(job_id)
            where = "job=? AND instr(folded,?)>0" if query else "job=?"
            values = (job_id, query.casefold()) if query else (job_id,)
            count = self.db.execute("SELECT count(*) FROM cues WHERE " + where, values).fetchone()[0]
            page = min(max(0, page), max(0, (count - 1) // PAGE))
            rows = self.db.execute(
                "SELECT id,start,end,text,speaker FROM cues WHERE " + where + " ORDER BY start,id LIMIT ? OFFSET ?",
                (*values, PAGE, page * PAGE),
            )
            speakers = self.db.execute("SELECT DISTINCT speaker FROM cues WHERE job=? AND speaker!='' ORDER BY speaker",
                                       (job_id,)).fetchall()
            return dict(job=self.public(job), rows=[dict(row) for row in rows], page=page, count=count, pageSize=PAGE,
                        voices=[row[0] for row in speakers])

    def full_text(self, job_id):
        with self.condition:
            self.get(job_id)
            rows = self.db.execute("SELECT text FROM cues WHERE job=? ORDER BY start,id", (job_id,))
            return " ".join(text for row in rows if (text := row[0].strip()))

    def act(self, job_id, action, data):
        with self.write():
            job = self.get(job_id)
            state = job["state"]
            if action == "pause" and state in {"queued", "running"}:
                target = "pausing" if state == "running" else "paused"
            elif action == "resume" and state in {"paused", "canceled", "error"}:
                target = "queued"
            elif action == "cancel" and state in {"queued", "paused", "running", "pausing"}:
                target = "canceling" if state in {"running", "pausing"} else "canceled"
            elif action == "rename":
                name, folder = str(data.get("name", "")).strip()[:180], str(data.get("folder", "")).strip()[:100]
                if not name:
                    raise ValueError("Введите название.")
                self.db.execute("UPDATE jobs SET name=?,folder=? WHERE id=?", (name, folder, job_id))
                return
            elif action == "delete" and state not in {"running", "pausing", "canceling", "queued"}:
                self.db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
                for suffix in (".m4a", "-speakers.json"):
                    (self.root / (job_id + suffix)).unlink(missing_ok=True)
                return
            else:
                raise ValueError("Это действие пока недоступно.")
            self.db.execute("UPDATE jobs SET state=?,note=NULL,error=NULL WHERE id=?", (target, job_id))

    def edit(self, job_id, action, data):
        with self.write():
            job = self.get(job_id)
            if job["state"] not in {"ready", "paused", "canceled", "error"} or not job["length"]:
                raise ValueError("Дождитесь обработки текущей части.")
            if action == "replace":
                query, replacement = str(data.get("query", ""))[:250], str(data.get("replacement", ""))[:1200]
                if not query:
                    raise ValueError("Введите текст для поиска.")
                pattern = re.compile(re.escape(query), flags=0 if data.get("case") else re.I)
                cursor = self.db.execute("SELECT id,text FROM cues WHERE job=?", (job_id,))
                for row in cursor:
                    value = pattern.sub(lambda _: replacement, row["text"])[:4000]
                    if value != row["text"]:
                        self.db.execute("UPDATE cues SET text=?,folded=? WHERE id=?", (value, value.casefold(), row["id"]))
                return
            if action == "speaker":
                names = json.loads(job["speakers"])
                key = str(data.get("key", ""))[:50]
                if key:
                    names[key] = str(data.get("name", "")).strip()[:100]
                    self.db.execute("UPDATE jobs SET speakers=? WHERE id=?", (json.dumps(names), job_id))
                return
            cue_id = int(data["id"]) if action != "add" else None
            current = self.db.execute("SELECT * FROM cues WHERE id=? AND job=?", (cue_id, job_id)).fetchone()
            if action != "add" and current is None:
                raise ValueError("Реплика не найдена.")
            if action == "delete":
                self.db.execute("DELETE FROM cues WHERE id=?", (cue_id,))
                return
            if action == "split":
                pivot = number(data["at"], current["start"] + .05, current["end"] - .05)
                position = int(data["character"])
                if not 0 < position < len(current["text"]):
                    raise ValueError("Укажите границу внутри текста.")
                left = current["text"][:position].strip()
                right = current["text"][position:].strip()
                self.db.execute("UPDATE cues SET end=?,text=?,folded=? WHERE id=?", (pivot, left, left.casefold(), cue_id))
                data = dict(start=pivot, end=current["end"], text=right, speaker=current["speaker"])
                action = "add"
            if action == "merge":
                next_row = self.db.execute(
                    """SELECT * FROM cues WHERE job=? AND (start>? OR (start=? AND id>?)) ORDER BY start,id LIMIT 1""",
                    (job_id, current["start"], current["start"], cue_id),
                ).fetchone()
                if not next_row:
                    raise ValueError("После этой реплики нет следующей.")
                value = (current["text"] + " " + next_row["text"]).strip()[:4000]
                self.db.execute("UPDATE cues SET end=?,text=?,folded=? WHERE id=?",
                                (max(current["end"], next_row["end"]), value, value.casefold(), cue_id))
                self.db.execute("DELETE FROM cues WHERE id=?", (next_row["id"],))
                return
            if action not in {"add", "save"}:
                raise ValueError("Неизвестная операция.")
            start = number(data["start"], 0, job["length"])
            end = number(data["end"], start + .04, job["length"])
            value = str(data.get("text", ""))[:4000]
            speaker = str(data.get("speaker", ""))[:50]
            values = (start, end, value, value.casefold(), speaker)
            if action == "add":
                self.db.execute("INSERT INTO cues(start,end,text,folded,speaker,job) VALUES(?,?,?,?,?,?)", (*values, job_id))
            else:
                self.db.execute("UPDATE cues SET start=?,end=?,text=?,folded=?,speaker=? WHERE id=?", (*values, cue_id))

    def export(self, ids, folder, kind, timestamps, names):
        if kind not in EXPORT_FORMATS:
            raise ValueError("Выберите формат экспорта.")
        if kind == "pdf":
            prepare_pdf(self.cache, lambda *_: None)
        deposited = []
        for job_id in ids:
            with self.condition:
                job = self.get(job_id)
            # A read snapshot lets long exports run while the library continues processing.
            with closing(sqlite3.connect(self.root / "library.sqlite3")) as db, TemporaryDirectory(dir=folder, prefix=".videoport-text-") as temp:
                db.row_factory = sqlite3.Row
                rows = db.execute("SELECT start,end,text,speaker FROM cues WHERE job=? ORDER BY start,id", (job_id,))
                path = Path(temp) / ("transcript." + kind)
                settings = json.loads(job["options"])
                language = settings.get("translated") or ("en" if settings.get("english") else settings["language"])
                write_export(path, rows, job["name"], json.loads(job["speakers"]) if names else None, timestamps, language)
                deposited.append(save_file(path, folder, job["name"], job_id).name)
        return deposited


def transcript_errors(action):
    @wraps(action)
    def guarded(*args, **kwargs):
        try:
            return action(*args, **kwargs)
        except (ValueError, KeyError, TypeError, OSError) as error:
            LOGGER.info("Invalid transcription request", exc_info=True)
            return jsonify(error=str(error) if isinstance(error, ValueError) else "Не удалось обработать запрос."), 400
    return guarded


def attach_transcripts(app, workspace, root, cache, tools):
    library = TranscriptLibrary(root, cache, tools)

    @app.get("/api/transcript")
    def transcript_library():
        with workspace.lock:
            downloads = [dict(id=job["id"], name=job["file"]) for job in workspace.jobs.values() if job["state"] == "done"]
        return jsonify(jobs=library.snapshot(), downloads=downloads, languages=LANGUAGES, translations=TRANSLATIONS)

    @app.post("/api/transcript")
    @transcript_errors
    def transcript_add():
        body = request.get_json()
        entries = body.get("files", [])
        downloads = body.get("downloads", [])
        if not isinstance(entries, list) or not isinstance(downloads, list) or not 1 <= len(entries) + len(downloads) <= 100:
            raise ValueError("Выберите от одного до ста файлов за раз.")
        sources = [Path(value) for value in entries]
        for key in downloads:
            target = workspace.file_for(key)
            if not target:
                raise ValueError("Загруженный файл не найден.")
            sources.append(target[0] / target[1])
        paths = list(dict.fromkeys(sources))
        ids = library.add(paths, options(body), str(body.get("folder", "")).strip()[:100])
        return jsonify(ids=ids), 202

    @app.get("/api/transcript/<job_id>")
    @transcript_errors
    def transcript_page(job_id):
        if request.args.get("view") == "text":
            return jsonify(text=library.full_text(job_id))
        return jsonify(library.page(job_id, int(request.args.get("page", 0)), request.args.get("q", "")[:250]))

    @app.post("/api/transcript/<job_id>")
    @transcript_errors
    def transcript_act(job_id):
        body = request.get_json()
        library.act(job_id, body["action"], body)
        return "", 204

    @app.post("/api/transcript/<job_id>/edit")
    @transcript_errors
    def transcript_edit(job_id):
        body = request.get_json()
        library.edit(job_id, body["action"], body)
        return "", 204

    @app.post("/api/transcript/<job_id>/translate")
    @transcript_errors
    def transcript_translate(job_id):
        body = request.get_json()
        return jsonify(id=library.add_translation(job_id, body["language"])), 202

    @app.post("/api/transcript/export")
    @transcript_errors
    def transcript_export():
        body = request.get_json()
        folder = Path(body["folder"])
        ids = body["ids"]
        if not folder.is_absolute() or not folder.is_dir() or not isinstance(ids, list) or not 1 <= len(ids) <= 100:
            raise ValueError("Выберите папку и записи для экспорта.")
        files = library.export(ids, folder, body["kind"], body.get("timestamps"), body.get("speakers"))
        return jsonify(files=files)

    @app.get("/transcript-media/<job_id>")
    def transcript_media(job_id):
        if not re.fullmatch("[a-f0-9]{32}", job_id):
            abort(404)
        with library.condition:
            try:
                job = library.get(job_id)
            except ValueError:
                abort(404)
        proxy = library.root / (job_id + ".m4a")
        source = Path(job["source"])
        if proxy.is_file():
            return send_file(proxy, mimetype="audio/mp4", conditional=True)
        mime = PLAYABLE.get(source.suffix.lower())
        if not mime or not source.is_file():
            abort(404)
        return send_file(source, mimetype=mime, conditional=True)

    return library
