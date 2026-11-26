"""Sample, save and reuse framing trajectories in source coordinates."""

from bisect import bisect_right
from hashlib import sha256
from importlib import import_module
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory, TemporaryFile
from zipfile import ZipFile

from .clip_analysis import FLAGS, download_asset
from .projects import write_json

WHEELS = (
    ("https://files.pythonhosted.org/packages/cb/3b/d58c12eafcb298d4e6d0d40216866ab15f59e55d148a5658bb3132311fcf/"
     "numpy-2.2.6-cp313-cp313-win_amd64.whl",
     "b0544343a702fa80c95ad5d3d608ea3599dd54d4632df855e4c8d24eb6ecfa1c", 12610885),
    ("https://files.pythonhosted.org/packages/86/8a/69176a64335aed183529207ba8bc3d329c2999d852b4f3818027203f50e6/"
     "opencv_python_headless-4.11.0.86-cp37-abi3-win_amd64.whl",
     "6c304df9caa7a6a5710b91709dd4786bf20a74d57672b3c31f7033cc638174ca", 39402386),
)
RATE = 5


def tracking_settings(raw, duration):
    raw = raw if isinstance(raw, dict) else {}
    target = raw.get("target")
    if target not in ("face", "motion", "object"):
        target = "face"
    result = {"enabled": raw.get("enabled") is True, "target": target}
    anchor = raw.get("anchor")
    if isinstance(anchor, dict):
        try:
            time, x, y = (float(anchor[key]) for key in ("time", "x", "y"))
            if 0 <= time <= duration and 0 <= x <= 1 and 0 <= y <= 1:
                result["anchor"] = dict(time=round(time, 3), x=round(x, 5), y=round(y, 5))
        except (KeyError, ValueError, TypeError):
            pass
    return result


def load_vision(cache, progress):
    try:
        return import_module("cv2"), import_module("numpy")
    except ImportError as error:
        if sys.platform != "win32" or sys.version_info[:2] != (3, 13):
            raise ValueError("Для автослежения нужен OpenCV (opencv-python-headless).") from error
    folder = cache / "vision-win64-v1"
    if not folder.is_dir():
        assets = [download_asset(manifest, cache, progress, "Загрузка компонентов автослежения")
                  for manifest in WHEELS]
        with TemporaryDirectory(prefix="vision-", dir=cache) as staging:
            unpacked = Path(staging) / "python"
            for asset in assets:
                with ZipFile(asset) as archive:
                    archive.extractall(unpacked)
            unpacked.replace(folder)
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
    try:
        return import_module("cv2"), import_module("numpy")
    except ImportError as error:
        raise ValueError("Windows не смогла загрузить компоненты OpenCV.") from error


def tracking_path(source, folder, clip):
    settings = clip["tracking"]
    target = settings["target"]
    anchor = settings.get("anchor") if target == "object" else None
    if target == "object" and anchor is None:
        raise ValueError("Выберите объект на кадре.")
    intervals = []
    for part in sorted(clip["segments"], key=lambda part: part["start"]):
        start, end = (round(part[key], 3) for key in ("start", "end"))
        if intervals and start <= intervals[-1][1]:
            intervals[-1][1] = max(end, intervals[-1][1])
        else:
            intervals.append([start, end])
    stat = source.stat()
    signature = [2, str(source), stat.st_size, stat.st_mtime_ns, target, anchor, intervals]
    key = sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
    return folder / "Tracking" / (key + ".json"), intervals


def read_trajectory(path):
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if result["key"] == path.stem and isinstance(result["points"], list):
            return result
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def frames(source, start, count, numpy, directory, sample=True):
    filters = "scale=320:320:force_original_aspect_ratio=decrease,setsar=1"
    if sample:
        filters += f",fps={RATE}:start_time=0:round=up"
    args = ["ffmpeg", "-nostdin", "-v", "error", "-ss", str(start), "-protocol_whitelist", "file,crypto",
            "-i", str(source), "-map", "0:V:0", "-vf", filters, "-an", "-frames:v", str(count),
            "-pix_fmt", "gray", "-threads", "1", "-c:v", "pgm", "-f", "image2pipe", "pipe:1"]
    with TemporaryFile(dir=directory) as log, subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=log, creationflags=FLAGS,
    ) as process:
        try:
            while process.stdout.readline() == b"P5\n":
                width, height = map(int, process.stdout.readline().split())
                process.stdout.readline()
                data = process.stdout.read(width * height)
                if len(data) != width * height:
                    break
                yield numpy.frombuffer(data, dtype=numpy.uint8).reshape(height, width)
            if process.wait():
                raise ValueError("Не удалось прочитать кадры для автослежения.")
        finally:
            if process.poll() is None:
                process.kill()


