# ComfyUI Scene Video Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local pipeline that splits a video by scene, applies scene-numbered person/background references through a replaceable ComfyUI workflow, and merges processed clips into a final MP4.

**Architecture:** Add a separate `video_replace.py` workflow instead of mixing this with the Kdenlive editing backend. The pipeline resolves references, detects/cuts scenes, patches and submits a ComfyUI workflow per scene, records manifests/reports, and merges successful processed clips while preserving source audio by default.

**Tech Stack:** Python 3.12 standard library, FFmpeg/ffprobe, optional PySceneDetect, ComfyUI HTTP API, existing `unittest` suite.

---

## File Structure

New production files:

- `reference_resolver.py`: resolves `scene_NNN_person.*`, `scene_NNN_background.*`, `default_person.*`, and `default_background.*`.
- `video_scene_splitter.py`: probes video metadata, detects scenes with optional PySceneDetect and FFmpeg fallback, normalizes ranges, cuts scene clips.
- `comfy_client.py`: loads workflow JSON, applies binding patches, submits prompts, polls history, resolves generated video outputs.
- `video_replace_pipeline.py`: builds job IDs, writes manifests/reports, coordinates splitting, ComfyUI processing, skip-existing behavior, concat merging, and ffprobe validation.
- `video_replace.py`: CLI entry point.

New tests:

- `tests/test_reference_resolver.py`
- `tests/test_video_scene_splitter.py`
- `tests/test_comfy_client.py`
- `tests/test_video_replace_pipeline.py`
- `tests/test_video_replace_cli.py`

Modified files:

- `config.example.json`: add ComfyUI/video replacement defaults.
- `README.md`: document the new video replacement command and reference naming.
- `.gitignore`: ignore generated video replacement artifacts only if not already covered.
- `requirements.txt`: add `scenedetect` only if the implementation imports it directly; otherwise keep it optional and do not add it.

## Task 1: Scene Reference Resolver

**Files:**
- Create: `reference_resolver.py`
- Create: `tests/test_reference_resolver.py`

- [ ] **Step 1: Write failing resolver tests**

Create `tests/test_reference_resolver.py`:

```python
import tempfile
import unittest
from pathlib import Path

from reference_resolver import MissingReferenceError, ReferenceResolver


class ReferenceResolverTests(unittest.TestCase):
    def test_scene_specific_references_win_over_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default_person.png").write_bytes(b"default person")
            (root / "default_background.png").write_bytes(b"default background")
            (root / "scene_002_person.jpg").write_bytes(b"scene person")
            (root / "scene_002_background.webp").write_bytes(b"scene background")

            resolved = ReferenceResolver(root).resolve(2)

        self.assertEqual(resolved.scene_index, 2)
        self.assertEqual(resolved.person.name, "scene_002_person.jpg")
        self.assertEqual(resolved.background.name, "scene_002_background.webp")

    def test_defaults_are_used_when_scene_specific_files_are_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default_person.png").write_bytes(b"default person")
            (root / "default_background.png").write_bytes(b"default background")

            resolved = ReferenceResolver(root).resolve(5)

        self.assertEqual(resolved.person.name, "default_person.png")
        self.assertEqual(resolved.background.name, "default_background.png")

    def test_missing_reference_names_the_missing_scene_and_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "default_person.png").write_bytes(b"default person")

            with self.assertRaises(MissingReferenceError) as raised:
                ReferenceResolver(root).resolve(3)

        self.assertIn("scene_003", str(raised.exception))
        self.assertIn("background", str(raised.exception))
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_reference_resolver
```

Expected: `ModuleNotFoundError: No module named 'reference_resolver'`.

- [ ] **Step 3: Implement resolver types and lookup rules**

Create `reference_resolver.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp", ".bmp"]


class MissingReferenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SceneReferences:
    scene_index: int
    person: Path
    background: Path


class ReferenceResolver:
    def __init__(self, refs_dir: Path) -> None:
        self.refs_dir = Path(refs_dir)

    def resolve(self, scene_index: int) -> SceneReferences:
        label = f"scene_{scene_index:03d}"
        person = self._find_first([f"{label}_person", "default_person"], label, "person")
        background = self._find_first([f"{label}_background", "default_background"], label, "background")
        return SceneReferences(scene_index=scene_index, person=person, background=background)

    def _find_first(self, stems: list[str], label: str, kind: str) -> Path:
        for stem in stems:
            for suffix in IMAGE_EXTENSIONS:
                candidate = self.refs_dir / f"{stem}{suffix}"
                if candidate.is_file():
                    return candidate
        raise MissingReferenceError(f"Missing {kind} reference for {label}")
```

