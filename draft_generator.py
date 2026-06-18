import argparse
import hashlib
import json
import re
import struct
import sys
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from operation_executor import ExecutionResult, OperationExecutor, build_full_ui_task_plan, write_execution_report, write_ui_task_plan


DEFAULT_FALLBACK_TRANSITIONS = ["叠化", "右移", "下移", "向上", "模糊"]
DEFAULT_CONFIG = {
    "draft_name": "web_transition_video",
    "resolution": [1080, 1920],
    "segment_duration_sec": 4.5,
    "max_chars_per_card": 72,
    "fallback_transitions": DEFAULT_FALLBACK_TRANSITIONS,
    "default_background_color": "#000000",
    "audio_asset": None,
    "sticker_manifest": "manifests/stickers.json",
    "effect_manifest": "manifests/effects.json",
    "filter_manifest": "manifests/filters.json",
    "ui_automation_enabled": False,
    "auto_generate_missing_assets": True,
    "default_beat_bpm": 120,
    "placeholder_stickers_enabled": True,
    "unique_draft_name_by_content": True,
    "backend": "kdenlive",
    "kdenlive_version": "26.04.2",
    "kdenlive_runtime_dir": "generated/runtime",
    "auto_download_kdenlive": True,
    "render_video_codec": "libx264",
    "render_audio_codec": "aac",
    "render_crf": 20,
    "render_preset": "medium",
    "render_fps": 30,
    "render_with_unsupported_operations": True,
}

SUPPORTED_ASSET_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".gif",
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".m4v",
}

SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}


@dataclass(frozen=True)
class TextCard:
    text: str


@dataclass(frozen=True)
class GeneratorConfig:
    draft_folder: Optional[Path]
    draft_name: str
    resolution: Tuple[int, int]
    segment_duration_sec: float
    max_chars_per_card: int
    fallback_transitions: List[str]
    default_background_color: str
    audio_asset: Optional[Path]
    sticker_manifest: Path
    effect_manifest: Path
    filter_manifest: Path
    ui_automation_enabled: bool
    auto_generate_missing_assets: bool
    default_beat_bpm: int
    placeholder_stickers_enabled: bool
    unique_draft_name_by_content: bool
    backend: str
    kdenlive_version: str
    kdenlive_runtime_dir: Path
    auto_download_kdenlive: bool
    render_video_codec: str
    render_audio_codec: str
    render_crf: int
    render_preset: str
    render_fps: int
    render_with_unsupported_operations: bool


@dataclass(frozen=True)
class OperationPlan:
    operations: List[dict]
    warnings: List[str]


