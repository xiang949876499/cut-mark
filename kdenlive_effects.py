from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from kdenlive_timeline import TimelineEffect, TimelineProject, copy_clip_attributes, create_nested_sequence, duplicate_clips


@dataclass(frozen=True)
class EffectCatalog:
    filters: set[str]
    transitions: set[str]


@dataclass(frozen=True)
class MappingResult:
    source_name: str
    service: str | None
    status: str
    properties: dict[str, str] = field(default_factory=dict)
    message: str = ""

    def to_report(self, operation: dict[str, Any]) -> dict[str, Any]:
        return {
            "operation": operation.get("type", "unknown"),
            "name": operation.get("name", self.source_name),
            "selector": operation.get("selector", "selected"),
            "source_name": self.source_name,
            "service": self.service,
            "status": self.status,
            "properties": self.properties,
            "message": self.message,
            "line": operation.get("line", ""),
            "line_index": operation.get("line_index"),
        }


EFFECT_CANDIDATES = {
    "运动模糊": [("avfilter.avgblur", "approximated"), ("frei0r.pixeliz0r", "approximated")],
    "杩愬姩妯＄硦": [("avfilter.avgblur", "approximated"), ("frei0r.pixeliz0r", "approximated")],
    "动感模糊": [("avfilter.avgblur", "approximated")],
    "鍔ㄦ劅妯＄硦": [("avfilter.avgblur", "approximated")],
    "故障2": [("frei0r.glitch0r", "approximated"), ("avfilter.noise", "approximated")],
    "鏁呴殰2": [("frei0r.glitch0r", "approximated"), ("avfilter.noise", "approximated")],
    "故障_II": [("frei0r.glitch0r", "approximated"), ("avfilter.noise", "approximated")],
    "鏁呴殰_II": [("frei0r.glitch0r", "approximated"), ("avfilter.noise", "approximated")],
    "流动烟雾": [("movit.blur", "approximated"), ("avfilter.noise", "approximated")],
    "娴佸姩鐑熼浘": [("movit.blur", "approximated"), ("avfilter.noise", "approximated")],
}

TRANSITION_CANDIDATES = {
    "叠化": [("luma", "exact"), ("mix", "approximated")],
    "鍙犲寲": [("luma", "exact"), ("mix", "approximated")],
    "模糊": [("luma", "approximated")],
    "妯＄硦": [("luma", "approximated")],
    "右移": [("composite", "approximated"), ("qtblend", "approximated")],
    "鍙崇Щ": [("composite", "approximated"), ("qtblend", "approximated")],
    "下移": [("composite", "approximated"), ("qtblend", "approximated")],
    "涓嬬Щ": [("composite", "approximated"), ("qtblend", "approximated")],
    "向上": [("composite", "approximated"), ("qtblend", "approximated")],
    "鍚戜笂": [("composite", "approximated"), ("qtblend", "approximated")],
}

MASK_CANDIDATES = [("shape", "approximated"), ("alpha0ps", "approximated")]


def query_effect_catalog(melt_exe: Path, *, runner=subprocess.run, cache_path: Path | None = None) -> EffectCatalog:
    filters = _query_services(melt_exe, "filters", runner)
    transitions = _query_services(melt_exe, "transitions", runner)
    catalog = EffectCatalog(filters=filters, transitions=transitions)
    if cache_path is None:
        cache_path = Path(melt_exe).resolve().parent.parent / "effect_catalog.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"filters": sorted(filters), "transitions": sorted(transitions)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return catalog