- [ ] **Step 4: Run resolver tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_reference_resolver
.\.venv\Scripts\python.exe -m py_compile reference_resolver.py
```

Expected: all tests pass.

## Task 2: Scene Detection, Normalization, And Cutting

**Files:**
- Create: `video_scene_splitter.py`
- Create: `tests/test_video_scene_splitter.py`

- [ ] **Step 1: Write failing scene splitter tests**

Create `tests/test_video_scene_splitter.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from video_scene_splitter import SceneRange, VideoMetadata, cut_scene_clips, normalize_scene_ranges, probe_video


class VideoSceneSplitterTests(unittest.TestCase):
    def test_normalize_scene_ranges_covers_full_duration_and_merges_short_ranges(self):
        ranges = normalize_scene_ranges(
            [0.0, 0.3, 2.0, 5.0],
            duration=5.0,
            min_scene_duration=0.8,
        )

        self.assertEqual(ranges, [SceneRange(1, 0.0, 2.0), SceneRange(2, 2.0, 5.0)])

    def test_no_cut_points_returns_single_scene(self):
        ranges = normalize_scene_ranges([], duration=4.2, min_scene_duration=0.8)

        self.assertEqual(ranges, [SceneRange(1, 0.0, 4.2)])

    def test_probe_video_reads_ffprobe_json(self):
        def fake_runner(command, **kwargs):
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": json.dumps(
                        {
                            "streams": [
                                {
                                    "codec_type": "video",
                                    "width": 1920,
                                    "height": 1080,
                                    "avg_frame_rate": "30000/1001",
                                }
                            ],
                            "format": {"duration": "12.5"},
                        }
                    ),
                    "stderr": "",
                },
            )()

        metadata = probe_video(Path("input.mp4"), runner=fake_runner)

        self.assertEqual(metadata.width, 1920)
        self.assertEqual(metadata.height, 1080)
        self.assertAlmostEqual(metadata.fps, 29.97, places=2)
        self.assertEqual(metadata.duration, 12.5)

    def test_cut_scene_clips_uses_ffmpeg_without_shell(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            Path(command[-1]).write_bytes(b"scene")
            return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            output_dir = root / "scenes"
            clips = cut_scene_clips(
                source,
                [SceneRange(1, 0.0, 1.5), SceneRange(2, 1.5, 3.0)],
                output_dir,
                runner=fake_runner,
            )

        self.assertEqual([clip.name for clip in clips], ["scene_001.mp4", "scene_002.mp4"])
        self.assertTrue(all(call[0] == "ffmpeg" for call in calls))
        self.assertTrue(all("-ss" in call for call in calls))
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_video_scene_splitter
```

Expected: `ModuleNotFoundError: No module named 'video_scene_splitter'`.

- [ ] **Step 3: Implement metadata and range normalization**

Create `video_scene_splitter.py` with:

```python
from __future__ import annotations

import json
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
    for start, end in raw:
        if not merged:
            merged.append((start, end))
            continue
        if end - start < min_scene_duration:
            previous_start, _ = merged[-1]
            merged[-1] = (previous_start, end)
        else:
            merged.append((start, end))
    return [SceneRange(index + 1, round(start, 3), round(end, 3)) for index, (start, end) in enumerate(merged)]
```

- [ ] **Step 4: Implement ffprobe and FFmpeg cutting**

Add:

```python
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


def _parse_fps(value: str) -> float:
    fraction = Fraction(value)
    return float(fraction) if fraction.denominator else 0.0
```

- [ ] **Step 5: Add scene detection stubs with fallback behavior**

Add `detect_scene_cut_points()`:

```python
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
```

Implement `_detect_with_pyscenedetect()` using dynamic imports so the dependency remains optional:

```python
def _detect_with_pyscenedetect(video_path: Path, *, threshold: float) -> list[float]:
    from scenedetect import ContentDetector, SceneManager, open_video

    video = open_video(str(video_path))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))
    manager.detect_scenes(video)
    scenes = manager.get_scene_list()
    return [start.get_seconds() for start, _ in scenes[1:]]
