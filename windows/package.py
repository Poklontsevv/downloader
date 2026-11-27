"""Build a portable Windows ZIP from a launcher and verified upstream archives."""

import argparse
import shutil
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent.parent


def copy_archive_members(archive: ZipFile, members: dict[str, Path]) -> None:
    for member, target in members.items():
        with archive.open(member) as original, target.open("wb") as destination:
            shutil.copyfileobj(original, destination)


def build(vendor: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="videoport-package-") as temp:
        root = Path(temp) / "Videoport"
        runtime = root / "runtime"
        tools = runtime / "tools"
        app = runtime / "app"
        licenses = root / "licenses"
        for directory in (tools, app, licenses):
            directory.mkdir(parents=True, exist_ok=True)

        shutil.copyfile(vendor / "Videoport.exe", root / "Videoport.exe")
        webview = vendor / "webview2"
        shutil.copyfile(webview / "webviewloader/sdk/x64/WebView2Loader.dll", root / "WebView2Loader.dll")
        shutil.copyfile(webview / "LICENSE", licenses / "go-webview2.txt")
        shutil.copyfile(webview / "webviewloader/sdk/LICENSE.txt", licenses / "WebView2 SDK.txt")
        shutil.copyfile(vendor / "node.exe", tools / "node.exe")
        shutil.copyfile(vendor / "node-license.txt", licenses / "Node.js.txt")
        cpp_runtime = vendor / "cpp-runtime"
        if cpp_runtime.is_dir():
            shutil.copytree(cpp_runtime, tools / "cpp-runtime")
        seven_zip = vendor / "7zip-windows"
        shutil.copyfile(seven_zip / "x64/7za.exe", tools / "7za.exe")
        shutil.copyfile(seven_zip / "License.txt", licenses / "7-Zip.txt")
        whisper = tools / "whisper"
        whisper.mkdir()
        with ZipFile(vendor / "whisper.zip") as archive:
            names = ("whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu.dll")
            copy_archive_members(archive, {f"Release/{name}": whisper / name for name in names})
        shutil.copyfile(vendor / "whisper-license.txt", licenses / "whisper.cpp.txt")

        with ZipFile(vendor / "python.zip") as archive:
            archive.extractall(runtime)
        path_file, = runtime.glob("python*._pth")
        standard_library = path_file.name.removesuffix("._pth") + ".zip"
        path_file.write_text(f"{standard_library}\n.\napp\napp/deps\n", encoding="ascii")
        shutil.copyfile(runtime / "LICENSE.txt", licenses / "Python.txt")

        with ZipFile(vendor / "ffmpeg-release.zip") as archive:
            prefix = next(name.split("/")[0] for name in archive.namelist() if name.endswith("/bin/ffmpeg.exe"))
            copy_archive_members(archive, {
                f"{prefix}/LICENSE": licenses / "FFmpeg GPLv3.txt",
                f"{prefix}/README.txt": licenses / "FFmpeg source and build.txt",
            })
        shutil.copyfile(vendor / "ffmpeg-release.7z", tools / (prefix + ".7z"))

        wheels = sorted((vendor / "wheels").glob("*.whl"))
        if not wheels:
            raise RuntimeError("Windows wheels are missing.")
        deps = app / "deps"
        for wheel in wheels:
            with ZipFile(wheel) as archive:
                archive.extractall(deps)
        shutil.copytree(ROOT / "videoport", app / "videoport", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(ROOT / "docs", app / "docs")
        shutil.copyfile(ROOT / "index.html", app / "index.html")
        shutil.copyfile(ROOT / "windows" / "portable-readme.txt", root / "README.txt")

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(".zip.part")
        try:
            with ZipFile(partial, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
                for path in sorted(root.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(root.parent).as_posix())
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("vendor", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build(args.vendor, args.destination)
