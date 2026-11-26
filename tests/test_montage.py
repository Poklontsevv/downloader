from unittest import TestCase

from videoport.montage import apply_montage, montage_actions, montage_material, speech_blocks, trim_candidate_pauses


class MontageTests(TestCase):
    def setUp(self):
        self.transcript = [
            {"start": 3, "end": 9, "text": "Подберём помол кофемолки для эспрессо."},
            {"start": 14, "end": 19, "text": "Пересадим фикус в новую землю."},
            {"start": 147, "end": 153, "text": "Отрегулируем кофемолку для эспрессо."},
        ]
        self.candidate = {"start": 0, "end": 22, "title": "Кофемолка", "text": "Подберём помол", "reason": "Речь"}

    def test_model_montage_uses_separated_related_passages(self):
        blocks = speech_blocks(self.transcript, [], [], 160)
        (item,), local = montage_material([self.candidate], blocks)
        allowed = {part["id"] for part in item["excerpts"]}
        self.assertEqual(allowed, {0, 1, 2})
        self.assertFalse(apply_montage(self.candidate, blocks, allowed, local[0], item["anchor"], [0, 99], 30))
        self.assertTrue(apply_montage(self.candidate, blocks, allowed, local[0], item["anchor"], [2, 1, 0], 30))
        first, last = self.candidate["segments"]
        self.assertLess(first["end"], 14)
        self.assertGreater(last["start"], 100)

    def test_motion_in_gap_preserves_the_continuous_candidate(self):
        motion = [(11, 14)]
        blocks = speech_blocks(self.transcript, motion, [], 160)
        cut_candidate = self.candidate.copy()
        trim_candidate_pauses([cut_candidate], blocks, [])
        self.assertEqual(len(cut_candidate["segments"]), 2)
        trim_candidate_pauses([self.candidate], blocks, motion)
        self.assertNotIn("segments", self.candidate)

    def test_groups_silent_actions_without_jumping_to_a_different_shot(self):
        candidate = dict(self.candidate, text="")
        motion = sorted([(time / 3, 0) for time in range(60)]
                        + [(1, 10), (2, 14), (3, 12), (9, 13), (10, 12), (19, 9)])
        montage_actions([candidate], motion, [16])
        first, second = candidate["segments"]
        self.assertLess(first["end"], second["start"])
        self.assertLess(second["end"], 16)