```

Implement `_detect_with_ffmpeg()` by parsing `showinfo` timestamps from stderr:

```python
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
```

- [ ] **Step 6: Run splitter tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_video_scene_splitter
.\.venv\Scripts\python.exe -m py_compile video_scene_splitter.py
```

Expected: all tests pass.

## Task 3: ComfyUI Workflow Client

**Files:**
- Create: `comfy_client.py`
- Create: `tests/test_comfy_client.py`

- [ ] **Step 1: Write failing ComfyUI client tests**

Create `tests/test_comfy_client.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from comfy_client import ComfyClient, WorkflowBindingError, patch_workflow


class ComfyClientTests(unittest.TestCase):
    def test_patch_workflow_updates_bound_inputs_only(self):
        workflow = {
            "10": {"inputs": {"video": "old.mp4", "keep": "same"}},
            "11": {"inputs": {"image": "old-person.png"}},
            "12": {"inputs": {"image": "old-bg.png"}},
            "13": {"inputs": {"filename_prefix": "old"}},
        }
        bindings = {
            "video_path": {"node": "10", "field": "video"},
            "person_image": {"node": "11", "field": "image"},
            "background_image": {"node": "12", "field": "image"},
            "output_prefix": {"node": "13", "field": "filename_prefix"},
        }

        patched = patch_workflow(
            workflow,
            bindings,
            video_path=Path("scene.mp4"),
            person_image=Path("person.png"),
            background_image=Path("background.png"),
            output_prefix="scene_001",
        )

        self.assertEqual(patched["10"]["inputs"]["video"], "scene.mp4")
        self.assertEqual(patched["10"]["inputs"]["keep"], "same")
        self.assertEqual(patched["11"]["inputs"]["image"], "person.png")
        self.assertEqual(patched["12"]["inputs"]["image"], "background.png")
        self.assertEqual(patched["13"]["inputs"]["filename_prefix"], "scene_001")

    def test_missing_binding_raises_named_error(self):
        with self.assertRaises(WorkflowBindingError) as raised:
            patch_workflow({}, {}, video_path=Path("scene.mp4"), person_image=Path("p.png"), background_image=Path("b.png"), output_prefix="x")

        self.assertIn("video_path", str(raised.exception))

    def test_submit_and_poll_success(self):
        calls = []

        def fake_request(method, url, payload=None, timeout=30):
            calls.append((method, url, payload))
            if url.endswith("/prompt"):
                return {"prompt_id": "abc"}
            if url.endswith("/history/abc"):
                return {"abc": {"outputs": {"1": {"gifs": [{"filename": "scene_001.mp4", "subfolder": "", "type": "output"}]}}}}
            raise AssertionError(url)

        client = ComfyClient("http://127.0.0.1:8188", request_json=fake_request, sleep=lambda _: None)
        result = client.submit_and_wait({"1": {"inputs": {}}}, timeout_sec=3, poll_interval=0.01)

        self.assertEqual(result.prompt_id, "abc")
        self.assertEqual(result.history["abc"]["outputs"]["1"]["gifs"][0]["filename"], "scene_001.mp4")
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comfy_client
```

Expected: `ModuleNotFoundError: No module named 'comfy_client'`.

- [ ] **Step 3: Implement workflow patching and binding validation**

Create `comfy_client.py`:

```python
from __future__ import annotations

import copy
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REQUIRED_BINDINGS = ["video_path", "person_image", "background_image", "output_prefix"]


class WorkflowBindingError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComfyPromptResult:
    prompt_id: str
    history: dict[str, Any]


def patch_workflow(
    workflow: dict[str, Any],
    bindings: dict[str, dict[str, str]],
    *,
    video_path: Path,
    person_image: Path,
    background_image: Path,
    output_prefix: str,
) -> dict[str, Any]:
    patched = copy.deepcopy(workflow)
    values = {
        "video_path": str(video_path),
        "person_image": str(person_image),
        "background_image": str(background_image),
        "output_prefix": output_prefix,
    }
    for name in REQUIRED_BINDINGS:
        binding = bindings.get(name)
        if not binding:
            raise WorkflowBindingError(f"Missing ComfyUI workflow binding: {name}")
        node = str(binding.get("node", ""))
        field = str(binding.get("field", ""))
        if node not in patched or "inputs" not in patched[node]:
            raise WorkflowBindingError(f"Binding {name} points to missing node: {node}")
        patched[node]["inputs"][field] = values[name]
    return patched
```