class _FallbackHTMLTextParser(HTMLParser):
    """Small fallback used when BeautifulSoup is not installed."""

    SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form"}
    BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div", "article", "section", "br"}

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts: List[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        cleaned = " ".join(data.split())
        if cleaned:
            self._parts.append(cleaned)

    def get_text(self) -> str:
        return "\n".join(_clean_lines(" ".join(self._parts).splitlines()))


def load_config(path: Path) -> GeneratorConfig:
    raw = DEFAULT_CONFIG.copy()
    with path.open("r", encoding="utf-8-sig") as handle:
        raw.update(json.load(handle))

    draft_folder_value = raw.get("draft_folder")
    draft_folder = Path(draft_folder_value) if draft_folder_value else None

    resolution = raw["resolution"]
    if len(resolution) != 2:
        raise ValueError("resolution must contain [width, height]")

    return GeneratorConfig(
        draft_folder=draft_folder,
        draft_name=str(raw["draft_name"]),
        resolution=(int(resolution[0]), int(resolution[1])),
        segment_duration_sec=float(raw["segment_duration_sec"]),
        max_chars_per_card=int(raw["max_chars_per_card"]),
        fallback_transitions=[str(item) for item in raw["fallback_transitions"]],
        default_background_color=str(raw["default_background_color"]),
        audio_asset=Path(raw["audio_asset"]) if raw.get("audio_asset") else None,
        sticker_manifest=Path(raw["sticker_manifest"]),
        effect_manifest=Path(raw["effect_manifest"]),
        filter_manifest=Path(raw["filter_manifest"]),
        ui_automation_enabled=bool(raw["ui_automation_enabled"]),
        auto_generate_missing_assets=bool(raw.get("auto_generate_missing_assets", True)),
        default_beat_bpm=int(raw.get("default_beat_bpm", 120)),
        placeholder_stickers_enabled=bool(raw.get("placeholder_stickers_enabled", True)),
        unique_draft_name_by_content=bool(raw.get("unique_draft_name_by_content", True)),
        backend=str(raw.get("backend", "kdenlive")),
        kdenlive_version=str(raw.get("kdenlive_version", "26.04.2")),
        kdenlive_runtime_dir=Path(raw.get("kdenlive_runtime_dir", "generated/runtime")),
        auto_download_kdenlive=bool(raw.get("auto_download_kdenlive", True)),
        render_video_codec=str(raw.get("render_video_codec", "libx264")),
        render_audio_codec=str(raw.get("render_audio_codec", "aac")),
        render_crf=int(raw.get("render_crf", 20)),
        render_preset=str(raw.get("render_preset", "medium")),
        render_fps=int(raw.get("render_fps", 30)),
        render_with_unsupported_operations=bool(raw.get("render_with_unsupported_operations", True)),
    )


def read_input_text(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".html", ".htm"}:
        return extract_text_from_html(content)
    return content


def extract_text_from_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        parser = _FallbackHTMLTextParser()
        parser.feed(html)
        return parser.get_text()

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
        tag.decompose()
    block_texts = [
        tag.get_text(" ", strip=True)
        for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li"])
    ]
    if block_texts:
        return "\n".join(_clean_lines(block_texts))
    return "\n".join(_clean_lines(soup.get_text("\n").splitlines()))


def split_into_cards(text: str, max_chars: int) -> List[TextCard]:
    paragraphs = _clean_lines(text.splitlines())
    cards: List[TextCard] = []
    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            cards.append(TextCard(paragraph))
            continue
        cards.extend(TextCard(chunk) for chunk in _split_long_paragraph(paragraph, max_chars))
    if not cards:
        raise ValueError("Input text did not contain any usable content")
    return cards


def build_operation_plan(text: str) -> OperationPlan:
    operations: List[dict] = []
    for index, line in enumerate(_clean_lines(text.splitlines()), start=1):
        normalized = _to_simplified_keywords(line)

        if _mentions_background_audio(normalized):
            operations.append({"type": "add_audio", "line": line, "line_index": index})

        transition = pick_transition(line, [], 0) if _mentions_transition(normalized) and _contains_transition_name(line) else None
        if transition:
            operations.append({"type": "add_transition", "name": transition, "line": line, "line_index": index})

        scale = _parse_scale_value(normalized)
        if scale is not None:
            operations.append({"type": "set_scale", "value": scale, "line": line, "line_index": index})

        if "圆形蒙版" in normalized or "圆形盟板" in normalized:
            operations.append({"type": "add_mask", "mask": "circle", "line": line, "line_index": index})
        elif "蒙版" in normalized or "盟板" in normalized:
            operations.append({"type": "add_mask", "mask": "circle", "line": line, "line_index": index})

        animation = _parse_animation(normalized)
        if animation:
            operations.append({**animation, "line": line, "line_index": index})

        if "AI卡点" in normalized or "自动卡点" in normalized:
            operations.append({"type": "ai_beat", "line": line, "line_index": index})
        if "复合片段" in normalized:
            operations.append({"type": "compound_clip", "line": line, "line_index": index})
        if _mentions_marker_split(normalized):
            operations.append(
                {
                    "type": "split_at_marker",
                    "marker_index": _parse_marker_index(normalized),
                    "line": line,
                    "line_index": index,
                }
            )
        if _mentions_sticker_add(normalized):
            operations.append(
                {
                    "type": "add_sticker",
                    "name": _parse_named_resource(normalized, "贴纸"),
                    "line": line,
                    "line_index": index,
                }
            )
        if _mentions_selection(normalized):
            operations.append({"type": "select", "selector": _parse_selector(normalized), "line": line, "line_index": index})
        if "复制" in normalized or "复製" in normalized:
            operations.append({"type": "copy", "line": line, "line_index": index})
        if "关键帧" in normalized or "关键针" in normalized:
            operations.append({"type": "keyframe", "line": line, "line_index": index})
        if _mentions_effect_or_filter(normalized):
            op_type = "add_filter" if "滤镜" in normalized else "add_effect"
            operations.append(
                {
                    "type": op_type,
                    "name": _parse_effect_name(normalized),
                    "selector": _parse_effect_selector(normalized),
                    "line": line,
                    "line_index": index,
                }
            )
        if "拖动" in normalized or "随机" in normalized:
            operations.append(
                {
                    "type": "random_place",
                    "selector": "named:光圈" if "光圈" in normalized else "selected",
                    "count": 6,
                    "line": line,
                    "line_index": index,
                }
            )

    return OperationPlan(operations=operations, warnings=[])


def write_operation_artifacts(plan: OperationPlan, output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    plan_path = output_dir / "operation_plan.json"
    warnings_path = output_dir / "operation_warnings.txt"
    plan_path.write_text(json.dumps(plan.operations, ensure_ascii=False, indent=2), encoding="utf-8")
    warnings_path.write_text("\n".join(plan.warnings) + ("\n" if plan.warnings else ""), encoding="utf-8")
    return {"plan": plan_path, "warnings": warnings_path}


def make_draft_name_for_content(base_name: str, content: str) -> str:
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:8]
    return f"{base_name}_{digest}"


def pick_transition(text: str, fallback_transitions: Sequence[str], index: int) -> str:
    normalized = _to_simplified_keywords(text)
    for name in available_transition_names():
        if name and (name in text or _to_simplified_keywords(name) in normalized):
            return name
    if not fallback_transitions:
        raise ValueError("fallback_transitions cannot be empty")
    return fallback_transitions[index % len(fallback_transitions)]


def available_transition_names() -> List[str]:
    try:
        from pyJianYingDraft import TransitionType
    except ImportError:
        return sorted(set(DEFAULT_FALLBACK_TRANSITIONS + ["信号故障"]), key=len, reverse=True)

    names = []
    for member in TransitionType:
        names.append(member.value.name)
        names.append(member.name.replace("_", " "))
    return sorted(set(names), key=len, reverse=True)


def ensure_assets(
    assets_dir: Path,
    generated_dir: Path,
    resolution: Tuple[int, int],
    default_background_color: str,
) -> List[Path]:
    assets = _list_assets(assets_dir)
    if assets:
        return assets

    generated_dir.mkdir(parents=True, exist_ok=True)
    default_path = generated_dir / "default_black.png"
    write_solid_png(default_path, resolution[0], resolution[1], default_background_color)
    return [default_path]


def write_solid_png(path: Path, width: int, height: int, color: str) -> None:
    red, green, blue = _parse_hex_color(color)
    raw = b"".join(b"\x00" + bytes((red, green, blue)) * width for _ in range(height))
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(raw))
    png += _png_chunk(b"IEND", b"")
    path.write_bytes(png)


