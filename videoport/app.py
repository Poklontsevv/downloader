from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from uuid import uuid4

from flask import Flask, abort, jsonify, request, send_from_directory

from .media import MediaService, error_message, parse_video_id, save_file, watch_url
from .sources import PLATFORMS, Source, catalog, parse_source

PAGES_ORIGIN = "https://poklontsevv.github.io"
ROOT = Path(__file__).resolve().parent.parent
BATCH_LIMIT = 50
ACTIVE = {"queued", "downloading", "processing"}


class Workspace:
    def __init__(self, media: MediaService, output: Path, preferences: Path | None = None):
        self.media = media
        self.output = output.resolve()
        self.preferences = preferences
        self.folders = dict.fromkeys(PLATFORMS, self.output)
        self.catalogs: dict[str, dict] = {}
        self.read_preferences()
        self.videos: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.lock = Lock()
        self.lookups = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lookup")
        self.catalog_worker = ThreadPoolExecutor(max_workers=3, thread_name_prefix="catalog")
        self.downloads = ThreadPoolExecutor(max_workers=1, thread_name_prefix="download")
        self.covers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cover")

    def read_preferences(self) -> None:
        if not self.preferences:
            return
        try:
            data = json.loads(self.preferences.read_text(encoding="utf-8"))
            for platform in PLATFORMS:
                folder = data.get("folders", {}).get(platform)
                if isinstance(folder, str) and Path(folder).is_absolute() and Path(folder).is_dir():
                    self.folders[platform] = Path(folder)
                if data.get("browsers", {}).get(platform) == "firefox":
                    self.media.browsers[platform] = "firefox"
        except (OSError, ValueError, AttributeError):
            pass

    def configure(self, platform: str, *, folder: Path | None = None, firefox: bool | None = None) -> None:
        with self.lock:
            folders = self.folders.copy()
            browsers = self.media.browsers.copy()
            if folder:
                folders[platform] = folder
            if firefox is not None:
                browsers[platform] = "firefox" if firefox else None
            if self.preferences:
                with TemporaryDirectory(dir=self.preferences.parent) as temp:
                    config = Path(temp) / "preferences.json"
                    config.write_text(json.dumps({"folders": {k: str(v) for k, v in folders.items()},
                                                  "browsers": browsers}), encoding="utf-8")
                    config.replace(self.preferences)
            self.folders = folders
            self.media.browsers = browsers

    def add(self, lines: list[str], platform: str = "youtube") -> tuple[list[str], list[dict]]:
        accepted = []
        rejected = []
        with self.lock:
            for number, link in enumerate(lines, 1):
                if not isinstance(link, str) or not (source := parse_source(link, platform)):
                    rejected.append({"line": number})
                    continue
                if source.collection or platform == "instagram":
                    key = sha256(source.url.encode()).hexdigest()[:24]
                    if self.catalogs.get(key, {}).get("state") != "loading":
                        self.catalogs[key] = {"id": key, "platform": platform, "state": "loading", "count": 0}
                        self.catalog_worker.submit(self.load_catalog, key, source)
                    accepted.append(key)
                    continue
                video_id = parse_video_id(source.url) if platform == "youtube" else sha256(source.url.encode()).hexdigest()[:24]
                accepted.append(video_id)
                if video_id not in self.videos or self.videos[video_id]["state"] == "error":
                    self.videos[video_id] = {"id": video_id, "url": source.url, "platform": platform, "state": "loading"}
                    self.lookups.submit(self.inspect, video_id)
        return list(dict.fromkeys(accepted)), rejected

    def load_catalog(self, key: str, source: Source) -> None:
        seen = set()
        error = None
        try:
            for item in catalog(source, self.media.platform_options(source.platform)):
                external_id = str(item.get("id") or "")
                if not external_id:
                    continue
                rich = bool(item.get("formats") or item.get("videoport_photo"))
                if source.platform == "youtube":
                    if not parse_video_id(watch_url(external_id)):
                        continue
                    video_id, url = external_id, watch_url(external_id)
                else:
                    if rich:
                        url = source.url
                    else:
                        raw = item.get("webpage_url") or item.get("url") or ""
                        if not isinstance(raw, str):
                            continue
                        entry = parse_source(raw, source.platform)
                        if entry is None or entry.collection:
                            continue
                        url = entry.url
                    video_id = sha256(f"{source.platform}:{external_id}".encode()).hexdigest()[:24]
                if video_id in seen:
                    continue
                seen.add(video_id)
                described = self.media.describe(item, external_id)
                covers = described.pop("thumbnails")
                video = {**described, "id": video_id, "platform": source.platform, "url": url, "covers": covers,
                         "preview": covers[0] if covers else None, "state": "ready",
                         "has_video": described["has_video"] or not rich, "details": rich}
                if rich and source.platform == "instagram":
                    video["_source_info"] = item
                    video["photo"] = described["photo"] or (not described["has_video"] and bool(covers))
                with self.lock:
                    previous = self.videos.get(video_id)
                    video["catalog_ids"] = list(dict.fromkeys([*(previous or {}).get("catalog_ids", []), key]))
                    if not previous or (previous["state"] != "loading" and not previous.get("details")):
                        self.videos[video_id] = video
                    else:
                        previous["catalog_ids"] = video["catalog_ids"]
                    self.catalogs[key]["count"] = len(seen)
        except Exception as failure:
            error = error_message(failure)
        with self.lock:
            status = self.catalogs[key]
            status["state"] = "error" if error or not seen else "done"
            if status["state"] == "error":
                status["error"] = error or "Доступных материалов не найдено."

    def request_details(self, video_id: str) -> None:
        with self.lock:
            video = self.videos.get(video_id)
            if not video or video.get("details") or video["state"] == "loading":
                return
            video["state"] = "loading"
            self.lookups.submit(self.inspect, video_id)

    def inspect(self, video_id: str) -> None:
        with self.lock:
            video = self.videos[video_id].copy()
        try:
            info = (self.media.inspect(video_id) if video["platform"] == "youtube"
                    else self.media.inspect(video_id, video["platform"], url=video["url"]))
            covers = info.pop("thumbnails")
            covers = covers or video.get("covers", [])
            result = {
                **info, "covers": covers, "preview": covers[0] if covers else video.get("preview"),
                "state": "ready", "details": True, "error": None,
            }
        except Exception as error:
            result = {"state": "error", "error": error_message(error)}
        with self.lock:
            self.videos[video_id].update(result)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "output": str(self.output),
                "folders": {platform: str(folder) for platform, folder in self.folders.items()},
                "firefox": {platform: browser == "firefox" for platform, browser in self.media.browsers.items()},
                "catalogs": [item.copy() for item in self.catalogs.values()],
                "videos": [{k: v for k, v in video.items() if k not in {"covers", "_source_info"}}
                           for video in self.videos.values()],
                "jobs": [{k: v for k, v in job.items() if k != "_folder"} for job in self.jobs.values()],
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

    def file_for(self, job_id: str) -> tuple[Path, str] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return (job["_folder"], job["file"]) if job and job["state"] == "done" else None

    def enqueue(self, items: list[dict], mode: str, profile: str, platform: str = "youtube") -> tuple[list[str], list[dict]]:
        created = []
        skipped = []
        with self.lock:
            active = {(job["video_id"], job["kind"]) for job in self.jobs.values() if job["state"] in ACTIVE}
            for item in items:
                video_id = item.get("id") if isinstance(item, dict) else None
                video = self.videos.get(video_id) if isinstance(video_id, str) else None
                if video is None or video["platform"] != platform or video["state"] not in {"ready", "loading"}:
                    skipped.append({"id": video_id, "reason": "Данные видео ещё недоступны."})
                    continue
                quality = item.get("quality")
                kinds = ("video", "cover") if mode == "both" else (mode,)
                if video.get("photo"):
                    kinds = ("photo",)
                for kind in kinds:
                    if kind == "video" and (video.get("live") or not (
                            (quality == "best" and video.get("has_video")) or
                            any(q["value"] == quality for q in video.get("qualities", [])))):
                        skipped.append({"id": video_id, "reason": "Для скачивания видео нужно доступное разрешение."})
                        continue
                    if kind in {"cover", "photo"} and not video.get("covers"):
                        skipped.append({"id": video_id, "reason": "Площадка не предоставила изображение."})
                        continue

                    if (video_id, kind) in active:
                        skipped.append({"id": video_id, "reason": "Файл уже в очереди."})
                        continue

                    job_id = uuid4().hex
                    self.jobs[job_id] = {
                        "id": job_id, "video_id": video_id, "kind": kind, "state": "queued",
                        "percent": None, "quality": quality if kind == "video" else None,
                        "platform": platform, "_folder": self.folders[platform],
                        "profile": profile if kind == "video" else None,
                    }
                    executor = self.covers if kind in {"photo", "cover"} else self.downloads
                    executor.submit(self.download, job_id, video.copy())
                    active.add((video_id, kind))
                    created.append(job_id)
        return created, skipped

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
            output = job["_folder"]
            output.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix=".videoport-", dir=output) as temp:
                directory = Path(temp)
                platform = video["platform"]
                if job["kind"] in {"cover", "photo"}:
                    source = self.media.cover(video["covers"], directory, platform)
                else:
                    source = self.media.video(video["id"], job["quality"], job["profile"], directory, progress,
                                              platform=platform, url=video["url"], info=video.get("_source_info"))
                target = save_file(source, output, video.get("title") or video["id"], video["id"])
            self.update_job(job_id, state="done", percent=100, file=target.name)
        except Exception as error:
            self.update_job(job_id, state="error", percent=None, error=error_message(error))