- [ ] **Step 4: Implement ComfyClient submit/poll**

Add:

```python
class ComfyClient:
    def __init__(
        self,
        base_url: str,
        *,
        request_json: Callable[..., dict[str, Any]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_json = request_json or _request_json
        self.sleep = sleep

    def submit_and_wait(self, workflow: dict[str, Any], *, timeout_sec: float, poll_interval: float = 1.0) -> ComfyPromptResult:
        submitted = self.request_json("POST", f"{self.base_url}/prompt", {"prompt": workflow}, timeout=30)
        prompt_id = submitted.get("prompt_id")
        if not prompt_id:
            raise RuntimeError("ComfyUI did not return prompt_id")
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            history = self.request_json("GET", f"{self.base_url}/history/{prompt_id}", timeout=30)
            if str(prompt_id) in history:
                return ComfyPromptResult(prompt_id=str(prompt_id), history=history)
            self.sleep(poll_interval)
        raise TimeoutError(f"ComfyUI prompt timed out: {prompt_id}")


def _request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 30) -> dict[str, Any]:
    data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
```

- [ ] **Step 5: Implement output discovery helper**

Add tests and helper:

```python
def find_video_outputs(history: dict[str, Any]) -> list[str]:
    outputs: list[str] = []
    for prompt in history.values():
        for node_output in prompt.get("outputs", {}).values():
            for key in ["gifs", "videos"]:
                for item in node_output.get(key, []):
                    filename = item.get("filename")
                    if filename and Path(filename).suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"}:
                        outputs.append(filename)
    return outputs
```

- [ ] **Step 6: Run client tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_comfy_client
.\.venv\Scripts\python.exe -m py_compile comfy_client.py
```

Expected: all tests pass.

## Task 4: Replacement Pipeline Orchestration

**Files:**
- Create: `video_replace_pipeline.py`
- Create: `tests/test_video_replace_pipeline.py`

- [ ] **Step 1: Write failing pipeline tests**

Create `tests/test_video_replace_pipeline.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from comfy_client import ComfyPromptResult
from reference_resolver import SceneReferences
from video_replace_pipeline import VideoReplaceConfig, build_job_id, process_video_replacement, write_concat_file
from video_scene_splitter import SceneRange, VideoMetadata


