from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from videoport.projects import Projects
from videoport.subtitles import caption_events


class ProjectsAndSubtitlesTests(TestCase):
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
            (3.5, 4, "Раз два"), (4, 6, "три четыре"), (6, 6.5, "пять шесть"),
        ])
