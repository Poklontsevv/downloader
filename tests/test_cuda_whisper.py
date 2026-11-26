from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from videoport.clip_analysis import decode_windows
from videoport.cuda_whisper import compatible_device, NvidiaDevice


class NvidiaWhisperTests(TestCase):
    def test_selects_pascal_with_enough_free_memory_and_compatible_driver(self):
        rows = ("GPU-a1, NVIDIA GeForce GTX 1050, 6.1, 2380, 537.58\n"
                "GPU-b1, NVIDIA card with insufficient memory, 8.6, 900, 551.23\n"
                "GPU-c1, NVIDIA card with old driver, 6.1, 2980, 516.94\n"
                "GPU-d1, Missing telemetry, N/A, N/A, 537.58")
        self.assertEqual(compatible_device(rows, 1650), NvidiaDevice("GPU-a1", "NVIDIA GeForce GTX 1050", 2380))
        self.assertIsNone(compatible_device(rows, 2500))

    def test_failed_cuda_batch_clears_partial_results_and_uses_cpu_for_the_rest(self):
        gpu = NvidiaDevice("GPU-a1", "NVIDIA GeForce GTX 1050", 2300)
        stages = []
        reports = []
        calls = []
        with TemporaryDirectory() as directory:
            root = Path(directory)
            windows = [(root / f"speech-{index}.wav", 0, 1, 0, 1) for index in range(41)]

            def run(executable, args, cache, batch, notify, device=None):
                calls.append((executable, len(batch), "-ng" in args))
                if device:
                    batch[0][0].with_suffix(".json").write_text("incomplete")
                    notify(1)
                    raise ValueError("CUDA out of memory")
                self.assertFalse(batch[0][0].with_suffix(".json").exists())
                notify(len(batch))
                return [piece[0].stem for piece in batch]

            with patch("videoport.clip_analysis.run_whisper_batch", side_effect=run), self.assertLogs():
                result = decode_windows(windows, "cpu.exe", Path("turbo.bin"), root, root, "ru", "large.v3.turbo",
                                        gpu, "cuda.exe", lambda label, percent: stages.append(label), reports.append)
        self.assertEqual(len(result), 41)
        self.assertEqual(calls, [("cuda.exe", 40, False), ("cpu.exe", 40, True), ("cpu.exe", 1, True)])
        self.assertIn("NVIDIA недоступна · продолжаем на процессоре", stages)
        self.assertEqual(reports[0]["backend"], "cpu")
