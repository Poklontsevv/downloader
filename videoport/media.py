from __future__ import annotations

import errno
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.networking.exceptions import RequestError
from yt_dlp.utils import DownloadError

VIDEO_ID = re.compile(r"[\w-]{11}", re.ASCII)
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
IMAGE_HOSTS = {"i.ytimg.com", "i9.ytimg.com", "img.youtube.com"}
IMAGE_EXTENSIONS = {"image/jpeg": "jpg", "image/webp": "webp", "image/png": "png", "image/avif": "avif"}
DEVICE_NAME = re.compile(r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[1-9¹²³])", re.I)


class MediaError(Exception):
    """A failure that can be displayed in the workspace."""


def parse_video_id(text: str) -> str | None:
    text = text.strip()
    if "://" not in text:
        text = "https://" + text
    try:
        url = urlsplit(text)
        if url.scheme not in {"http", "https"} or url.username or url.password or url.port is not None:
            return None
        parts = url.path.strip("/").split("/")
        video_id = ""
        if url.hostname in {"youtu.be", "www.youtu.be"} and len(parts) == 1:
            video_id = parts[0]
        elif url.hostname in YOUTUBE_HOSTS:
            if parts == ["watch"]:
                video_id = parse_qs(url.query).get("v", [""])[0]
            elif len(parts) == 2 and parts[0] in {"shorts", "live", "embed", "v"}:
                video_id = parts[1]
        elif url.hostname in {"youtube-nocookie.com", "www.youtube-nocookie.com"}:
            if len(parts) == 2 and parts[0] == "embed":
                video_id = parts[1]
        return video_id if VIDEO_ID.fullmatch(video_id) else None
    except ValueError:
        return None


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def file_stem(title: str, video_id: str) -> str:
    clean = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "", title)
    clean = clean.strip().rstrip(". ")
    clean = clean.encode("utf-8")[:220].decode("utf-8", errors="ignore").rstrip(". ")
    if DEVICE_NAME.fullmatch(clean.partition(".")[0].rstrip()):
        clean = "_" + clean
    return clean or video_id


def save_file(source: Path, output: Path, title: str, video_id: str) -> Path:
    stem = file_stem(title, video_id)
    number = 1
    while True:
        suffix = f" ({number})" if number > 1 else ""
        target = output / f"{stem}{suffix}{source.suffix}"
        try:
            os.link(source, target)
        except FileExistsError:
            number += 1
            continue
        except OSError as error:
            if error.errno not in {errno.EPERM, errno.EACCES, errno.ENOSYS, errno.ENOTSUP, errno.EXDEV, errno.EMLINK}:
                raise
            try:
                destination = target.open("xb")
            except FileExistsError:
                number += 1
                continue
            try:
                with destination, source.open("rb") as original:
                    shutil.copyfileobj(original, destination, length=1024 * 1024)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        return target


def resolutions(info: dict) -> list[dict]:
    formats = [fmt for fmt in info.get("formats", []) if not fmt.get("has_drm")]
    separate_audio = any(
        fmt.get("vcodec") == "none" and fmt.get("acodec") not in {None, "none"} for fmt in formats
    )
    sizes = set()
    for fmt in formats:
        width, height = fmt.get("width"), fmt.get("height")
        has_audio = fmt.get("acodec") not in {None, "none"} or separate_audio
        if has_audio and width and height and fmt.get("vcodec") not in {None, "none"}:
            sizes.add((int(width), int(height)))
    return [
        {"value": f"{w}x{h}", "width": w, "height": h, "p": min(w, h)}
        for w, h in sorted(sizes, key=lambda size: (min(size), size[0] * size[1]), reverse=True)
    ]


def thumbnails(info: dict) -> list[str]:
    candidates = sorted(
        info.get("thumbnails") or [],
        key=lambda img: ((img.get("width") or 0) * (img.get("height") or 0), img.get("preference") or 0),
        reverse=True,
    )
    urls = [image.get("url", "") for image in candidates] + [info.get("thumbnail") or ""]
    return list(dict.fromkeys(
        url for url in urls
        if url.startswith("https://") and urlsplit(url).netloc in IMAGE_HOSTS
    ))


def best_format(formats: list[dict], quality: str) -> dict:
    width, height = map(int, quality.split("x"))
    available = [fmt for fmt in reversed(formats) if not fmt.get("has_drm")]
    audio = next(
        (fmt for fmt in available if fmt.get("vcodec") == "none" and fmt.get("acodec") not in {None, "none"}),
        None,
    )
    for fmt in available:
        if fmt.get("width") == width and fmt.get("height") == height and fmt.get("vcodec") not in {None, "none"}:
            if fmt.get("acodec") not in {None, "none"}:
                return fmt
            if audio:
                return {
                    "format_id": f"{fmt['format_id']}+{audio['format_id']}",
                    "ext": "mkv",
                    "requested_formats": [fmt, audio],
                    "protocol": f"{fmt['protocol']}+{audio['protocol']}",
                }
    raise MediaError("YouTube больше не предлагает это разрешение со звуком. Обновите данные ролика.")


