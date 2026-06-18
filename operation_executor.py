import json
import math
import random
import shutil
import struct
import subprocess
import tempfile
import wave
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence


EXECUTED = "executed"
SKIPPED = "skipped"
UNSUPPORTED = "unsupported"
NEEDS_ASSET = "needs_asset"
UI_ATTEMPTED = "ui_attempted"
UI_FAILED = "ui_failed"
UI_REQUIRED = "ui_required"
UI_EXECUTED = "ui_executed"
UI_SKIPPED = "ui_skipped"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
AUDIO_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

WARNING_STATUSES = {UNSUPPORTED, NEEDS_ASSET, UI_FAILED, SKIPPED}

EFFECT_ALIASES = {
    "流動煙霧": "流动烟雾",
    "流动烟雾": "流动烟雾",
    "故障2": "故障_II",
    "故障II": "故障_II",
    "故障Ⅱ": "故障_II",
    "運動模糊": "动感模糊",
    "运动模糊": "动感模糊",
}


@dataclass
class ExecutionResult:
    report: List[Dict[str, Any]]
    warnings: List[str]
    marker_times: List[float]
    sticker_assets: List[Dict[str, Any]]
    random_offsets: List[Dict[str, float]]
    resolved_audio_path: str | None = None
    effects: List[Dict[str, Any]] = field(default_factory=list)
    filters: List[Dict[str, Any]] = field(default_factory=list)
    current_selector: str = "selected"
    ui_tasks: List[Dict[str, Any]] = field(default_factory=list)


