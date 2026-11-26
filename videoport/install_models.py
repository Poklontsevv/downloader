"""Provision the pinned Qwen weights and engine before installing the application."""

from argparse import ArgumentParser
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from .clip_analysis import LLAMA, QWEN, download_asset, llama_binary


def reuse_asset(manifest, destination, candidates, progress):
    url, expected, size = manifest
    name = url.rsplit("/", 1)[-1]
    target = destination / name
    if target.is_file() and target.stat().st_size == size:
        return
    for folder in candidates:
        source = folder / name
        if folder == destination or not source.is_file() or source.stat().st_size != size:
            continue
        try:
            with TemporaryDirectory(prefix="reuse-", dir=destination) as temp:
                partial = Path(temp) / name
                digest, written = sha256(), 0
                with source.open("rb") as original, partial.open("wb") as output:
                    for block in iter(lambda: original.read(1024 * 1024), b""):
                        digest.update(block)
                        output.write(block)
                        written += len(block)
                        progress("Copying " + name, min(99, 100 * written // size))
                if digest.hexdigest() != expected:
                    continue
                partial.replace(target)
                target.with_suffix(target.suffix + ".verified").write_text(expected)
                return
        except OSError:
            continue


def install_models(destination, candidates=()):
    destination.mkdir(parents=True, exist_ok=True)
    last = None

    def progress(label, percent):
        nonlocal last
        step = int(percent or 0) // 5 * 5
        update = (label, step)
        if update != last:
            print(f"{label}: {step}%", flush=True)
            last = update

    for manifest in (QWEN, LLAMA):
        reuse_asset(manifest, destination, candidates, progress)
    download_asset(QWEN, destination, progress, "Downloading Qwen")
    os.environ.pop("VIDEOPORT_LLAMA_SERVER", None)
    llama_binary(destination, lambda _label, pct: progress("Downloading llama.cpp", pct))
    print("Qwen and llama.cpp are ready.", flush=True)


def main():
    parser = ArgumentParser()
    parser.add_argument("destination", type=Path)
    parser.add_argument("legacy", type=Path, nargs="*")
    args = parser.parse_args()
    try:
        install_models(args.destination, [path for path in args.legacy if path.is_absolute()])
    except Exception as error:
        print("Model setup failed: " + str(error), flush=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
