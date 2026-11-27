"""Local Qwen conversations, practical source notes and editable storyboards."""

from concurrent.futures import ThreadPoolExecutor
import json
import logging
import re
from pathlib import Path
from threading import Lock
from time import time
from uuid import uuid4

from flask import abort, jsonify, request, send_file

from .clip_analysis import qwen_session
from .projects import PROJECT_ID, write_json
from .script_writer import EMPTY_BRIEF, WRITER, array, brief_for, draft, excerpt, generate_json, required, script_preview
from .transcripts import transcript_errors

LOGGER = logging.getLogger(__name__)
SOURCES = json.loads(Path(__file__).with_name("script_sources.json").read_text(encoding="utf-8"))
PLATFORMS = {"youtube": "YouTube", "shorts": "YouTube Shorts", "reels": "Instagram Reels", "tiktok": "TikTok",
             "vk": "VK", "rutube": "RuTube", "dzen": "Дзен", "general": "Любая площадка"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}

def context_for(platform, mode):
    return [note for note in SOURCES if mode in note["format"] and (not note["platform"] or platform in note["platform"])]


def fit_shots(shots, seconds):
    """Allocate whole seconds by relative weight while preserving the exact final time."""
    shots = shots[:min(14, seconds)]
    if not shots:
        raise ValueError("Модель не смогла составить раскадровку.")
    total = sum(shot["seconds"] for shot in shots)
    elapsed = 0
    allocated = seconds - len(shots)
    for shot in shots:
        before = round(allocated * elapsed / total)
        elapsed += shot["seconds"]
        shot["seconds"] = 1 + round(allocated * elapsed / total) - before
    return shots


def relevant_notes(notes, query):
    terms = {word for word in re.findall(r"[^\W\d_]{4,}", query.casefold())}
    pieces = []
    for paragraph in re.split(r"\n\s*\n", notes.strip()):
        pieces.extend(paragraph[index:index + 700] for index in range(0, len(paragraph), 700))
    picks = sorted(pieces, key=lambda paragraph: len(set(re.findall(r"[^\W\d_]{4,}", paragraph.casefold())) & terms),
                   reverse=True)
    return "\n\n".join(picks[:3])


def story_version(project, number=None):
    if not project["versions"]:
        raise ValueError("Сначала создайте сценарий в чате.")
    if number in (None, ""):
        return project["versions"][-1]
    for version in project["versions"]:
        if version["number"] == int(number):
            return version
    raise ValueError("Версия сценария не найдена.")


def add_version(project, text, based_on=None, brief=""):
    number = project["versions"][-1]["number"] + 1 if project["versions"] else 1
    entry = dict(number=number, text=text, preview=script_preview(text), created=time(), basedOn=based_on, brief=brief)
    project["versions"].append(entry)
    return entry


def upgrade_project(project):
    if "versions" in project:
        return project
    project["versions"], project["boards"] = [], []
    old = project.pop("latest", "")
    if old.strip():
        entry = add_version(project, old)
        for message in reversed(project["messages"]):
            if message["role"] == "assistant" and message["text"] == old:
                message.update(kind="version", version=entry["number"], text=entry["preview"])
                break
    shots = project.pop("shots", [])
    if shots:
        project["boards"].append(dict(id=project["id"], number=1, version=1 if old.strip() else None,
                                      mode="manual", style="sketch", shots=shots, continuity="", reference=""))
    return project


