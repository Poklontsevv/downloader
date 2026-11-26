"""Supported platform URLs and upstream catalog extraction."""

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from yt_dlp import YoutubeDL
from yt_dlp.extractor.instagram import InstagramIE, InstagramStoryIE

from .media import MediaError, parse_video_id, watch_url

PLATFORMS = ("youtube", "instagram", "tiktok")
EXTRACTORS = {
    "youtube": ["youtube", "youtube:tab"],
    "instagram": ["instagram", "instagram:story", "instagram:user"],
    "tiktok": ["tiktok", "tiktok:user", "tiktok:vm", "tiktok:collection"],
}


@dataclass(frozen=True)
class Source:
    platform: str
    url: str
    collection: bool = False
    category: str | None = None


def parse_source(text: str, platform: str) -> Source | None:
    text = text.strip()
    if platform == "youtube" and (video := parse_video_id(text)):
        return Source(platform, watch_url(video), category="shorts" if "/shorts/" in text else "videos")
    try:
        parsed = urlsplit(text if "://" in text else "https://" + text)
        if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password or parsed.port is not None:
            return None
    except ValueError:
        return None
    path = parsed.path.rstrip("/")
    host = (parsed.hostname or "").removeprefix("www.")
    if platform == "youtube" and host in {"youtube.com", "m.youtube.com"}:
        match = re.fullmatch(r"/(@[^/?#]+|(?:channel|c|user)/[^/?#]+)(?:/(?:videos|shorts|streams|featured))?", path)
        if match:
            return Source(platform, "https://www.youtube.com/" + match[1], True)
    if platform == "instagram" and host == "instagram.com":
        if re.fullmatch(r"/(?:[\w.]+/)?(?:p|reel|reels|tv)/[\w-]+", path, re.ASCII):
            return Source(platform, "https://www.instagram.com" + path, category="reels" if re.search(r"/reels?/", path) else "posts")
        if re.fullmatch(r"/stories/(?:highlights/\d+|[\w.]+(?:/\d+)?)", path, re.ASCII):
            return Source(platform, "https://www.instagram.com" + path, True,
                          "highlights" if path.startswith("/stories/highlights/") else "stories")
        if re.fullmatch(r"/[\w.]+(?:/reels)?", path, re.ASCII):
            return Source(platform, "https://www.instagram.com/" + path.split("/")[1], True)
    if platform == "tiktok":
        if host in {"vm.tiktok.com", "vt.tiktok.com"} and re.fullmatch(r"/[\w-]+", path, re.ASCII):
            return Source(platform, f"https://{host}{path}/")
        if host == "tiktok.com":
            if re.fullmatch(r"/(?:@[\w.-]+/video/\d+|t/[\w-]+|embed/\d+|share/video/\d+)", path, re.ASCII):
                return Source(platform, "https://www.tiktok.com" + path)
            if re.fullmatch(r"/@[\w.-]+(?:/collection/[\w.%-]+-\d+)?", path, re.ASCII):
                return Source(platform, "https://www.tiktok.com" + path, True)
    return None


class InstagramPhotos:
    """Keep images in posts, carousels and stories."""

    def _extract_product_media(self, *args, **kwargs):
        info = super()._extract_product_media(*args, **kwargs)
        if not info.get("formats") and info.get("thumbnails"):
            image = max(info["thumbnails"], key=lambda item: (item.get("width") or 0) * (item.get("height") or 0))
            info["formats"] = [{**image, "ext": "jpg", "vcodec": "none", "acodec": "none"}]
            info["videoport_photo"] = True
        return info


class VideoportStoryIE(InstagramPhotos, InstagramStoryIE):
    ie_key = InstagramStoryIE.ie_key


class VideoportPostIE(InstagramPhotos, InstagramIE):
    ie_key = InstagramIE.ie_key


