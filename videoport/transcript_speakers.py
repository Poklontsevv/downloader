"""Match speech blocks to local voice profiles throughout a recording."""

import json
import subprocess

from .clip_analysis import FLAGS, download_asset
from .optional import optional_module
from .projects import write_json
from .tracking import WHEELS as VISION_WHEELS

WHEELS = (
    VISION_WHEELS[0],
    ("https://files.pythonhosted.org/packages/d0/fd/7d0cc54be0e809019745739cef60e1967c3b9db78ee44f31bbdd3c018ae2/"
     "sherpa_onnx-2.0.3-cp313-cp313-win_amd64.whl",
     "472329ad2f8ca5e2d0e517cba279d61c86c23729060920abe6a5816fb6e50fab", 2474590),
    ("https://files.pythonhosted.org/packages/86/60/267823e104a5856b38601784ae6ca388cd00f219b49c8e78de380ceb63b0/"
     "sherpa_onnx_core-2.0.3-py3-none-win_amd64.whl",
     "798ba3803b6461bac9eca5a7f3927f04e7a54173155e3f0479e6abd89bb4bc59", 17216593),
)
ASSET = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
    "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",
    "53cab20ba8f2eb2f6e2d89ecc18e88ef3222169b61b4419cbc13666744a4574b", 28281165,
)


class SpeakerLabels:
    def __init__(self, cache, path, count, progress):
        stage = "Загрузка распознавания говорящих"
        self.np = optional_module("numpy", cache, "speaker-libraries-v1", WHEELS, stage, progress)
        sherpa = optional_module("sherpa_onnx", cache, "speaker-libraries-v1", WHEELS, stage, progress)
        model = download_asset(ASSET, cache, progress, stage)
        config = sherpa.SpeakerEmbeddingExtractorConfig(model=str(model), num_threads=2, provider="cpu")
        self.extractor = sherpa.SpeakerEmbeddingExtractor(config)
        self.maximum = count if count > 0 else 16
        self.path = path
        self.progress = progress
        initial = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        self.profiles = [self.np.array(vector, dtype=self.np.float32) for vector in initial]

    def label(self, source, start, end, cues):
        if not cues:
            return
        self.progress("Разметка говорящих", None)
        origin = max(0, start - 1)
        audio = subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(origin), "-protocol_whitelist", "file,crypto",
             "-i", str(source), "-t", str(end + 1 - origin), "-map", "0:a:0", "-ar", "16000", "-ac", "1",
             "-f", "s16le", "pipe:1"], check=True, capture_output=True, creationflags=FLAGS,
        )
        signal = self.np.frombuffer(audio.stdout, dtype="<i2").astype(self.np.float32) / 32768
        for cue in cues:
            center = (cue["start"] + cue["end"]) / 2
            half = min(3, max(.7, (cue["end"] - cue["start"]) / 2))
            lo, hi = (round(max(0, time - origin) * 16000) for time in (center - half, center + half))
            wave = signal[lo:hi]
            if len(wave) < 10000 or self.np.max(self.np.abs(wave)) < .009:
                continue
            stream = self.extractor.create_stream()
            stream.accept_waveform(16000, wave)
            stream.input_finished()
            if not self.extractor.is_ready(stream):
                continue
            vector = self.np.asarray(self.extractor.compute(stream), dtype=self.np.float32)
            norm = self.np.linalg.norm(vector)
            if not norm:
                continue
            vector /= norm
            scores = [float(self.np.dot(vector, prior)) for prior in self.profiles]
            best = max(range(len(scores)), key=scores.__getitem__) if scores else 0
            if not scores or (scores[best] < .52 and len(scores) < self.maximum):
                best = len(scores)
                self.profiles.append(vector)
            elif scores[best] > .63:
                merged = .85 * self.profiles[best] + .15 * vector
                self.profiles[best] = merged / self.np.linalg.norm(merged)
            cue["speaker"] = f"Голос {best + 1}"
        write_json(self.path, [vector.tolist() for vector in self.profiles])
