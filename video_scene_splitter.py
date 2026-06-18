from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    duration: float


@dataclass(frozen=True)
class SceneRange:
    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


def normalize_scene_ranges(cut_points: list[float], *, duration: float, min_scene_duration: float) -> list[SceneRange]:
    points = [0.0]
    points.extend(point for point in sorted(set(cut_points)) if 0.0 < point < duration)
    points.append(duration)
    raw = [(points[index], points[index + 1]) for index in range(len(points) - 1)]
    merged: list[tuple[float, float]] = []
    index = 0
    while index < len(raw):
        start, end = raw[index]
        if end - start < min_scene_duration and index + 1 < len(raw):
            _, next_end = raw[index + 1]
            raw[index + 1] = (start, next_end)
            index += 1
            continue
        if end - start < min_scene_duration and merged:
            previous_start, _ = merged[-1]
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
        index += 1
    return [SceneRange(index + 1, round(start, 3), round(end, 3)) for index, (start, end) in enumerate(merged)]


def probe_video(video_path: Path, *, runner: Callable[..., Any] = subprocess.run) -> VideoMetadata:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(video_path),
    ]
    result = runner(command, capture_output=True, text=True, timeout=120)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"ffprobe failed: {getattr(result, 'stderr', '')}")
    data = json.loads(getattr(result, "stdout", "") or "{}")
    video = next(stream for stream in data.get("streams", []) if stream.get("codec_type") == "video")
    return VideoMetadata(
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=_parse_fps(video.get("avg_frame_rate", "0/1")),
        duration=float(data.get("format", {}).get("duration", 0.0)),
    )


def detect_scene_cut_points(
    video_path: Path,
    *,
    backend: str = "auto",
    threshold: float = 27.0,
    ffmpeg_scene_threshold: float = 0.35,
    runner: Callable[..., Any] = subprocess.run,
) -> list[float]:
    if backend in {"auto", "pyscenedetect"}:
        try:
            return _detect_with_pyscenedetect(video_path, threshold=threshold)
        except Exception:
            if backend == "pyscenedetect":
                raise
    return _detect_with_ffmpeg(video_path, threshold=ffmpeg_scene_threshold, runner=runner)


def cut_scene_clips(
    source_video: Path,
    ranges: list[SceneRange],
    output_dir: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    clips: list[Path] = []
    for scene in ranges:
        output = output_dir / f"scene_{scene.index:03d}.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{scene.start:.3f}",
            "-to",
            f"{scene.end:.3f}",
            "-i",
            str(source_video),
            "-c",
            "copy",
            str(output),
        ]
        result = runner(command, capture_output=True, text=True, timeout=300)
        if getattr(result, "returncode", 1) != 0:
            fallback = [
                "ffmpeg",
                "-y",
                "-ss",
                f"{scene.start:.3f}",
                "-to",
                f"{scene.end:.3f}",
                "-i",
                str(source_video),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                str(output),
            ]
            result = runner(fallback, capture_output=True, text=True, timeout=300)
            if getattr(result, "returncode", 1) != 0:
                raise RuntimeError(f"ffmpeg scene cut failed: {getattr(result, 'stderr', '')}")
        clips.append(output)
    return clips


def _detect_with_pyscenedetect(video_path: Path, *, threshold: float) -> list[float]:
    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video)
    scenes = manager.get_scene_list()
    return [start.get_seconds() for start, _ in scenes[1:]]


def _detect_with_ffmpeg(video_path: Path, *, threshold: float, runner: Callable[..., Any]) -> list[float]:
    command = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vf",
        f"select='gt(scene,{threshold})',showinfo",
        "-f",
        "null",
        "-",
    ]
    result = runner(command, capture_output=True, text=True, timeout=300)
    output = f"{getattr(result, 'stdout', '')}\n{getattr(result, 'stderr', '')}"
    return _parse_showinfo_times(output)


def _parse_showinfo_times(output: str) -> list[float]:
    times = []
    for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", output):
        times.append(round(float(match.group(1)), 3))
    return times


def _parse_fps(value: str) -> float:
    fraction = Fraction(value)
    return float(fraction) if fraction.denominator else 0.0
