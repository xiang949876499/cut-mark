# Kdenlive Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kdenlive 26.04.2 the default editing backend, generate an editable `.kdenlive` project from the existing text/operation pipeline, and automatically render a verified H.264/AAC MP4.

**Architecture:** Keep input extraction, card splitting, operation parsing, marker generation, placeholder media, and deterministic random placement. Add a Kdenlive runtime manager, a backend-neutral timeline model, an MLT/Kdenlive XML builder, a runtime-aware effect mapper, and a renderer/validator. Route the existing CLI to the new backend by default while preserving `direct`, `jianying`, `draft`, and `ui-only` as explicit legacy routes.

**Tech Stack:** Python 3.11+, standard library (`dataclasses`, `hashlib`, `json`, `subprocess`, `urllib.request`, `xml.etree.ElementTree`), Kdenlive 26.04.2 Windows Standalone, MLT `melt.exe`, FFmpeg/ffprobe, existing `unittest` test suite.

---

## File Structure

New production files:

- `kdenlive_runtime.py`: pinned runtime metadata, download, SHA-256 verification, extraction, executable discovery, and MLT capability queries.
- `kdenlive_timeline.py`: backend-neutral project, track, clip, marker, keyframe, and effect data classes plus second/frame conversion.
- `kdenlive_effects.py`: operation selector resolution and runtime-aware mapping from tutorial operations to Kdenlive/MLT effects and transitions.
- `kdenlive_project.py`: structured MLT/Kdenlive XML generation and validation.
- `kdenlive_renderer.py`: `melt.exe` rendering, log capture, ffprobe validation, and render report data.
- `kdenlive_backend.py`: orchestration from the existing config/input/operation results to project and MP4 artifacts.

New tests:

- `tests/test_kdenlive_runtime.py`
- `tests/test_kdenlive_timeline.py`
- `tests/test_kdenlive_effects.py`
- `tests/test_kdenlive_project.py`
- `tests/test_kdenlive_renderer.py`
- `tests/test_kdenlive_backend.py`
- `tests/test_kdenlive_cli.py`

Modified files:

- `draft_generator.py`: add Kdenlive configuration and routes; make Kdenlive the default.
- `operation_executor.py`: make effect preparation backend-neutral and preserve source operation details.
- `config.example.json`: document Kdenlive defaults.
- `config.json`: add local Kdenlive settings without removing the user's current Jianying settings.
- `.gitignore`: ignore downloaded runtime archives and extracted runtime.
- `README.md`: replace the primary workflow with Kdenlive project + MP4 generation and move Jianying to a legacy section.
- `requirements.txt`: keep `pyJianYingDraft` only for the explicit Jianying route; do not add a new XML dependency.

## Pinned Runtime

Use this exact release metadata:

```python
KDENLIVE_26_04_2 = RuntimeSpec(
    version="26.04.2",
    url="https://download.kde.org/stable/kdenlive/26.04/windows/kdenlive-26.04.2_standalone.exe",
    filename="kdenlive-26.04.2_standalone.exe",
    size_bytes=133_466_947,
    sha256="4f5a9167a65fa7df411ca6655fa826f0d9ec502ddd157b3ddbf70cd77398dff4",
)
```

The size and SHA-256 are published by KDE Mirrorbits for the pinned artifact.

### Task 1: Add Kdenlive Configuration and Route Contract

**Files:**
- Modify: `draft_generator.py`
- Test: `tests/test_kdenlive_cli.py`
- Test: `tests/test_draft_generator.py`
- Test: `tests/test_capability_routing.py`

- [ ] **Step 1: Write failing tests for Kdenlive defaults and optional Jianying config**

Create `tests/test_kdenlive_cli.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from draft_generator import load_config, parse_args


class KdenliveCliTests(unittest.TestCase):
    def test_default_route_is_kdenlive(self):
        args = parse_args(["--config", "config.json", "--input", "input/content.txt"])
        self.assertEqual(args.route, "kdenlive")

    def test_kdenlive_project_route_is_available(self):
        args = parse_args(
            [
                "--config",
                "config.json",
                "--input",
                "input/content.txt",
                "--route",
                "kdenlive-project",
            ]
        )
        self.assertEqual(args.route, "kdenlive-project")

    def test_kdenlive_config_does_not_require_draft_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "draft_name": "demo",
                        "backend": "kdenlive",
                        "kdenlive_version": "26.04.2",
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(path)

        self.assertIsNone(config.draft_folder)
        self.assertEqual(config.backend, "kdenlive")
        self.assertEqual(config.kdenlive_version, "26.04.2")
```

