"""Start the bundled engine on a private loopback port for the Windows window."""

import argparse
import os
import sys
from pathlib import Path

from werkzeug.serving import WSGIRequestHandler, make_server

from .app import create_app
from .media import MediaService


class DesktopRequests(WSGIRequestHandler):
    def log_request(self, code="-", size="-"):
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ready-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    args = parser.parse_args()
    modules = Path(sys.executable).parent
    os.environ["PATH"] = str(modules / "tools") + os.pathsep + os.environ.get("PATH", "")
    args.output.mkdir(parents=True, exist_ok=True)

    with args.log.open("a", encoding="utf-8", buffering=1) as log:
        sys.stdout = sys.stderr = log
        app = create_app(args.output, MediaService(), desktop=True)
        server = make_server("127.0.0.1", 0, app, threaded=True, request_handler=DesktopRequests)
        args.ready_file.write_text(str(server.server_port), encoding="ascii")
        server.serve_forever()


if __name__ == "__main__":
    main()