def object_template(source, anchor, numpy, folder):
    generator = frames(source, anchor["time"], 1, numpy, folder, sample=False)
    try:
        image = next(generator, None)
    finally:
        generator.close()
    if image is None:
        raise ValueError("Нет кадра в этой позиции. Выберите объект немного раньше.")
    height, width = image.shape
    radius = max(4, min(height, width) // 10)
    x = max(radius, min(width - radius, round(anchor["x"] * width)))
    y = max(radius, min(height - radius, round(anchor["y"] * height)))
    return image[y-radius:y+radius, x-radius:x+radius].copy()


def detect_center(cv, frame, previous, center, target, model):
    height, width = frame.shape
    if target == "object":
        response = cv.matchTemplate(frame, model, cv.TM_SQDIFF_NORMED)
        score, _, point, _ = cv.minMaxLoc(response)
        if score > 0.30:
            return None
        h, w = model.shape
        return (point[0] + w / 2) / width, (point[1] + h / 2) / height
    if target == "face":
        boxes = model.detectMultiScale(cv.equalizeHist(frame), scaleFactor=1.15, minNeighbors=4, minSize=(20, 20))
    elif previous is not None:
        diff = cv.absdiff(frame, previous)
        _, mask = cv.threshold(diff, 26, 255, cv.THRESH_BINARY)
        if cv.countNonZero(mask) > frame.size * .62:
            return None
        mask = cv.dilate(mask, None, iterations=3)
        contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
        boxes = [cv.boundingRect(contour) for contour in contours if cv.contourArea(contour) >= 75]
        if len(boxes) > 12:
            return None
    else:
        return None
    if not len(boxes):
        return None
    x, y, w, h = max(boxes, key=lambda box: (
        box[2] * box[3] / (1 + 4 * abs((box[0] + box[2] / 2) / width - center[0]))
    ))
    return (x + w / 2) / width, (y + h / 2) / height


def calculate_trajectory(source, path, intervals, clip, cache, progress):
    cached = read_trajectory(path)
    if cached is not None:
        return cached
    path.parent.mkdir(parents=True, exist_ok=True)
    cv, numpy = load_vision(cache, progress)
    cv.setNumThreads(2)
    target = clip["tracking"]["target"]
    model = None
    if target == "face":
        xml = (Path(cv.data.haarcascades) / "haarcascade_frontalface_default.xml").read_text(encoding="utf-8")
        storage = cv.FileStorage(xml, cv.FILE_STORAGE_READ | cv.FILE_STORAGE_MEMORY | cv.FILE_STORAGE_FORMAT_XML)
        model = cv.CascadeClassifier()
        loaded = model.read(storage.getFirstTopLevelNode())
        storage.release()
        if not loaded:
            raise ValueError("Не удалось открыть модель обнаружения лиц.")
    if target == "object":
        model = object_template(source, clip["tracking"]["anchor"], numpy, path.parent)
    progress("Автослежение · анализ кадров", 0)
    points, found = [], 0
    total = sum(max(1, round((end - start) * RATE)) for start, end in intervals)
    for start, end in intervals:
        previous = None
        center = (0.5, 0.5)
        first = len(points)
        count = max(1, round((end - start) * RATE))
        for index, frame in enumerate(frames(source, start, count, numpy, path.parent)):
            detected = detect_center(cv, frame, previous, center, target, model)
            if detected is not None:
                if not found or index < 2:
                    center = detected
                else:
                    center = tuple(0.62 * before + 0.38 * after for before, after in zip(center, detected))
                found += 1
            t = round(start + index / RATE, 3)
            points.append([t, *(round(value, 5) for value in center)])
            previous = frame
            if index % RATE == 0:
                progress("Автослежение · анализ кадров", min(99, 100 * len(points) // total))
        if len(points) == first:
            points.append([start, *center])
        points.append([end, *points[-1][1:]])
    result = {"key": path.stem, "points": points, "found": found}
    write_json(path, result)
    return result


def center_at(points, time):
    index = bisect_right(points, time, key=lambda point: point[0])
    if index == 0:
        return points[0][1:]
    before = points[index - 1]
    if index == len(points):
        return before[1:]
    after = points[index]
    fraction = (time - before[0]) / (after[0] - before[0])
    return [left + (right - left) * fraction for left, right in zip(before[1:], after[1:])]


def crop_filter(directory, width, height, start, end, points):
    center = lambda time: center_at(points, start + time)
    x, y = center(0)
    with (directory / "follow.txt").open("w", encoding="ascii") as commands:
        for frame in range(1, max(1, round((end - start) * 15))):
            t = frame / 15
            cx, cy = center(t)
            commands.write(f"{t:.4f} crop@follow x iw*{cx:.5f}-ow/2, crop@follow y ih*{cy:.5f}-oh/2;\n")
        if commands.tell() == 0:
            commands.write(f"0.000 crop@follow x iw*{x:.5f}-ow/2;\n")
    return (f"setpts=PTS-STARTPTS,sendcmd=f=follow.txt,"
            f"crop@follow={width}:{height}:iw*{x:.5f}-ow/2:ih*{y:.5f}-oh/2")