- [ ] **Step 2: Run the tests and verify the expected failures**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_cli
```

Expected: FAIL because the default route is `auto`, `kdenlive-project` is not a valid choice, and `load_config()` still requires `draft_folder`.

- [ ] **Step 3: Extend `GeneratorConfig` and CLI choices**

In `draft_generator.py`, add these defaults:

```python
DEFAULT_CONFIG.update(
    {
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
)
```

Change `GeneratorConfig` so `draft_folder` is optional and add:

```python
draft_folder: Optional[Path]
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
```

In `load_config()`, only require `draft_folder` when `backend == "jianying"` or an explicit Jianying route calls `build_draft()`:

```python
draft_folder_value = raw.get("draft_folder")
draft_folder = Path(draft_folder_value) if draft_folder_value else None
```

Set the CLI route choices and default:

```python
choices=["kdenlive", "kdenlive-project", "direct", "jianying", "draft", "ui-only"]
default="kdenlive"
```

Update every direct `GeneratorConfig` constructor in existing tests, especially `_config()` in `tests/test_capability_routing.py`, with the new Kdenlive fields. Keep `draft_folder=root / "drafts"` in tests that still exercise the legacy draft backend.

- [ ] **Step 4: Add a legacy-route guard**

Add:

```python
def require_jianying_draft_folder(config: GeneratorConfig) -> Path:
    if config.draft_folder is None:
        raise ValueError("draft_folder is required for the Jianying route")
    return config.draft_folder
```

Call it at the top of `build_draft()` before importing or using `pyJianYingDraft`.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_cli tests.test_draft_generator
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add draft_generator.py tests/test_kdenlive_cli.py tests/test_draft_generator.py tests/test_capability_routing.py
git commit -m "feat: add Kdenlive route configuration"
```

### Task 2: Implement the Pinned Kdenlive Runtime Manager

**Files:**
- Create: `kdenlive_runtime.py`
- Create: `tests/test_kdenlive_runtime.py`
- Modify: `.gitignore`

- [ ] **Step 1: Write failing tests for metadata, reuse, checksum rejection, and executable discovery**

Create `tests/test_kdenlive_runtime.py`:

```python
import hashlib
import tempfile
import unittest
from pathlib import Path

from kdenlive_runtime import (
    KDENLIVE_26_04_2,
    KdenliveRuntime,
    RuntimeIntegrityError,
)


class KdenliveRuntimeTests(unittest.TestCase):
    def test_release_metadata_is_pinned(self):
        self.assertEqual(KDENLIVE_26_04_2.version, "26.04.2")
        self.assertEqual(KDENLIVE_26_04_2.size_bytes, 133_466_947)
        self.assertEqual(
            KDENLIVE_26_04_2.sha256,
            "4f5a9167a65fa7df411ca6655fa826f0d9ec502ddd157b3ddbf70cd77398dff4",
        )

    def test_verify_archive_rejects_wrong_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "runtime.exe"
            archive.write_bytes(b"not kdenlive")
            runtime = KdenliveRuntime(Path(tmp), auto_download=False)

            with self.assertRaises(RuntimeIntegrityError):
                runtime.verify_archive(archive)

    def test_resolve_reuses_existing_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            install = root / "kdenlive-26.04.2"
            (install / "bin").mkdir(parents=True)
            (install / "bin" / "kdenlive.exe").write_bytes(b"")
            (install / "bin" / "melt.exe").write_bytes(b"")

            paths = KdenliveRuntime(root, auto_download=False).resolve()

        self.assertEqual(paths.kdenlive_exe.name, "kdenlive.exe")
        self.assertEqual(paths.melt_exe.name, "melt.exe")
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_runtime
```

Expected: ERROR with `ModuleNotFoundError: No module named 'kdenlive_runtime'`.

- [ ] **Step 3: Implement runtime data types and integrity verification**

Create `kdenlive_runtime.py` with:

```python
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class RuntimeIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class RuntimeSpec:
    version: str
    url: str
    filename: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class RuntimePaths:
    root: Path
    kdenlive_exe: Path
    melt_exe: Path
    ffmpeg_exe: Path | None = None
    ffprobe_exe: Path | None = None


KDENLIVE_26_04_2 = RuntimeSpec(
    version="26.04.2",
    url="https://download.kde.org/stable/kdenlive/26.04/windows/kdenlive-26.04.2_standalone.exe",
    filename="kdenlive-26.04.2_standalone.exe",
    size_bytes=133_466_947,
    sha256="4f5a9167a65fa7df411ca6655fa826f0d9ec502ddd157b3ddbf70cd77398dff4",
)
```

Implement `verify_archive()` using streamed SHA-256 and exact size comparison. A mismatch must raise `RuntimeIntegrityError`.

- [ ] **Step 4: Implement download-to-part and atomic replacement**

Use:

```python
def download(self, spec: RuntimeSpec = KDENLIVE_26_04_2) -> Path:
    self.runtime_dir.mkdir(parents=True, exist_ok=True)
    archive = self.runtime_dir / spec.filename
    part = archive.with_suffix(archive.suffix + ".part")
    request = urllib.request.Request(spec.url, headers={"User-Agent": "cut-mark/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as output:
        shutil.copyfileobj(response, output, length=1024 * 1024)
    self.verify_archive(part, spec)
    part.replace(archive)
    return archive
```

On exceptions, delete only the `.part` file. Never delete a previously verified archive or extracted runtime.

- [ ] **Step 5: Implement standalone extraction and path discovery**

Extract the pinned 7-Zip SFX into a staging directory:

```python
subprocess.run(
    [str(archive), "-y", f"-o{staging_dir}"],
    check=True,
    capture_output=True,
    text=True,
)
```

After extraction:

1. Resolve every extracted path and assert it remains under the staging directory.
2. Find exactly one `kdenlive.exe` and one `melt.exe` recursively.
3. Find bundled `ffmpeg.exe` and `ffprobe.exe` when present and record them as optional runtime paths.
4. Atomically rename staging to `runtime_dir / "kdenlive-26.04.2"`.
5. Write `runtime.json` containing version, archive SHA-256, and executable relative paths.

`resolve()` must reuse a valid existing runtime, download/extract only when absent and `auto_download=True`, and otherwise raise a clear `RuntimeError`.

- [ ] **Step 6: Add extraction and downloader tests using injected callables**

Extend the constructor:

```python
def __init__(
    self,
    runtime_dir: Path,
    *,
    auto_download: bool = True,
    opener=urllib.request.urlopen,
    runner=subprocess.run,
) -> None:
```

Tests must verify:

- `.part` is removed after a failed download.
- a wrong-size response is rejected.
- extraction command uses `-y` and a staging output path.
- discovery rejects missing or duplicate `melt.exe`.
- resolved paths remain under the configured runtime directory.

- [ ] **Step 7: Ignore runtime artifacts**

Append to `.gitignore`:

```gitignore
generated/runtime/
*.exe.part
```

- [ ] **Step 8: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_runtime
```

Expected: PASS without network access.

- [ ] **Step 9: Commit**

```powershell
git add kdenlive_runtime.py tests/test_kdenlive_runtime.py .gitignore
git commit -m "feat: manage pinned Kdenlive runtime"
```

### Task 3: Add the Backend-Neutral Timeline Model

**Files:**
- Create: `kdenlive_timeline.py`
- Create: `tests/test_kdenlive_timeline.py`

- [ ] **Step 1: Write failing tests for frame conversion and marker splitting**

Create `tests/test_kdenlive_timeline.py`:

```python
import unittest
from pathlib import Path

from kdenlive_timeline import (
    TimelineClip,
    TimelineProject,
    TimelineTrack,
    frames_from_seconds,
    split_ranges_from_markers,
)


class KdenliveTimelineTests(unittest.TestCase):
    def test_seconds_convert_to_frames_with_rounding(self):
        self.assertEqual(frames_from_seconds(4.5, 30), 135)
        self.assertEqual(frames_from_seconds(1 / 30, 30), 1)

    def test_markers_create_contiguous_frame_ranges(self):
        ranges = split_ranges_from_markers([0.0, 1.0, 2.5], count=3, fps=30, fallback_frames=60)
        self.assertEqual(ranges, [(0, 29), (30, 74), (75, 134)])

    def test_selector_matches_named_stickers(self):
        project = TimelineProject(width=1080, height=1920, fps=30)
        project.tracks.append(
            TimelineTrack(
                id="stickers",
                kind="video",
                role="sticker",
                clips=[
                    TimelineClip(
                        id="sticker-1",
                        source=Path("ring.png"),
                        start_frame=0,
                        duration_frames=60,
                        name="旋彩光圈",
                        role="sticker",
                    )
                ],
            )
        )
        self.assertEqual([clip.id for clip in project.select("named:光圈")], ["sticker-1"])
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_timeline
```

Expected: ERROR because `kdenlive_timeline` does not exist.

- [ ] **Step 3: Implement focused timeline data classes**

Create:

```python
@dataclass
class Keyframe:
    frame: int
    value: str


@dataclass
class TimelineEffect:
    service: str
    properties: dict[str, str]
    keyframes: list[Keyframe] = field(default_factory=list)
    status: str = "exact"
    source_name: str = ""


@dataclass
class TimelineClip:
    id: str
    source: Path | None
    start_frame: int
    duration_frames: int
    source_in_frame: int = 0
    name: str = ""
    role: str = "video"
    effects: list[TimelineEffect] = field(default_factory=list)
    nested_sequence_id: str | None = None


@dataclass
class TimelineTrack:
    id: str
    kind: str
    role: str
    clips: list[TimelineClip] = field(default_factory=list)


@dataclass
class TimelineProject:
    width: int
    height: int
    fps: int
    tracks: list[TimelineTrack] = field(default_factory=list)
    markers: list[int] = field(default_factory=list)
    sequences: dict[str, "TimelineProject"] = field(default_factory=dict)
```

Implement selectors:

- `selected`: the most recently targeted clips supplied by the mapper.
- `all_video_segments`: clips with role `video`.
- `all_stickers`: clips with role `sticker`.
- `named:光圈`: case-insensitive substring match against clip name; the text after `named:` is the query.

- [ ] **Step 4: Implement deterministic time helpers**

```python
def frames_from_seconds(seconds: float, fps: int) -> int:
    return max(0, int(round(seconds * fps)))
```

`split_ranges_from_markers()` must return inclusive `(in_frame, out_frame)` ranges, never overlap, and use `fallback_frames` after the last marker.

- [ ] **Step 5: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_timeline
```

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add kdenlive_timeline.py tests/test_kdenlive_timeline.py
git commit -m "feat: add Kdenlive timeline model"
```

### Task 4: Build Minimal Valid MLT/Kdenlive XML

**Files:**
- Create: `kdenlive_project.py`
- Create: `tests/test_kdenlive_project.py`

- [ ] **Step 1: Write failing XML structure tests**

Create `tests/test_kdenlive_project.py`:

```python
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from kdenlive_project import KdenliveProjectBuilder, validate_project_xml
from kdenlive_timeline import TimelineClip, TimelineProject, TimelineTrack


class KdenliveProjectTests(unittest.TestCase):
    def test_builder_writes_profile_producer_playlist_and_tractor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root / "black.png"
            image.write_bytes(b"png")
            project = TimelineProject(width=1080, height=1920, fps=30)
            project.tracks.append(
                TimelineTrack(
                    id="video-main",
                    kind="video",
                    role="video",
                    clips=[
                        TimelineClip(
                            id="clip-1",
                            source=image,
                            start_frame=0,
                            duration_frames=90,
                        )
                    ],
                )
            )
            output = root / "demo.kdenlive"

            KdenliveProjectBuilder().write(project, output)
            tree = ET.parse(output)

        self.assertEqual(tree.getroot().tag, "mlt")
        self.assertIsNotNone(tree.find(".//profile"))
        self.assertIsNotNone(tree.find(".//producer"))
        self.assertIsNotNone(tree.find(".//playlist"))
        self.assertIsNotNone(tree.find(".//tractor"))

    def test_validator_rejects_missing_producer_reference(self):
        xml = b'<mlt><playlist id="p"><entry producer="missing" in="0" out="29"/></playlist></mlt>'
        with self.assertRaises(ValueError):
            validate_project_xml(xml)
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_project
```

Expected: ERROR because `kdenlive_project` does not exist.

- [ ] **Step 3: Implement the root profile and project metadata**

Use `xml.etree.ElementTree` exclusively. The root and profile must be constructed as:

```python
root = ET.Element(
    "mlt",
    {
        "LC_NUMERIC": "C",
        "version": "7.32.0",
        "title": "cut-mark",
        "producer": "main_bin",
    },
)
ET.SubElement(
    root,
    "profile",
    {
        "description": f"{project.width}x{project.height} {project.fps} fps",
        "width": str(project.width),
        "height": str(project.height),
        "progressive": "1",
        "sample_aspect_num": "1",
        "sample_aspect_den": "1",
        "display_aspect_num": str(project.width),
        "display_aspect_den": str(project.height),
        "frame_rate_num": str(project.fps),
        "frame_rate_den": "1",
        "colorspace": "709",
    },
)
```

Add Kdenlive document properties such as `kdenlive:docproperties.version`, `kdenlive:docproperties.profile`, and `kdenlive:docproperties.documentid` to the main tractor.

- [ ] **Step 4: Implement producers and playlists**

For every unique local media source:

```xml
<producer id="producer-1" in="0" out="89">
  <property name="mlt_service">qimage</property>
  <property name="resource">D:/absolute/path/black.png</property>
  <property name="kdenlive:clipname">black.png</property>
</producer>
```

Use:

- `qimage` for still images.
- `avformat-novalidate` for video and audio.
- a nested tractor ID for sequence clips.

Generate a blank such as `<blank length="30"/>` whenever a clip starts 30 frames after the previous clip's end. Entry `in` and `out` values are inclusive.

- [ ] **Step 5: Implement tractor composition**

Create one playlist per timeline track, one `<track producer="playlist-id"/>` per playlist, and a main tractor that references all tracks. Add `hide="video"` to audio-only tracks and `hide="audio"` to silent overlay tracks.

- [ ] **Step 6: Implement XML validation**

`validate_project_xml()` must:

1. Parse bytes successfully.
2. Require exactly one `<profile>`.
3. Collect all producer, playlist, and tractor IDs.
4. Reject duplicate IDs.
5. Reject `<entry producer>`, `<track producer>`, or sequence references to missing IDs.
6. Reject entries where `out < in`.
7. Require at least one main tractor.

Write through a temporary file and replace the destination only after validation passes.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_project
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add kdenlive_project.py tests/test_kdenlive_project.py
git commit -m "feat: generate minimal Kdenlive projects"
```

### Task 5: Convert Cards, Assets, Audio, Markers, and Stickers into the Timeline

**Files:**
- Create: `kdenlive_backend.py`
- Create: `tests/test_kdenlive_backend.py`
- Modify: `kdenlive_project.py`
- Modify: `operation_executor.py`
- Modify: `tests/test_operation_executor.py`

- [ ] **Step 1: Write failing backend conversion tests**

Create `tests/test_kdenlive_backend.py`:

```python
import tempfile
import unittest
from pathlib import Path

from draft_generator import DEFAULT_CONFIG, GeneratorConfig, OperationPlan
from kdenlive_backend import build_timeline
from operation_executor import ExecutionResult


class KdenliveBackendTests(unittest.TestCase):
    def test_build_timeline_uses_markers_and_sticker_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            background = root / "background.png"
            sticker = root / "ring.png"
            background.write_bytes(b"image")
            sticker.write_bytes(b"image")
            execution = ExecutionResult(
                report=[],
                warnings=[],
                marker_times=[0.0, 1.0, 2.0],
                sticker_assets=[{"name": "旋彩光圈", "path": str(sticker)}],
                random_offsets=[{"transform_x": 0.2, "transform_y": -0.3}],
            )

            timeline = build_timeline(
                _config(root),
                cards=["第一段", "第二段"],
                assets=[background],
                operation_plan=OperationPlan([], []),
                execution_result=execution,
                include_text=False,
            )

        main = next(track for track in timeline.tracks if track.id == "video-main")
        stickers = next(track for track in timeline.tracks if track.role == "sticker")
        self.assertEqual([clip.duration_frames for clip in main.clips], [30, 30])
        self.assertEqual(stickers.clips[0].name, "旋彩光圈")
        self.assertEqual(timeline.markers, [0, 30, 60])
```

Add a local `_config()` fixture containing all `GeneratorConfig` fields introduced in Task 1.

Use:

```python
def _config(root: Path) -> GeneratorConfig:
    return GeneratorConfig(
        draft_folder=None,
        draft_name="web_transition_video",
        resolution=(1080, 1920),
        segment_duration_sec=1.0,
        max_chars_per_card=72,
        fallback_transitions=list(DEFAULT_CONFIG["fallback_transitions"]),
        default_background_color="#000000",
        audio_asset=None,
        sticker_manifest=root / "stickers.json",
        effect_manifest=root / "effects.json",
        filter_manifest=root / "filters.json",
        ui_automation_enabled=False,
        auto_generate_missing_assets=True,
        default_beat_bpm=120,
        placeholder_stickers_enabled=True,
        unique_draft_name_by_content=True,
        backend="kdenlive",
        kdenlive_version="26.04.2",
        kdenlive_runtime_dir=root / "generated" / "runtime",
        auto_download_kdenlive=False,
        render_video_codec="libx264",
        render_audio_codec="aac",
        render_crf=20,
        render_preset="medium",
        render_fps=30,
        render_with_unsupported_operations=True,
    )
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_backend
```

Expected: ERROR because `kdenlive_backend` does not exist.

- [ ] **Step 3: Implement `build_timeline()`**

The function must:

1. Create `TimelineProject` with configured resolution and FPS.
2. Convert `marker_times` to frame markers.
3. Create a main video track from cards and naturally sorted assets.
4. Reuse assets cyclically.
5. Use marker ranges when available; otherwise use `segment_duration_sec`.
6. Add the resolved audio as one audio clip covering the project duration.
7. Add one overlay track per sticker asset.
8. Carry deterministic offsets into a `transform` timeline effect.
9. Skip card text when `include_text=False`.

Use normalized transform properties:

```python
TimelineEffect(
    service="qtblend",
    source_name="transform",
    properties={
        "rect": f"{x_percent}% {y_percent}% {scale_percent}% {scale_percent}% 1",
        "compositing": "0",
    },
)
```

The later runtime-aware mapping task may replace `qtblend` if unavailable.

- [ ] **Step 4: Add audio, marker, and filter XML support**

In `kdenlive_project.py`:

- Serialize project markers as Kdenlive guide properties.
- Add audio producers/playlists.
- Serialize `TimelineEffect` as `<filter>` children of the relevant producer/playlist entry context.
- Preserve effect `status` and `source_name` in Kdenlive properties for diagnostics.

- [ ] **Step 5: Write a failing test for real beat detection**

Add `import struct` to `tests/test_operation_executor.py`, then add a WAV fixture containing short pulses at 0.5, 1.0, and 1.5 seconds:

```python
def test_generate_audio_markers_detects_pulse_beats(self):
    with tempfile.TemporaryDirectory() as tmp:
        wav_path = Path(tmp) / "beats.wav"
        sample_rate = 16000
        samples = [0] * (sample_rate * 2)
        for beat_second in (0.5, 1.0, 1.5):
            start = int(beat_second * sample_rate)
            for index in range(start, start + 800):
                samples[index] = 24000
        with wave.open(str(wav_path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))

        markers = generate_audio_markers(wav_path, segment_duration_sec=1.0)

    self.assertEqual(markers[0], 0.0)
    self.assertTrue(any(abs(marker - 0.5) < 0.08 for marker in markers))
    self.assertTrue(any(abs(marker - 1.0) < 0.08 for marker in markers))
    self.assertTrue(any(abs(marker - 1.5) < 0.08 for marker in markers))
```

Run:

```powershell
uv run python -m unittest tests.test_operation_executor.OperationExecutorTests.test_generate_audio_markers_detects_pulse_beats
```

Expected: FAIL because the current implementation returns fixed one-second intervals.

- [ ] **Step 6: Implement deterministic local beat detection**

In `operation_executor.py`:

1. Decode non-WAV audio to a temporary mono 16 kHz PCM WAV using FFmpeg.
2. Read 16-bit samples with `wave` and `struct`.
3. Compute RMS energy in 50 ms windows.
4. Compute the median window energy.
5. Select local maxima above `max(500.0, median_energy * 2.5)`.
6. Enforce a 250 ms refractory interval between beats.
7. Return `[0.0] + detected_peak_times`.
8. If fewer than two useful markers are found, preserve the existing fixed-interval fallback.

Inject the subprocess runner into the decode helper so non-WAV conversion is unit-testable without invoking FFmpeg.

- [ ] **Step 7: Add tests for black fallback and hidden tutorial text**

Test that:

- `ensure_assets()` output can be passed directly to `build_timeline()`.
- operations mode has no title/subtitle producer when `include_text=False`.
- cards mode creates text/title clips or an equivalent subtitle track when `include_text=True`.

- [ ] **Step 8: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_backend tests.test_kdenlive_project tests.test_operation_executor
```

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add kdenlive_backend.py kdenlive_project.py operation_executor.py tests/test_kdenlive_backend.py tests/test_kdenlive_project.py tests/test_operation_executor.py
git commit -m "feat: build Kdenlive timelines from parsed content"
```

### Task 6: Map Selectors, Transforms, Effects, Filters, Masks, and Transitions

**Files:**
- Create: `kdenlive_effects.py`
- Create: `tests/test_kdenlive_effects.py`
- Modify: `kdenlive_backend.py`
- Modify: `operation_executor.py`

- [ ] **Step 1: Write failing tests for runtime-aware mappings**

Create `tests/test_kdenlive_effects.py`:

```python
import unittest

from kdenlive_effects import EffectCatalog, KdenliveOperationMapper
from kdenlive_timeline import TimelineClip, TimelineProject, TimelineTrack


class KdenliveEffectsTests(unittest.TestCase):
    def test_motion_blur_uses_first_available_candidate(self):
        catalog = EffectCatalog(filters={"avfilter.avgblur", "frei0r.pixeliz0r"}, transitions=set())
        mapper = KdenliveOperationMapper(catalog)

        result = mapper.resolve_effect("运动模糊")

        self.assertEqual(result.status, "approximated")
        self.assertEqual(result.service, "avfilter.avgblur")

    def test_unknown_effect_is_reported_unsupported(self):
        mapper = KdenliveOperationMapper(EffectCatalog(filters=set(), transitions=set()))
        result = mapper.resolve_effect("不存在的效果")
        self.assertEqual(result.status, "unsupported")

    def test_named_selector_only_changes_matching_sticker(self):
        project = TimelineProject(width=1080, height=1920, fps=30)
        project.tracks.append(
            TimelineTrack(
                id="stickers",
                kind="video",
                role="sticker",
                clips=[
                    TimelineClip("ring", None, 0, 30, name="旋彩光圈", role="sticker"),
                    TimelineClip("ball", None, 0, 30, name="蓝色球体", role="sticker"),
                ],
            )
        )
        mapper = KdenliveOperationMapper(EffectCatalog(filters={"qtblend"}, transitions=set()))

        mapper.apply(project, [{"type": "set_scale", "value": 0.2, "selector": "named:光圈"}])

        self.assertEqual(len(project.select("named:光圈")[0].effects), 1)
        self.assertEqual(len(project.select("named:球体")[0].effects), 0)
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_effects
```

Expected: ERROR because `kdenlive_effects` does not exist.

- [ ] **Step 3: Implement capability catalog parsing**

Define:

```python
@dataclass(frozen=True)
class EffectCatalog:
    filters: set[str]
    transitions: set[str]
```

Add `query_effect_catalog(melt_exe, runner=subprocess.run)` that executes:

```powershell
melt.exe -query filters
melt.exe -query transitions
```

Parse service names from output into sets. Cache the result in `generated/runtime/kdenlive-26.04.2/effect_catalog.json`.

- [ ] **Step 4: Implement explicit alias candidates**

Use ordered candidate tables:

```python
EFFECT_CANDIDATES = {
    "运动模糊": [("avfilter.avgblur", "approximated"), ("frei0r.pixeliz0r", "approximated")],
    "动感模糊": [("avfilter.avgblur", "approximated")],
    "故障2": [("frei0r.glitch0r", "approximated"), ("avfilter.noise", "approximated")],
    "故障_II": [("frei0r.glitch0r", "approximated"), ("avfilter.noise", "approximated")],
    "流动烟雾": [("movit.blur", "approximated"), ("avfilter.noise", "approximated")],
}

TRANSITION_CANDIDATES = {
    "叠化": [("luma", "exact"), ("mix", "approximated")],
    "模糊": [("luma", "approximated")],
    "右移": [("composite", "approximated"), ("qtblend", "approximated")],
    "下移": [("composite", "approximated"), ("qtblend", "approximated")],
    "向上": [("composite", "approximated"), ("qtblend", "approximated")],
}
```

Return an immutable mapping result with `source_name`, `service`, `status`, and properties.

- [ ] **Step 5: Implement operation application**

`KdenliveOperationMapper.apply()` must process operations in source order and maintain the current selector. Implement:

- `select`
- `set_scale`
- `random_place`
- `add_effect`
- `add_filter`
- `add_mask`
- `add_animation`
- `keyframe`
- `add_transition`

For masks, prefer available `shape`/`alpha0ps` candidates. If none exist, return `unsupported` and do not invent a successful filter.

- [ ] **Step 6: Remove Jianying enum resolution from the shared preparation path**

In `operation_executor.py`:

- Add `backend: str = "kdenlive"` to `OperationExecutor.__init__()` and pass `config.backend` from `draft_generator.main()`.
- Preserve the original effect/filter name and selector in `ExecutionResult.effects` and `.filters`.
- Do not require `pyJianYingDraft` when the selected backend is Kdenlive.
- Keep `resolve_effect_or_filter()` only for the explicit Jianying route, or rename it to `resolve_jianying_effect_or_filter()` and call it from `build_draft()`.
- Treat manifest-only sticker `resource_id` values as Jianying-specific. For `backend="kdenlive"`, require a local image path or generate the existing PNG placeholder; never return a bare Jianying resource ID as a resolved Kdenlive sticker.

Add regression tests:

```python
def test_kdenlive_backend_does_not_require_pyjianyingdraft(self):
    plan = build_operation_plan("添加运动模糊特效")
    with tempfile.TemporaryDirectory() as tmp:
        result = OperationExecutor(
            Path(tmp),
            generated_dir=Path(tmp) / "generated",
            backend="kdenlive",
        ).prepare(plan, card_count=2)
    self.assertEqual(result.effects[0]["name"], "运动模糊")


def test_kdenlive_ignores_jianying_sticker_resource_id_and_generates_png(self):
    plan = build_operation_plan("添加旋彩光圈贴纸")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        manifest = root / "stickers.json"
        manifest.write_text(
            json.dumps({"旋彩光圈": {"resource_id": "jianying-only-id"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        result = OperationExecutor(
            root,
            generated_dir=root / "generated",
            sticker_manifest=manifest,
            backend="kdenlive",
        ).prepare(plan, card_count=2)
    self.assertTrue(result.sticker_assets[0]["path"].endswith(".png"))
    self.assertNotIn("resource_id", result.sticker_assets[0])
```

Update the existing `test_executor_resolves_manifest_sticker_id` constructor call by adding the keyword argument `backend="jianying"`, preserving its legacy expectation.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_effects tests.test_operation_executor tests.test_warning_automation
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add kdenlive_effects.py kdenlive_backend.py operation_executor.py tests/test_kdenlive_effects.py tests/test_operation_executor.py tests/test_warning_automation.py
git commit -m "feat: map tutorial operations to MLT effects"
```

### Task 7: Implement Copy Operations and Nested Sequences

**Files:**
- Modify: `kdenlive_timeline.py`
- Modify: `kdenlive_effects.py`
- Modify: `kdenlive_project.py`
- Test: `tests/test_kdenlive_effects.py`
- Test: `tests/test_kdenlive_project.py`

- [ ] **Step 1: Write failing copy and compound tests**

Add:

```python
def project_with_clips(*clips: TimelineClip) -> TimelineProject:
    project = TimelineProject(width=1080, height=1920, fps=30)
    project.tracks.append(
        TimelineTrack(id="video-main", kind="video", role="video", clips=list(clips))
    )
    return project


def project_with_two_stickers() -> TimelineProject:
    project = TimelineProject(width=1080, height=1920, fps=30)
    project.tracks.append(
        TimelineTrack(
            id="stickers",
            kind="video",
            role="sticker",
            clips=[
                TimelineClip("ring-1", None, 0, 30, name="旋彩光圈", role="sticker"),
                TimelineClip("ring-2", None, 30, 30, name="旋彩光圈", role="sticker"),
            ],
        )
    )
    return project


def test_copy_attributes_clones_effects_without_moving_clip(self):
    source = TimelineClip("source", None, 0, 30)
    source.effects.append(TimelineEffect("qtblend", {"rect": "0 0 20% 20% 1"}))
    target = TimelineClip("target", None, 30, 30)
    project = project_with_clips(source, target)

    copy_clip_attributes(source, [target])

    self.assertEqual(target.start_frame, 30)
    self.assertEqual(target.effects, source.effects)
    self.assertIsNot(target.effects[0], source.effects[0])


def test_compound_clip_creates_nested_sequence(self):
    project = project_with_two_stickers()
    mapper = KdenliveOperationMapper(EffectCatalog(filters=set(), transitions=set()))

    mapper.apply(
        project,
        [
            {"type": "select", "selector": "all_stickers"},
            {"type": "compound_clip", "selector": "all_stickers"},
        ],
    )

    self.assertEqual(len(project.sequences), 1)
    self.assertEqual(
        sum(1 for track in project.tracks for clip in track.clips if clip.nested_sequence_id),
        1,
    )
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_effects tests.test_kdenlive_project
```

Expected: FAIL because copy helpers and nested sequence generation are missing.

- [ ] **Step 3: Implement deep copy helpers**

Add:

```python
def copy_clip_attributes(source: TimelineClip, targets: list[TimelineClip]) -> None:
    for target in targets:
        target.effects = copy.deepcopy(source.effects)


def duplicate_clips(clips: list[TimelineClip], offset_frames: int) -> list[TimelineClip]:
    duplicates = copy.deepcopy(clips)
    for index, clip in enumerate(duplicates):
        clip.id = f"{clip.id}-copy-{index + 1}"
        clip.start_frame += offset_frames
    return duplicates
```

Interpret a copy operation as “copy attributes” when its source line contains `属性` or `屬性`; otherwise duplicate the selected clips immediately after the selected range.

- [ ] **Step 4: Implement nested sequence extraction**

For selected clips:

1. Find the minimum start frame and maximum end frame.
2. Create a child `TimelineProject`.
3. Move deep copies of selected clips into child tracks with starts normalized to zero.
4. Remove originals from parent tracks.
5. Insert one parent clip with `nested_sequence_id`.
6. Preserve parent track order and sequence duration.

- [ ] **Step 5: Serialize sequences**

In `kdenlive_project.py`, serialize each child sequence as its own playlists and tractor before the main tractor. A parent nested clip references the child tractor ID as its producer.

Validation must include nested tractor references.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_effects tests.test_kdenlive_project
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add kdenlive_timeline.py kdenlive_effects.py kdenlive_project.py tests/test_kdenlive_effects.py tests/test_kdenlive_project.py
git commit -m "feat: support copied clips and nested sequences"
```

### Task 8: Render with Melt and Validate with ffprobe

**Files:**
- Create: `kdenlive_renderer.py`
- Create: `tests/test_kdenlive_renderer.py`

- [ ] **Step 1: Write failing renderer tests**

Create `tests/test_kdenlive_renderer.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from kdenlive_renderer import KdenliveRenderer, RenderSettings
from kdenlive_runtime import RuntimePaths


class KdenliveRendererTests(unittest.TestCase):
    def test_preflight_requires_avformat_and_requested_encoders(self):
        outputs = {
            "melt.exe": "consumers:\n  avformat\n",
            "ffmpeg.exe": "libx264\naac\n",
        }

        def fake_runner(command, **kwargs):
            name = Path(command[0]).name.lower()
            return type("Result", (), {"returncode": 0, "stdout": outputs[name], "stderr": ""})()

        runtime = RuntimePaths(
            Path("runtime"),
            Path("runtime/kdenlive.exe"),
            Path("runtime/melt.exe"),
            Path("runtime/ffmpeg.exe"),
            Path("runtime/ffprobe.exe"),
        )
        renderer = KdenliveRenderer(runtime, runner=fake_runner)

        renderer.preflight(RenderSettings(width=1080, height=1920, fps=30))

    def test_renderer_invokes_melt_and_validates_output(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            executable = Path(command[0]).name.lower()
            if executable == "melt.exe" and command[1:] == ["-query", "consumers"]:
                return type("Result", (), {"returncode": 0, "stdout": "avformat\n", "stderr": ""})()
            if executable == "ffmpeg.exe":
                return type(
                    "Result",
                    (),
                    {"returncode": 0, "stdout": "libx264\naac\n", "stderr": ""},
                )()
            if executable == "melt.exe":
                consumer = next(item for item in command if item.startswith("avformat:"))
                Path(consumer.split(":", 1)[1]).write_bytes(b"mp4")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "streams": [
                                {"codec_type": "video", "width": 1080, "height": 1920},
                                {"codec_type": "audio"},
                            ],
                            "format": {"duration": "2.000"},
                        }
                    ),
                    "stderr": "",
                },
            )()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo.kdenlive"
            project.write_text("<mlt/>", encoding="utf-8")
            runtime = RuntimePaths(
                root,
                root / "kdenlive.exe",
                root / "melt.exe",
                root / "ffmpeg.exe",
                root / "ffprobe.exe",
            )
            result = KdenliveRenderer(runtime, runner=fake_runner).render(
                project,
                root / "demo.mp4",
                RenderSettings(width=1080, height=1920, fps=30),
            )

        self.assertTrue(result.valid)
        render_call = next(command for command in calls if "-consumer" in command)
        self.assertEqual(Path(render_call[0]).name, "melt.exe")
        self.assertIn("vcodec=libx264", render_call)
        self.assertIn("acodec=aac", render_call)
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_renderer
```

Expected: ERROR because `kdenlive_renderer` does not exist.

- [ ] **Step 3: Implement render settings and command**

Create:

```python
@dataclass(frozen=True)
class RenderSettings:
    width: int
    height: int
    fps: int
    video_codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 20
    preset: str = "medium"


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    valid: bool
    command: list[str]
    melt_returncode: int
    probe: dict
    log_path: Path
```

Implement `preflight()` before `render()`:

1. Run `melt.exe -query consumers` and require `avformat`.
2. Use `RuntimePaths.ffmpeg_exe` when present, otherwise `shutil.which("ffmpeg")`.
3. Run `ffmpeg -hide_banner -encoders`.
4. Require the configured video and audio codec names.
5. Raise a message naming the missing consumer or encoder.

Implement:

```python
def render(
    self,
    project_path: Path,
    output_path: Path,
    settings: RenderSettings,
    *,
    expect_audio: bool = False,
) -> RenderResult:
```

Call `self.preflight(settings)` first, then build this command inside `render()`:

```python
command = [
    str(runtime.melt_exe),
    str(project_path),
    "-consumer",
    f"avformat:{output_path}",
    f"vcodec={settings.video_codec}",
    f"acodec={settings.audio_codec}",
    f"crf={settings.crf}",
    f"preset={settings.preset}",
    "movflags=+faststart",
]
```

Capture output and write `generated/kdenlive_render.log`.

- [ ] **Step 4: Implement ffprobe validation**

Locate `ffprobe.exe` beside configured/system FFmpeg using `shutil.which("ffprobe")`. Execute:

```powershell
ffprobe -v error -show_streams -show_format -of json output.mp4
```

Validate:

- output exists and is non-empty;
- exactly one or more video streams;
- video width and height match config;
- duration is positive;
- audio stream exists when the timeline contains audio.

Normalize successful probe data to:

```python
{
    "video": {"width": 1080, "height": 1920, "codec_name": "h264"},
    "audio": {"present": True, "codec_name": "aac"},
    "duration": 2.0,
}
```

Raise `RuntimeError` on a nonzero `melt` exit. Return `valid=False` for probe mismatches while preserving the MP4 and logs.

- [ ] **Step 5: Add failure preservation tests**

Test that:

- nonzero melt exit leaves the `.kdenlive` project untouched;
- probe failure does not delete the MP4;
- log includes command, stdout, stderr, and return code.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_renderer
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add kdenlive_renderer.py tests/test_kdenlive_renderer.py
git commit -m "feat: render and validate Kdenlive projects"
```

### Task 9: Add End-to-End Kdenlive Backend Orchestration and Reports

**Files:**
- Modify: `kdenlive_backend.py`
- Modify: `draft_generator.py`
- Modify: `tests/test_kdenlive_backend.py`
- Modify: `tests/test_kdenlive_cli.py`

- [ ] **Step 1: Write failing orchestration tests**

Add tests using fake runtime and renderer:

```python
def test_generate_kdenlive_writes_project_mp4_and_report(self):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        input_path = root / "content.txt"
        input_path.write_text("第一段内容", encoding="utf-8")
        runtime = FakeRuntime(root)
        renderer = FakeRenderer()

        result = generate_kdenlive(
            config=_config(root),
            input_path=input_path,
            assets_dir=root / "assets",
            operation_plan=OperationPlan([], []),
            execution_result=_empty_execution_result(),
            output_dir=root / "output",
            generated_dir=root / "generated",
            runtime=runtime,
            renderer=renderer,
        )

        self.assertTrue(result.project_path.exists())
        self.assertTrue(result.video_path.exists())
        report = json.loads((root / "generated" / "kdenlive_report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["route"], "kdenlive")
        self.assertEqual(report["runtime"]["version"], "26.04.2")
```

Add a CLI test patching `generate_kdenlive()` and asserting:

- default route calls Kdenlive;
- `kdenlive-project` passes `render=False`;
- `direct` still calls `render_direct_video()`;
- `jianying` still calls `build_draft()`;
- default route never imports or calls `jianying_ui`.

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_backend tests.test_kdenlive_cli
```

Expected: FAIL because `generate_kdenlive()` and CLI routing are incomplete.

- [ ] **Step 3: Implement `generate_kdenlive()`**

The orchestration order is:

```python
input_text = read_input_text(input_path)
cards = split_into_cards(input_text, config.max_chars_per_card)
assets = ensure_assets(assets_dir, generated_dir, config.resolution, config.default_background_color)
runtime_paths = runtime.resolve()
catalog = query_effect_catalog(runtime_paths.melt_exe)
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
render_settings = RenderSettings(
    width=config.resolution[0],
    height=config.resolution[1],
    fps=config.render_fps,
    video_codec=config.render_video_codec,
    audio_codec=config.render_audio_codec,
    crf=config.render_crf,
    preset=config.render_preset,
)
render_result = (
    renderer.render(project_path, video_path, render_settings, expect_audio=bool(execution_result.resolved_audio_path))
    if render
    else None
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
)
```

Return:

```python
@dataclass(frozen=True)
class KdenliveBackendResult:
    project_path: Path
    video_path: Path | None
    report_path: Path
    mapping_report: list[dict]
    render_result: RenderResult | None
```

- [ ] **Step 4: Implement unique output paths**

Use the same eight-character SHA-256 content suffix as existing draft/MP4 names:

```python
def make_kdenlive_output_paths(base_name: str, content: str, output_dir: Path) -> tuple[Path, Path]:
    suffix = hashlib.sha256(content.encode("utf-8")).hexdigest()[:8]
    stem = f"{base_name}_{suffix}"
    return output_dir / f"{stem}.kdenlive", output_dir / f"{stem}.mp4"
```

- [ ] **Step 5: Write `kdenlive_report.json` and final decision**

The report must include:

```json
{
  "route": "kdenlive",
  "content_hash": "8 hex characters",
  "runtime": {
    "version": "26.04.2",
    "root": "D:/zx/剪辑/generated/runtime/kdenlive-26.04.2",
    "kdenlive": "D:/zx/剪辑/generated/runtime/kdenlive-26.04.2/bin/kdenlive.exe",
    "melt": "D:/zx/剪辑/generated/runtime/kdenlive-26.04.2/bin/melt.exe"
  },
  "project_path": "D:/zx/剪辑/output/web_transition_video_7382d461.kdenlive",
  "video_path": "D:/zx/剪辑/output/web_transition_video_7382d461.mp4",
  "markers": [],
  "assets": [],
  "operations": [],
  "unsupported": [],
  "render": {}
}
```

Write `generated/final_decision.txt` as:

```text
route=kdenlive
project=D:/zx/剪辑/output/web_transition_video_7382d461.kdenlive
video=D:/zx/剪辑/output/web_transition_video_7382d461.mp4
unsupported=0
```

For project-only runs, write `video=not-rendered`.

- [ ] **Step 6: Replace the primary CLI branch**

In `main()`:

1. Parse and prepare operations exactly once.
2. Route `kdenlive` and `kdenlive-project` before direct/Jianying branches.
3. Print both project and MP4 paths.
4. Return immediately after the Kdenlive backend.
5. Do not generate `ui_task_plan.json` unless `--route ui-only` or `--route jianying` needs it.

- [ ] **Step 7: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_backend tests.test_kdenlive_cli tests.test_capability_routing tests.test_ui_only_route
```

Expected: PASS.

- [ ] **Step 8: Commit**

```powershell
git add kdenlive_backend.py draft_generator.py tests/test_kdenlive_backend.py tests/test_kdenlive_cli.py
git commit -m "feat: make Kdenlive the default backend"
```

### Task 10: Update Configuration, Documentation, and Dependency Boundaries

**Files:**
- Modify: `config.example.json`
- Modify: `config.json`
- Modify: `README.md`
- Modify: `requirements.txt`
- Test: `tests/test_kdenlive_cli.py`

- [ ] **Step 1: Add a failing configuration-example test**

Add:

```python
def test_example_config_contains_supported_kdenlive_defaults(self):
    example = json.loads(Path("config.example.json").read_text(encoding="utf-8"))
    self.assertEqual(example["backend"], "kdenlive")
    self.assertEqual(example["kdenlive_version"], "26.04.2")
    self.assertTrue(example["auto_download_kdenlive"])
    self.assertEqual(example["render_video_codec"], "libx264")
    self.assertEqual(example["render_audio_codec"], "aac")
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_cli.KdenliveCliTests.test_example_config_contains_supported_kdenlive_defaults
```

Expected: FAIL because the fields are absent.

- [ ] **Step 3: Update both configuration files**

Add:

```json
"backend": "kdenlive",
"kdenlive_version": "26.04.2",
"kdenlive_runtime_dir": "generated/runtime",
"auto_download_kdenlive": true,
"render_video_codec": "libx264",
"render_audio_codec": "aac",
"render_crf": 20,
"render_preset": "medium",
"render_fps": 30,
"render_with_unsupported_operations": true
```

Keep the current `draft_folder` and `ui_automation_enabled` values in the user's `config.json` for legacy Jianying routes.

- [ ] **Step 4: Rewrite the README primary flow**

Document:

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations
```

Expected outputs:

```text
output/web_transition_video_7382d461.kdenlive
output/web_transition_video_7382d461.mp4
generated/kdenlive_report.json
generated/kdenlive_render.log
generated/final_decision.txt
```

Explain that `7382d461` is an example eight-character content hash and changes with the input.

Include:

- first run downloads about 127.3 MB;
- runtime is cached under `generated/runtime`;
- Kdenlive route does not activate Jianying;
- `--route kdenlive-project` skips rendering;
- `--route jianying` and `--route ui-only` are legacy fallbacks;
- exact/approximated/unsupported report meanings.

- [ ] **Step 5: Clarify dependency boundaries**

Keep `pyJianYingDraft` in `requirements.txt` while Jianying fallback remains. Add comments through a companion `requirements-kdenlive.txt` only if installation of the legacy dependency becomes a problem; otherwise avoid splitting dependencies in this change.

- [ ] **Step 6: Run focused tests**

Run:

```powershell
uv run python -m unittest tests.test_kdenlive_cli
```

Expected: PASS.

- [ ] **Step 7: Commit**

```powershell
git add config.example.json config.json README.md requirements.txt tests/test_kdenlive_cli.py
git commit -m "docs: make Kdenlive the primary workflow"
```

### Task 11: Add Real Runtime Integration Verification

**Files:**
- Create: `tests/test_kdenlive_integration.py`
- Modify: `kdenlive_runtime.py`
- Modify: `kdenlive_renderer.py`

- [ ] **Step 1: Write an opt-in integration test**

Create:

```python
import os
import tempfile
import unittest
from pathlib import Path

from draft_generator import DEFAULT_CONFIG, GeneratorConfig, OperationPlan
from kdenlive_backend import generate_kdenlive
from operation_executor import ExecutionResult


def integration_config(root: Path) -> GeneratorConfig:
    return GeneratorConfig(
        draft_folder=None,
        draft_name="integration_video",
        resolution=(1080, 1920),
        segment_duration_sec=1.0,
        max_chars_per_card=72,
        fallback_transitions=list(DEFAULT_CONFIG["fallback_transitions"]),
        default_background_color="#000000",
        audio_asset=None,
        sticker_manifest=root / "stickers.json",
        effect_manifest=root / "effects.json",
        filter_manifest=root / "filters.json",
        ui_automation_enabled=False,
        auto_generate_missing_assets=True,
        default_beat_bpm=120,
        placeholder_stickers_enabled=True,
        unique_draft_name_by_content=True,
        backend="kdenlive",
        kdenlive_version="26.04.2",
        kdenlive_runtime_dir=root / "generated" / "runtime",
        auto_download_kdenlive=True,
        render_video_codec="libx264",
        render_audio_codec="aac",
        render_crf=20,
        render_preset="medium",
        render_fps=30,
        render_with_unsupported_operations=True,
    )


def empty_execution_result() -> ExecutionResult:
    return ExecutionResult(
        report=[],
        warnings=[],
        marker_times=[],
        sticker_assets=[],
        random_offsets=[],
    )


@unittest.skipUnless(
    os.environ.get("RUN_KDENLIVE_INTEGRATION") == "1",
    "set RUN_KDENLIVE_INTEGRATION=1 to download and render with Kdenlive",
)
class KdenliveIntegrationTests(unittest.TestCase):
    def test_download_build_render_and_probe_vertical_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "content.txt"
            input_path.write_text("测试片段", encoding="utf-8")

            result = generate_kdenlive(
                config=integration_config(root),
                input_path=input_path,
                assets_dir=root / "assets",
                operation_plan=OperationPlan([], []),
                execution_result=empty_execution_result(),
                output_dir=root / "output",
                generated_dir=root / "generated",
            )

            self.assertTrue(result.project_path.exists())
            self.assertTrue(result.video_path.exists())
            self.assertTrue(result.render_result.valid)
            self.assertEqual(result.render_result.probe["video"]["width"], 1080)
            self.assertEqual(result.render_result.probe["video"]["height"], 1920)
```

- [ ] **Step 2: Run the normal suite and verify the integration test skips**

Run:

```powershell
uv run python -m unittest discover -s tests
```

Expected: PASS with one skipped Kdenlive integration test.

- [ ] **Step 3: Run the real integration test**

Run:

```powershell
$env:RUN_KDENLIVE_INTEGRATION="1"
uv run python -m unittest tests.test_kdenlive_integration -v
Remove-Item Env:RUN_KDENLIVE_INTEGRATION
```

Expected:

- the pinned runtime downloads or is reused;
- SHA-256 verification passes;
- `.kdenlive` project is generated;
- `melt.exe` exits `0`;
- ffprobe confirms 1080x1920 H.264 video;
- test passes.

- [ ] **Step 4: Diagnose runtime-specific service mismatches without weakening assertions**

If Kdenlive 26.04.2 exposes different MLT service names:

1. Save `melt -query filters` and `melt -query transitions` output in the generated runtime cache.
2. Update only the ordered candidate tables in `kdenlive_effects.py`.
3. Add the observed service name to unit-test catalogs.
4. Rerun all unit tests and the integration test.

Do not mark an unavailable effect `exact`; use `approximated` or `unsupported`.

- [ ] **Step 5: Open the generated project in Kdenlive**

Run:

```powershell
$report = Get-Content generated/kdenlive_report.json -Encoding UTF8 | ConvertFrom-Json
& $report.runtime.kdenlive $report.project_path
```

If executable discovery found a different relative location, use the path recorded in `generated/kdenlive_report.json`.

Manual acceptance:

- project opens without a corruption warning;
- main media appears on the timeline;
- audio track is present when expected;
- sticker overlays appear;
- project resolution is 1080x1920;
- nested sequences open from the timeline.

- [ ] **Step 6: Commit**

```powershell
git add tests/test_kdenlive_integration.py kdenlive_runtime.py kdenlive_renderer.py kdenlive_effects.py
git commit -m "test: verify real Kdenlive rendering"
```

### Task 12: Final Regression, Security, and Deliverable Verification

**Files:**
- Review: all changed files
- Modify only if verification reveals a defect.

- [ ] **Step 1: Run syntax compilation**

```powershell
uv run python -m py_compile generate_draft.py draft_generator.py operation_executor.py video_renderer.py kdenlive_runtime.py kdenlive_timeline.py kdenlive_effects.py kdenlive_project.py kdenlive_renderer.py kdenlive_backend.py
```

Expected: exit code `0`, no output.

- [ ] **Step 2: Run the complete unit suite**

```powershell
uv run python -m unittest discover -s tests -v
```

Expected: all non-integration tests pass; the real Kdenlive integration test is skipped unless enabled.

- [ ] **Step 3: Run dependency audit**

```powershell
uv run python -m pip install pip-audit
uv run python -m pip_audit -r requirements.txt
```

Expected: no known vulnerable dependency in the resolved environment. If an advisory affects only the legacy Jianying route, document it explicitly rather than hiding it.

- [ ] **Step 4: Review filesystem and network boundaries**

Verify in code and tests:

- download host is exactly `download.kde.org`;
- archive size and SHA-256 are checked before execution;
- extraction stays under `generated/runtime`;
- no command uses `shell=True`;
- no output path can escape configured output/generated directories;
- a failed render never deletes the project or existing valid MP4;
- default route never launches Jianying.

- [ ] **Step 5: Run formatting and diff checks**

```powershell
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; only intended files are changed.

- [ ] **Step 6: Run the user command**

```powershell
uv run python generate_draft.py --config config.json --input input/content.txt --assets assets --mode operations --markers audio --seed 42
```

Expected:

- Kdenlive runtime is downloaded or reused;
- project path is printed;
- MP4 path is printed;
- `generated/kdenlive_report.json` exists;
- `generated/final_decision.txt` starts with `route=kdenlive`;
- no Jianying process/window is launched.

- [ ] **Step 7: Inspect final reports**

Run:

```powershell
Get-Content generated/kdenlive_report.json -Encoding UTF8
Get-Content generated/final_decision.txt -Encoding UTF8
```

Confirm every parsed operation has one of `exact`, `approximated`, or `unsupported`, and no operation silently disappears.

- [ ] **Step 8: Commit final verification fixes**

If verification required changes:

```powershell
git add -- kdenlive_runtime.py kdenlive_timeline.py kdenlive_effects.py kdenlive_project.py kdenlive_renderer.py kdenlive_backend.py draft_generator.py operation_executor.py tests/test_kdenlive_runtime.py tests/test_kdenlive_timeline.py tests/test_kdenlive_effects.py tests/test_kdenlive_project.py tests/test_kdenlive_renderer.py tests/test_kdenlive_backend.py tests/test_kdenlive_cli.py tests/test_kdenlive_integration.py README.md config.example.json requirements.txt .gitignore
git commit -m "fix: complete Kdenlive backend verification"
```

If no fixes were required, do not create an empty commit.

## Completion Criteria

Implementation is complete only when:

1. The default CLI route is `kdenlive`.
2. A missing runtime is downloaded from the pinned official URL and verified against size `133466947` and SHA-256 `4f5a9167a65fa7df411ca6655fa826f0d9ec502ddd157b3ddbf70cd77398dff4`.
3. Different input content produces different `.kdenlive` and `.mp4` names.
4. The generated project contains real timeline entries, audio, markers, stickers, effects, transitions, copies, and nested sequences when requested.
5. Operations mode does not place tutorial instruction text into the video.
6. `melt.exe` renders an MP4 and ffprobe validates it.
7. Unsupported effects are reported and never presented as successfully executed.
8. The existing direct and Jianying fallback routes remain callable explicitly.
9. The normal test suite passes and the real Kdenlive integration test passes when enabled.
