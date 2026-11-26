from __future__ import annotations

from copy import deepcopy
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
    sizes = set()
    for fmt in formats:
        width, height = fmt.get("width"), fmt.get("height")
        if width and height and fmt.get("vcodec") != "none":
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
        url for url in urls if safe_image(url)
    ))


def safe_image(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        cdns = ("cdninstagram.com", "fbcdn.net", "tiktokcdn.com", "tiktokcdn-us.com",
                "tiktokcdn-eu.com", "tiktokv.com", "byteoversea.com", "ibytedtos.com")
        return (parsed.scheme == "https" and not parsed.username and parsed.port is None
                and (host in IMAGE_HOSTS or any(host.endswith("." + cdn) for cdn in cdns)))
    except ValueError:
        return False


def best_format(formats: list[dict], quality: str) -> dict:
    size = tuple(map(int, quality.split("x"))) if quality != "best" else None
    available = [fmt for fmt in reversed(formats) if not fmt.get("has_drm")]
    audio = next(
        (fmt for fmt in available if fmt.get("vcodec") == "none" and fmt.get("acodec") not in {None, "none"}),
        None,
    )
    for fmt in available:
        if fmt.get("vcodec") == "none" or (size and (fmt.get("width"), fmt.get("height")) != size):
            continue
        if fmt.get("acodec") != "none" or not audio:
            return fmt
        return {
            "format_id": f"{fmt['format_id']}+{audio['format_id']}",
            "ext": "mkv",
            "requested_formats": [fmt, audio],
            "protocol": f"{fmt['protocol']}+{audio['protocol']}",
        }
    raise MediaError("Выбранное качество больше недоступно. Обновите данные материала.")


def ffmpeg_profile(source: Path, target: Path, profile: str) -> None:
    hide_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(source)],
        capture_output=True, text=True, check=True, creationflags=hide_window,
    )
    streams = json.loads(probe.stdout)["streams"]
    video = next((stream for stream in streams if stream["codec_type"] == "video"), None)
    audio = next((stream for stream in streams if stream["codec_type"] == "audio"), None)
    if not video:
        raise MediaError("В загруженном файле отсутствует видеодорожка.")

    command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?"]
    if profile == "mkv":
        command += ["-c", "copy"]
    else:
        if video["codec_name"] == "h264" and video.get("pix_fmt") == "yuv420p":
            command += ["-c:v", "copy"]
        else:
            command += ["-c:v", "libx264", "-preset", "fast", "-crf", "18", "-pix_fmt", "yuv420p"]
            if video["width"] % 2 or video["height"] % 2:
                command += ["-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2"]
        if not audio or audio["codec_name"] == "aac":
            command += ["-c:a", "copy"]
        else:
            command += ["-c:a", "aac", "-b:a", "192k"]
        command += ["-movflags", "+faststart"]

    subprocess.run([*command, str(target)], capture_output=True, check=True, creationflags=hide_window)


def error_message(error: Exception) -> str:
    if isinstance(error, MediaError):
        return str(error)
    if isinstance(error, DownloadError):
        text = str(error).lower()
        if "proxy" in text or "timed out" in text or "unable to download webpage" in text:
            return "Не удалось подключиться к площадке. Проверьте доступ к сайту и попробуйте снова."
        if "sign in to confirm" in text or "not a bot" in text or "429" in text:
            return "Площадка ограничила запрос. Попробуйте позже или войдите через локальный профиль браузера."
        if any(fragment in text for fragment in ("private", "members-only", "login", "sign in", "age-restricted")):
            return "Для этого видео нужен профиль браузера с доступом к ролику."
        if "no formats" in text or "format is not available" in text:
            return "Площадка не отдаёт подходящие форматы для этого материала."
        return "Площадка не вернула материал. Проверьте ссылку, доступ к ролику и версию yt-dlp."
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
        self.browsers = dict.fromkeys(("youtube", "instagram", "tiktok"), browser)

    def platform_options(self, platform: str) -> dict:
        from .sources import EXTRACTORS
        options = {**self.options, "allowed_extractors": EXTRACTORS[platform]}
        if browser := self.browsers[platform]:
            options["cookiesfrombrowser"] = (browser,)
        return options

    def inspect(self, video_id: str, platform: str = "youtube", *, url: str | None = None) -> dict:
        with YoutubeDL({**self.platform_options(platform), "ignore_no_formats_error": True}) as client:
            info = client.extract_info(url or watch_url(video_id), download=False)
        return self.describe(info, video_id)

    def describe(self, info: dict, video_id: str) -> dict:
        return {
            "title": info.get("title") or video_id,
            "photo": bool(info.get("videoport_photo")),
            "has_video": any(fmt.get("vcodec") != "none" for fmt in info.get("formats", []) if not fmt.get("has_drm")),
            "channel": info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration"),
            "live": info.get("live_status") in {"is_live", "is_upcoming", "post_live"},
            "qualities": resolutions(info),
            "thumbnails": thumbnails(info),
        }

    def cover(self, urls: list[str], directory: Path, platform: str = "youtube") -> Path:
        with YoutubeDL(self.platform_options(platform)) as client:
            for url in filter(safe_image, urls):
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
        raise MediaError("Площадка не отдала изображение. Обновите данные ролика и попробуйте снова.")

    def video(
        self, video_id: str, quality: str, profile: str, directory: Path,
        progress: Callable[[str, int | None], None], *, platform: str = "youtube",
        url: str | None = None, info: dict | None = None,
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
            **self.platform_options(platform),
            "format": select,
            "outtmpl": {"default": str(directory / "source.%(ext)s")},
            "merge_output_format": "mkv",
            "progress_hooks": [hook],
        }
        if profile == "mp4" and quality != "best":
            options["format_sort"] = ["vcodec:h264", "acodec:aac"]
        with YoutubeDL(options) as client:
            if info is None:
                info = client.extract_info(url or watch_url(video_id), download=True)
            else:
                info = client.process_ie_result(deepcopy(info), download=True)

        files = info.get("requested_downloads") or []
        source = Path(files[0]["filepath"]) if files and files[0].get("filepath") else None
        if source is None or not source.is_file():
            raise MediaError("Загрузка завершилась без видеофайла.")

        progress("processing", None)
        target = directory / f"export.{profile}"
        ffmpeg_profile(source, target, profile)
        return target