class VideoReplacePipelineTests(unittest.TestCase):
    def test_build_job_id_changes_when_source_metadata_changes(self):
        first = build_job_id(Path("source.mp4"), source_size=100, source_mtime_ns=1, workflow_path=Path("workflow.json"), output_path=Path("out.mp4"))
        second = build_job_id(Path("source.mp4"), source_size=101, source_mtime_ns=1, workflow_path=Path("workflow.json"), output_path=Path("out.mp4"))

        self.assertNotEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{12}$")

    def test_write_concat_file_uses_processed_scene_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "processed" / "scene_001.mp4"
            second = root / "processed" / "scene_002.mp4"
            first.parent.mkdir()
            first.write_bytes(b"1")
            second.write_bytes(b"2")

            concat = write_concat_file([first, second], root / "concat.txt")

        self.assertIn("scene_001.mp4", concat.read_text(encoding="utf-8").splitlines()[0])
        self.assertIn("scene_002.mp4", concat.read_text(encoding="utf-8").splitlines()[1])

    def test_pipeline_processes_scenes_and_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            source.write_bytes(b"video")
            workflow = root / "workflow.json"
            workflow.write_text(json.dumps({"10": {"inputs": {"video": ""}}, "11": {"inputs": {"image": ""}}, "12": {"inputs": {"image": ""}}, "13": {"inputs": {"filename_prefix": ""}}}), encoding="utf-8")
            person = root / "refs" / "default_person.png"
            background = root / "refs" / "default_background.png"
            person.parent.mkdir()
            person.write_bytes(b"p")
            background.write_bytes(b"b")

            def fake_probe(path):
                return VideoMetadata(width=640, height=360, fps=30.0, duration=2.0)

            def fake_detect(path):
                return [1.0]

            def fake_cut(path, ranges, output_dir):
                output_dir.mkdir(parents=True, exist_ok=True)
                clips = []
                for scene in ranges:
                    clip = output_dir / f"scene_{scene.index:03d}.mp4"
                    clip.write_bytes(b"scene")
                    clips.append(clip)
                return clips

            class FakeClient:
                def submit_and_wait(self, workflow_data, *, timeout_sec, poll_interval=1.0):
                    return ComfyPromptResult("abc", {"abc": {"outputs": {}}})

            def fake_comfy_output(scene_clip, references, output_path):
                output_path.write_bytes(b"processed")

            def fake_merge(processed, source_audio, output, concat_path):
                output.write_bytes(b"merged")
                concat_path.write_text("concat", encoding="utf-8")
                return ["ffmpeg", "concat"]

            result = process_video_replacement(
                source_video=source,
                refs_dir=root / "refs",
                workflow_path=workflow,
                output_path=root / "output.mp4",
                generated_root=root / "generated",
                config=VideoReplaceConfig(
                    comfy_workflow_bindings={
                        "video_path": {"node": "10", "field": "video"},
                        "person_image": {"node": "11", "field": "image"},
                        "background_image": {"node": "12", "field": "image"},
                        "output_prefix": {"node": "13", "field": "filename_prefix"},
                    }
                ),
                probe=fake_probe,
                detect_cut_points=fake_detect,
                cut_scenes=fake_cut,
                comfy_client=FakeClient(),
                materialize_comfy_output=fake_comfy_output,
                merge_clips=fake_merge,
            )

            manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

        self.assertTrue(result.output_path.exists())
        self.assertEqual(len(manifest["scenes"]), 2)
        self.assertTrue(all(scene["status"] == "succeeded" for scene in manifest["scenes"]))
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_video_replace_pipeline
```

Expected: `ModuleNotFoundError: No module named 'video_replace_pipeline'`.

- [ ] **Step 3: Implement config, result, job ID, and concat writer**

Create `video_replace_pipeline.py`:

```python
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from comfy_client import ComfyClient, patch_workflow
from reference_resolver import ReferenceResolver, SceneReferences
from video_scene_splitter import SceneRange, VideoMetadata, cut_scene_clips, detect_scene_cut_points, normalize_scene_ranges, probe_video


@dataclass(frozen=True)
class VideoReplaceConfig:
    comfy_url: str = "http://127.0.0.1:8188"
    comfy_workflow_bindings: dict[str, dict[str, str]] = field(default_factory=dict)
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


def build_job_id(source_video: Path, *, source_size: int, source_mtime_ns: int, workflow_path: Path, output_path: Path) -> str:
    digest = hashlib.sha256()
    for value in [str(source_video.resolve()), str(source_size), str(source_mtime_ns), str(workflow_path.resolve()), str(output_path.resolve())]:
        digest.update(value.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:12]


