"""Isolate one transfer so the workspace can pause or stop its process tree."""

import json
from pathlib import Path
import sys

from .media import MediaService, error_message


def report(**event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def main():
    if sys.argv[1] == "--inspect":
        task = json.load(sys.stdin)
        media = MediaService(task["browser"], task["runtime"])
        try:
            report(info=media.inspect(task["id"], task["platform"], url=task["url"]))
        except Exception as error:
            report(error=error_message(error))
        return

    directory = Path(sys.argv[1])
    task = json.loads((directory / "task.json").read_text(encoding="utf-8"))
    media = MediaService(task["browser"], task["runtime"])
    video, job = task["video"], task["job"]
    platform = video["platform"]

    def progress(state, percent, **details):
        report(state=state, percent=percent, **details)

    try:
        progress("downloading", None, stage="connecting")
        if job["kind"] in {"cover", "photo"}:
            path = media.cover(video["covers"], directory, platform)
        else:
            path = media.video(video["id"], job["quality"], job["profile"], directory, progress,
                               platform=platform, url=video["url"], info=video.get("_source_info"))
        report(result=path.name)
    except Exception as error:
        report(error=error_message(error))


if __name__ == "__main__":
    main()
