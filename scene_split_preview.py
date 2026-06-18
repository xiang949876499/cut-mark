from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict
from pathlib import Path
from typing import Sequence

from clothing_change_detector import ClothingChangeOptions, detect_clothing_change_points
from video_scene_splitter import (
    SceneRange,
    cut_scene_clips,
    detect_scene_cut_points,
    normalize_scene_ranges,
    probe_video,
)


DEFAULT_BACKEND = "auto"
DEFAULT_FFMPEG_SCENE_THRESHOLD = 0.35
OUTFIT_CHANGE_FFMPEG_SCENE_THRESHOLD = 0.05


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview scene detection and video splitting.")
    parser.add_argument("video", help="Source video file.")
    parser.add_argument("--output-dir", default="generated/scene_split_preview", help="Preview output folder.")
    parser.add_argument("--preset", default="default", choices=["default", "outfit-change"])
    parser.add_argument("--detector", default="auto", choices=["auto", "scene", "clothing"])
    parser.add_argument("--backend", default=DEFAULT_BACKEND, choices=["auto", "pyscenedetect", "ffmpeg"])
    parser.add_argument("--threshold", type=float, default=27.0, help="PySceneDetect content threshold.")
    parser.add_argument(
        "--ffmpeg-scene-threshold",
        type=float,
        default=DEFAULT_FFMPEG_SCENE_THRESHOLD,
        help="FFmpeg scene threshold.",
    )
    parser.add_argument("--min-scene-duration", type=float, default=0.8)
    parser.add_argument("--sample-duration", type=float, default=1.5, help="Seconds sampled from each scene for preview.mp4.")
    parser.add_argument("--clip-prefix", default="scene", help="Output clip filename prefix, for example outfit.")
    parser.add_argument("--clothing-sample-fps", type=float, default=3.0)
    parser.add_argument("--clothing-threshold", type=float, default=0.4)
    parser.add_argument("--clothing-confirmation-frames", type=int, default=1)
    parser.add_argument("--clothing-analysis-width", type=int, default=360)
    parser.add_argument("--person-detector", default="none", choices=["none", "auto", "yolo"])
    parser.add_argument("--yolo-model", default="yolo11n.pt")
    parser.add_argument("--yolo-confidence", type=float, default=0.35)
    parser.add_argument("--yolo-imgsz", type=int, default=640)
    return parser.parse_args(argv)


def resolve_detection_options(args: argparse.Namespace) -> dict:
    detector = args.detector
    backend = args.backend
    ffmpeg_scene_threshold = args.ffmpeg_scene_threshold
    if args.preset == "outfit-change":
        if detector == "auto":
            detector = "clothing"
        person_detector = args.person_detector
        if person_detector == "none":
            person_detector = "auto"
        if backend == DEFAULT_BACKEND:
            backend = "ffmpeg"
        if ffmpeg_scene_threshold == DEFAULT_FFMPEG_SCENE_THRESHOLD:
            ffmpeg_scene_threshold = OUTFIT_CHANGE_FFMPEG_SCENE_THRESHOLD
    else:
        person_detector = args.person_detector
    if detector == "auto":
        detector = "scene"
    return {
        "detector": detector,
        "backend": backend,
        "threshold": args.threshold,
        "ffmpeg_scene_threshold": ffmpeg_scene_threshold,
        "min_scene_duration": args.min_scene_duration,
        "clothing_sample_fps": args.clothing_sample_fps,
        "clothing_threshold": args.clothing_threshold,
        "clothing_confirmation_frames": args.clothing_confirmation_frames,
        "clothing_analysis_width": args.clothing_analysis_width,
        "person_detector": person_detector,
        "yolo_model": args.yolo_model,
        "yolo_confidence": args.yolo_confidence,
        "yolo_imgsz": args.yolo_imgsz,
    }