class ScriptStudio:
    def __init__(self, folder, cache):
        self.folder, self.cache = folder, cache
        folder.mkdir(parents=True, exist_ok=True)
        self.guard = Lock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="scenario")
        self.busy = None
        self.stage = ""

    def path(self, key):
        if not isinstance(key, str) or not PROJECT_ID.fullmatch(key):
            raise ValueError("Проект не найден.")
        return self.folder / key

    def read(self, key):
        path = self.path(key) / "script.json"
        if not path.is_file():
            raise ValueError("Проект не найден.")
        return upgrade_project(json.loads(path.read_text(encoding="utf-8")))

    def save(self, project):
        project["updated"] = time()
        write_json(self.path(project["id"]) / "script.json", project)

    def public(self, project):
        messages = []
        for item in project["messages"]:
            if item.get("kind") == "script":
                item = dict(item, text=script_preview(item["text"]), legacy=item["text"] if len(item["text"]) > 340 else "")
            messages.append(item)
        return dict(project, messages=messages, busy=self.busy == project["id"],
                    stage=self.stage if self.busy == project["id"] else "",
                    sources=[{field: row.get(field) for field in ("title", "url", "year")}
                             for row in context_for(project["platform"], project["mode"])])

    def list(self):
        with self.guard:
            projects = [json.loads(path.read_text(encoding="utf-8")) for path in self.folder.glob("*/script.json")]
            fields = ("id", "title", "updated")
            return [{field: row[field] for field in fields} for row in sorted(projects, key=lambda row: row["updated"], reverse=True)]

    def create(self, data):
        title = str(data.get("title", "")).strip()[:140]
        mode, platform = data.get("mode"), data.get("platform")
        seconds = int(data["seconds"])
        if not title or mode not in {"short", "long"} or platform not in PLATFORMS or not 5 <= seconds <= 3600:
            raise ValueError("Укажите тему, формат и длительность от 5 секунд до часа.")
        key = uuid4().hex
        project = dict(id=key, title=title, brief=str(data.get("title", "")).strip()[:4500], mode=mode,
                       platform=platform, seconds=seconds, audience=str(data.get("audience", "")).strip()[:500],
                       messages=[], versions=[], boards=[], notes="", error="")
        with self.guard:
            self.path(key).mkdir()
            self.save(project)
            return self.public(project)

    def launch(self, project, callback, *args):
        if self.busy:
            raise ValueError("Дождитесь окончания текущей генерации.")
        project["error"] = ""
        self.save(project)
        self.busy, self.stage = project["id"], "Подготовка…"
        self.worker.submit(self.run, project["id"], callback, *args)

    def submit(self, key, mode, text, parent):
        if mode not in {"chat", "draft", "discussion"} or (mode == "discussion" and not text):
            raise ValueError("Введите сообщение.")
        with self.guard:
            if self.busy:
                raise ValueError("Дождитесь ответа Qwen.")
            project = self.read(key)
            previous = story_version(project, parent) if project["versions"] else None
            task = "discussion" if mode == "discussion" else "draft"
            brief = brief_for(project, text)
            if previous and previous.get("brief"):
                brief = previous["brief"]
                if task == "draft" and text and not EMPTY_BRIEF.fullmatch(text):
                    brief = excerpt(brief + "\nУточнение автора: " + text, 4700)
            project["messages"].append(dict(role="user", mode=task, text=text or "Написать сценарий"))
            self.launch(project, self.write, task, text, brief, previous)

    def progress(self, message, percent=None):
        with self.guard:
            self.stage = message if percent is None else f"{message} · {percent}%"

    def run(self, key, callback, *args):
        error = ""
        try:
            callback(key, *args)
        except Exception as reason:
            LOGGER.warning("Local creative job failed", exc_info=True)
            error = str(reason) if isinstance(reason, ValueError) else "Генерация прервалась. Повторите запрос."
        finally:
            with self.guard:
                try:
                    if error:
                        project = self.read(key)
                        project["error"] = error
                        self.save(project)
                finally:
                    self.busy, self.stage = None, ""

    def write(self, key, task, text, brief, previous):
        project = self.read(key)
        references = context_for(project["platform"], project["mode"])
        # The brief and genre stay in charge. Platform notes remain context for discussion.
        author_notes = relevant_notes(project["notes"], brief)
        if not re.search(r"асмр|asmr|медитац", brief, re.I):
            practices = "\n".join(reference["note"] for reference in references[:2])[:650]
            author_notes = (author_notes[:745] + "\nСправка по площадке для автора, не часть реплик: " + practices).strip()
        base = previous["text"] if previous else ""
        with qwen_session(self.cache, self.progress, WRITER) as complete:
            if task == "draft":
                response = draft(complete, project, brief, author_notes, base, self.progress)
            else:
                self.progress("Qwen отвечает")
                notes = "\n".join(reference["note"] for reference in references)[:2100]
                history = [dict(role=item["role"], content=item["text"][:650])
                           for item in project["messages"][:-1] if item.get("mode") == "discussion" or item.get("kind") == "discussion"][-6:]
                response = complete({"messages": [
                    {"role": "system", "content": "Ты русскоязычный редактор видео. Ответь конкретно на вопрос автора. "
                     "Учитывай жанр и бриф. Отметь, какие предложения основаны на справке, и не обещай охваты."},
                    *history, {"role": "user", "content": f"Бриф: {brief}\n"
                     f"Сценарий: {excerpt(base, 1800)}\nСправка: {notes}\nВопрос: {text}"},
                ], "temperature": .5, "repeat_penalty": 1.14, "max_tokens": 1000}).strip()
                if not response:
                    raise ValueError("Qwen не вернул ответ. Попробуйте ещё раз.")
        with self.guard:
            project = self.read(key)
            if task == "draft":
                entry = add_version(project, response, previous["number"] if previous else None, brief)
                project["messages"].append(dict(role="assistant", kind="version", text=entry["preview"], version=entry["number"]))
            else:
                project["messages"].append(dict(role="assistant", kind="discussion", text=response))
            self.save(project)

    def edit(self, key, data):
        with self.guard:
            if self.busy == key:
                raise ValueError("Дождитесь конца генерации.")
            project = self.read(key)
            if "notes" in data:
                project["notes"] = str(data["notes"]).strip()[:12000]
            else:
                text = str(data.get("content", "")).strip()[:80000]
                if not text:
                    raise ValueError("Введите текст сценария.")
                previous = story_version(project, data.get("version"))
                if text != previous["text"]:
                    entry = add_version(project, text, previous["number"], previous.get("brief", ""))
                    project["messages"].append(dict(role="assistant", kind="version", text=entry["preview"],
                                                    version=entry["number"], author=True))
            self.save(project)
            return self.public(project)

    def select_board(self, project, board_id):
        for board in project["boards"]:
            if board["id"] == board_id:
                return board
        raise ValueError("Вариант раскадровки не найден.")

    def board_action(self, key, data):
        with self.guard:
            if self.busy == key:
                raise ValueError("Дождитесь конца генерации.")
            project = self.read(key)
            action = data["action"]
            if action in {"manual", "auto"}:
                style = data["style"]
                if style not in {"sketch", "realism"}:
                    raise ValueError("Выберите стиль кадров.")
                version = story_version(project, data["version"])
                self.check_board_limit(project)
                board = dict(id=uuid4().hex, number=len(project["boards"]) + 1, style=style,
                             mode=action, version=version["number"], continuity="", shots=[], reference="")
                project["boards"].append(board)
                if action == "auto":
                    self.launch(project, self.create_board, board["id"])
                else:
                    self.save(project)
                return jsonify(board=board["id"], project=self.public(project))
            board = self.select_board(project, data["board"])
            if action == "add":
                self.add_shot(project, board, data)
            elif action == "edit":
                shot = self.select_shot(board, data["shot"])
                shot["instruction"] = str(data.get("instruction", ""))[:1500].strip()
                self.save(project)
            elif action == "remove":
                shot = self.select_shot(board, data["shot"])
                board["shots"].remove(shot)
                self.save(project)
            elif action in {"render", "regenerate"}:
                if not board["shots"] and board["mode"] == "manual":
                    raise ValueError("Выделите в сценарии хотя бы один кадр.")
                if action == "regenerate":
                    shot = self.select_shot(board, data["shot"])
                    shot["instruction"] = str(data.get("instruction", shot.get("instruction", "")))[:1500].strip()
                    self.launch(project, self.render_board, board["id"], [shot["id"]], True)
                elif not board["shots"]:
                    self.launch(project, self.create_board, board["id"])
                else:
                    pending = [shot["id"] for shot in board["shots"] if not shot.get("image")]
                    if pending:
                        self.launch(project, self.render_board, board["id"], pending)
            else:
                raise ValueError("Неизвестное действие.")
            return jsonify(board=board["id"], project=self.public(project))

    @staticmethod
    def check_board_limit(project):
        if len(project["boards"]) >= 120:
            raise ValueError("В проекте уже 120 вариантов. Начните новый проект.")

    @staticmethod
    def select_shot(board, shot_id):
        for shot in board["shots"]:
            if shot["id"] == shot_id:
                return shot
        raise ValueError("Кадр не найден.")

    def add_shot(self, project, board, data):
        if board["mode"] != "manual":
            raise ValueError("Выберите ручную раскадровку.")
        source = story_version(project, board["version"])["text"]
        start, end = int(data["start"]), int(data["end"])
        if not 0 <= start < end <= len(source) or end - start > 2500:
            raise ValueError("Выделите один блок текста длиной до 2500 символов.")
        if len(board["shots"]) >= 60:
            raise ValueError("В ручной раскадровке может быть до 60 кадров.")
        if any(start < shot["end"] and end > shot["start"] for shot in board["shots"]):
            raise ValueError("Этот текст уже относится к кадру. Выделите другой блок.")
        board["shots"].append(dict(id=uuid4().hex, start=start, end=end, source=source[start:end],
                                   instruction=str(data.get("instruction", ""))[:1500].strip(), image="", visual="", prompt=""))
        board["shots"].sort(key=lambda shot: shot["start"])
        self.save(project)

    def set_reference(self, key, board_id, file):
        payload = file.stream.read(8_000_001)
        if len(payload) > 8_000_000:
            raise ValueError("Изображение должно весить до 8 МБ.")
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            extension = ".png"
        elif payload.startswith(b"\xff\xd8\xff"):
            extension = ".jpg"
        elif payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
            extension = ".webp"
        else:
            raise ValueError("Выберите PNG, JPEG или WebP.")
        with self.guard:
            if self.busy == key:
                raise ValueError("Дождитесь конца генерации.")
            project = self.read(key)
            board = self.select_board(project, board_id)
            filename = uuid4().hex + extension
            (self.path(key) / filename).write_bytes(payload)
            board["reference"] = filename
            self.save(project)
            return self.public(project)

    def create_board(self, key, board_id):
        self.render_board(key, board_id, None)

    def prompt_shots(self, project, board, shots, revise=False):
        version = story_version(project, board["version"])
        user_brief = version.get("brief") or brief_for(project, "")
        prop = {"type": "string", "maxLength": 170}
        shot_schema = required({"visual": prop, "prompt": {"type": "string", "maxLength": 140}})
        n = len(shots) if shots else min(8, max(3, project["seconds"] // 70 + 2))
        schema = required({"continuity": prop, "frames": array(shot_schema, n, n)})
        input_frames = [dict(source=shot.get("source", ""), previous=shot.get("visual", ""),
                             assignment=shot.get("instruction", "")) for shot in shots]
        with qwen_session(self.cache, self.progress, WRITER) as complete:
            self.progress("Qwen планирует кадры")
            result = generate_json(
                complete,
                "Ты режиссёр раскадровки. Читай бриф и сценарий. Соблюдай ракурс и основную геометрию сцены. "
                "Задача — сделать разные кадры одного видео. В continuity перечисли неизменные черты взрослого "
                "персонажа, одежду и детали локации на английском, до 22 слов. Используй уже заданный "
                "continuity при наличии. В visual опиши только видимые действия на русском. В prompt опиши "
                "уникальный момент и композицию на простом английском, до 16 слов. Не перечисляй тексты для надписей. "
                "Если даны blocks, создай по кадру на блок в том же порядке. Задание в блоке приоритетно. "
                "При перегенерации предложи новую композицию прошлому кадру, сохраняя стиль и локацию.",
                dict(brief=user_brief[:2200], script=excerpt(version["text"], 4700), blocks=input_frames,
                     continuity=board["continuity"], count=n, revision=revise, style=board["style"]),
                schema, lambda data: validate_frame_plan(data, n), min(2500, max(500, n * 220)),
            )
        return result

    def update_board(self, key, board_id, apply):
        with self.guard:
            project = self.read(key)
            target = self.select_board(project, board_id)
            apply(target)
            self.save(project)

    def render_board(self, key, board_id, ids, revise=False):
        from .storyboard_images import StoryboardRenderer

        project = self.read(key)
        board = self.select_board(project, board_id)
        prepare = board["shots"] if ids is None else [self.select_shot(board, shot_id) for shot_id in ids]
        need_prompts = [shot for shot in prepare if not shot.get("prompt") or revise]
        if ids is None or need_prompts:
            # Each local prompt stays small even when the author marked many frames.
            batches = [None] if ids is None else [need_prompts[i:i+4] for i in range(0, len(need_prompts), 4)]
            for batch in batches:
                result = self.prompt_shots(project, board, batch or [], revise)
                if not board["continuity"]:
                    board["continuity"] = result["continuity"]
                if batch is None:
                    slots = fit_shots([dict(row, seconds=1) for row in result["frames"]], project["seconds"])
                    at = 0
                    for shot in slots:
                        shot.update(id=uuid4().hex, image="", instruction="", time=at)
                        at += shot["seconds"]
                    board["shots"] = prepare = slots
                else:
                    for shot, labels in zip(batch, result["frames"]):
                        shot.update(labels)
            def save_plan(target):
                target["continuity"] = board["continuity"]
                target["shots"] = board["shots"]
            self.update_board(key, board_id, save_plan)
        renderer = StoryboardRenderer(self.cache, self.progress)
        folder = self.path(key)
        for index, shot in enumerate(prepare, 1):
            self.progress(f"Иллюстрация {index} из {len(prepare)}")
            seed = int(uuid4().hex[:7], 16)
            filename = uuid4().hex + ".png"
            ref = board.get("reference")
            generated = renderer.draw(folder / filename, board["style"], project["mode"], board["continuity"],
                                      shot["prompt"], seed, (folder / ref) if ref else None)
            if not generated.is_file():
                raise ValueError("Модель не записала изображение.")
            if not board.get("reference"):
                board["reference"] = filename
            def save_image(target):
                entry = self.select_shot(target, shot["id"])
                entry["image"], entry["seed"] = filename, seed
                target["reference"] = board["reference"]
            self.update_board(key, board_id, save_image)


def validate_frame_plan(data, count):
    frames = data["frames"]
    if len(frames) != count:
        raise ValueError("Incomplete storyboard")
    checked = [dict(visual=shot["visual"].strip()[:800], prompt=shot["prompt"].strip()[:300]) for shot in frames]
    if any(not shot["visual"] or not shot["prompt"] for shot in checked):
        raise ValueError("Empty shot")
    if len({shot["visual"].casefold() for shot in checked}) != count:
        raise ValueError("Repeated shot")
    return dict(continuity=data["continuity"].strip()[:220], frames=checked)


def attach_scripts(app, root, cache):
    studio = ScriptStudio(root, cache)

    @app.get("/api/scripts")
    def scripts():
        return jsonify(projects=studio.list())

    @app.post("/api/scripts")
    @transcript_errors
    def scripts_create():
        return jsonify(studio.create(request.get_json())), 201

    @app.get("/api/scripts/<key>")
    @transcript_errors
    def scripts_state(key):
        with studio.guard:
            return jsonify(studio.public(studio.read(key)))

    @app.post("/api/scripts/<key>")
    @transcript_errors
    def scripts_talk(key):
        data = request.get_json()
        studio.submit(key, str(data["mode"]), str(data.get("text", "")).strip()[:6500], data.get("version"))
        return "", 202

    @app.post("/api/scripts/<key>/edit")
    @transcript_errors
    def scripts_edit(key):
        return jsonify(studio.edit(key, request.get_json()))

    @app.post("/api/scripts/<key>/boards")
    @transcript_errors
    def scripts_board(key):
        return studio.board_action(key, request.get_json())

    @app.post("/api/scripts/<key>/boards/<board_id>/reference")
    @transcript_errors
    def scripts_reference(key, board_id):
        request.max_content_length = 8_300_000
        return jsonify(studio.set_reference(key, board_id, request.files["image"]))

    @app.get("/script-media/<key>/<name>")
    def scripts_image(key, name):
        ext = Path(name).suffix
        if not PROJECT_ID.fullmatch(key) or not PROJECT_ID.fullmatch(Path(name).stem) or ext not in IMAGE_TYPES:
            abort(404)
        path = studio.path(key) / name
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype=IMAGE_TYPES[ext], conditional=True)

    @app.post("/api/scripts/<key>/export")
    @transcript_errors
    def scripts_export(key):
        from tempfile import TemporaryDirectory
        from .media import save_file

        data = request.get_json()
        folder = Path(data["folder"])
        if not folder.is_absolute() or not folder.is_dir() or data["kind"] != "txt":
            raise ValueError("Выберите папку для сценария.")
        with studio.guard:
            project = studio.read(key)
            version = story_version(project, data.get("version"))
        with TemporaryDirectory(dir=folder, prefix=".videoport-script-") as directory:
            staged = Path(directory) / "script.txt"
            staged.write_text(version["text"], encoding="utf-8")
            result = save_file(staged, folder, f"{project['title']} · Версия {version['number']}", key)
        return jsonify(name=result.name)

    return studio
