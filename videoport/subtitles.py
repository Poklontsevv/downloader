"""Caption style and timing shared by the clip editor and FFmpeg exports."""

import json
from pathlib import Path
import re
from shutil import copyfile
from tempfile import NamedTemporaryFile
from uuid import uuid4

from fontTools.ttLib import TTFont, TTLibError

from .projects import write_json


FONTS = {"blocks": "Unbounded", "manrope": "Manrope", "oswald": "Oswald"}
FONT_DIR = Path(__file__).resolve().parent.parent / "docs" / "fonts"
DEFAULT_STYLE = dict(font="blocks", mode="3", size=78, color="#ffffff", stroke="#171215",
                     outline=5, shadow=2, position=78, background=False)
MAX_CUES = 160


class CaptionFonts:
    def __init__(self, root):
        self.root = root
        self.index = root / "fonts.json"
        self.imported = {}
        if self.index.is_file():
            try:
                self.imported = json.loads(self.index.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass

    def list(self):
        return [dict(id=key, name=asset["name"], cyrillic=asset["cyrillic"]) for key, asset in self.imported.items()]

    def resolve(self, key):
        if key in FONTS:
            return FONTS[key], FONT_DIR / (FONTS[key] + ".ttf")
        asset = self.imported[key]
        return asset["name"], self.root / asset["file"]

    def add(self, uploaded):
        suffix = Path(uploaded.filename or "").suffix.lower()
        if suffix not in {".ttf", ".otf"}:
            raise ValueError("Выберите файл шрифта TTF или OTF.")
        self.root.mkdir(parents=True, exist_ok=True)
        key = uuid4().hex
        filename = key + suffix
        target = self.root / filename
        with NamedTemporaryFile(dir=self.root, delete=False) as handle:
            temporary = Path(handle.name)
            try:
                uploaded.save(handle)
                handle.close()
                with TTFont(temporary, lazy=True) as font:
                    name = font["name"].getBestFamilyName()
                    chars = font.getBestCmap() or {}
                if (not name or len(name) > 100 or re.search(r"[,\[\]{}\x00-\x1f]", name)
                        or not chars):
                    raise ValueError("Не удалось прочитать название шрифта.")
                entry = dict(name=name, cyrillic=all(ord(char) in chars for char in "ЯяЖжЁё"), file=filename)
                temporary.replace(target)
                updated = dict(self.imported, **{key: entry})
                try:
                    write_json(self.index, updated)
                except OSError:
                    target.unlink(missing_ok=True)
                    raise
                self.imported = updated
                return dict(id=key, name=entry["name"], cyrillic=entry["cyrillic"])
            except (TTLibError, KeyError, UnicodeError, IndexError, AssertionError):
                raise ValueError("Не удалось прочитать файл шрифта.") from None
            finally:
                handle.close()
                temporary.unlink(missing_ok=True)


def normalize_style(style, imported=()):
    result = DEFAULT_STYLE.copy()
    if not isinstance(style, dict):
        return result
    for field, choices in (("font", set(FONTS) | set(imported)), ("mode", {"1", "2", "3", "lines", "phrase"})):
        value = style.get(field)
        if isinstance(value, str) and value in choices:
            result[field] = value
    for field, low, high in (("size", 32, 135), ("position", 8, 92), ("outline", 0, 15), ("shadow", 0, 12)):
        value = style.get(field)
        if type(value) in (int, float) and low <= value <= high:
            result[field] = round(value)
    for field in ("color", "stroke"):
        value = style.get(field)
        if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
            result[field] = value
    result["background"] = style.get("background") is True
    return result


def normalize_cues(items, duration):
    if not isinstance(items, list) or len(items) > MAX_CUES:
        raise ValueError("Слишком много блоков субтитров в одном отрезке.")
    cues = []
    for item in items:
        start, end = float(item["start"]), float(item["end"])
        text = str(item["text"]).strip()[:600]
        if not (0 <= start < end <= duration + 0.05):
            raise ValueError("Проверьте время начала и конца блока субтитров.")
        cue = dict(start=start, end=min(end, duration), text=text)
        words = item.get("words")
        if isinstance(words, list) and len(words) <= 120:
            parsed = []
            for word in words:
                at, until = float(word["start"]), float(word["end"])
                if start <= at < until <= end:
                    parsed.append(dict(start=at, end=until, text=str(word["text"])[:100]))
            if parsed and " ".join(word["text"] for word in parsed) == " ".join(text.split()):
                cue["words"] = parsed
        cues.append(cue)
    return sorted(cues, key=lambda cue: cue["start"])


def caption_events(cues, start, end, mode):
    """Return source-relative display events and keep word timestamps when available."""
    for cue in cues:
        begin, finish = cue["start"], cue["end"]
        text = cue["text"].strip()
        if finish <= start or begin >= end or not text:
            continue
        if mode == "phrase":
            yield max(start, begin), min(end, finish), text
            continue
        words = cue.get("words")
        if not words:
            parts = text.split()
            step = (finish - begin) / len(parts)
            words = [dict(text=word, start=begin + index * step, end=begin + (index + 1) * step)
                     for index, word in enumerate(parts)]
        size = 6 if mode == "lines" else int(mode)
        for index in range(0, len(words), size):
            group = words[index:index + size]
            begin_group = max(start, group[0]["start"])
            end_group = min(end, group[-1]["end"])
            if end_group <= begin_group:
                continue
            labels = [word["text"] for word in group]
            first = " ".join(labels[:3])
            yield begin_group, end_group, first + ("\n" + " ".join(labels[3:]) if len(labels) > 3 else "")


def ass_time(seconds):
    cs = max(0, round(seconds * 100))
    hours, remainder = divmod(cs, 360000)
    minutes, remainder = divmod(remainder, 6000)
    return f"{hours}:{minutes:02d}:{remainder // 100:02d}.{remainder % 100:02d}"


def ass_color(color, alpha="00"):
    return "&H" + alpha + color[5:7] + color[3:5] + color[1:3]


def caption_size(text, style):
    if style["mode"] == "phrase":
        return style["size"]
    longest = max(map(len, text.splitlines()), default=1)
    return min(style["size"], max(1, round(980 / (max(1, longest) * 0.96))))


def write_styled_subtitles(directory, clip, transcript):
    style = clip["style"]
    start = clip["start"]
    cues = clip.get("cues")
    events = sorted(caption_events(transcript if cues is None else cues, start, clip["end"], style["mode"]),
                    key=lambda event: event[0])
    if not events:
        return False
    family, font_path = clip.get("font_asset") or (FONTS[style["font"]], FONT_DIR / (FONTS[style["font"]] + ".ttf"))
    fonts = directory / "fonts"
    fonts.mkdir(exist_ok=True)
    copyfile(font_path, fonts / font_path.name)
    shadow = style["shadow"]
    border = 3 if style["background"] else 1
    outline = ass_color(style["stroke"], "38" if style["background"] else "00")
    header = (
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nScaledBorderAndShadow: yes\n"
        "WrapStyle: 0\n\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Captions,{family},{style['size']},{ass_color(style['color'])},&H000000FF,{outline},&H80000000,"
        f"0,0,0,0,100,100,0,0,{border},{style['outline']},{shadow},5,40,40,0,1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for index, (begin, end, text) in enumerate(events):
        if index + 1 < len(events):
            end = min(end, events[index + 1][0])
        if end <= begin:
            continue
        safe = text.replace("\\", "／").replace("{", "｛").replace("}", "｝").replace("\r", "").replace("\n", r"\N")
        position = r"{\pos(540," + str(round(1920 * style["position"] / 100)) + ")"
        if style["mode"] != "phrase":
            position += r"\q2\fs" + str(caption_size(text, style))
        position += "}"
        lines.append(f"Dialogue: 0,{ass_time(begin-start)},{ass_time(end-start)},Captions,,0,0,0,,{position}{safe}\n")
    (directory / "captions.ass").write_text(header + "".join(lines), encoding="utf-8")
    return True