def build_draft(
    config: GeneratorConfig,
    input_path: Path,
    assets_dir: Path,
    operation_plan: OperationPlan | None = None,
    execution_result: ExecutionResult | None = None,
    include_text: bool = True,
) -> None:
    draft_folder_path = require_jianying_draft_folder(config)
    try:
        import pyJianYingDraft as draft
    except ImportError as exc:
        raise RuntimeError("pyJianYingDraft is required. Install dependencies with: pip install -r requirements.txt") from exc

    input_text = read_input_text(input_path)
    draft_name = (
        make_draft_name_for_content(config.draft_name, input_text)
        if config.unique_draft_name_by_content
        else config.draft_name
    )
    cards = split_into_cards(input_text, config.max_chars_per_card)
    assets = ensure_assets(assets_dir, Path("generated"), config.resolution, config.default_background_color)
    operations = operation_plan.operations if operation_plan else []
    scale = _last_operation_value(operations, "set_scale", default=1.0)
    transition_names = [op["name"] for op in operations if op.get("type") == "add_transition"]
    mask_ops = [op for op in operations if op.get("type") == "add_mask"]
    animation_ops = [op for op in operations if op.get("type") == "add_animation"]
    resolved_audio_path = _resolved_audio_path(config, assets_dir, execution_result)
    marker_times = execution_result.marker_times if execution_result else []
    sticker_assets = execution_result.sticker_assets if execution_result else []
    sticker_assets = _attach_random_offsets(sticker_assets, execution_result.random_offsets if execution_result else [])

    draft_folder = draft.DraftFolder(str(draft_folder_path))
    try:
        script = draft_folder.create_draft(
            draft_name,
            config.resolution[0],
            config.resolution[1],
            allow_replace=True,
        )
    except PermissionError as exc:
        raise RuntimeError(
            f"Cannot replace draft '{draft_name}'. Close it in Jianying/剪映, "
            "or change draft_name in config.json and run again."
        ) from exc

    if resolved_audio_path:
        script.add_track(draft.TrackType.audio)
    script.add_track(draft.TrackType.video, "main_video")
    if sticker_assets:
        for sticker_index, item in enumerate(sticker_assets):
            track_type = draft.TrackType.sticker if item.get("resource_id") else draft.TrackType.video
            script.add_track(track_type, f"sticker_overlay_{sticker_index}")
    if include_text:
        script.add_track(draft.TrackType.text)

    duration_us = int(round(config.segment_duration_sec * 1_000_000))
    timeranges = _make_timeranges(draft, len(cards), duration_us, marker_times)
    total_duration_us = max((tr.start + tr.duration for tr in timeranges), default=duration_us * len(cards))
    if resolved_audio_path:
        audio_segment = draft.AudioSegment(str(resolved_audio_path), draft.trange(0, total_duration_us), volume=0.6)
        script.add_segment(audio_segment)

    video_segments = []
    sticker_segments = []
    for index, card in enumerate(cards):
        timerange = timeranges[index]
        asset_path = str(assets[index % len(assets)])
        video_segment = draft.VideoSegment(
            asset_path,
            timerange,
            clip_settings=draft.ClipSettings(scale_x=scale, scale_y=scale),
        )
        _apply_video_operations(draft, video_segment, mask_ops, animation_ops)
        _apply_effects_and_filters_to_segment(draft, video_segment, "main_video", True, execution_result)
        video_segments.append(video_segment)

        script.add_segment(video_segment, track_name="main_video")
        if include_text:
            text_segment = draft.TextSegment(
                card.text,
                timerange,
                style=draft.TextStyle(
                    size=7.0,
                    color=(1.0, 1.0, 1.0),
                    align=1,
                    auto_wrapping=True,
                    max_line_width=0.82,
                ),
                border=draft.TextBorder(width=28.0, color=(0.0, 0.0, 0.0)),
                background=draft.TextBackground(color="#000000", alpha=0.55, round_radius=0.08, width=0.82, height=0.2),
                clip_settings=draft.ClipSettings(transform_y=0.55),
            )
            script.add_segment(text_segment)
        sticker_segments.extend(_add_sticker_overlays(draft, script, sticker_assets, timerange, index, execution_result))

    for index, segment in enumerate(video_segments[:-1]):
        transition_name = (
            transition_names[index % len(transition_names)]
            if transition_names
            else pick_transition(cards[index].text, config.fallback_transitions, index)
        )
        transition_type = _resolve_transition_type(draft.TransitionType, transition_name)
        segment.add_transition(transition_type)

    script.save()
    _print_asset_summary(assets, len(cards))
    print(f"Draft saved: {draft_name}")


