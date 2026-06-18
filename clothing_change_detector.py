from __future__ import annotations

import colorsys
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from video_scene_splitter import probe_video


@dataclass(frozen=True)
class ClothingChangeOptions:
    sample_fps: float = 3.0
    analysis_width: int = 360
    threshold: float = 0.55
    confirmation_frames: int = 2
    min_change_gap_sec: float = 0.8
    crop: tuple[float, float, float, float] = (0.2, 0.18, 0.6, 0.52)
    person_torso_crop: tuple[float, float, float, float] = (0.18, 0.22, 0.64, 0.48)
    comparison_mode: str = "anchor"
    person_detector: str = "none"
    yolo_model: str = "yolo11n.pt"
    yolo_confidence: float = 0.35
    yolo_imgsz: int = 640


@dataclass(frozen=True)
class ClothingSample:
    time_sec: float
    histogram: list[float]


@dataclass(frozen=True)
class PersonBox:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float


class PersonDetector(Protocol):
    def __call__(self, rgb: bytes, width: int, height: int) -> PersonBox | None:
        ...


class YoloPersonDetector:
    def __init__(self, model_path: str, *, confidence: float = 0.35, imgsz: int = 640) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as error:
            raise RuntimeError("ultralytics is not installed. Run: uv pip install ultralytics") from error
        self.model = YOLO(model_path)
        self.confidence = confidence
        self.imgsz = imgsz

    def __call__(self, rgb: bytes, width: int, height: int) -> PersonBox | None:
        try:
            from PIL import Image
        except ImportError as error:
            raise RuntimeError("Pillow is required for YOLO person detection") from error
        image = Image.frombytes("RGB", (width, height), rgb)
        results = self.model.predict(source=image, conf=self.confidence, imgsz=self.imgsz, verbose=False)
        if not results:
            return None
        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None or len(getattr(boxes, "xyxy", [])) == 0:
            return None
        xyxy = boxes.xyxy.cpu().numpy()
        conf = boxes.conf.cpu().numpy()
        cls = boxes.cls.cpu().numpy().astype(int)
        best: PersonBox | None = None
        best_score = -1.0
        for index, box in enumerate(xyxy):
            class_id = int(cls[index])
            name = str(getattr(result, "names", {}).get(class_id, class_id))
            if class_id != 0 and name != "person":
                continue
            x1, y1, x2, y2 = [float(value) for value in box]
            area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
            score = area * float(conf[index])
            if score > best_score:
                best_score = score
                best = PersonBox(x1=x1, y1=y1, x2=x2, y2=y2, confidence=float(conf[index]))
        return best


def detect_clothing_change_points(
    video_path: Path,
    options: ClothingChangeOptions | None = None,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    person_detector: PersonDetector | None = None,
) -> list[float]:
    options = options or ClothingChangeOptions()
    samples = sample_clothing_histograms(video_path, options, runner=runner, person_detector=person_detector)
    return select_confirmed_change_points(samples, options)


