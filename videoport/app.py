from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4

from flask import Flask, jsonify, request, send_from_directory

from .media import MediaService, error_message, parse_video_id, save_file, watch_url

PAGES_ORIGIN = "https://poklontsevv.github.io"
FRONTEND = Path(__file__).resolve().parent.parent / "docs"
BATCH_LIMIT = 50
ACTIVE = {"queued", "downloading", "processing"}


class Workspace:
    def __init__(self, media: MediaService, output: Path):
        self.media = media
        self.output = output.resolve()
        self.videos: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.lock = Lock()
        self.lookups = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lookup")
        self.downloads = ThreadPoolExecutor(max_workers=1, thread_name_prefix="download")

    def add(self, lines: list[str]) -> tuple[list[str], list[dict]]:
        accepted = []
        rejected = []
        with self.lock:
            for number, link in enumerate(lines, 1):
                if not isinstance(link, str) or not (video_id := parse_video_id(link)):
                    rejected.append({"line": number})
                    continue

                if video_id not in accepted:
                    accepted.append(video_id)
                if video_id not in self.videos or self.videos[video_id]["state"] == "error":
                    self.videos[video_id] = {"id": video_id, "url": watch_url(video_id), "state": "loading"}
                    self.lookups.submit(self.inspect, video_id)
        return accepted, rejected

    def inspect(self, video_id: str) -> None:
        try:
            info = self.media.inspect(video_id)
            covers = info.pop("thumbnails")
            result = {
                **info, "covers": covers, "preview": covers[0] if covers else None, "state": "ready",
            }
        except Exception as error:
            result = {"state": "error", "error": error_message(error)}
        with self.lock:
            self.videos[video_id].update(result)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "output": str(self.output),
                "videos": [{k: v for k, v in video.items() if k != "covers"} for video in self.videos.values()],
                "jobs": [job.copy() for job in self.jobs.values()],
            }

    def remove(self, video_id: str) -> bool:
        with self.lock:
            video = self.videos.get(video_id)
            if video is None or video["state"] == "loading":
                return False
            if any(job["video_id"] == video_id and job["state"] in ACTIVE for job in self.jobs.values()):
                return False
            del self.videos[video_id]
            for job_id in [key for key, job in self.jobs.items() if job["video_id"] == video_id]:
                del self.jobs[job_id]
            return True

    def enqueue(self, items: list[dict], mode: str, profile: str) -> tuple[int, list[dict]]:
        count = 0
        skipped = []
        with self.lock:
            for item in items:
                video_id = item.get("id") if isinstance(item, dict) else None
                video = self.videos.get(video_id) if isinstance(video_id, str) else None
                if video is None or video["state"] != "ready":
                    skipped.append({"id": video_id, "reason": "Данные видео ещё недоступны."})
                    continue
                quality = item.get("quality")
                kinds = ("video", "cover") if mode == "both" else (mode,)
                for kind in kinds:
                    if kind == "video" and (video["live"] or not any(q["value"] == quality for q in video["qualities"])):
                        skipped.append({"id": video_id, "reason": "Для скачивания видео нужно доступное разрешение."})
                        continue
                    if kind == "cover" and not video["covers"]:
                        skipped.append({"id": video_id, "reason": "YouTube не предоставил обложку."})
                        continue

                    if any(job["video_id"] == video_id and job["kind"] == kind and job["state"] in ACTIVE
                           for job in self.jobs.values()):
                        skipped.append({"id": video_id, "reason": "Файл уже в очереди."})
                        continue

                    job_id = uuid4().hex
                    self.jobs[job_id] = {
                        "id": job_id, "video_id": video_id, "kind": kind, "state": "queued",
                        "percent": None, "quality": quality if kind == "video" else None,
                        "profile": profile if kind == "video" else None,
                    }
                    self.downloads.submit(self.download, job_id, video.copy())
                    count += 1
        return count, skipped

    def update_job(self, job_id: str, **changes) -> None:
        with self.lock:
            self.jobs[job_id].update(changes)

    def download(self, job_id: str, video: dict) -> None:
        with self.lock:
            job = self.jobs[job_id].copy()

        def progress(state: str, percent: int | None) -> None:
            self.update_job(job_id, state=state, percent=percent)

        try:
            progress("downloading", None)
            self.output.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".videoport-", dir=self.output) as temp:
                directory = Path(temp)
                if job["kind"] == "cover":
                    source = self.media.cover(video["covers"], directory)
                else:
                    source = self.media.video(video["id"], job["quality"], job["profile"], directory, progress)
                target = save_file(source, self.output, video["title"], video["id"])
            self.update_job(job_id, state="done", percent=100, file=target.name)
        except Exception as error:
            self.update_job(job_id, state="error", percent=None, error=error_message(error))


def create_app(output: Path, media: MediaService, allow_pages: bool = False) -> Flask:
    app = Flask(__name__, static_folder=str(FRONTEND), static_url_path="")
    app.config.update(MAX_CONTENT_LENGTH=100_000, TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"])
    workspace = Workspace(media, output)

    @app.before_request
    def check_origin():
        if not request.path.startswith("/api/"):
            return None
        allowed = {request.host_url.rstrip("/")}
        if allow_pages:
            allowed.add(PAGES_ORIGIN)
        origin = request.headers.get("Origin")
        if origin and origin not in allowed:
            return jsonify(error="Этот сайт не подключён к Videoport."), 403
        if request.method != "OPTIONS" and request.headers.get("X-Videoport") != "1":
            return jsonify(error="Откройте интерфейс Videoport для подключения."), 403
        return None

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        if allow_pages and request.path.startswith("/api/") and request.headers.get("Origin") == PAGES_ORIGIN:
            response.headers["Access-Control-Allow-Origin"] = PAGES_ORIGIN
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, DELETE, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Videoport"
            response.headers["Access-Control-Allow-Private-Network"] = "true"
            response.vary.add("Origin")
        return response

    @app.get("/")
    def index():
        return send_from_directory(FRONTEND, "index.html")

    @app.get("/api/workspace")
    def get_workspace():
        return jsonify(workspace.snapshot())

    @app.post("/api/videos")
    def add_videos():
        body = request.get_json(silent=True)
        lines = body.get("links") if isinstance(body, dict) else None
        if not isinstance(lines, list) or not 1 <= len(lines) <= BATCH_LIMIT:
            return jsonify(error=f"Добавьте от 1 до {BATCH_LIMIT} ссылок за раз."), 400
        accepted, rejected = workspace.add(lines)
        return jsonify(accepted=accepted, rejected=rejected), 202

    @app.delete("/api/videos/<video_id>")
    def remove_video(video_id):
        if not workspace.remove(video_id):
            return jsonify(error="Дождитесь окончания обработки ролика."), 409
        return "", 204

    @app.post("/api/jobs")
    def start_job():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Проверьте параметры скачивания."), 400
        items, mode, profile = body.get("items"), body.get("mode"), body.get("profile")
        if (not isinstance(items, list) or not 1 <= len(items) <= BATCH_LIMIT
                or mode not in ("video", "cover", "both") or profile not in ("mp4", "mkv")):
            return jsonify(error="Выберите до 50 роликов и формат скачивания."), 400
        created, skipped = workspace.enqueue(items, mode, profile)
        return jsonify(created=created, skipped=skipped), 202

    return app
