"""Install pinned Windows wheels without changing the bundled Python runtime."""

from importlib import import_module
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Lock
from zipfile import ZipFile

from .clip_analysis import download_asset

LOCK = Lock()


def optional_module(name, cache, directory, wheels, stage, progress):
    try:
        return import_module(name)
    except ImportError as error:
        if sys.platform != "win32" or sys.version_info[:2] != (3, 13):
            raise ValueError(f"Установите модуль Python {name}.") from error
    folder = cache / directory
    with LOCK:
        if not folder.is_dir():
            files = [download_asset(wheel, cache, progress, stage) for wheel in wheels]
            with TemporaryDirectory(dir=cache, prefix="wheel-") as staging:
                unpacked = Path(staging) / "python"
                for file in files:
                    with ZipFile(file) as archive:
                        archive.extractall(unpacked)
                unpacked.replace(folder)
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
        try:
            return import_module(name)
        except ImportError as error:
            raise ValueError(f"Windows не смогла загрузить модуль {name}.") from error
