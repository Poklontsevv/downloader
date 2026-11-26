"""Start the bundled engine on a private loopback port for the Windows window."""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from werkzeug.serving import WSGIRequestHandler, make_server

from .app import create_app
from .media import MediaService


class DesktopRequests(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        pass


def ffmpeg_directory(tools: Path, data: Path) -> Path:
    archive = next(tools.glob("ffmpeg-*.7z"), None)
    if archive is None:
        return tools
    destination = data / archive.stem
    filenames = {"ffmpeg.exe", "ffprobe.exe"}
    if all((destination / name).is_file() for name in filenames):
        return destination

    with TemporaryDirectory(prefix="tools-", dir=data) as temp:
        staging = Path(temp)
        unpacker = tools / "7za.exe"
        subprocess.run(
            [str(unpacker), "e", "-y", f"-o{staging}", str(archive),
             *(f"{archive.stem}/bin/{name}" for name in sorted(filenames))],
            check=True, stdout=sys.stdout, stderr=sys.stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if not all((staging / name).is_file() for name in filenames):
            raise RuntimeError("Не удалось распаковать FFmpeg и FFprobe.")
        try:
            staging.rename(destination)
        except FileExistsError:
            if not all((destination / name).is_file() for name in filenames):
                raise
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--model-cache", type=Path)
    parser.add_argument("--projects", type=Path)
    args = parser.parse_args()
    tools = Path(sys.executable).parent / "tools"
    args.output.mkdir(parents=True, exist_ok=True)

    with args.log.open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        ffmpeg = ffmpeg_directory(tools, args.log.parent)
        os.environ["PATH"] = os.pathsep.join([str(ffmpeg), str(tools), os.environ.get("PATH", "")])
        app = create_app(args.output, MediaService(), desktop=True,
                         preferences=args.log.parent / "preferences.json", tools=tools, model_cache=args.model_cache,
                         projects=args.projects)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=DesktopRequests)
        args.ready_file.write_text(str(server.server_port), encoding="ascii")
        server.serve_forever()


if __name__ == "__main__":
    main()