def ffmpeg_profile(source: Path, target: Path, profile: str) -> None:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next((stream for stream in streams if stream["codec_type"] == "video"), None)
    audio = next((stream for stream in streams if stream["codec_type"] == "audio"), None)
    if not video or not audio:
        raise MediaError("В загруженном файле отсутствует видеодорожка или звук.")

    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0"]
    if profile == "mkv":
        command += ["-c", "copy"]
    else:
        if video["codec_name"] == "h264" and video.get("pix_fmt") == "yuv420p":
            command += ["-c:v", "copy"]
        else:
            command += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p"]
            if video["width"] % 2 or video["height"] % 2:
                command += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        if audio["codec_name"] == "aac":
            command += ["-c:a", "copy"]
        else:
            command += ["-c:a", "aac", "-b:a", "192k"]
        command += ["-movflags", "+faststart"]

    subprocess.run([*command, str(target)], capture_output=True, check=True)


def error_message(error: Exception) -> str:
    if isinstance(error, MediaError):
        return str(error)
    if isinstance(error, DownloadError):
        text = str(error).lower()
        if "proxy" in text or "timed out" in text or "unable to download webpage" in text:
            return "Не удалось подключиться к YouTube. Проверьте доступ к сайту и попробуйте снова."
        if "sign in to confirm" in text or "not a bot" in text or "429" in text:
            return "YouTube ограничил запрос. Попробуйте позже или войдите через локальный профиль браузера."
        if any(fragment in text for fragment in ("private", "members-only", "login", "sign in", "age-restricted")):
            return "Для этого видео нужен профиль браузера с доступом к ролику."
        if "no formats" in text or "format is not available" in text:
            return "YouTube сейчас не отдаёт подходящие форматы для этого ролика."
        return "YouTube не вернул видео. Проверьте ссылку, доступ к ролику и версию yt-dlp."
    if isinstance(error, subprocess.CalledProcessError):
        return "FFmpeg не смог собрать файл. Проверьте установку FFmpeg или попробуйте формат MKV."
    if isinstance(error, OSError):
        return "Не удалось записать файл. Проверьте папку выгрузки и свободное место."
    return "Произошла ошибка. Попробуйте ещё раз."


class MediaService:
    def __init__(self, browser: str | None = None, runtime: str = "node"):
        self.options = {
            "allowed_extractors": ["youtube"],
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "noplaylist": True,
            "js_runtimes": {runtime: {}},
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
        }
        if browser:
            self.options["cookiesfrombrowser"] = (browser,)

    def inspect(self, video_id: str) -> dict:
        with YoutubeDL({**self.options, "ignore_no_formats_error": True}) as client:
            info = client.extract_info(watch_url(video_id), download=False)
        return {
            "title": info.get("title") or video_id,
            "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration"),
            "live": info.get("live_status") in {"is_live", "is_upcoming", "post_live"},
            "qualities": resolutions(info),
            "thumbnails": thumbnails(info),
        }

    def cover(self, urls: list[str], directory: Path) -> Path:
        with YoutubeDL(self.options) as client:
            for url in urls:
                try:
                    with client.urlopen(url) as response:
                        extension = IMAGE_EXTENSIONS.get(response.headers.get("Content-Type", "").split(";")[0].lower())
                        if not extension:
                            continue
                        cover = directory / ("cover." + extension)
                        with cover.open("wb") as stream:
                            shutil.copyfileobj(response, stream)
                        return cover
                except RequestError:
                    continue
        raise MediaError("YouTube не отдал обложку. Обновите данные ролика и попробуйте снова.")

    def video(
        self, video_id: str, quality: str, profile: str, directory: Path,
        progress: Callable[[str, int | None], None],
    ) -> Path:
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise MediaError("Установите FFmpeg и FFprobe, затем повторите скачивание.")

        maximum = 0

        def hook(event: dict) -> None:
            nonlocal maximum
            if event.get("status") == "downloading":
                total = event.get("total_bytes") or event.get("total_bytes_estimate")
                if total:
                    # Video and audio may be separate. Keep the indicator monotonic across streams.
                    percent = min(96, round(95 * (event.get("downloaded_bytes") or 0) / total))
                    maximum = max(maximum, percent)
                    progress("downloading", maximum)
            elif event.get("status") == "finished":
                progress("downloading", maximum or None)

        def select(context: dict):
            yield best_format(context["formats"], quality)

        options = {
            **self.options,
            "format": select,
            "outtmpl": {"default": str(directory / "source.%(ext)s")},
            "merge_output_format": "mkv",
            "progress_hooks": [hook],
        }
        if profile == "mp4":
            options["format_sort"] = ["vcodec:h264", "acodec:aac"]
        with YoutubeDL(options) as client:
            info = client.extract_info(watch_url(video_id), download=True)

        files = info.get("requested_downloads") or []
        source = Path(files[0]["filepath"]) if files and files[0].get("filepath") else None
        if source is None or not source.is_file():
            raise MediaError("Загрузка завершилась без видеофайла.")

        progress("processing", None)
        target = directory / f"export.{profile}"
        ffmpeg_profile(source, target, profile)
        return target
