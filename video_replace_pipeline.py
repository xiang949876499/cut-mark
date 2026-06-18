from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Protocol

from comfy_client import ComfyClient, ComfyPromptResult, find_video_outputs, patch_workflow
from reference_resolver import ReferenceResolver, SceneReferences
from video_scene_splitter import (
    SceneRange,
    VideoMetadata,
    cut_scene_clips,
    detect_scene_cut_points,
    normalize_scene_ranges,
    probe_video,
)


@dataclass(frozen=True)
class VideoReplaceConfig:
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_workflow_bindings: dict[str, dict[str, str]] | None = None
    scene_backend: str = "auto"
    scene_threshold: float = 27.0
    ffmpeg_scene_threshold: float = 0.35
    min_scene_duration_sec: float = 0.8
    preserve_source_audio: bool = True
    skip_existing_processed: bool = True
    comfy_timeout_sec: float = 1800.0


@dataclass(frozen=True)
class VideoReplaceResult:
    output_path: Path
    job_dir: Path
    manifest_path: Path
    report_path: Path


class ComfySubmitter(Protocol):
    def submit_and_wait(self, workflow: dict, *, timeout_sec: float) -> ComfyPromptResult:
        ...


DEFAULT_WORKFLOW_BINDINGS = {
    "video_path": {"node": "10", "field": "video"},
    "person_image": {"node": "11", "field": "image"},
    "background_image": {"node": "12", "field": "image"},
    "output_prefix": {"node": "13", "field": "filename_prefix"},
}


def build_job_id(
    source_video: Path,
    source_size: int,
    source_mtime_ns: int,
    workflow_path: Path,
    output_path: Path,
) -> str:
    digest = hashlib.sha256()
    for value in [
        str(Path(source_video).resolve()),
        str(source_size),
        str(source_mtime_ns),
        str(Path(workflow_path).resolve()),
        str(Path(output_path).resolve()),
    ]:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def write_concat_file(clips: list[Path], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"file '{_escape_concat_path(clip)}'" for clip in clips]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def process_video_replacement(
    *,
    source_video: Path,
    refs_dir: Path,
    workflow_path: Path,
    output_dir: Path,
    config: VideoReplaceConfig,
    probe: Callable[[Path], VideoMetadata] = probe_video,
    detect: Callable[[Path], list[float]] | None = None,
    cut: Callable[[Path, list[SceneRange], Path], list[Path]] = cut_scene_clips,
    client: ComfySubmitter | None = None,
    materialize_comfy_output: Callable[[ComfyPromptResult, Path, SceneReferences, Path], Path] | None = None,
    merge: Callable[[list[Path], Path, Path, Path], None] | None = None,
    work_root: Path = Path("generated/video_replace"),
) -> VideoReplaceResult:
    source_video = Path(source_video)
    refs_dir = Path(refs_dir)
    workflow_path = Path(workflow_path)
    output_dir = Path(output_dir)
    if not source_video.is_file():
        raise FileNotFoundError(f"Source video not found: {source_video}")
    if not workflow_path.is_file():
        raise FileNotFoundError(f"ComfyUI workflow not found: {workflow_path}")

    stat = source_video.stat()
    job_id = build_job_id(source_video, stat.st_size, stat.st_mtime_ns, workflow_path, output_dir)
    output_path = output_dir / f"{source_video.stem}_replaced_{job_id}.mp4"
    job_dir = Path(work_root) / f"{source_video.stem}_{job_id}"
    scenes_dir = job_dir / "scenes"
    processed_dir = job_dir / "processed"
    manifest_path = job_dir / "manifest.json"
    report_path = job_dir / "report.json"
    concat_path = job_dir / "concat.txt"
    output_dir.mkdir(parents=True, exist_ok=True)
    job_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)

    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    bindings = config.comfy_workflow_bindings or DEFAULT_WORKFLOW_BINDINGS
    metadata = probe(source_video)
    cut_points = (
        detect(source_video)
        if detect
        else detect_scene_cut_points(
            source_video,
            backend=config.scene_backend,
            threshold=config.scene_threshold,
            ffmpeg_scene_threshold=config.ffmpeg_scene_threshold,
        )
    )
    ranges = normalize_scene_ranges(
        cut_points,
        duration=metadata.duration,
        min_scene_duration=config.min_scene_duration_sec,
    )
    scene_clips = cut(source_video, ranges, scenes_dir)
    resolver = ReferenceResolver(refs_dir)
    comfy = client or ComfyClient(config.comfy_url)
    materializer = materialize_comfy_output or materialize_from_comfy_history
    processed_clips: list[Path] = []
    manifest = {
        "source_video": str(source_video),
        "output_path": str(output_path),
        "job_id": job_id,
        "metadata": asdict(metadata),
        "scenes": [],
    }

    for scene, scene_clip in zip(ranges, scene_clips):
        references = resolver.resolve(scene.index)
        processed_path = processed_dir / f"scene_{scene.index:03d}_processed.mp4"
        if config.skip_existing_processed and processed_path.is_file():
            status = "reused"
        else:
            patched = patch_workflow(
                workflow,
                bindings,
                video_path=scene_clip,
                person_image=references.person,
                background_image=references.background,
                output_prefix=str((processed_dir / f"scene_{scene.index:03d}").as_posix()),
            )
            prompt_result = comfy.submit_and_wait(patched, timeout_sec=config.comfy_timeout_sec)
            materializer(prompt_result, scene_clip, references, processed_path)
            status = "succeeded"
        processed_clips.append(processed_path)
        manifest["scenes"].append(_manifest_scene(scene, references, scene_clip, processed_path, status))
        _write_json(manifest_path, manifest)

    if merge:
        merge(processed_clips, source_video, output_path, concat_path)
    else:
        merge_processed_clips(
            processed_clips,
            source_video,
            output_path,
            concat_path,
            preserve_source_audio=config.preserve_source_audio,
        )
    report = {
        "status": "succeeded",
        "job_id": job_id,
        "scene_count": len(processed_clips),
        "output_path": str(output_path),
        "manifest_path": str(manifest_path),
    }
    _write_json(report_path, report)
    return VideoReplaceResult(output_path, job_dir, manifest_path, report_path)