def create_scene_split_preview(
    video_path: Path,
    output_dir: Path,
    *,
    backend: str = "auto",
    threshold: float = 27.0,
    ffmpeg_scene_threshold: float = 0.35,
    min_scene_duration: float = 0.8,
    sample_duration: float = 1.5,
    clip_prefix: str = "scene",
    detector: str = "scene",
    clothing_options: ClothingChangeOptions | None = None,
) -> dict:
    video_path = Path(video_path)
    output_dir = Path(output_dir)
    scenes_dir = output_dir / "clips"
    preview_dir = output_dir / "preview_clips"
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)

    metadata = probe_video(video_path)
    if detector == "clothing":
        cut_points = detect_clothing_change_points(video_path, clothing_options)
    else:
        cut_points = detect_scene_cut_points(
            video_path,
            backend=backend,
            threshold=threshold,
            ffmpeg_scene_threshold=ffmpeg_scene_threshold,
        )
    ranges = normalize_scene_ranges(
        cut_points,
        duration=metadata.duration,
        min_scene_duration=min_scene_duration,
    )
    clips = rename_clips_with_prefix(cut_scene_clips(video_path, ranges, scenes_dir), clip_prefix)
    preview_clips = _create_preview_clips(clips, ranges, preview_dir, sample_duration=sample_duration)
    preview_path = output_dir / "preview.mp4"
    _concat_preview_clips(preview_clips, output_dir / "preview_concat.txt", preview_path)
    manifest = {
        "source_video": str(video_path),
        "metadata": asdict(metadata),
        "scene_count": len(ranges),
        "detector": detector,
        "person_detector": clothing_options.person_detector if detector == "clothing" and clothing_options else None,
        "cut_points": cut_points,
        "preview_video": str(preview_path),
        "clips_dir": str(scenes_dir),
        "scenes": [
            {
                "index": scene.index,
                "start": scene.start,
                "end": scene.end,
                "duration": scene.duration,
                "clip": str(clip),
                "preview_clip": str(preview_clip),
            }
            for scene, clip, preview_clip in zip(ranges, clips, preview_clips)
        ],
    }
    manifest_path = output_dir / "scene_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    options = resolve_detection_options(args)
    manifest = create_scene_split_preview(
        Path(args.video),
        Path(args.output_dir),
        detector=options["detector"],
        backend=options["backend"],
        threshold=options["threshold"],
        ffmpeg_scene_threshold=options["ffmpeg_scene_threshold"],
        min_scene_duration=options["min_scene_duration"],
        sample_duration=args.sample_duration,
        clip_prefix=args.clip_prefix,
        clothing_options=ClothingChangeOptions(
            sample_fps=options["clothing_sample_fps"],
            analysis_width=options["clothing_analysis_width"],
            threshold=options["clothing_threshold"],
            confirmation_frames=options["clothing_confirmation_frames"],
            min_change_gap_sec=options["min_scene_duration"],
            crop=(0.25, 0.25, 0.5, 0.45),
            comparison_mode="adjacent",
            person_detector=options["person_detector"],
            yolo_model=options["yolo_model"],
            yolo_confidence=options["yolo_confidence"],
            yolo_imgsz=options["yolo_imgsz"],
        ),
    )
    print(f"Scene count: {manifest['scene_count']}")
    print(f"Preview video: {manifest['preview_video']}")
    print(f"Clips dir: {manifest['clips_dir']}")
    print(f"Manifest: {Path(args.output_dir) / 'scene_manifest.json'}")
    return 0


def rename_clips_with_prefix(clips: list[Path], prefix: str) -> list[Path]:
    clean_prefix = (prefix or "scene").strip() or "scene"
    renamed = []
    for index, clip in enumerate(clips, start=1):
        destination = clip.with_name(f"{clean_prefix}_{index:03d}{clip.suffix}")
        if destination != clip:
            destination.unlink(missing_ok=True)
            clip.rename(destination)
        renamed.append(destination)
    return renamed


def _create_preview_clips(
    clips: list[Path],
    ranges: list[SceneRange],
    preview_dir: Path,
    *,
    sample_duration: float,
) -> list[Path]:
    outputs = []
    for clip, scene in zip(clips, ranges):
        output = preview_dir / f"preview_{scene.index:03d}.mp4"
        duration = max(0.1, min(sample_duration, scene.duration))
        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip),
            "-t",
            f"{duration:.3f}",
            "-vf",
            "scale=720:-2,setsar=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output),
        ]
        _run(command, timeout=300)
        outputs.append(output)
    return outputs


def _concat_preview_clips(clips: list[Path], concat_path: Path, output_path: Path) -> None:
    concat_path.write_text(
        "\n".join(
            f"file '{clip.resolve().as_posix().replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'"
            for clip in clips
        )
        + "\n",
        encoding="utf-8",
    )
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        str(output_path),
    ]
    _run(command, timeout=300)


def _run(command: list[str], *, timeout: int) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