def write_concat_file(clips: list[Path], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"file '{clip.resolve().as_posix().replace(\"'\", \"'\\\\''\")}'" for clip in clips]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
```

- [ ] **Step 4: Implement processing orchestration with injectable dependencies**

Add:

```python
def process_video_replacement(
    *,
    source_video: Path,
    refs_dir: Path,
    workflow_path: Path,
    output_path: Path,
    generated_root: Path = Path("generated/video_replace"),
    config: VideoReplaceConfig,
    probe: Callable[[Path], VideoMetadata] = probe_video,
    detect_cut_points: Callable[[Path], list[float]] | None = None,
    cut_scenes: Callable[[Path, list[SceneRange], Path], list[Path]] = cut_scene_clips,
    comfy_client: Any | None = None,
    materialize_comfy_output: Callable[[Path, SceneReferences, Path], None] | None = None,
    merge_clips: Callable[[list[Path], Path, Path, Path], list[str]] | None = None,
) -> VideoReplaceResult:
    source_video = Path(source_video)
    if not source_video.is_file():
        raise FileNotFoundError(source_video)
    workflow = json.loads(Path(workflow_path).read_text(encoding="utf-8"))
    metadata = probe(source_video)
    detector = detect_cut_points or (lambda path: detect_scene_cut_points(path, backend=config.scene_backend, threshold=config.scene_threshold, ffmpeg_scene_threshold=config.ffmpeg_scene_threshold))
    cut_points = detector(source_video)
    ranges = normalize_scene_ranges(cut_points, duration=metadata.duration, min_scene_duration=config.min_scene_duration_sec)
    job_id = build_job_id(
        source_video,
        source_size=source_video.stat().st_size,
        source_mtime_ns=source_video.stat().st_mtime_ns,
        workflow_path=workflow_path,
        output_path=output_path,
    )
    job_dir = generated_root / job_id
    scenes_dir = job_dir / "scenes"
    processed_dir = job_dir / "processed"
    scene_clips = cut_scenes(source_video, ranges, scenes_dir)
    resolver = ReferenceResolver(refs_dir)
    client = comfy_client or ComfyClient(config.comfy_url)
    processed_dir.mkdir(parents=True, exist_ok=True)
    manifest_scenes: list[dict[str, Any]] = []
    report_scenes: list[dict[str, Any]] = []
    for scene_range, scene_clip in zip(ranges, scene_clips):
        references = resolver.resolve(scene_range.index)
        processed = processed_dir / f"scene_{scene_range.index:03d}.mp4"
        status = "succeeded"
        prompt_id = None
        if processed.exists() and config.skip_existing_processed:
            status = "skipped_existing"
        else:
            patched = patch_workflow(
                workflow,
                config.comfy_workflow_bindings,
                video_path=scene_clip,
                person_image=references.person,
                background_image=references.background,
                output_prefix=f"scene_{scene_range.index:03d}",
            )
            result = client.submit_and_wait(patched, timeout_sec=config.comfy_timeout_sec)
            prompt_id = result.prompt_id
            if materialize_comfy_output:
                materialize_comfy_output(scene_clip, references, processed)
            if not processed.exists():
                raise RuntimeError(f"ComfyUI did not produce processed clip: {processed}")
        manifest_scenes.append(_manifest_scene(scene_range, references, scene_clip, processed, status))
        report_scenes.append({"scene_index": scene_range.index, "prompt_id": prompt_id, "status": status})
    if any(scene["status"] == "failed" for scene in manifest_scenes):
        raise RuntimeError("At least one scene failed; merge skipped")
    concat_path = job_dir / "concat.txt"
    merger = merge_clips or merge_processed_clips
    merge_command = merger([Path(scene["processed_clip"]) for scene in manifest_scenes], source_video, output_path, concat_path)
    manifest_path = job_dir / "scene_manifest.json"
    report_path = job_dir / "comfy_report.json"
    _write_json(manifest_path, {"source": str(source_video), "metadata": metadata.__dict__, "scenes": manifest_scenes})
    _write_json(report_path, {"comfy_url": config.comfy_url, "workflow_path": str(workflow_path), "scenes": report_scenes, "merge_command": merge_command, "output_path": str(output_path)})
    return VideoReplaceResult(output_path=output_path, job_dir=job_dir, manifest_path=manifest_path, report_path=report_path)
```

- [ ] **Step 5: Implement merge helper**

Add:

```python
def merge_processed_clips(processed_clips: list[Path], source_video: Path, output_path: Path, concat_path: Path) -> list[str]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_concat_file(processed_clips, concat_path)
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-i",
        str(source_video),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-shortest",
        str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"ffmpeg merge failed: {getattr(result, 'stderr', '')}")
    return command
```

- [ ] **Step 6: Run pipeline tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_video_replace_pipeline
.\.venv\Scripts\python.exe -m py_compile video_replace_pipeline.py
```

Expected: all tests pass.

## Task 5: CLI And Configuration

**Files:**
- Create: `video_replace.py`
- Create: `tests/test_video_replace_cli.py`
- Modify: `config.example.json`

- [ ] **Step 1: Write failing CLI tests**

