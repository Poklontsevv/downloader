from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from videoport.clip_analysis import replace_transcript, transcript_rows
from videoport.projects import Projects
from videoport.subtitles import caption_events


class ProjectsAndSubtitlesTests(TestCase):
    def test_measured_words_do_not_fill_a_thirty_second_decoder_window(self):
        row = dict(offsets={"from": 0, "to": 30000}, text="Первая реплика. Вторая реплика.",
                   tokens=[dict(text=text, offsets={"from": start, "to": end}) for text, start, end in [
                       (" Первая", 2000, 2500), (" реплика.", 2500, 3100),
                       (" Вторая", 21000, 21500), (" реплика.", 21500, 22200),
                   ]])
        cues = transcript_rows([row], offset=10, limit=30)
        self.assertEqual([(cue["start"], cue["end"]) for cue in cues], [(12, 13.1), (31, 32.2)])

    def test_reprocessing_cut_preserves_unrelated_speech(self):
        past = dict(start=0, end=2, text="До отрезка")
        after = dict(start=20, end=22, text="После отрезка")
        old = dict(start=4, end=18, text="Старая длинная реплика")
        new = dict(start=6, end=7, text="Исправлено")
        self.assertEqual(replace_transcript([past, old, after], [new], 4, 18), [past, new, after])

    def test_started_project_can_be_opened_after_restart(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "Ролик.mp4"
            before = Projects(root / "Projects")
            project = before.create(source, {"language": "ru", "quality": "medium"})
            after = Projects(root / "Projects")
            saved = after.read(project["id"])
            self.assertEqual(saved["status"], "interrupted")
            self.assertEqual(saved["source"], str(source))
            self.assertEqual(saved["options"]["language"], "ru")

    def test_word_groups_use_original_word_times_and_keep_the_line_break(self):
        tokens = "Раз два три четыре пять шесть".split()
        cue = {"start": 2, "end": 8, "text": " ".join(tokens), "words": [
            {"start": index + 2, "end": index + 3, "text": word}
            for index, word in enumerate(tokens)
        ]}
        self.assertEqual(list(caption_events([cue], 2, 8, "lines")), [(2, 8, "Раз два три\nчетыре пять шесть")])
        self.assertEqual(list(caption_events([cue], 3.5, 6.5, "2")), [
            (3.5, 5, "два три"), (5, 6.5, "четыре пять"),
        ])
