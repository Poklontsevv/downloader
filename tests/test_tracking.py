from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from videoport.clip_analysis import suggest_clips
from videoport.clips import parse_clip
from videoport.tracking import center_at, tracking_path


class TrackingTests(TestCase):
    def test_same_source_ranges_reuse_trajectory_after_format_and_montage_changes(self):
        clip = {
            "title": "Клип", "segments": [{"start": 1, "end": 5}, {"start": 8, "end": 12}],
            "tracking": {"target": "object", "enabled": True, "anchor": {"time": 2, "x": .2, "y": .75}},
        }
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.mp4"
            source.write_bytes(b"original")
            original = parse_clip(clip, 20, [])
            changed = parse_clip(dict(
                clip, aspect="16:9", tracking=dict(clip["tracking"], enabled=False),
                segments=[{"start": 8, "end": 12}, {"start": 1, "end": 3}, {"start": 3, "end": 5}],
            ), 20, [])
            first, intervals = tracking_path(source, folder, original)
            second, _ = tracking_path(source, folder, changed)
            self.assertEqual(intervals, [[1, 5], [8, 12]])
            self.assertEqual(first, second)
            changed["tracking"]["anchor"]["x"] = .8
            self.assertNotEqual(first, tracking_path(source, folder, changed)[0])
            source.write_bytes(b"replaced file")
            self.assertNotEqual(first, tracking_path(source, folder, original)[0])

    def test_trajectory_interpolation_and_endpoint_holds(self):
        points = [[1, 0.25, 0.5], [3, 0.75, 0.75]]
        self.assertEqual(center_at(points, 0), [0.25, 0.5])
        self.assertEqual(center_at(points, 2), [0.5, 0.625])
        self.assertEqual(center_at(points, 4), [0.75, 0.75])

    def test_custom_brief_surfaces_both_quiet_and_active_silent_windows(self):
        motion = [(second, 1 if 60 <= second < 120 else 20) for second in range(180)]
        results = suggest_clips([], motion, [], 180, 30, custom=True)
        self.assertTrue(any(60 <= item["start"] <= 90 and item["motion"] == 1 for item in results))
        self.assertTrue(any(item["motion"] == 20 for item in results))
