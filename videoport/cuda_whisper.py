"""Discover NVIDIA hardware and install the optional Windows Whisper backend."""

import csv
from dataclasses import dataclass
import os
from pathlib import Path
import re
from shutil import copyfileobj, which
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile


WHISPER_CUDA = (
    "https://github.com/ggml-org/whisper.cpp/releases/download/v1.8.6/whisper-cublas-11.8.0-bin-x64.zip",
    "ae207301674fe407fd99a3d803ed47947ca4a680a3138241718fb9a4edbbacb7", 61537974,
)
CUBLAS = (
    "https://developer.download.nvidia.com/compute/cuda/redist/libcublas/windows-x86_64/libcublas-windows-x86_64-11.11.3.6-archive.zip",
    "67b0934a6359e4ee26fff823c356021589d392c4fd49ca12624f570edc08e2b9", 420850025,
)
WHISPER_FILES = (
    "whisper-cli.exe", "whisper.dll", "ggml.dll", "ggml-base.dll",
    "ggml-cpu.dll", "ggml-cuda.dll", "cudart64_110.dll",
)
CUBLAS_FILES = ("cublas64_11.dll", "cublasLt64_11.dll")


@dataclass(frozen=True)
class NvidiaDevice:
    uuid: str
    name: str
    available_mib: int


def compatible_device(output, minimum_mib):
    devices = []
    for row in csv.reader(output.splitlines(), skipinitialspace=True):
        if len(row) != 5:
            continue
        uuid, name, capability, memory, driver = [value.strip() for value in row]
        try:
            architecture = tuple(int(part) for part in capability.split("."))
            version = tuple(int(part) for part in driver.split("."))
            available = int(memory)
        except ValueError:
            continue
        # CUDA 11.8 PTX requires a driver which supports that toolkit's JIT.
        if (re.fullmatch(r"GPU-[a-fA-F0-9-]+", uuid) and (5, 2) <= architecture
                and version >= (522, 6) and available >= minimum_mib):
            devices.append(NvidiaDevice(uuid, name, available))
    return max(devices, key=lambda device: device.available_mib, default=None)


def find_nvidia(quality):
    if os.name != "nt":
        return None
    candidates = (
        Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "nvidia-smi.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe",
    )
    executable = next((str(path) for path in candidates if path.is_file()), None) or which("nvidia-smi.exe")
    if not executable:
        return None
    try:
        result = subprocess.run([executable, "--query-gpu=uuid,name,compute_cap,memory.free,driver_version",
                                 "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=5, check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        return None
    return compatible_device(result.stdout, 900 if quality == "small" else 1650)


def cuda_executable(cache, progress, download):
    target = cache / "whisper-cuda-1.8.6"
    required = WHISPER_FILES + CUBLAS_FILES
    executable = target / "whisper-cli.exe"
    if all((target / name).is_file() for name in required):
        return executable

    with TemporaryDirectory(prefix="cuda-", dir=cache) as temporary:
        staging = Path(temporary) / "runtime"
        staging.mkdir()
        sources = [
            (WHISPER_CUDA, "Загрузка Whisper для NVIDIA", "Release/", WHISPER_FILES),
            (CUBLAS, "Загрузка библиотеки NVIDIA cuBLAS",
             "libcublas-windows-x86_64-11.11.3.6-archive/bin/", CUBLAS_FILES),
        ]
        for manifest, stage, prefix, names in sources:
            path = download(manifest, Path(temporary), progress, stage)
            progress("Подготовка распознавания на NVIDIA", None)
            with ZipFile(path) as archive:
                for name in names:
                    with archive.open(prefix + name) as source, (staging / name).open("wb") as output:
                        copyfileobj(source, output, length=1024 * 1024)
            path.unlink()
        target.mkdir(exist_ok=True)
        for name in required:
            (staging / name).replace(target / name)
    return executable