def sample_clothing_histograms(
    video_path: Path,
    options: ClothingChangeOptions,
    *,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    person_detector: PersonDetector | None = None,
) -> list[ClothingSample]:
    metadata = probe_video(Path(video_path), runner=runner)
    width, height = _scaled_size(metadata.width, metadata.height, options.analysis_width)
    command = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"fps={options.sample_fps},scale={width}:{height}",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    result = runner(command, capture_output=True, timeout=600)
    if result.returncode != 0:
        stderr = getattr(result, "stderr", b"")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        raise RuntimeError(f"ffmpeg frame sampling failed: {stderr}")
    frame_size = width * height * 3
    raw = bytes(result.stdout)
    samples = []
    detector = person_detector or _create_person_detector(options)
    for index in range(0, len(raw) // frame_size):
        frame = raw[index * frame_size : (index + 1) * frame_size]
        crop = options.crop
        if detector:
            person_box = detector(frame, width, height)
            if person_box:
                crop = clothing_crop_from_person_box(person_box, width, height, options.person_torso_crop)
        samples.append(
            ClothingSample(
                time_sec=round(index / options.sample_fps, 3),
                histogram=hsv_histogram_from_rgb_bytes(frame, width=width, height=height, crop=crop),
            )
        )
    return samples


def clothing_crop_from_person_box(
    box: PersonBox,
    width: int,
    height: int,
    torso_crop: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    rel_x, rel_y, rel_w, rel_h = torso_crop
    person_width = max(1.0, box.x2 - box.x1)
    person_height = max(1.0, box.y2 - box.y1)
    x = (box.x1 + person_width * rel_x) / width
    y = (box.y1 + person_height * rel_y) / height
    w = (person_width * rel_w) / width
    h = (person_height * rel_h) / height
    return _clamp_crop((round(x, 4), round(y, 4), round(w, 4), round(h, 4)))


def select_confirmed_change_points(
    samples: list[ClothingSample],
    options: ClothingChangeOptions,
) -> list[float]:
    if not samples:
        return []
    anchor = samples[0].histogram
    previous = samples[0].histogram
    cut_points: list[float] = []
    changed_streak = 0
    candidate_time: float | None = None
    last_cut = float("-inf")
    for sample in samples[1:]:
        baseline = previous if options.comparison_mode == "adjacent" else anchor
        distance = histogram_distance(baseline, sample.histogram)
        if distance >= options.threshold:
            if changed_streak == 0:
                candidate_time = sample.time_sec
            changed_streak += 1
            if (
                changed_streak >= options.confirmation_frames
                and candidate_time is not None
                and candidate_time - last_cut >= options.min_change_gap_sec - 1e-6
            ):
                cut_points.append(round(candidate_time, 3))
                anchor = sample.histogram
                previous = sample.histogram
                last_cut = candidate_time
                changed_streak = 0
                candidate_time = None
        else:
            changed_streak = 0
            candidate_time = None
        previous = sample.histogram
    return cut_points


def hsv_histogram_from_rgb_bytes(
    rgb: bytes,
    *,
    width: int,
    height: int,
    crop: tuple[float, float, float, float] = (0.0, 0.0, 1.0, 1.0),
    h_bins: int = 16,
    s_bins: int = 4,
    v_bins: int = 4,
) -> list[float]:
    x0, y0, x1, y1 = _crop_bounds(width, height, crop)
    bins = [0.0] * (h_bins * s_bins * v_bins)
    total = 0
    for y in range(y0, y1):
        row = y * width * 3
        for x in range(x0, x1):
            offset = row + x * 3
            r, g, b = rgb[offset], rgb[offset + 1], rgb[offset + 2]
            h, s, v = colorsys.rgb_to_hsv(r / 255.0, g / 255.0, b / 255.0)
            h_index = min(h_bins - 1, int(h * h_bins))
            s_index = min(s_bins - 1, int(s * s_bins))
            v_index = min(v_bins - 1, int(v * v_bins))
            bins[(h_index * s_bins + s_index) * v_bins + v_index] += 1.0
            total += 1
    if total:
        bins = [value / total for value in bins]
    return bins


def histogram_distance(first: list[float], second: list[float]) -> float:
    intersection = sum(min(a, b) for a, b in zip(first, second))
    return max(0.0, min(1.0, 1.0 - intersection))


def _crop_bounds(width: int, height: int, crop: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    x, y, w, h = crop
    x0 = max(0, min(width - 1, int(width * x)))
    y0 = max(0, min(height - 1, int(height * y)))
    x1 = max(x0 + 1, min(width, int(width * (x + w))))
    y1 = max(y0 + 1, min(height, int(height * (y + h))))
    return x0, y0, x1, y1


def _clamp_crop(crop: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = crop
    x = max(0.0, min(0.9999, x))
    y = max(0.0, min(0.9999, y))
    w = max(0.0001, min(1.0 - x, w))
    h = max(0.0001, min(1.0 - y, h))
    return (x, y, w, h)


def _create_person_detector(options: ClothingChangeOptions) -> PersonDetector | None:
    if options.person_detector == "none":
        return None
    if options.person_detector in {"auto", "yolo"}:
        try:
            return YoloPersonDetector(options.yolo_model, confidence=options.yolo_confidence, imgsz=options.yolo_imgsz)
        except RuntimeError:
            if options.person_detector == "auto":
                return None
            raise
    raise ValueError(f"Unsupported person detector: {options.person_detector}")


def _scaled_size(width: int, height: int, target_width: int) -> tuple[int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Video metadata has invalid dimensions")
    scaled_width = min(width, target_width)
    scaled_height = max(1, round(height * (scaled_width / width)))
    return scaled_width, scaled_height
