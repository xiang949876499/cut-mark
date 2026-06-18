from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Sequence

from draft_generator import GeneratorConfig, OperationPlan, ensure_assets, read_input_text, split_into_cards
from kdenlive_effects import EffectCatalog, KdenliveOperationMapper, query_effect_catalog
from kdenlive_project import KdenliveProjectBuilder
from kdenlive_renderer import KdenliveRenderer, RenderResult, RenderSettings
from kdenlive_runtime import KDENLIVE_26_04_2, KdenliveRuntime, RuntimePaths
from kdenlive_timeline import (
    TimelineClip,
    TimelineEffect,
    TimelineProject,
    TimelineTrack,
    frames_from_seconds,
    split_ranges_from_markers,
)
from operation_executor import ExecutionResult


@dataclass(frozen=True)
class KdenliveBackendResult:
    project_path: Path
    video_path: Path | None
    report_path: Path
    mapping_report: list[dict[str, Any]]
    render_result: RenderResult | None


def build_timeline(
    config: GeneratorConfig,
    *,
    cards: Sequence[str],
    assets: Sequence[Path],
    operation_plan: OperationPlan | None,
    execution_result: ExecutionResult | None,
    include_text: bool,
) -> TimelineProject:
    fps = config.render_fps
    project = TimelineProject(width=config.resolution[0], height=config.resolution[1], fps=fps)
    marker_times = list(execution_result.marker_times) if execution_result else []
    project.markers = [frames_from_seconds(marker, fps) for marker in marker_times]

    fallback_frames = max(1, frames_from_seconds(config.segment_duration_sec, fps))
    ranges = split_ranges_from_markers(marker_times, count=len(cards), fps=fps, fallback_frames=fallback_frames)
    main_track = TimelineTrack(id="video-main", kind="video", role="video")
    asset_list = [Path(asset) for asset in assets]
    if not asset_list:
        raise ValueError("At least one asset is required to build a Kdenlive timeline")

    for index, (start_frame, end_frame) in enumerate(ranges):
        main_track.clips.append(
            TimelineClip(
                id=f"video-{index + 1}",
                source=asset_list[index % len(asset_list)],
                start_frame=start_frame,
                duration_frames=end_frame - start_frame + 1,
                name=f"card-{index + 1}",
                role="video",
            )
        )
    project.tracks.append(main_track)

    audio_path = Path(execution_result.resolved_audio_path) if execution_result and execution_result.resolved_audio_path else None
    total_frames = _project_duration_frames(project, fallback_frames * max(1, len(cards)))
    if audio_path:
        project.tracks.append(
            TimelineTrack(
                id="audio-main",
                kind="audio",
                role="audio",
                clips=[
                    TimelineClip(
                        id="audio-1",
                        source=audio_path,
                        start_frame=0,
                        duration_frames=total_frames,
                        name=audio_path.stem,
                        role="audio",
                    )
                ],
            )
        )

    sticker_assets = list(execution_result.sticker_assets) if execution_result else []
    random_offsets = list(execution_result.random_offsets) if execution_result else []
    for sticker_index, sticker in enumerate(sticker_assets):
        source = sticker.get("path")
        if not source:
            continue
        track = TimelineTrack(id=f"sticker-{sticker_index + 1}", kind="video", role="sticker")
        offsets = random_offsets[sticker_index % len(random_offsets)] if random_offsets else {}
        effects = [_transform_effect(offsets)]
        track.clips.append(
            TimelineClip(
                id=f"sticker-{sticker_index + 1}-clip",
                source=Path(source),
                start_frame=0,
                duration_frames=total_frames,
                name=str(sticker.get("name") or Path(source).stem),
                role="sticker",
                effects=effects,
            )
        )
        project.tracks.append(track)

    if include_text:
        text_track = TimelineTrack(id="text-main", kind="video", role="text")
        for index, (start_frame, end_frame) in enumerate(ranges):
            text_track.clips.append(
                TimelineClip(
                    id=f"text-{index + 1}",
                    source=None,
                    start_frame=start_frame,
                    duration_frames=end_frame - start_frame + 1,
                    name=cards[index],
                    role="text",
                    effects=[_text_effect(cards[index])],
                )
            )
        project.tracks.append(text_track)

    return project


