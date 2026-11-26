"""Project manifests and generated files kept beside the desktop program."""

import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from time import time
from uuid import uuid4


PROJECT_ID = re.compile(r"[a-f0-9]{32}\Z")


def write_json(path, data):
    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            json.dump(data, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.close()
            temporary.replace(path)
        finally:
            handle.close()
            temporary.unlink(missing_ok=True)


class Projects:
    def __init__(self, root):
        self.root = root
        self.catalog = {}
        for path in root.glob("*/project.json"):
            try:
                project = self.read(path.parent.name)
                self.remember(project)
            except (KeyError, ValueError, OSError):
                continue

    def path(self, project_id):
        if not isinstance(project_id, str) or not PROJECT_ID.fullmatch(project_id):
            raise ValueError("Проект не найден.")
        return self.root / project_id

    def read(self, project_id):
        project = json.loads((self.path(project_id) / "project.json").read_text(encoding="utf-8"))
        if project["id"] != project_id:
            raise ValueError("Проект не найден.")
        if project["status"] == "analysis":
            project.update(status="interrupted", stage="Анализ прерван. Его можно запустить снова.")
        return project

    def remember(self, project):
        fields = ("id", "name", "status", "updated")
        self.catalog[project["id"]] = {key: project[key] for key in fields}

    def save(self, project):
        project["updated"] = time()
        write_json(self.path(project["id"]) / "project.json", project)
        self.remember(project)

    def create(self, source, options):
        project_id = uuid4().hex
        folder = self.path(project_id)
        folder.mkdir(parents=True)
        project = dict(id=project_id, name=source.name, source=str(source), options=options,
                       status="analysis", stage="Запуск анализа…", clips=[], files=[],
                       folder=str(folder / "Clips"), duration=None)
        self.save(project)
        return project

    def projects(self):
        return sorted(self.catalog.values(), key=lambda project: project["updated"], reverse=True)

    def transcript(self, project_id):
        path = self.path(project_id) / "transcript.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []

    def save_transcript(self, project_id, transcript):
        write_json(self.path(project_id) / "transcript.json", transcript)