def extract_video_text(video_url: str, *, extractor=None, config: dict | None = None) -> str:
    if extractor is None:
        try:
            import video_subtitle
        except ImportError as exc:
            raise RuntimeError("video_subtitle.py must be available next to generate_draft.py") from exc
        config = video_subtitle.load_config() if config is None else config
        extractor = video_subtitle.extract

    extraction_config = dict(config or {})
    extraction_config["extract_frames"] = False
    result = extractor(video_url, extraction_config)
    if not result:
        raise RuntimeError("Video subtitle extraction returned no result")
    if result.get("error"):
        raise RuntimeError(result["error"])

    text = (result.get("subtitle_text") or "").strip()
    if not text:
        raise RuntimeError("Video subtitle extraction did not return subtitle_text")
    return text


def write_video_text_input(
    video_url: str,
    output_path: Path,
    *,
    extractor=None,
    config: dict | None = None,
) -> Path:
    text = extract_video_text(video_url, extractor=extractor, config=config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a Jianying draft from pasted web text or HTML.")
    parser.add_argument("--config", default="config.json", type=Path, help="Path to config.json")
    parser.add_argument(
        "--mode",
        choices=["cards", "operations"],
        default="cards",
        help="cards: text-card draft; operations: parse executable editing operations and warnings",
    )
    parser.add_argument("--ui-mode", choices=["off", "assist", "experimental"], default="off", help="Optional Jianying UI automation mode")
    parser.add_argument("--markers", choices=["audio", "manual", "none"], default="audio", help="Marker generation mode for operations")
    parser.add_argument("--seed", type=int, default=42, help="Seed for deterministic random placement")
    parser.add_argument(
        "--route",
        choices=["kdenlive", "kdenlive-project", "direct", "jianying", "draft", "ui-only"],
        default="kdenlive",
        help=(
            "kdenlive: generate a Kdenlive project and render MP4; "
            "kdenlive-project: generate project only; direct: MP4 approximation only; "
            "jianying: MP4 approximation plus Jianying draft; draft: old draft-only path; "
            "ui-only: execute operations through Jianying UI only"
        ),
    )
    parser.add_argument("--output-dir", default=Path("output"), type=Path, help="Directory for direct MP4 renders")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="Path to pasted .txt/.html content")
    source.add_argument("--video-url", help="Video URL to extract subtitle/copy text from video_subtitle.py")
    parser.add_argument(
        "--extracted-input",
        default=Path("input/extracted_from_video.txt"),
        type=Path,
        help="Where to save subtitle text extracted from --video-url",
    )
    parser.add_argument("--assets", default=Path("assets"), type=Path, help="Directory containing optional 01.*, 02.* assets")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        config = load_config(args.config)
        input_path = args.input
        if args.video_url:
            input_path = write_video_text_input(args.video_url, args.extracted_input)
            print(f"Extracted video text saved: {input_path}")
        operation_plan = None
        execution_result = None
        artifacts = None
        selected_route = "draft" if args.route == "draft" else ("ui-only" if args.route == "ui-only" else None)
        include_text = args.mode == "cards"
        if args.mode == "operations":
            input_text = read_input_text(input_path)
            operation_plan = build_operation_plan(input_text)
            card_count = len(split_into_cards(input_text, config.max_chars_per_card))
            executor = OperationExecutor(
                args.assets,
                generated_dir=Path("generated"),
                sticker_manifest=config.sticker_manifest,
                effect_manifest=config.effect_manifest,
                filter_manifest=config.filter_manifest,
                seed=args.seed,
                markers_mode=args.markers,
                segment_duration_sec=config.segment_duration_sec,
                audio_asset=config.audio_asset,
                ui_mode=args.ui_mode if config.ui_automation_enabled else "off",
                backend=config.backend,
                auto_generate_missing_assets=config.auto_generate_missing_assets,
                default_beat_bpm=config.default_beat_bpm,
                placeholder_stickers_enabled=config.placeholder_stickers_enabled,
            )
            execution_result = executor.prepare(operation_plan, card_count=card_count)
            operation_plan = OperationPlan(operation_plan.operations, execution_result.warnings)
            artifacts = write_operation_artifacts(operation_plan, Path("generated"))
            report_path = write_execution_report(execution_result, Path("generated"))
            print(f"Operation plan saved: {artifacts['plan']}")
            print(f"Operation warnings saved: {artifacts['warnings']}")
            print(f"Operation report saved: {report_path}")
            if args.route in {"ui-only", "jianying"}:
                ui_tasks = build_full_ui_task_plan(operation_plan.operations) if args.route == "ui-only" else execution_result.ui_tasks
                ui_plan_path = write_ui_task_plan(ui_tasks, Path("generated"))
                print(f"UI task plan saved: {ui_plan_path}")
        route_plan = operation_plan or OperationPlan([], [])
        if args.route in {"kdenlive", "kdenlive-project"}:
            from kdenlive_backend import generate_kdenlive

            kdenlive_result = generate_kdenlive(
                config=config,
                input_path=input_path,
                assets_dir=args.assets,
                operation_plan=route_plan,
                execution_result=execution_result or ExecutionResult([], [], [], [], []),
                output_dir=args.output_dir,
                generated_dir=Path("generated"),
                render=args.route == "kdenlive",
                include_text=include_text,
            )
            print(f"Kdenlive project saved: {kdenlive_result.project_path}")
            if kdenlive_result.video_path:
                print(f"MP4 saved: {kdenlive_result.video_path}")
            else:
                print("Kdenlive project route selected; render skipped.")
            print(f"Kdenlive report saved: {kdenlive_result.report_path}")
            return
        if args.route == "ui-only":
            from video_renderer import write_final_decision

            if args.ui_mode == "experimental":
                from jianying_ui import JianyingUIAutomation, RunConfig

                ui_report = JianyingUIAutomation().run(
                    RunConfig(
                        plan_path=Path("generated/ui_task_plan.json"),
                        profile_path=Path("generated/jianying_ui_profile.json"),
                        output_dir=Path("generated/ui_artifacts"),
                        report_path=Path("generated/ui_report.json"),
                        auto_confirm_profile=True,
                        require_editor=True,
                    )
                )
                failed = [task for task in ui_report.get("tasks", []) if task.get("status") == "ui_failed"]
                write_final_decision(
                    "ui-only",
                    f"Jianying UI-only mode executed through UI. Failed tasks: {len(failed)}.",
                    Path("generated"),
                )
                print(f"UI report saved: generated/ui_report.json ({len(ui_report.get('tasks', []))} tasks)")
            else:
                write_final_decision(
                    "ui-only",
                    "Jianying UI-only mode only generated UI task plan; pass --ui-mode experimental to operate Jianying.",
                    Path("generated"),
                )
                print("UI-only route selected. UI task plan generated; no Jianying UI actions executed.")
            return

        if args.route != "draft":
            from video_renderer import (
                choose_route,
                evaluate_capabilities,
                render_direct_video,
                write_capability_report,
                write_final_decision,
            )

            capability_report = evaluate_capabilities(route_plan)
            capability_path = write_capability_report(capability_report, Path("generated"))
            route = choose_route(capability_report, requested=args.route)
            selected_route = route
            print(f"Capability report saved: {capability_path}")
            if route == "direct":
                render_result = render_direct_video(
                    config,
                    input_path,
                    args.assets,
                    route_plan,
                    execution_result,
                    output_dir=args.output_dir,
                    generated_dir=Path("generated"),
                    include_text=include_text,
                )
                write_final_decision(
                    route,
                    f"Direct MP4 render selected. Output: {render_result.output_path}",
                    Path("generated"),
                )
                print(f"MP4 saved: {render_result.output_path}")
                return
            if route == "jianying":
                render_message = "Direct MP4 approximation was not generated."
                try:
                    render_result = render_direct_video(
                        config,
                        input_path,
                        args.assets,
                        route_plan,
                        execution_result,
                        output_dir=args.output_dir,
                        generated_dir=Path("generated"),
                        include_text=include_text,
                    )
                    render_message = f"Approximate MP4 kept at {render_result.output_path}."
                    print(f"Approximate MP4 saved: {render_result.output_path}")
                except Exception as render_exc:
                    render_message = f"Approximate MP4 failed: {render_exc}"
                    print(render_message, file=sys.stderr)
                write_final_decision(
                    route,
                    (
                        f"Critical operations require Jianying UI. {render_message} "
                        "Jianying UI actions were not executed; use --ui-mode experimental "
                        "and set config.ui_automation_enabled=true to run them."
                    ),
                    Path("generated"),
                )
        build_draft(
            config,
            input_path,
            args.assets,
            operation_plan=operation_plan,
            execution_result=execution_result,
            include_text=include_text,
        )
        if (
            selected_route == "jianying"
            and operation_plan
            and config.ui_automation_enabled
            and args.ui_mode == "experimental"
        ):
            from jianying_ui import JianyingUIAutomation, RunConfig

            ui_report = JianyingUIAutomation().run(
                RunConfig(
                    plan_path=Path("generated/ui_task_plan.json"),
                    profile_path=Path("generated/jianying_ui_profile.json"),
                    output_dir=Path("generated/ui_artifacts"),
                    report_path=Path("generated/ui_report.json"),
                )
            )
            from video_renderer import write_final_decision

            failed = [task for task in ui_report.get("tasks", []) if task.get("status") == "ui_failed"]
            write_final_decision(
                "jianying",
                f"Jianying UI actions executed. Failed tasks: {len(failed)}.",
                Path("generated"),
            )
            print(f"UI report saved: generated/ui_report.json ({len(ui_report.get('tasks', []))} tasks)")
        if operation_plan and artifacts:
            write_operation_artifacts(operation_plan, Path("generated"))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def require_jianying_draft_folder(config: GeneratorConfig) -> Path:
    if config.draft_folder is None:
        raise ValueError("draft_folder is required for the Jianying route")
    return config.draft_folder