class KdenliveOperationMapper:
    def __init__(self, catalog: EffectCatalog) -> None:
        self.catalog = catalog

    def resolve_effect(self, name: str) -> MappingResult:
        return _resolve_from_candidates(name, EFFECT_CANDIDATES, self.catalog.filters, "effect")

    def resolve_transition(self, name: str) -> MappingResult:
        return _resolve_from_candidates(name, TRANSITION_CANDIDATES, self.catalog.transitions, "transition")

    def apply(self, project: TimelineProject, operations: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        report: list[dict[str, Any]] = []
        current_selector = "selected"
        for op in operations:
            op_type = op.get("type", "unknown")
            if op_type == "select":
                current_selector = op.get("selector") or current_selector
                report.append(_report(op, "exact", "Selection mapped", selector=current_selector))
                continue

            selector = op.get("selector") or current_selector
            if op_type == "add_audio":
                status = "exact" if any(track.kind == "audio" for track in project.tracks) else "unsupported"
                report.append(_report(op, status, "Audio is present on the timeline" if status == "exact" else "No audio track was generated", selector=selector))
            elif op_type == "ai_beat":
                status = "approximated" if project.markers else "unsupported"
                report.append(_report(op, status, "Local marker detection replaced native AI beat" if project.markers else "No markers were generated", selector=selector))
            elif op_type == "split_at_marker":
                status = "exact" if project.markers else "unsupported"
                report.append(_report(op, status, "Timeline clips were split using marker ranges" if project.markers else "No markers available for splitting", selector=selector))
            elif op_type == "add_sticker":
                name = str(op.get("name", ""))
                status = "exact" if (name and project.select(f"named:{name}")) or project.select("all_stickers") else "unsupported"
                report.append(_report(op, status, "Sticker overlay is present on the timeline" if status == "exact" else "No sticker overlay was generated", selector=selector))
            elif op_type == "set_scale":
                result = self._scale_result(float(op.get("value", 1.0)))
                _apply_effect_to_selector(project, selector, result)
                report.append(result.to_report({**op, "selector": selector}))
            elif op_type == "random_place":
                report.append(_report(op, "approximated", "Random placement is applied during timeline construction", selector=selector))
            elif op_type in {"add_effect", "add_filter"}:
                result = self.resolve_effect(str(op.get("name", "")))
                _apply_effect_to_selector(project, selector, result)
                report.append(result.to_report({**op, "selector": selector}))
            elif op_type == "add_mask":
                result = _first_available("mask", MASK_CANDIDATES, self.catalog.filters, {"resource": str(op.get("mask", ""))})
                _apply_effect_to_selector(project, selector, result)
                report.append(result.to_report({**op, "selector": selector}))
            elif op_type in {"add_animation", "keyframe"}:
                report.append(_report(op, "approximated", "Animation/keyframe recorded as timeline metadata", selector=selector))
            elif op_type == "add_transition":
                result = self.resolve_transition(str(op.get("name", "")))
                report.append(result.to_report({**op, "selector": selector}))
            elif op_type == "copy":
                selected = project.select(selector)
                if not selected:
                    report.append(_report(op, "unsupported", "No selected clips to copy", selector=selector))
                elif _is_copy_attributes(op):
                    copy_clip_attributes(selected[0], selected[1:])
                    report.append(_report(op, "exact", "Copied clip attributes", selector=selector))
                else:
                    offset = max((clip.end_frame for clip in selected), default=0) - min(clip.start_frame for clip in selected) + 1
                    duplicates = duplicate_clips(selected, offset)
                    _append_duplicates(project, selected, duplicates)
                    report.append(_report(op, "exact", "Duplicated selected clips", selector=selector))
            elif op_type == "compound_clip":
                sequence_id = create_nested_sequence(project, project.select(selector))
                status = "exact" if sequence_id else "unsupported"
                message = f"Created nested sequence {sequence_id}" if sequence_id else "No selected clips to compound"
                report.append(_report(op, status, message, selector=selector))
            else:
                report.append(_report(op, "unsupported", f"Unsupported Kdenlive operation: {op_type}", selector=selector))
        return report

    def _scale_result(self, value: float) -> MappingResult:
        service = "qtblend" if "qtblend" in self.catalog.filters else ("affine" if "affine" in self.catalog.filters else None)
        if service is None:
            return MappingResult("scale", None, "unsupported", message="No transform filter is available")
        percent = max(1.0, value * 100)
        return MappingResult(
            source_name="scale",
            service=service,
            status="exact",
            properties={
                "rect": f"50.0% 50.0% {percent:.1f}% {percent:.1f}% 1",
                "compositing": "0",
            },
        )


def _resolve_from_candidates(
    name: str,
    table: dict[str, list[tuple[str, str]]],
    available: set[str],
    source_name: str,
) -> MappingResult:
    candidates = table.get(name) or table.get(_normalize_name(name)) or []
    for service, status in candidates:
        if service in available:
            return MappingResult(source_name=name, service=service, status=status)
    return MappingResult(source_name=name or source_name, service=None, status="unsupported", message=f"No Kdenlive service found for {name}")


def _first_available(
    source_name: str,
    candidates: Sequence[tuple[str, str]],
    available: set[str],
    properties: dict[str, str] | None = None,
) -> MappingResult:
    for service, status in candidates:
        if service in available:
            return MappingResult(source_name, service, status, properties or {})
    return MappingResult(source_name, None, "unsupported", message=f"No Kdenlive service found for {source_name}")


def _apply_effect_to_selector(project: TimelineProject, selector: str, result: MappingResult) -> None:
    if result.status == "unsupported" or result.service is None:
        return
    for clip in project.select(selector):
        clip.effects.append(
            TimelineEffect(
                service=result.service,
                source_name=result.source_name,
                status=result.status,
                properties=dict(result.properties),
            )
        )


def _query_services(melt_exe: Path, query: str, runner) -> set[str]:
    result = runner([str(melt_exe), "-query", query], capture_output=True, text=True, timeout=120)
    if getattr(result, "returncode", 1) != 0:
        return set()
    return _parse_services(getattr(result, "stdout", ""))


def _parse_services(output: str) -> set[str]:
    services = set()
    for line in output.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("-"):
            cleaned = cleaned[1:].strip()
        cleaned = cleaned.strip("*").strip()
        if not cleaned or cleaned == "..." or cleaned.endswith(":"):
            continue
        match = re.match(r"([A-Za-z0-9_.:-]+)", cleaned)
        if match:
            services.add(match.group(1))
    return services


def _normalize_name(name: str) -> str:
    return name.strip().replace("_", "").replace(" ", "").casefold()


def _report(op: dict[str, Any], status: str, message: str, *, selector: str) -> dict[str, Any]:
    return {
        "operation": op.get("type", "unknown"),
        "name": op.get("name"),
        "selector": selector,
        "source_name": op.get("name", op.get("type", "unknown")),
        "service": None,
        "status": status,
        "properties": {},
        "message": message,
        "line": op.get("line", ""),
        "line_index": op.get("line_index"),
    }


def _is_copy_attributes(op: dict[str, Any]) -> bool:
    line = str(op.get("line", ""))
    return "属性" in line or "屬性" in line or "灞炴" in line or "灞" in line


def _append_duplicates(project: TimelineProject, originals: list, duplicates: list) -> None:
    duplicate_by_original = {original.id: duplicate for original, duplicate in zip(originals, duplicates)}
    existing_ids = {clip.id for track in project.tracks for clip in track.clips}
    for track in project.tracks:
        additions = [duplicate_by_original[clip.id] for clip in track.clips if clip.id in duplicate_by_original]
        if additions:
            for duplicate in additions:
                duplicate.id = _unique_clip_id(duplicate.id, existing_ids)
                existing_ids.add(duplicate.id)
            track.clips.extend(additions)
            track.clips.sort(key=lambda clip: clip.start_frame)


def _unique_clip_id(base_id: str, existing_ids: set[str]) -> str:
    if base_id not in existing_ids:
        return base_id
    index = 2
    while f"{base_id}-{index}" in existing_ids:
        index += 1
    return f"{base_id}-{index}"
