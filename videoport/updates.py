"""Check and download the official Windows installer for in-place updates."""

from concurrent.futures import ThreadPoolExecutor
import logging
import re
from threading import Lock

import requests
from flask import jsonify, request

from .clip_analysis import download_asset

VERSION = "4.7.0"
REPOSITORY = "Poklontsevv/downloader"
RELEASE_API = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
TAG = re.compile(r"v(\d{1,3})\.(\d{1,3})\.(\d{1,3})\Z")
DIGEST = re.compile(r"sha256:([a-f0-9]{64})\Z")
LOGGER = logging.getLogger(__name__)


def latest_installer():
    response = requests.get(RELEASE_API, timeout=(5, 12))
    if response.status_code == 404:
        return None
    response.raise_for_status()
    release = response.json()
    tag = release.get("tag_name", "")
    version = TAG.fullmatch(tag)
    if not version or tuple(map(int, version.groups())) <= tuple(map(int, VERSION.split("."))):
        return None
    filename = f"Videoport-Setup-x64-{tag}.exe"
    uri = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{filename}"
    for asset in release.get("assets", []):
        signature = DIGEST.fullmatch(asset.get("digest") or "")
        if (asset.get("name") == filename and asset.get("browser_download_url") == uri and signature
                and type(asset.get("size")) is int and 1024 <= asset["size"] <= 300_000_000):
            return {"version": tag[1:], "name": filename, "uri": uri, "size": asset["size"], "sha256": signature[1]}
    raise ValueError("Новая версия опубликована без проверяемого установщика.")


class InstallerUpdates:
    def __init__(self, cache):
        self.cache = cache
        self.guard = Lock()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="update")
        self.state = {"current": VERSION, "available": None, "state": "idle", "percent": None, "error": ""}

    def snapshot(self):
        with self.guard:
            return self.state.copy()

    def start(self, action):
        with self.guard:
            if self.state["state"] in {"checking", "downloading"}:
                return
            if action == "download":
                asset = self.state["available"]
                if not asset:
                    raise ValueError("Сначала проверьте обновления.")
                self.state.update(state="downloading", percent=0, error="")
                self.worker.submit(self.download, asset)
            elif action == "check":
                self.state.update(state="checking", percent=None, error="")
                self.worker.submit(self.check)
            else:
                raise ValueError("Неизвестное действие.")

    def change(self, **fields):
        with self.guard:
            self.state.update(fields)

    def check(self):
        try:
            asset = latest_installer()
            self.change(available=asset, state="available" if asset else "current")
        except (ValueError, requests.RequestException):
            LOGGER.warning("Videoport update check failed", exc_info=True)
            self.change(state="error", error="Не удалось проверить обновления на GitHub.")

    def download(self, asset):
        def progress(_label, percent):
            self.change(percent=percent)
        try:
            manifest = (asset["uri"], asset["sha256"], asset["size"])
            download_asset(manifest, self.cache, progress, "Загрузка установщика")
            self.change(state="ready", percent=100)
        except (ValueError, OSError, requests.RequestException):
            LOGGER.warning("Videoport update download failed", exc_info=True)
            self.change(state="error", error="Не удалось загрузить и проверить установщик.")


def attach_updates(app, cache):
    updates = InstallerUpdates(cache)

    @app.get("/api/update")
    def update_state():
        return jsonify(updates.snapshot())

    @app.post("/api/update")
    def update_action():
        body = request.get_json(silent=True)
        try:
            updates.start(body.get("action") if isinstance(body, dict) else None)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return "", 202

    return updates