def _resolve_transition_type(transition_type, transition_name: str):
    normalized = _to_simplified_keywords(transition_name)
    for member in transition_type:
        if (
            member.value.name == transition_name
            or member.name == transition_name
            or member.name.replace("_", " ") == transition_name
            or _to_simplified_keywords(member.value.name) == normalized
        ):
            return member
    raise ValueError(f"Unknown transition: {transition_name}")


def _make_timeranges(draft, count: int, duration_us: int, marker_times: Sequence[float]):
    if len(marker_times) >= 2:
        timeranges = []
        for index in range(count):
            if index + 1 < len(marker_times):
                start_us = int(round(marker_times[index] * 1_000_000))
                end_us = int(round(marker_times[index + 1] * 1_000_000))
                timeranges.append(draft.trange(start_us, max(duration_us // 4, end_us - start_us)))
            else:
                start_us = int(round(marker_times[-1] * 1_000_000)) + (index - len(marker_times) + 1) * duration_us
                timeranges.append(draft.trange(start_us, duration_us))
        return timeranges
    return [draft.trange(index * duration_us, duration_us) for index in range(count)]


def _add_sticker_overlays(
    draft,
    script,
    sticker_assets: Sequence[dict],
    timerange,
    index: int,
    execution_result: ExecutionResult | None,
) -> List[dict]:
    if not sticker_assets:
        return []
    created = []
    for sticker_index, sticker in enumerate(sticker_assets):
        offsets = sticker.get("offsets") or {}
        clip_settings = draft.ClipSettings(
            scale_x=0.35,
            scale_y=0.35,
            transform_x=float(offsets.get("transform_x", 0.0)),
            transform_y=float(offsets.get("transform_y", 0.0)),
        )
        if sticker.get("resource_id"):
            segment = draft.StickerSegment(sticker["resource_id"], timerange, clip_settings=clip_settings)
            _apply_effects_and_filters_to_segment(draft, segment, sticker.get("name", ""), False, execution_result)
            script.add_segment(segment, track_name=f"sticker_overlay_{sticker_index}")
        else:
            segment = draft.VideoSegment(sticker["path"], timerange, clip_settings=clip_settings)
            _apply_effects_and_filters_to_segment(draft, segment, sticker.get("name", ""), False, execution_result)
            script.add_segment(segment, track_name=f"sticker_overlay_{sticker_index}")
        created.append({"name": sticker.get("name", ""), "segment": segment, "index": sticker_index})
    return created


def _apply_video_operations(draft, video_segment, mask_ops: Sequence[dict], animation_ops: Sequence[dict]) -> None:
    for op in mask_ops:
        if op.get("mask") == "circle":
            video_segment.add_mask(draft.MaskType.圆形, size=0.5)
            break

    for op in animation_ops:
        animation = _resolve_video_animation(draft, op.get("phase", ""), op.get("name", ""))
        if animation:
            video_segment.add_animation(animation)


def _apply_effects_and_filters_to_segment(
    draft,
    segment,
    segment_name: str,
    is_main_video: bool,
    execution_result: ExecutionResult | None,
) -> None:
    if not execution_result:
        return
    for effect in execution_result.effects:
        if not _selector_matches_segment(effect.get("selector", "selected"), segment_name, is_main_video):
            continue
        member = _effect_member_from_result(draft, effect)
        if member and hasattr(segment, "add_effect"):
            segment.add_effect(member)
    for filter_item in execution_result.filters:
        if not _selector_matches_segment(filter_item.get("selector", "selected"), segment_name, is_main_video):
            continue
        member = _effect_member_from_result(draft, filter_item)
        if member and hasattr(segment, "add_filter"):
            segment.add_filter(member)


def _selector_matches_segment(selector: str, segment_name: str, is_main_video: bool) -> bool:
    if selector == "all_video_segments":
        return is_main_video
    if selector == "all_stickers":
        return not is_main_video
    if selector.startswith("named:"):
        target = selector.split(":", 1)[1]
        return (not is_main_video) and target in segment_name
    return is_main_video


def _effect_member_from_result(draft, result: dict):
    enum_type_name = result.get("enum_type")
    member_name = result.get("member_name")
    if not enum_type_name or not member_name or not hasattr(draft, enum_type_name):
        return None
    enum_type = getattr(draft, enum_type_name)
    return getattr(enum_type, member_name, None)


def _resolve_video_animation(draft, phase: str, name: str):
    if phase == "outro":
        return _find_enum_member(draft.OutroType, name)
    if phase == "loop":
        return _find_enum_member(draft.GroupAnimationType, name) or _find_enum_member(draft.IntroType, name)
    return None


def _find_enum_member(enum_type, name: str):
    normalized = _to_simplified_keywords(name)
    for member in enum_type:
        if member.name == name or normalized in _to_simplified_keywords(member.name):
            return member
    return None


def _last_operation_value(operations: Sequence[dict], operation_type: str, *, default):
    matches = [op.get("value") for op in operations if op.get("type") == operation_type]
    return matches[-1] if matches else default


def _first_audio_asset(assets_dir: Path) -> Path | None:
    if not assets_dir.exists():
        return None
    audio_files = sorted(
        (path for path in assets_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS),
        key=_natural_key,
    )
    return audio_files[0] if audio_files else None


def _resolved_audio_path(config: GeneratorConfig, assets_dir: Path, execution_result: ExecutionResult | None) -> Path | None:
    if execution_result:
        return Path(execution_result.resolved_audio_path) if execution_result.resolved_audio_path else None
    return config.audio_asset or _first_audio_asset(assets_dir)


def _attach_random_offsets(sticker_assets: Sequence[dict], random_offsets: Sequence[dict]) -> List[dict]:
    if not sticker_assets:
        return []
    if not random_offsets:
        return [dict(item) for item in sticker_assets]
    return [
        {**item, "offsets": random_offsets[index % len(random_offsets)]}
        for index, item in enumerate(sticker_assets)
    ]


def _print_asset_summary(assets: Sequence[Path], card_count: int) -> None:
    if len(assets) == 1 and assets[0].name == "default_black.png":
        print("No assets found. Using generated/default_black.png for every card.")
    elif len(assets) < card_count:
        print(f"Only {len(assets)} assets for {card_count} cards. Assets will loop.")
    elif len(assets) > card_count:
        print(f"{len(assets) - card_count} extra assets ignored.")


def _list_assets(assets_dir: Path) -> List[Path]:
    if not assets_dir.exists():
        return []
    return sorted(
        (path for path in assets_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_ASSET_EXTENSIONS),
        key=_natural_key,
    )


def _natural_key(path: Path) -> List[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def _clean_lines(lines: Iterable[str]) -> List[str]:
    cleaned = []
    for line in lines:
        compact = _normalize_inline_text(" ".join(line.split()))
        if compact:
            cleaned.append(compact)
    return cleaned


def _normalize_inline_text(text: str) -> str:
    return re.sub(r"\s+([。！？；，、,.!?;:：])", r"\1", text)


def _split_long_paragraph(paragraph: str, max_chars: int) -> List[str]:
    sentences = re.findall(r".+?[。！？；，,.!?;:]|.+$", paragraph)
    chunks: List[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars))
        elif not current:
            current = sentence
        elif len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            chunks.append(current)
            current = sentence
    if current:
        chunks.append(current)
    return chunks


def _parse_hex_color(color: str) -> Tuple[int, int, int]:
    normalized = color.strip().lstrip("#")
    if len(normalized) != 6:
        raise ValueError("default_background_color must be #RRGGBB")
    return int(normalized[0:2], 16), int(normalized[2:4], 16), int(normalized[4:6], 16)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _contains_transition_name(text: str) -> bool:
    normalized = _to_simplified_keywords(text)
    return any(name and (_to_simplified_keywords(name) in normalized or name in text) for name in available_transition_names())


def _mentions_transition(text: str) -> bool:
    return "转场" in text or "切换" in text


def _mentions_background_audio(text: str) -> bool:
    return ("背景音乐" in text or "背景音" in text) and any(word in text for word in ["导入", "倒入", "加入", "添加"])


def _mentions_marker_split(text: str) -> bool:
    return (("标记点" in text or "节拍点" in text) and any(word in text for word in ["分割", "切", "采检"])) or "切一刀" in text


def _mentions_sticker_add(text: str) -> bool:
    return "贴纸" in text and any(word in text for word in ["添加", "加入", "导入", "倒入", "拖入", "偷入", "放入"])


def _mentions_selection(text: str) -> bool:
    return any(word in text for word in ["框选", "全选", "空选"])


def _mentions_effect_or_filter(text: str) -> bool:
    return "特效" in text or "滤镜" in text or "运动模糊" in text or "动感模糊" in text


def _parse_scale_value(text: str) -> float | None:
    if "缩放" not in text and "放大" not in text:
        return None
    match = re.search(r"(?:缩放|放大)(?:改成|调整为|调到|到)?\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    value = float(match.group(1))
    return value / 100.0 if value > 2 else value


def _parse_marker_index(text: str) -> int | None:
    match = re.search(r"第\s*(\d+)\s*个?(?:标记点|节拍点|点)", text)
    return int(match.group(1)) if match else None


def _parse_named_resource(text: str, keyword: str) -> str:
    match = re.search(r"(?:添加|导入|倒入|加入|放入|拖入|偷入)?\s*([\w\u4e00-\u9fff]+?)" + re.escape(keyword), text)
    if match:
        name = match.group(1).strip()
        prefixes = ["首先", "接着", "然后", "再", "点击", "点开", "偷入", "拖入", "导入", "倒入", "加入", "放入", "添加"]
        changed = True
        while changed:
            changed = False
            for prefix in prefixes:
                if name.startswith(prefix):
                    name = name[len(prefix) :]
                    changed = True
        return name or keyword
    return keyword


def _parse_selector(text: str) -> str:
    if "贴纸" in text:
        return "all_stickers"
    if "光圈" in text:
        return "named:光圈"
    if "片段" in text or "素材" in text:
        return "all_video_segments"
    return "selected"


def _parse_effect_selector(text: str) -> str:
    if "光圈" in text:
        return "named:光圈"
    if "贴纸" in text:
        return "all_stickers"
    return "all_video_segments"


def _parse_effect_name(text: str) -> str:
    if "运动模糊" in text:
        return "运动模糊"
    name = text
    for phrase in [
        "最后",
        "再",
        "给光圈",
        "给顶部",
        "顶部",
        "加上",
        "添加",
        "勾选",
        "选择",
        "看看效果",
        "的",
        "特效",
        "滤镜",
    ]:
        name = name.replace(phrase, "")
    return name.strip() or "effect"


def _parse_animation(text: str) -> dict | None:
    if "出场" in text and "放大" in text:
        return {"type": "add_animation", "phase": "outro", "name": "放大"}
    if "循环" in text and "旋转" in text:
        return {"type": "add_animation", "phase": "loop", "name": "旋转"}
    return None


def _to_simplified_keywords(text: str) -> str:
    replacements = {
        "導": "导",
        "樂": "乐",
        "點": "点",
        "選": "选",
        "擇": "择",
        "給": "给",
        "採": "采",
        "後": "后",
        "標記": "标记",
        "個": "个",
        "進": "进",
        "視": "视",
        "頻": "频",
        "軌": "轨",
        "時": "时",
        "長": "长",
        "縮": "缩",
        "動畫": "动画",
        "動": "动",
        "循環": "循环",
        "隨機": "随机",
        "拖動": "拖动",
        "擺": "摆",
        "鍵": "键",
        "復合": "复合",
        "複合": "复合",
        "複製": "复制",
        "復製": "复制",
        "藍": "蓝",
        "體": "体",
        "關鍵針": "关键帧",
        "關鍵幀": "关键帧",
        "濾": "滤",
        "鏡": "镜",
        "運": "运",
        "煙": "烟",
        "霧": "雾",
        "頂": "顶",
        "貼": "贴",
        "紙": "纸",
        "圓": "圆",
        "屬": "属",
        "框選": "框选",
        "全選": "全选",
        "空選": "空选",
        "視頻": "视频",
        "音樂": "音乐",
        "盟板": "蒙版",
        "蒙板": "蒙版",
        "新键": "新建",
    }
    normalized = text
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return normalized


if __name__ == "__main__":
    main()