def generate_kdenlive(
    *,
    config: GeneratorConfig,
    input_path: Path,
    assets_dir: Path,
    operation_plan: OperationPlan,
    execution_result: ExecutionResult,
    output_dir: Path = Path("output"),
    generated_dir: Path = Path("generated"),
    runtime=None,
    renderer=None,
    render: bool = True,
    include_text: bool = False,
    catalog: EffectCatalog | None = None,
) -> KdenliveBackendResult:
    input_text = read_input_text(input_path)
    cards = split_into_cards(input_text, config.max_chars_per_card)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)

    assets = ensure_assets(assets_dir, generated_dir, config.resolution, config.default_background_color)
    runtime = runtime or KdenliveRuntime(config.kdenlive_runtime_dir, auto_download=config.auto_download_kdenlive)
    runtime_paths = runtime.resolve()
    catalog = catalog or _safe_query_catalog(runtime_paths)
    timeline = build_timeline(
        config,
        cards=[card.text for card in cards],
        assets=assets,
        operation_plan=operation_plan,
        execution_result=execution_result,
        include_text=include_text,
    )
    mapping_report = KdenliveOperationMapper(catalog).apply(timeline, operation_plan.operations)
    project_path, video_path = make_kdenlive_output_paths(config.draft_name, input_text, output_dir)
    KdenliveProjectBuilder().write(timeline, project_path)

    render_result: RenderResult | None = None
    if render:
        renderer = renderer or KdenliveRenderer(runtime_paths, log_dir=generated_dir)
        settings = RenderSettings(
            width=config.resolution[0],
            height=config.resolution[1],
            fps=config.render_fps,
            video_codec=config.render_video_codec,
            audio_codec=config.render_audio_codec,
            crf=config.render_crf,
            preset=config.render_preset,
        )
        render_result = renderer.render(
            project_path,
            video_path,
            settings,
            expect_audio=bool(execution_result.resolved_audio_path),
        )
    report_path = write_kdenlive_report(
        generated_dir=generated_dir,
        content=input_text,
        runtime_paths=runtime_paths,
        project_path=project_path,
        video_path=video_path if render else None,
        timeline=timeline,
        mapping_report=mapping_report,
        render_result=render_result,
        assets=assets,
        operations=operation_plan.operations,
        config=config,
    )
    write_kdenlive_final_decision(
        generated_dir=generated_dir,
        project_path=project_path,
        video_path=video_path if render else None,
        unsupported_count=sum(1 for item in mapping_report if item.get("status") == "unsupported"),
    )
    return KdenliveBackendResult(project_path, video_path if render else None, report_path, mapping_report, render_result)


def make_kdenlive_output_paths(base_name: str, content: str, output_dir: Path) -> tuple[Path, Path]:
    suffix = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    stem = f"{base_name}_{suffix}"
    return output_dir / f"{stem}.kdenlive", output_dir / f"{stem}.mp4"


def write_kdenlive_report(
    *,
    generated_dir: Path,
    content: str,
    runtime_paths: RuntimePaths,
    project_path: Path,
    video_path: Path | None,
    timeline: TimelineProject,
    mapping_report: list[dict[str, Any]],
    render_result: RenderResult | None,
    assets: Sequence[Path],
    operations: Sequence[dict[str, Any]],
    config: GeneratorConfig,
) -> Path:
    report = {
        "route": "kdenlive",
        "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest()[:8],
        "runtime": {
            "version": config.kdenlive_version or KDENLIVE_26_04_2.version,
            "root": str(runtime_paths.root),
            "kdenlive": str(runtime_paths.kdenlive_exe),
            "melt": str(runtime_paths.melt_exe),
        },
        "project_path": str(project_path),
        "video_path": str(video_path) if video_path else None,
        "markers": timeline.markers,
        "assets": [str(path) for path in assets],
        "operations": list(operations),
        "mapping_report": mapping_report,
        "unsupported": [item for item in mapping_report if item.get("status") == "unsupported"],
        "render": _render_report(render_result),
    }
    generated_dir.mkdir(parents=True, exist_ok=True)
    path = generated_dir / "kdenlive_report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_kdenlive_final_decision(
    *,
    generated_dir: Path,
    project_path: Path,
    video_path: Path | None,
    unsupported_count: int,
) -> Path:
    generated_dir.mkdir(parents=True, exist_ok=True)
    path = generated_dir / "final_decision.txt"
    path.write_text(
        "\n".join(
            [
                "route=kdenlive",
                f"project={project_path}",
                f"video={video_path if video_path else 'not-rendered'}",
                f"unsupported={unsupported_count}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def _safe_query_catalog(runtime_paths: RuntimePaths) -> EffectCatalog:
    try:
        return query_effect_catalog(runtime_paths.melt_exe)
    except Exception:
        return EffectCatalog(filters={"qtblend", "avfilter.avgblur", "shape"}, transitions={"luma", "mix", "composite"})


def _render_report(render_result: RenderResult | None) -> dict[str, Any]:
    if render_result is None:
        return {"status": "not-rendered"}
    return {
        "status": "rendered",
        "valid": render_result.valid,
        "output_path": str(render_result.output_path),
        "command": render_result.command,
        "melt_returncode": render_result.melt_returncode,
        "probe": render_result.probe,
        "log_path": str(render_result.log_path),
    }


def _project_duration_frames(project: TimelineProject, fallback: int) -> int:
    return max((clip.end_frame + 1 for track in project.tracks for clip in track.clips), default=fallback)


def _transform_effect(offsets: dict) -> TimelineEffect:
    x_percent = 50 + float(offsets.get("transform_x", 0.0)) * 35
    y_percent = 50 + float(offsets.get("transform_y", 0.0)) * 35
    scale_percent = 35
    return TimelineEffect(
        service="qtblend",
        source_name="transform",
        properties={
            "rect": f"{x_percent:.1f}% {y_percent:.1f}% {scale_percent}% {scale_percent}% 1",
            "compositing": "0",
        },
    )


def _text_effect(text: str) -> TimelineEffect:
    return TimelineEffect(
        service="dynamictext",
        source_name="text",
        properties={
            "argument": text,
            "geometry": "8% 12% 84% 20% 1",
            "family": "Microsoft YaHei",
            "size": "64",
            "fgcolour": "0xffffffff",
            "bgcolour": "0x99000000",
        },
    )