Create `tests/test_video_replace_cli.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import video_replace


class VideoReplaceCliTests(unittest.TestCase):
    def test_parse_args_defaults_comfy_url(self):
        args = video_replace.parse_args([
            "--video",
            "input/source.mp4",
            "--refs",
            "refs",
            "--workflow",
            "comfy/workflows/realistic_replace.json",
            "--output",
            "output/replaced.mp4",
        ])

        self.assertEqual(args.comfy_url, "http://127.0.0.1:8188")

    def test_main_calls_pipeline_with_cli_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "source.mp4"
            refs = root / "refs"
            workflow = root / "workflow.json"
            output = root / "out.mp4"
            video.write_bytes(b"video")
            refs.mkdir()
            workflow.write_text("{}", encoding="utf-8")

            with patch("video_replace.process_video_replacement") as process:
                process.return_value = type("Result", (), {"output_path": output, "job_dir": root / "generated", "manifest_path": root / "manifest.json", "report_path": root / "report.json"})()
                video_replace.main([
                    "--video",
                    str(video),
                    "--refs",
                    str(refs),
                    "--workflow",
                    str(workflow),
                    "--output",
                    str(output),
                    "--comfy-url",
                    "http://localhost:8188",
                ])

        self.assertEqual(process.call_args.kwargs["source_video"], video)
        self.assertEqual(process.call_args.kwargs["config"].comfy_url, "http://localhost:8188")
```

- [ ] **Step 2: Run and verify RED**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_video_replace_cli
```

Expected: `ModuleNotFoundError: No module named 'video_replace'`.

- [ ] **Step 3: Implement CLI**

Create `video_replace.py`:

```python
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from video_replace_pipeline import VideoReplaceConfig, process_video_replacement


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replace people and backgrounds in a video through scene-based ComfyUI processing.")
    parser.add_argument("--video", required=True, type=Path, help="Source video path")
    parser.add_argument("--refs", required=True, type=Path, help="Scene-numbered reference image directory")
    parser.add_argument("--workflow", required=True, type=Path, help="ComfyUI workflow JSON template")
    parser.add_argument("--output", required=True, type=Path, help="Final MP4 output path")
    parser.add_argument("--config", type=Path, default=Path("config.json"), help="Optional config.json path")
    parser.add_argument("--comfy-url", default=None, help="ComfyUI URL; defaults to config or http://127.0.0.1:8188")
    parser.add_argument("--generated-root", default=Path("generated/video_replace"), type=Path, help="Intermediate job root")
    return parser.parse_args(argv)


