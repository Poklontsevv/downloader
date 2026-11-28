from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from videoport.clips import parse_clip, write_subtitles


class SegmentSettingsTests(TestCase):
    def test_preserves_individual_framing_and_caption_style(self):
        clip = parse_clip({"title": "Клип", "aspect": "16:9", "style": {"font": "blocks"}, "segments": [
            {"start": 1, "end": 5, "frame": {"x": 26, "y": -15, "zoom": 75, "blur": 12}, "style": {"font": "oswald"}},
            {"start": 15, "end": 19, "frame": {}},
            {"start": 25, "end": 30},
        ]}, 40, [])
        first, second, legacy = clip["segments"]
        self.assertEqual(clip["aspect"], "16:9")
        self.assertEqual(first["style"]["font"], "oswald")
        self.assertEqual(tuple(first["frame"][key] for key in ("x", "y", "zoom", "blur")), (26, -15, 75, 12))
        self.assertEqual(tuple(second["frame"][key] for key in ("x", "y", "zoom", "blur")), (0, 0, 100, 0))
        self.assertNotIn("frame", legacy)
        self.assertEqual(clip["style"]["font"], "blocks")

class ClipSubtitleTests(TestCase):
    def test_skips_subtitle_file_when_there_is_no_caption_text(self):
        clip = {"start": 0, "end": 31.57, "captions": " \n "}
        cue = {"start": 1, "end": 2, "text": "Подпись удалена вручную"}
        with TemporaryDirectory() as folder:
            path = Path(folder) / "captions.srt"
            for transcript in ([], [cue]):
                with self.subTest(transcript=transcript):
                    self.assertFalse(write_subtitles(path, clip, transcript))
                    self.assertFalse(path.exists())