def catalog(source: Source, options: dict):
    if source.platform == "youtube" and source.collection and not source.category:
        problems = []
        for suffix, category in (("videos", "videos"), ("shorts", "shorts"), ("streams", "videos")):
            tab = Source("youtube", source.url + "/" + suffix, True, category)
            try:
                yield from catalog(tab, options)
            except MediaError as error:
                problems.append(error)
        if len(problems) == 3:
            raise problems[0]
        return
    if (source.platform == "instagram" and options.get("cookiesfrombrowser")
            and re.fullmatch(r"/[\w.]+", urlsplit(source.url).path, re.ASCII)):
        yield from instagram_profile(source, options)
        return
    direct_story = bool(re.search(r"/stories/(?!highlights/)[\w.]+/\d+$", source.url))
    with YoutubeDL({**options, "extract_flat": "in_playlist", "lazy_playlist": True,
                    "noplaylist": direct_story, "ignoreerrors": True, "ignore_no_formats_error": True}) as client:
        if source.platform == "instagram":
            client.add_info_extractor(VideoportStoryIE())
            client.add_info_extractor(VideoportPostIE())
        info = client.extract_info(source.url, download=False)
        if info is None:
            raise MediaError("Площадка не вернула материалы. Проверьте ссылку и доступ через Firefox.")
        for entry in catalog_entries(info):
            yield {**entry, "videoport_category": source.category or "posts"}


def catalog_entries(info: dict):
    if info.get("_type") in {"playlist", "multi_video"}:
        for entry in info.get("entries") or ():
            if entry:
                yield from catalog_entries(entry)
    elif info.get("live_status") not in {"is_live", "is_upcoming"}:
        yield info


def instagram_profile(source: Source, options: dict):
    from instaloader import Instaloader, Profile
    from instaloader.exceptions import InstaloaderException

    username = source.url.rsplit("/", 1)[-1]
    with YoutubeDL(options) as client:
        cookies = {cookie.name: cookie.value for cookie in client.cookiejar
                   if cookie.domain.lstrip(".") == "instagram.com" and not cookie.is_expired()}
    if not {"sessionid", "csrftoken"} <= cookies.keys():
        raise MediaError("Войдите в Instagram в Firefox и повторите поиск.")

    def story_items(groups, label, category):
        for group in groups:
            title = getattr(group, "title", None) or label
            for item in group.get_items():
                video_url = item.video_url if item.is_video else None
                yield {
                    "_type": "video",
                    "id": str(item.mediaid),
                    "title": f"{title} · {username} · {item.date_local:%Y-%m-%d %H-%M}",
                    "channel": username,
                    "thumbnail": item.url,
                    "videoport_photo": not item.is_video,
                    "videoport_category": category,
                    "formats": [{"url": video_url, "ext": "mp4", "protocol": "https", "format_id": "original"}]
                    if video_url else [],
                }

    with Instaloader(quiet=True, max_connection_attempts=1, request_timeout=20) as loader:
        loader.load_session(username, cookies)
        profile = Profile.from_username(loader.context, username)

        reel_ids = set()

        def reels():
            for post in profile.get_reels():
                reel_ids.add(post.shortcode)
                yield {"id": post.shortcode, "title": post.title or f"Рилс · {username} · {post.shortcode}",
                       "channel": username, "thumbnail": post.url, "videoport_category": "reels",
                       "url": f"https://www.instagram.com/reel/{post.shortcode}/"}

        def posts():
            for post in profile.get_posts():
                if post.shortcode in reel_ids or post._node.get("product_type") == "clips":
                    continue
                title = post.title or f"Пост · {username} · {post.shortcode}"
                media = ((node.display_url, node.video_url if node.is_video else None)
                         for node in post.get_sidecar_nodes()) if post.typename == "GraphSidecar" else (
                             (post.url, post.video_url if post.is_video else None),)
                for index, (image, video_url) in enumerate(media, 1):
                    yield {"_type": "video", "id": f"{post.shortcode}_{index}", "title": title,
                           "channel": username, "thumbnail": image, "videoport_photo": not video_url,
                           "videoport_category": "posts",
                           "formats": [{"url": video_url, "ext": "mp4", "protocol": "https", "format_id": "original"}]
                           if video_url else []}

        sections = {
            "рилсы": reels,
            "посты": posts,
            "сторис": lambda: story_items(loader.get_stories([profile.userid]), "Сторис", "stories"),
            "хайлайты": lambda: story_items(loader.get_highlights(profile), "Хайлайт", "highlights"),
        }
        failed = []
        for name, items in sections.items():
            try:
                yield from items()
            except (InstaloaderException, KeyError):
                failed.append(name)
        if failed:
            raise MediaError("Instagram не вернул некоторые разделы (" + ", ".join(failed)
                             + "). Можно попробовать прямую ссылку на материал.")
