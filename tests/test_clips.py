from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from videoport.clips import write_subtitles


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