def create_app(
    output: Path, media: MediaService, allow_pages: bool = False, *,
    desktop: bool = False, preferences: Path | None = None,
) -> Flask:
    app = Flask(__name__, static_folder=str(ROOT / "docs"), static_url_path="/docs")
    app.config.update(MAX_CONTENT_LENGTH=100_000, TRUSTED_HOSTS=["127.0.0.1", "localhost", "[::1]"])
    workspace = Workspace(media, output, preferences)

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
        if request.path.startswith(("/api/", "/files/")):
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
        return send_from_directory(ROOT, "index.html")

    @app.get("/files/<job_id>")
    def download_file(job_id):
        filename = workspace.file_for(job_id)
        if not filename:
            abort(404)
        return send_from_directory(*filename, as_attachment=True, conditional=True)

    @app.get("/api/workspace")
    def get_workspace():
        return jsonify(**workspace.snapshot(), desktop=desktop)

    @app.post("/api/videos")
    def add_videos():
        body = request.get_json(silent=True)
        lines = body.get("links") if isinstance(body, dict) else None
        if not isinstance(lines, list) or not 1 <= len(lines) <= BATCH_LIMIT:
            return jsonify(error=f"Добавьте от 1 до {BATCH_LIMIT} ссылок за раз."), 400
        platform = body.get("platform", "youtube")
        if platform not in PLATFORMS:
            return jsonify(error="Выберите вкладку площадки."), 400
        accepted, rejected = workspace.add(lines, platform)
        return jsonify(accepted=accepted, rejected=rejected), 202

    @app.post("/api/videos/<video_id>/details")
    def get_details(video_id):
        workspace.request_details(video_id)
        return "", 204

    @app.post("/api/settings/<platform>")
    def configure(platform):
        if platform not in PLATFORMS:
            abort(404)
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            abort(400)
        folder = body.get("folder")
        firefox = body.get("firefox")
        try:
            selected = Path(folder) if isinstance(folder, str) else None
            if folder is not None and (not desktop or selected is None or not selected.is_absolute() or not selected.is_dir()):
                return jsonify(error="Выберите существующую папку на компьютере."), 400
            if firefox is not None and (not desktop or not isinstance(firefox, bool)
                                        or request.headers.get("Origin") == PAGES_ORIGIN):
                return jsonify(error="Вход через Firefox доступен в программе Windows."), 400
            workspace.configure(platform, folder=selected, firefox=firefox)
        except (OSError, ValueError):
            return jsonify(error="Не удалось сохранить настройки папки или входа."), 400
        return "", 204

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
        platform = body.get("platform", "youtube")
        if platform not in PLATFORMS:
            return jsonify(error="Выберите вкладку площадки."), 400
        created, skipped = workspace.enqueue(items, mode, profile, platform)
        return jsonify(created=len(created), jobs=created, skipped=skipped), 202

    return app
