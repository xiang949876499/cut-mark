from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from video_replace_pipeline import VideoReplaceConfig, process_video_replacement


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scene-based ComfyUI video person/background replacement.")
    parser.add_argument("--video", required=True, help="Source video file.")
    parser.add_argument("--refs", required=True, help="Reference image folder.")
    parser.add_argument("--workflow", required=True, help="ComfyUI API workflow JSON.")
    parser.add_argument("--config", default="config.json", help="Optional project config JSON.")
    parser.add_argument("--output-dir", default="output", help="Final MP4 output folder.")
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188", help="ComfyUI server URL.")
    parser.add_argument("--work-root", default="generated/video_replace", help="Intermediate job folder root.")
    return parser.parse_args(argv)


def load_video_replace_config(config_path: Path, *, comfy_url_override: str | None) -> VideoReplaceConfig:
    data = _read_config(config_path)
    scene = data.get("scene_detection", {})
    replace = data.get("video_replace", {})
    return VideoReplaceConfig(
        comfy_url=comfy_url_override or data.get("comfy_url", "http://127.0.0.1:8188"),
        comfy_workflow_bindings=data.get("comfy_workflow_bindings"),
        scene_backend=scene.get("backend", "auto"),
        scene_threshold=float(scene.get("threshold", 27.0)),
        ffmpeg_scene_threshold=float(scene.get("ffmpeg_scene_threshold", 0.35)),
        min_scene_duration_sec=float(scene.get("min_scene_duration_sec", 0.8)),
        preserve_source_audio=bool(replace.get("preserve_source_audio", True)),
        skip_existing_processed=bool(replace.get("skip_existing_processed", True)),
        comfy_timeout_sec=float(replace.get("comfy_timeout_sec", 1800.0)),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_video_replace_config(Path(args.config), comfy_url_override=args.comfy_url)
    result = process_video_replacement(
        source_video=Path(args.video),
        refs_dir=Path(args.refs),
        workflow_path=Path(args.workflow),
        output_dir=Path(args.output_dir),
        config=config,
        work_root=Path(args.work_root),
    )
    print(f"Video saved: {result.output_path}")
    print(f"Manifest saved: {result.manifest_path}")
    print(f"Report saved: {result.report_path}")
    return 0


def _read_config(config_path: Path) -> dict:
    if not config_path.is_file():
        return {}
    return json.loads(config_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