def load_manifest(path: Path) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def write_execution_report(result: ExecutionResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "operation_report.json"
    path.write_text(
        json.dumps(
            {
                "report": result.report,
                "warnings": result.warnings,
                "marker_times": result.marker_times,
                "sticker_assets": result.sticker_assets,
                "random_offsets": result.random_offsets,
                "resolved_audio_path": result.resolved_audio_path,
                "effects": result.effects,
                "filters": result.filters,
                "current_selector": result.current_selector,
                "ui_tasks": result.ui_tasks,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def write_ui_task_plan(ui_tasks: Sequence[Dict[str, Any]], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "ui_task_plan.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "tasks": list(ui_tasks),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def build_full_ui_task_plan(operations: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    current_selector = "selected"
    for op in operations:
        op_type = op.get("type", "unknown")
        if op_type == "select":
            current_selector = op.get("selector") or current_selector
        selector = op.get("selector") or current_selector
        tasks.append(
            {
                "type": op_type,
                "status": UI_REQUIRED,
                "line": op.get("line", ""),
                "line_index": op.get("line_index"),
                "selector": selector,
                "ui_kind": _ui_task_kind(op_type),
                "precondition": _ui_task_precondition(op_type),
                "operation": dict(op),
            }
        )
    return tasks


def _ui_task_kind(op_type: str) -> str:
    if op_type in {"ai_beat", "compound_clip", "add_effect", "add_filter"}:
        return "experimental"
    return "stable"


def _ui_task_precondition(op_type: str) -> str:
    messages = {
        "add_audio": "Import or place background audio in Jianying timeline",
        "ai_beat": "Use Jianying native AI beat detection when available",
        "split_at_marker": "Split selected timeline clip at marker or playhead",
        "add_sticker": "Search or import sticker and add it to timeline",
        "set_scale": "Apply basic transform scale in Jianying inspector",
        "select": "Map selection instruction to timeline selection",
        "random_place": "Drag selected overlays to deterministic visible positions",
        "compound_clip": "Create native Jianying compound clip",
        "add_effect": "Search Jianying effects panel and apply exact match",
        "add_filter": "Search Jianying filters panel and apply exact match",
        "copy": "Copy segment or attributes in Jianying UI",
        "keyframe": "Add or adjust keyframe in Jianying inspector",
        "add_mask": "Apply mask through Jianying inspector",
        "add_transition": "Apply transition through Jianying transition panel",
    }
    return messages.get(op_type, "Execute operation through Jianying UI")


def random_positions(count: int, *, seed: int, span: float = 0.7) -> List[Dict[str, float]]:
    rng = random.Random(seed)
    return [
        {
            "transform_x": round(rng.uniform(-span, span), 3),
            "transform_y": round(rng.uniform(-span, span), 3),
        }
        for _ in range(count)
    ]


def generate_audio_markers(audio_path: Path, *, segment_duration_sec: float) -> List[float]:
    if not audio_path or not audio_path.exists():
        return []
    decoded_temp: Path | None = None
    try:
        wav_path = audio_path
        if audio_path.suffix.lower() != ".wav":
            decoded_temp = _decode_to_marker_wav(audio_path)
            if decoded_temp is None:
                return []
            wav_path = decoded_temp
        samples, rate = _read_mono_16bit_samples(wav_path)
    finally:
        if decoded_temp and decoded_temp.exists():
            decoded_temp.unlink()

    if rate <= 0:
        return []
    duration = len(samples) / rate
    if duration <= 0:
        return []
    detected = _detect_energy_peaks(samples, rate)
    if len(detected) >= 2:
        return [0.0] + detected
    return _fixed_interval_markers(duration, segment_duration_sec)


def _decode_to_marker_wav(audio_path: Path, *, runner=subprocess.run) -> Path | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    handle = tempfile.NamedTemporaryFile(prefix="cut_mark_audio_", suffix=".wav", delete=False)
    decoded = Path(handle.name)
    handle.close()
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(audio_path),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(decoded),
    ]
    result = runner(command, capture_output=True, text=True, timeout=120)
    if getattr(result, "returncode", 1) != 0:
        decoded.unlink(missing_ok=True)
        return None
    return decoded


def _read_mono_16bit_samples(wav_path: Path) -> tuple[list[int], int]:
    with wave.open(str(wav_path), "rb") as handle:
        channels = handle.getnchannels()
        sample_width = handle.getsampwidth()
        rate = handle.getframerate()
        raw = handle.readframes(handle.getnframes())
    if sample_width != 2 or rate <= 0:
        return [], rate
    values = struct.unpack(f"<{len(raw) // 2}h", raw)
    if channels <= 1:
        return list(values), rate
    mono = []
    for index in range(0, len(values), channels):
        frame = values[index : index + channels]
        mono.append(int(sum(frame) / max(1, len(frame))))
    return mono, rate


def _detect_energy_peaks(samples: Sequence[int], sample_rate: int) -> List[float]:
    if not samples:
        return []
    window_size = max(1, int(sample_rate * 0.05))
    energies = []
    for start in range(0, len(samples), window_size):
        window = samples[start : start + window_size]
        if not window:
            continue
        rms = math.sqrt(sum(sample * sample for sample in window) / len(window))
        energies.append((start, rms))
    if not energies:
        return []
    sorted_energy = sorted(value for _, value in energies)
    median = sorted_energy[len(sorted_energy) // 2]
    threshold = max(500.0, median * 2.5)
    peaks: List[float] = []
    last_peak = -999.0
    for index, (start, value) in enumerate(energies):
        previous_value = energies[index - 1][1] if index > 0 else 0.0
        next_value = energies[index + 1][1] if index + 1 < len(energies) else 0.0
        peak_time = round((start + window_size / 2) / sample_rate, 3)
        if value >= threshold and value >= previous_value and value >= next_value and peak_time - last_peak >= 0.25:
            peaks.append(peak_time)
            last_peak = peak_time
    return peaks


def _fixed_interval_markers(duration: float, segment_duration_sec: float) -> List[float]:
    marker_count = max(1, int(math.ceil(duration / segment_duration_sec)))
    return [round(i * segment_duration_sec, 3) for i in range(marker_count + 1)]


def resolve_effect_or_filter(name: str, operation_type: str) -> Dict[str, Any] | None:
    try:
        import pyJianYingDraft as draft
    except ImportError:
        return None

    normalized = _normalize_effect_name(name)
    if operation_type == "add_filter":
        return _find_enum(draft.FilterType, normalized, "filter")

    return (
        _find_enum(draft.VideoSceneEffectType, normalized, "scene_effect")
        or _find_enum(draft.VideoCharacterEffectType, normalized, "character_effect")
    )


class OperationExecutor:
    def __init__(
        self,
        assets_dir: Path,
        *,
        generated_dir: Path | None = None,
        sticker_manifest: Path | None = None,
        effect_manifest: Path | None = None,
        filter_manifest: Path | None = None,
        seed: int = 42,
        markers_mode: str = "audio",
        segment_duration_sec: float = 4.5,
        audio_asset: Path | None = None,
        ui_mode: str = "off",
        ui_driver=None,
        backend: str = "kdenlive",
        auto_generate_missing_assets: bool = True,
        default_beat_bpm: int = 120,
        placeholder_stickers_enabled: bool = True,
    ) -> None:
        self.assets_dir = assets_dir
        self.generated_dir = generated_dir or Path("generated")
        self.sticker_manifest = sticker_manifest or Path("manifests/stickers.json")
        self.effect_manifest = effect_manifest or Path("manifests/effects.json")
        self.filter_manifest = filter_manifest or Path("manifests/filters.json")
        self.seed = seed
        self.markers_mode = markers_mode
        self.segment_duration_sec = segment_duration_sec
        self.audio_asset = audio_asset
        self.ui_mode = ui_mode
        self.ui_driver = ui_driver
        self.backend = backend
        self.auto_generate_missing_assets = auto_generate_missing_assets
        self.default_beat_bpm = default_beat_bpm
        self.placeholder_stickers_enabled = placeholder_stickers_enabled

    def prepare(self, plan, *, card_count: int = 0) -> ExecutionResult:
        report: List[Dict[str, Any]] = []
        marker_times: List[float] = []
        sticker_assets: List[Dict[str, Any]] = []
        random_offsets: List[Dict[str, float]] = []
        effects: List[Dict[str, Any]] = []
        filters: List[Dict[str, Any]] = []
        ui_tasks: List[Dict[str, Any]] = []
        current_selector = "selected"
        resolved_audio: Path | None = None

        sticker_manifest = load_manifest(self.sticker_manifest)
        effect_manifest = load_manifest(self.effect_manifest)
        filter_manifest = load_manifest(self.filter_manifest)
        required_markers = self._required_marker_count(plan, card_count)

        for op in getattr(plan, "operations", []):
            op_type = op.get("type", "unknown")
            if op_type == "add_audio":
                resolved_audio = self._resolve_or_generate_audio(required_markers)
                if resolved_audio:
                    report.append(self._record(op, EXECUTED, "Resolved audio asset", {"path": str(resolved_audio)}))
                else:
                    report.append(self._record(op, NEEDS_ASSET, "Background music needs an audio file in assets/ or config.audio_asset"))
            elif op_type == "ai_beat":
                ui_tasks.append(self._ui_task(op, current_selector, "Use Jianying native AI beat detection when available"))
                if self.markers_mode != "none":
                    if resolved_audio is None:
                        resolved_audio = self._resolve_or_generate_audio(required_markers)
                    marker_times = self._marker_times_from_audio_or_count(resolved_audio, required_markers)
                if marker_times:
                    report.append(self._record(op, EXECUTED, "Generated local marker times", {"marker_times": marker_times}))
                elif self.ui_mode == "experimental":
                    report.append(self._ui_action(op, "attempt_ai_beat"))
                else:
                    report.append(self._record(op, NEEDS_ASSET, "AI卡点 needs an audio marker source or generated fallback"))
            elif op_type == "split_at_marker":
                if not marker_times and self.markers_mode != "none":
                    if resolved_audio is None:
                        resolved_audio = self._resolve_or_generate_audio(required_markers)
                    marker_times = self._marker_times_from_audio_or_count(resolved_audio, required_markers)
                if marker_times:
                    report.append(self._record(op, EXECUTED, "Will split by marker times", {"marker_index": op.get("marker_index")}))
                else:
                    report.append(self._record(op, SKIPPED, "No marker times available"))
            elif op_type == "add_sticker":
                resolved = self._resolve_sticker(op, sticker_manifest)
                if resolved:
                    sticker_assets.append(resolved)
                    report.append(self._record(op, EXECUTED, "Resolved sticker asset", resolved))
                else:
                    report.append(self._record(op, NEEDS_ASSET, "Sticker requires assets/stickers/<name>.* or manifest resource_id"))
            elif op_type == "select":
                current_selector = op.get("selector") or current_selector
                report.append(self._record(op, EXECUTED, "Mapped UI selection to logical selector", {"selector": current_selector}))
            elif op_type == "random_place":
                current_selector = op.get("selector") or current_selector
                random_offsets = random_positions(int(op.get("count", 6)), seed=self.seed)
                report.append(
                    self._record(
                        op,
                        EXECUTED,
                        "Generated deterministic random placement",
                        {"selector": current_selector, "offsets": random_offsets},
                    )
                )
            elif op_type in {"add_transition", "set_scale", "add_mask", "add_animation"}:
                report.append(self._record(op, EXECUTED, "Supported by draft generation"))
            elif op_type == "copy":
                ui_tasks.append(self._ui_task(op, current_selector, "Copy segment or attributes in Jianying UI"))
                report.append(self._record(op, EXECUTED, "Recorded copy operation as draft-level logical action"))
            elif op_type == "keyframe":
                report.append(self._record(op, EXECUTED, "Recorded keyframe instruction as draft-level logical action"))
            elif op_type == "compound_clip":
                ui_tasks.append(self._ui_task(op, current_selector, "Create native Jianying compound clip"))
                report.append(self._record(op, EXECUTED, "Recorded logical compound clip group"))
            elif op_type in {"add_effect", "add_filter"}:
                resolved = self._resolve_effect_or_filter(op, effect_manifest if op_type == "add_effect" else filter_manifest)
                if resolved:
                    if op_type == "add_filter":
                        filters.append(resolved)
                    else:
                        effects.append(resolved)
                    report.append(self._record(op, EXECUTED, "Resolved effect/filter", resolved))
                else:
                    report.append(self._record(op, NEEDS_ASSET, "Effect/filter needs an enum name or manifest id"))
            else:
                report.append(self._record(op, UNSUPPORTED, "Unknown operation type"))

        if not marker_times and required_markers > 1 and self.markers_mode != "none":
            if resolved_audio is None:
                resolved_audio = self._resolve_or_generate_audio(required_markers)
            marker_times = self._marker_times_from_audio_or_count(resolved_audio, required_markers)

        warnings = _warnings_from_report(report)
        return ExecutionResult(
            report=report,
            warnings=warnings,
            marker_times=marker_times,
            sticker_assets=sticker_assets,
            random_offsets=random_offsets,
            resolved_audio_path=str(resolved_audio) if resolved_audio else None,
            effects=effects,
            filters=filters,
            current_selector=current_selector,
            ui_tasks=ui_tasks,
        )

    def _record(self, op: Dict[str, Any], status: str, message: str, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
        return {
            "operation": op.get("type", "unknown"),
            "status": status,
            "message": message,
            "line": op.get("line", ""),
            "line_index": op.get("line_index"),
            "details": details or {},
        }

    def _ui_task(self, op: Dict[str, Any], selector: str, precondition: str) -> Dict[str, Any]:
        return {
            "type": op.get("type", "unknown"),
            "status": UI_REQUIRED,
            "line": op.get("line", ""),
            "line_index": op.get("line_index"),
            "selector": op.get("selector") or selector,
            "precondition": precondition,
        }

    def _ui_action(self, op: Dict[str, Any], method_name: str) -> Dict[str, Any]:
        driver = self.ui_driver
        if driver is None:
            try:
                from jianying_ui import JianyingUIAutomation
            except ImportError:
                return self._record(op, UI_FAILED, "UI automation module is unavailable")
            driver = JianyingUIAutomation()
        method = getattr(driver, method_name)
        result = method()
        return self._record(op, result.get("status", UI_FAILED), result.get("message", "UI action failed"), result.get("details", {}))

    def _required_marker_count(self, plan, card_count: int) -> int:
        max_marker = 0
        for op in getattr(plan, "operations", []):
            marker_index = op.get("marker_index")
            if isinstance(marker_index, int):
                max_marker = max(max_marker, marker_index)
        return max(2, card_count + 1, max_marker + 1)

    def _resolve_or_generate_audio(self, required_markers: int) -> Path | None:
        audio = self.audio_asset or self._first_audio_asset()
        if audio:
            return audio
        if not self.auto_generate_missing_assets:
            return None
        duration = max(self.segment_duration_sec, (required_markers - 1) * self.segment_duration_sec)
        output = self.generated_dir / "default_beat.wav"
        write_beat_wav(output, duration_sec=duration, bpm=self.default_beat_bpm)
        return output

    def _marker_times_from_audio_or_count(self, audio: Path | None, required_markers: int) -> List[float]:
        markers = generate_audio_markers(audio, segment_duration_sec=self.segment_duration_sec) if audio else []
        if len(markers) >= required_markers:
            return markers
        return [round(i * self.segment_duration_sec, 3) for i in range(required_markers)]

    def _first_audio_asset(self) -> Path | None:
        if not self.assets_dir.exists():
            return None
        for path in sorted(self.assets_dir.iterdir(), key=lambda p: p.name.lower()):
            if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS:
                return path
        return None

    def _resolve_sticker(self, op: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any] | None:
        name = op.get("name", "") or "贴纸"
        sticker_dir = self.assets_dir / "stickers"
        if sticker_dir.exists():
            for path in sorted(sticker_dir.iterdir(), key=lambda p: p.name.lower()):
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS and path.stem == name:
                    return {"name": name, "path": str(path), "status": "local"}
        item = manifest.get(name)
        if self.backend != "kdenlive" and isinstance(item, dict) and item.get("resource_id"):
            return {"name": name, "resource_id": item["resource_id"], "status": "manifest"}
        if self.backend != "kdenlive" and isinstance(item, str):
            return {"name": name, "resource_id": item, "status": "manifest"}
        if self.placeholder_stickers_enabled and self.auto_generate_missing_assets:
            path = self.generated_dir / "stickers" / f"{safe_filename(name)}.png"
            write_placeholder_sticker(path, name)
            return {"name": name, "path": str(path), "status": "generated"}
        return None

    def _resolve_effect_or_filter(self, op: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any] | None:
        name = op.get("name", "")
        selector = op.get("selector") or "selected"
        if self.backend == "kdenlive":
            return {"name": name, "selector": selector, "source": "operation"}
        item = manifest.get(name)
        if isinstance(item, dict) and item.get("resource_id"):
            return {"name": name, "selector": selector, "resource_id": item["resource_id"], "source": "manifest"}
        if isinstance(item, str):
            return {"name": name, "selector": selector, "resource_id": item, "source": "manifest"}

        resolved = resolve_effect_or_filter(name, op.get("type", "add_effect"))
        if resolved:
            return {**resolved, "name": name, "selector": selector, "source": "enum"}
        return None


def write_beat_wav(path: Path, *, duration_sec: float, bpm: int = 120, sample_rate: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total_samples = max(1, int(duration_sec * sample_rate))
    beat_interval = max(1, int(sample_rate * 60 / max(1, bpm)))
    click_len = int(sample_rate * 0.035)
    frames = bytearray()
    for index in range(total_samples):
        beat_pos = index % beat_interval
        if beat_pos < click_len:
            envelope = 1.0 - beat_pos / max(1, click_len)
            sample = int(9000 * envelope * math.sin(2 * math.pi * 880 * index / sample_rate))
        else:
            sample = 0
        frames.extend(struct.pack("<h", sample))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))


def write_placeholder_sticker(path: Path, name: str, size: int = 320) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = bytearray()
    normalized = _normalize_effect_name(name)
    for y in range(size):
        for x in range(size):
            dx = x - size / 2
            dy = y - size / 2
            distance = math.sqrt(dx * dx + dy * dy)
            angle = (math.atan2(dy, dx) + math.pi) / (2 * math.pi)
            if "光圈" in normalized:
                band = size * 0.26 < distance < size * 0.42
                r = int(80 + 175 * angle)
                g = int(220 - 120 * angle)
                b = int(180 + 60 * math.sin(angle * math.pi))
                a = 255 if band else 0
            elif "球" in normalized:
                inside = distance < size * 0.35
                shade = max(0.25, 1 - distance / (size * 0.45))
                r, g, b = 35, int(120 * shade + 40), int(255 * shade)
                a = 255 if inside else 0
            else:
                inside = distance < size * 0.38
                r, g, b = 245, 210, 80
                a = 255 if inside else 0
            pixels.extend((r, g, b, a))
    write_rgba_png(path, size, size, bytes(pixels))


def write_rgba_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    rows = []
    stride = width * 4
    for y in range(height):
        rows.append(b"\x00" + rgba[y * stride : (y + 1) * stride])
    png = b"\x89PNG\r\n\x1a\n"
    png += _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += _png_chunk(b"IDAT", zlib.compress(b"".join(rows)))
    png += _png_chunk(b"IEND", b"")
    path.write_bytes(png)


def safe_filename(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip()
    return cleaned or "sticker"


def _find_enum(enum_type, name: str, enum_kind: str) -> Dict[str, Any] | None:
    target = _compact_name(name)
    for member in enum_type:
        display = getattr(member.value, "name", "")
        candidates = {member.name, display, member.name.replace("_", ""), display.replace(" ", "")}
        if any(_compact_name(candidate) == target for candidate in candidates):
            return {
                "enum_kind": enum_kind,
                "enum_type": enum_type.__name__,
                "member_name": member.name,
                "display_name": display,
            }
    for member in enum_type:
        display = getattr(member.value, "name", "")
        if target and (target in _compact_name(member.name) or target in _compact_name(display)):
            return {
                "enum_kind": enum_kind,
                "enum_type": enum_type.__name__,
                "member_name": member.name,
                "display_name": display,
            }
    return None


def _normalize_effect_name(name: str) -> str:
    normalized = name.strip()
    replacements = {
        "煙": "烟",
        "霧": "雾",
        "運": "运",
        "動": "动",
        "濾": "滤",
        "鏡": "镜",
        "頂": "顶",
        "點": "点",
        "貼": "贴",
        "紙": "纸",
        "體": "体",
        "藍": "蓝",
    }
    for old, new in replacements.items():
        normalized = normalized.replace(old, new)
    return EFFECT_ALIASES.get(normalized, normalized)


def _compact_name(name: str) -> str:
    return _normalize_effect_name(name).replace("_", "").replace(" ", "").replace("-", "").replace("Ⅱ", "II").lower()


def _warnings_from_report(report: Sequence[Dict[str, Any]]) -> List[str]:
    warnings = []
    for item in report:
        if item["status"] in WARNING_STATUSES:
            line_index = item.get("line_index")
            prefix = f"Line {line_index}: " if line_index else ""
            line = item.get("line", "")
            suffix = f"：{line}" if line else ""
            warnings.append(f"{prefix}{item['message']}{suffix}")
    return warnings


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
