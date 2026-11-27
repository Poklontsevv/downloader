from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from unittest import TestCase
from unittest.mock import Mock, patch
from zipfile import ZipFile

from videoport.scripts import context_for, fit_shots, relevant_notes
from videoport.transcript_export import write_export
from videoport.transcripts import TranscriptLibrary, options
from videoport.updates import feed_installer, latest_installer


class DesktopServicesTests(TestCase):
    def test_pause_and_resume_only_unprocessed_audio(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "запись.wav"
            source.touch()
            arrived = Event()
            release = Event()
            calls = []

            def decode(_source, _cache, _tools, _language, _progress, _quality, start, end, **kwargs):
                calls.append((start, end))
                if len(calls) == 1:
                    arrived.set()
                    release.wait(5)
                return [{"start": min(start + 3, end - .9), "end": min(start + 4, end), "text": "Реплика"}]

            def wait(library, job_id, expected):
                deadline = monotonic() + 5
                while monotonic() < deadline:
                    ready = library.page(job_id, 0, "")
                    if ready["job"]["state"] == expected:
                        return ready
                    sleep(.01)
                self.fail(f"Expected {expected}, got {ready}")

            with patch("videoport.transcripts.probe_audio", return_value=241.0), patch("videoport.transcripts.transcribe", side_effect=decode):
                library = TranscriptLibrary(root / "jobs", root, root)
                [job_id] = library.add([source], options({}), "")
                self.assertTrue(arrived.wait(5))
                library.act(job_id, "pause", {})
                release.set()
                paused = wait(library, job_id, "paused")
                self.assertEqual(paused["job"]["cursor"], 120)
                self.assertEqual(paused["count"], 1)
                library.act(job_id, "resume", {})
                ready = wait(library, job_id, "ready")
                self.assertEqual(ready["job"]["cursor"], 241)
                self.assertEqual(len(ready["rows"]), 3)
                self.assertEqual(calls, [(0, 122), (118, 241), (238, 241)])

    def test_exports_long_timestamps_and_docx_escape(self):
        row = {"start": 360001.25, "end": 360003.5, "text": "А & Б < кадр", "speaker": ""}
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            srt, docx = folder / "test.srt", folder / "test.docx"
            write_export(srt, [row], "Расшифровка", {}, True)
            write_export(docx, [row], "Расшифровка", {}, True)
            self.assertIn("100:00:01,250 --> 100:00:03,500", srt.read_text(encoding="utf-8"))
            with ZipFile(docx) as document:
                xml = document.read("word/document.xml").decode()
                self.assertIn("[100:00:01] А &amp; Б &lt; кадр", xml)

    def test_script_scope_and_timing(self):
        self.assertTrue(any(note["id"] == "tiktok-codes" for note in context_for("tiktok", "short")))
        self.assertFalse(any(note["id"] == "tiktok-codes" for note in context_for("youtube", "long")))
        self.assertTrue(relevant_notes("Окна и осень\n\nСценарий для кофе", "Темп кофе").startswith("Сценарий"))
        self.assertEqual(sum(row["seconds"] for row in fit_shots([{"seconds": 1}, {"seconds": 4}], 27)), 27)

    def test_update_requires_expected_installer_and_digest(self):
        data = {
            "tag_name": "v4.8.0",
            "assets": [{"name": "Videoport-Setup-x64-v4.8.0.exe",
                        "browser_download_url": "https://github.com/Poklontsevv/downloader/releases/download/v4.8.0/Videoport-Setup-x64-v4.8.0.exe",
                        "size": 1024, "digest": "sha256:" + "a" * 64}],
        }
        result = Mock(status_code=200)
        result.json.return_value = data
        def request(url, **_):
            return Mock(status_code=404) if url.endswith("latest.json") else result

        with patch("videoport.updates.requests.get", side_effect=request), patch("videoport.updates.VERSION", "4.7.0"):
            self.assertEqual(latest_installer()["sha256"], "a" * 64)
            data["assets"][0]["digest"] = None
            with self.assertRaises(ValueError):
                latest_installer()
            data["tag_name"] = "v4.7.0"
            self.assertIsNone(latest_installer())

    def test_feed_constructs_official_url_and_keeps_sha256(self):
        data = feed_installer({"tag": "v4.9.1", "size": 121_000, "sha256": "a" * 64})
        self.assertTrue(data["uri"].endswith("windows-updates/Videoport-Setup-x64-v4.9.1.exe"))
        self.assertEqual(data["sha256"], "a" * 64)