def load_video_replace_config(path: Path, comfy_url_override: str | None) -> VideoReplaceConfig:
    raw = {}
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    scene = raw.get("scene_detection", {})
    video_replace = raw.get("video_replace", {})
    return VideoReplaceConfig(
        comfy_url=comfy_url_override or raw.get("comfy_url", "http://127.0.0.1:8188"),
        comfy_workflow_bindings=raw.get("comfy_workflow_bindings", {}),
        scene_backend=scene.get("backend", "auto"),
        scene_threshold=float(scene.get("threshold", 27.0)),
        ffmpeg_scene_threshold=float(scene.get("ffmpeg_scene_threshold", 0.35)),
        min_scene_duration_sec=float(scene.get("min_scene_duration_sec", 0.8)),
        preserve_source_audio=bool(video_replace.get("preserve_source_audio", True)),
        skip_existing_processed=bool(video_replace.get("skip_existing_processed", True)),
        comfy_timeout_sec=float(video_replace.get("comfy_timeout_sec", 1800)),
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    config = load_video_replace_config(args.config, args.comfy_url)
    result = process_video_replacement(
        source_video=args.video,
        refs_dir=args.refs,
        workflow_path=args.workflow,
        output_path=args.output,
        generated_root=args.generated_root,
        config=config,
    )
    print(f"Video saved: {result.output_path}")
    print(f"Job directory: {result.job_dir}")
    print(f"Scene manifest: {result.manifest_path}")
    print(f"ComfyUI report: {result.report_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add config example defaults**

Modify `config.example.json` and add:

```json
"comfy_url": "http://127.0.0.1:8188",
"comfy_workflow_bindings": {
  "video_path": {"node": "VIDEO_INPUT_NODE_ID", "field": "video"},
  "person_image": {"node": "PERSON_IMAGE_NODE_ID", "field": "image"},
  "background_image": {"node": "BACKGROUND_IMAGE_NODE_ID", "field": "image"},
  "output_prefix": {"node": "OUTPUT_NODE_ID", "field": "filename_prefix"}
},
"scene_detection": {
  "backend": "auto",
  "threshold": 27.0,
  "ffmpeg_scene_threshold": 0.35,
  "min_scene_duration_sec": 0.8
},
"video_replace": {
  "preserve_source_audio": true,
  "skip_existing_processed": true,
  "comfy_timeout_sec": 1800
}
```

- [ ] **Step 5: Run CLI tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_video_replace_cli
.\.venv\Scripts\python.exe -m py_compile video_replace.py
```

Expected: all tests pass.

## Task 6: Docs, Workflow Placeholder, And Verification

**Files:**
- Create: `comfy/workflows/realistic_replace.json`
- Modify: `README.md`
- Modify: `.gitignore`
- Test: all tests

- [ ] **Step 1: Add placeholder workflow**

Create `comfy/workflows/realistic_replace.json`:

```json
{
  "description": "Placeholder ComfyUI workflow. Replace this file with an exported workflow and update comfy_workflow_bindings in config.json.",
  "10": {"inputs": {"video": ""}},
  "11": {"inputs": {"image": ""}},
  "12": {"inputs": {"image": ""}},
  "13": {"inputs": {"filename_prefix": ""}}
}
```

- [ ] **Step 2: Update README**

Add a section:

```markdown
## ComfyUI 场景级人物和背景替换

准备参考图：

```text
refs/
  scene_001_person.png
  scene_001_background.png
  default_person.png
  default_background.png
```

运行：

```powershell
uv run python video_replace.py --video input/source.mp4 --refs refs --workflow comfy/workflows/realistic_replace.json --output output/replaced.mp4
```

默认 ComfyUI 地址是 `http://127.0.0.1:8188`。替换真实工作流后，在 `config.json` 中把 `comfy_workflow_bindings` 的 node/field 改成你的工作流节点。
```

- [ ] **Step 3: Update ignore rules**

Append only if missing:

```gitignore
generated/video_replace/
```

- [ ] **Step 4: Run focused tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_reference_resolver tests.test_video_scene_splitter tests.test_comfy_client tests.test_video_replace_pipeline tests.test_video_replace_cli
```

Expected: all new tests pass.

- [ ] **Step 5: Run full verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m py_compile reference_resolver.py video_scene_splitter.py comfy_client.py video_replace_pipeline.py video_replace.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\pip-audit.exe -r requirements.txt --progress-spinner off --timeout 30
git diff --check
```

Expected:

- Python compile exits `0`.
- Unit suite passes with the existing Kdenlive integration test skipped unless enabled.
- `pip-audit` reports no known vulnerabilities.
- `git diff --check` reports no whitespace errors.

- [ ] **Step 6: Manual dry-run expectation**

With a real source video and a real ComfyUI workflow configured:

```powershell
uv run python video_replace.py --video input/source.mp4 --refs refs --workflow comfy/workflows/realistic_replace.json --output output/replaced.mp4
```

Expected:

- `generated/video_replace/<job_id>/scene_manifest.json` exists.
- `generated/video_replace/<job_id>/comfy_report.json` exists.
- `generated/video_replace/<job_id>/scenes/scene_001.mp4` exists.
- `generated/video_replace/<job_id>/processed/scene_001.mp4` exists after ComfyUI succeeds.
- `output/replaced.mp4` exists after every scene succeeds.

## Completion Criteria

Implementation is complete when:

1. `video_replace.py` accepts a source video, refs directory, workflow JSON, output path, and ComfyUI URL.
2. Reference images resolve by scene number with default fallback.
3. Scene ranges cover the entire video and short scenes are merged.
4. Scene clips are cut into `generated/video_replace/<job_id>/scenes/`.
5. ComfyUI workflows are patched through explicit bindings only.
6. Successful processed scene clips are stored under `processed/`.
7. Existing processed clips can be skipped on rerun.
8. Final MP4 merges all processed clips and preserves original audio by default.
9. Reports trace every scene and every ComfyUI prompt.
10. All new and existing tests pass.
