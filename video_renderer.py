from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence

from draft_generator import (
    GeneratorConfig,
    OperationPlan,
    TextCard,
    ensure_assets,
    read_input_text,
    split_into_cards,
)
from operation_executor import ExecutionResult, write_beat_wav


DIRECT_SUPPORTED = "direct_supported"
DIRECT_APPROXIMATE = "direct_approximate"
DIRECT_MISSING = "direct_missing"
JIANYING_UI_REQUIRED = "jianying_ui_required"

CRITICAL_UI_OPERATIONS = {"ai_beat", "compound_clip", "copy", "add_effect", "add_filter"}
APPROXIMATE_OPERATIONS = {"add_transition", "add_mask", "add_animation", "keyframe", "split_at_marker"}
SUPPORTED_OPERATIONS = {"add_audio", "set_scale", "add_sticker", "select", "random_place"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass(frozen=True)
class RenderResult:
    route: str
    output_path: Path
    report_path: Path
    command: List[str]


def evaluate_capabilities(plan: OperationPlan) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    critical_missing: List[Dict[str, Any]] = []
    for op in plan.operations:
        op_type = op.get("type", "unknown")
        if op_type in CRITICAL_UI_OPERATIONS:
            status = JIANYING_UI_REQUIRED
            critical = True
            reason = "Critical operation requires Jianying UI for reliable native behavior"
        elif op_type in APPROXIMATE_OPERATIONS:
            status = DIRECT_APPROXIMATE
            critical = False
            reason = "Direct MP4 renderer can approximate this operation"
        elif op_type in SUPPORTED_OPERATIONS:
            status = DIRECT_SUPPORTED
            critical = False
            reason = "Direct MP4 renderer supports this operation"
        else:
            status = DIRECT_MISSING
            critical = False
            reason = "Operation is not known to the direct MP4 renderer"

        item = {
            "operation": op_type,
            "status": status,
            "critical": critical,
            "reason": reason,
            "line": op.get("line", ""),
            "line_index": op.get("line_index"),
        }
        items.append(item)
        if critical or status == DIRECT_MISSING:
            critical_missing.append(item)

    selected_route = "jianying_ui" if any(item["critical"] for item in critical_missing) else "direct_mp4"
    return {
        "version": 1,
        "selected_route": selected_route,
        "items": items,
        "critical_missing": critical_missing,
        "summary": {
            "total_operations": len(items),
            "direct_supported": sum(1 for item in items if item["status"] == DIRECT_SUPPORTED),
            "direct_approximate": sum(1 for item in items if item["status"] == DIRECT_APPROXIMATE),
            "direct_missing": sum(1 for item in items if item["status"] == DIRECT_MISSING),
            "jianying_ui_required": sum(1 for item in items if item["status"] == JIANYING_UI_REQUIRED),
        },
    }


def choose_route(capability_report: Dict[str, Any], *, requested: str = "auto") -> str:
    if requested == "direct":
        return "direct"
    if requested in {"jianying", "draft"}:
        return requested
    return "jianying" if capability_report.get("selected_route") == "jianying_ui" else "direct"


def write_capability_report(report: Dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "capability_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_final_decision(route: str, message: str, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "final_decision.txt"
    path.write_text(f"route={route}\n{message}\n", encoding="utf-8")
    return path


def make_render_output_path(base_name: str, content: str, output_dir: Path) -> Path:
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:8]
    return output_dir / f"{base_name}_{digest}.mp4"


def render_direct_video(
    config: GeneratorConfig,
    input_path: Path,
    assets_dir: Path,
    operation_plan: OperationPlan | None,
    execution_result: ExecutionResult | None,
    *,
    output_dir: Path = Path("output"),
    generated_dir: Path = Path("generated"),
    runner: Callable[..., Any] = subprocess.run,
    include_text: bool = True,
) -> RenderResult:
    input_text = read_input_text(input_path)
    cards = split_into_cards(input_text, config.max_chars_per_card)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    output_path = make_render_output_path(config.draft_name, input_text, output_dir)
    frame_dir = generated_dir / "render_frames" / output_path.stem
    frame_dir.mkdir(parents=True, exist_ok=True)

    assets = ensure_assets(assets_dir, generated_dir, config.resolution, config.default_background_color)
    sticker_assets = _sticker_assets_with_offsets(execution_result)
    frame_paths = _render_frames(cards, assets, sticker_assets, config, frame_dir, include_text=include_text)
    concat_path = _write_concat_file(frame_paths, config.segment_duration_sec, generated_dir / f"{output_path.stem}_concat.txt")
    audio_path = _resolve_render_audio(config, execution_result, generated_dir, len(cards))
    ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
    command = _build_ffmpeg_command(ffmpeg, concat_path, output_path, audio_path)
    result = runner(command, capture_output=True, text=True, timeout=300)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"ffmpeg render failed: {getattr(result, 'stderr', '')}")

    report = {
        "version": 1,
        "route": "direct_mp4",
        "status": "executed",
        "output_path": str(output_path),
        "cards": len(cards),
        "include_text": include_text,
        "assets": [str(path) for path in assets],
        "sticker_assets": sticker_assets,
        "audio_path": str(audio_path) if audio_path else None,
        "operation_summary": _operation_summary(operation_plan),
        "ffmpeg_command": command,
    }
    report_path = generated_dir / "render_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return RenderResult("direct_mp4", output_path, report_path, command)


