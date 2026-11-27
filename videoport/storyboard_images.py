"""Local storyboard illustration using Stable Diffusion and a shared reference."""

import logging
import os
from pathlib import Path
from shutil import copyfile
import subprocess
from tempfile import NamedTemporaryFile, TemporaryDirectory
from zipfile import ZipFile

from .clip_analysis import FLAGS, download_asset

LOGGER = logging.getLogger(__name__)
MODEL = (
    "https://huggingface.co/second-state/stable-diffusion-v1-5-GGUF/resolve/main/"
    "stable-diffusion-v1-5-pruned-emaonly-Q4_0.gguf",
    "b8944e9fe0b69b36ae1b5bb0185b3a7b8ef14347fe0fa9af6c64c4829022261f", 1566768416,
)
RUNTIME = (
    "https://github.com/leejet/stable-diffusion.cpp/releases/download/master-1141-e637c7b/"
    "sd-master-e637c7b-bin-win-cpu-x64.zip",
    "dae8a190227736d987486ab7d7697616ad1dea872f758e5ca550ad7e6692311c", 19123577,
)
STYLES = {
    "sketch": "black and white storyboard penciled sketch, clean pencil linework, grey values, white paper",
    "realism": "realistic film still, natural skin, cinematic lighting, realistic scene color",
}
PNG = b"\x89PNG\r\n\x1a\n"


def image_runtime(cache, progress):
    override = os.environ.get("VIDEOPORT_SD_CLI")
    if override:
        return Path(override)
    folder = cache / "stable-diffusion-e637c7b"
    cli = folder / "sd-cli.exe"
    if cli.is_file():
        return cli
    archive = download_asset(RUNTIME, cache, progress, "Загрузка движка иллюстраций")
    with TemporaryDirectory(prefix="sd-runtime-", dir=cache) as temp:
        staging = Path(temp) / "runtime"
        staging.mkdir()
        with ZipFile(archive) as source:
            for item in source.infolist():
                name = Path(item.filename).name
                if Path(name).suffix.lower() in {".dll", ".txt"} or name.lower() == "sd-cli.exe":
                    (staging / name).write_bytes(source.read(item))
        if not (staging / "sd-cli.exe").is_file():
            raise ValueError("Не найден локальный движок иллюстраций.")
        staging.replace(folder)
    return cli


def prompt_text(style, continuity, composition):
    # Stable Diffusion 1.5 encodes short phrases best. Put the board's identity before shot detail.
    return ", ".join(part for part in (STYLES[style], continuity[:210], composition[:230], "single storyboard panel") if part)


class StoryboardRenderer:
    def __init__(self, cache, progress):
        self.cache = cache
        self.model = download_asset(MODEL, cache, progress, "Первый запуск иллюстраций · загрузка модели (1,57 ГБ)")
        self.binary = image_runtime(cache, progress)

    def draw(self, destination, style, format_name, continuity, composition, seed, reference=None):
        width, height = (288, 512) if format_name == "short" else (512, 288)
        with TemporaryDirectory(prefix="shot-", dir=self.cache) as temp:
            staging = Path(temp)
            base = Path(staging.name)
            output = staging / "frame.png"
            prompt = staging / "prompt.txt"
            prompt.write_text(prompt_text(style, continuity, composition), encoding="utf-8")
            model = self.model.name if self.model.parent == self.cache else str(self.model)
            args = [str(self.binary), "-m", model, "--prompt-file", str(base / "prompt.txt"),
                    "-n", "watermark, words, caption, logo, collage, double image, extra limbs, different outfit",
                    "-o", str(base / output.name), "-W", str(width), "-H", str(height),
                    "--steps", "16", "--sampling-method", "euler_a", "--cfg-scale", "6.5", "-s", str(seed),
                    "-t", str(min(6, os.cpu_count() or 2))]
            if reference and reference.is_file():
                init_image = staging / ("reference" + reference.suffix.lower())
                copyfile(reference, init_image)
                args.extend(("--init-img", str(base / init_image.name), "--strength", "0.59"))
            try:
                result = subprocess.run(args, cwd=self.cache, stdin=subprocess.DEVNULL, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", creationflags=FLAGS, timeout=900)
            except (subprocess.TimeoutExpired, OSError) as error:
                raise ValueError("Локальный движок не смог завершить иллюстрацию.") from error
            if result.returncode or not output.is_file():
                LOGGER.error("Storyboard renderer failed: %s", (result.stderr + result.stdout)[-2400:])
                raise ValueError("Движок не смог нарисовать кадр. Попробуйте перегенерировать его.")
            with output.open("rb") as actual:
                if actual.read(len(PNG)) != PNG:
                    raise ValueError("Движок вернул некорректное изображение.")
            with NamedTemporaryFile(prefix=".shot-", suffix=".png", dir=destination.parent, delete=False) as temp_file:
                staged = Path(temp_file.name)
            try:
                copyfile(output, staged)
                staged.replace(destination)
            finally:
                staged.unlink(missing_ok=True)
        return destination