def merge_processed_clips(
    processed_clips: list[Path],
    source_video: Path,
    output_path: Path,
    concat_path: Path,
    *,
    preserve_source_audio: bool = True,
) -> None:
    write_concat_file(processed_clips, concat_path)
    temp_video = output_path.with_suffix(".video.mp4")
    concat_command = [
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
        str(temp_video),
    ]
    _run_ffmpeg(concat_command)
    if not preserve_source_audio:
        shutil.move(temp_video, output_path)
        return
    mux_command = [
        "ffmpeg",
        "-y",
        "-i",
        str(temp_video),
        "-i",
        str(source_video),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    _run_ffmpeg(mux_command)
    temp_video.unlink(missing_ok=True)


def materialize_from_comfy_history(
    result: ComfyPromptResult,
    scene_clip: Path,
    references: SceneReferences,
    output_path: Path,
) -> Path:
    for filename in find_video_outputs(result.history):
        candidate = Path(filename)
        candidates = [
            candidate,
            output_path.parent / candidate.name,
            Path("output") / candidate.name,
        ]
        for source in candidates:
            if source.is_file():
                output_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, output_path)
                return output_path
    raise RuntimeError(f"ComfyUI finished but no video output was found for scene_{references.scene_index:03d}")


def _manifest_scene(
    scene: SceneRange,
    references: SceneReferences,
    scene_clip: Path,
    processed_path: Path,
    status: str,
) -> dict:
    return {
        "index": scene.index,
        "start": scene.start,
        "end": scene.end,
        "duration": scene.duration,
        "person_image": str(references.person),
        "background_image": str(references.background),
        "scene_clip": str(scene_clip),
        "processed_clip": str(processed_path),
        "status": status,
    }


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {result.stderr}")


def _escape_concat_path(path: Path) -> str:
    return Path(path).as_posix().replace("'", "'\\''")