def _render_frames(
    cards: Sequence[TextCard],
    assets: Sequence[Path],
    sticker_assets: Sequence[Dict[str, Any]],
    config: GeneratorConfig,
    frame_dir: Path,
    *,
    include_text: bool,
) -> List[Path]:
    frames: List[Path] = []
    for index, card in enumerate(cards):
        background = _load_background(assets[index % len(assets)], config)
        _paste_stickers(background, sticker_assets, index)
        if include_text:
            _draw_text_card(background, card.text, config)
        frame_path = frame_dir / f"frame_{index + 1:04d}.png"
        background.save(frame_path)
        frames.append(frame_path)
    return frames


def _load_background(asset: Path, config: GeneratorConfig):
    from PIL import Image

    width, height = config.resolution
    if asset.suffix.lower() in IMAGE_EXTENSIONS and asset.exists():
        try:
            image = Image.open(asset).convert("RGB")
            return _cover_resize(image, width, height)
        except Exception:
            pass
    red, green, blue = _parse_hex_color(config.default_background_color)
    return Image.new("RGB", (width, height), (red, green, blue))


def _cover_resize(image, width: int, height: int):
    ratio = max(width / image.width, height / image.height)
    resized = image.resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))))
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def _paste_stickers(background, sticker_assets: Sequence[Dict[str, Any]], card_index: int) -> None:
    if not sticker_assets:
        return
    from PIL import Image

    for sticker in sticker_assets:
        path = sticker.get("path")
        if not path or not Path(path).exists():
            continue
        try:
            image = Image.open(path).convert("RGBA")
        except Exception:
            continue
        scale = 0.28
        target_width = max(1, int(background.width * scale))
        target_height = max(1, int(image.height * target_width / max(1, image.width)))
        image = image.resize((target_width, target_height))
        offsets = sticker.get("offsets") or {}
        x = int((background.width - target_width) / 2 + float(offsets.get("transform_x", 0)) * background.width * 0.35)
        y = int((background.height - target_height) / 2 + float(offsets.get("transform_y", 0)) * background.height * 0.35)
        if card_index % max(1, int(sticker.get("repeat_every", 1))) == 0:
            background.paste(image, (x, y), image)


def _draw_text_card(background, text: str, config: GeneratorConfig) -> None:
    from PIL import ImageDraw

    draw = ImageDraw.Draw(background)
    font = _load_font(max(18, int(config.resolution[0] * 0.045)))
    lines = _wrap_text(draw, text, font, int(config.resolution[0] * 0.78))
    line_height = max(24, int(font.size * 1.35)) if hasattr(font, "size") else 32
    box_width = int(config.resolution[0] * 0.84)
    box_height = max(line_height * len(lines) + 48, int(config.resolution[1] * 0.12))
    left = (config.resolution[0] - box_width) // 2
    top = int(config.resolution[1] * 0.12)
    draw.rounded_rectangle((left, top, left + box_width, top + box_height), radius=18, fill=(0, 0, 0))
    y = top + 24
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        x = left + (box_width - (bbox[2] - bbox[0])) // 2
        draw.text((x, y), line, font=font, fill=(255, 255, 255))
        y += line_height


def _load_font(size: int):
    from PIL import ImageFont

    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("C:/Windows/Fonts/simsun.ttc"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int) -> List[str]:
    lines: List[str] = []
    current = ""
    for char in text:
        candidate = current + char
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if current and bbox[2] - bbox[0] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:5] or [text]


def _write_concat_file(frame_paths: Sequence[Path], duration_sec: float, path: Path) -> Path:
    lines: List[str] = []
    for frame in frame_paths:
        lines.append(f"file '{_ffconcat_path(frame)}'")
        lines.append(f"duration {duration_sec:.3f}")
    if frame_paths:
        lines.append(f"file '{_ffconcat_path(frame_paths[-1])}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _build_ffmpeg_command(ffmpeg: str, concat_path: Path, output_path: Path, audio_path: Path | None) -> List[str]:
    command = [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path)]
    if audio_path:
        command.extend(["-i", str(audio_path), "-shortest"])
    command.extend(["-vf", "fps=30,format=yuv420p", "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if audio_path:
        command.extend(["-c:a", "aac"])
    command.append(str(output_path))
    return command


def _resolve_render_audio(
    config: GeneratorConfig,
    execution_result: ExecutionResult | None,
    generated_dir: Path,
    card_count: int,
) -> Path | None:
    if execution_result and execution_result.resolved_audio_path:
        return Path(execution_result.resolved_audio_path)
    if config.audio_asset:
        return config.audio_asset
    if not config.auto_generate_missing_assets:
        return None
    duration = max(config.segment_duration_sec, config.segment_duration_sec * max(1, card_count))
    path = generated_dir / "default_beat.wav"
    write_beat_wav(path, duration_sec=duration, bpm=config.default_beat_bpm)
    return path


def _sticker_assets_with_offsets(execution_result: ExecutionResult | None) -> List[Dict[str, Any]]:
    if not execution_result or not execution_result.sticker_assets:
        return []
    if not execution_result.random_offsets:
        return [dict(item) for item in execution_result.sticker_assets]
    return [
        {**item, "offsets": execution_result.random_offsets[index % len(execution_result.random_offsets)]}
        for index, item in enumerate(execution_result.sticker_assets)
    ]


def _operation_summary(operation_plan: OperationPlan | None) -> Dict[str, int]:
    summary: Dict[str, int] = {}
    for op in operation_plan.operations if operation_plan else []:
        op_type = op.get("type", "unknown")
        summary[op_type] = summary.get(op_type, 0) + 1
    return summary


def _parse_hex_color(color: str) -> tuple[int, int, int]:
    normalized = color.strip().lstrip("#")
    if len(normalized) != 6:
        return (0, 0, 0)
    return int(normalized[0:2], 16), int(normalized[2:4], 16), int(normalized[4:6], 16)


def _ffconcat_path(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "'\\''")
