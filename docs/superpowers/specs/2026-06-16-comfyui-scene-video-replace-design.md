# ComfyUI Scene-Based Video Replacement Design

## Goal

Add a local video replacement pipeline that takes one source video, splits it at scene boundaries, replaces the person and background in each scene through a user-supplied ComfyUI workflow, and merges the processed scene clips into a final MP4.

The target visual style is realistic/live-action rather than anime or heavy stylization. The pipeline should favor stability, traceability, and rerun-friendly intermediate artifacts over a one-shot black box.

## Non-Goals

- Do not build or hardcode a specific ComfyUI graph for identity/background replacement in v1.
- Do not train models, manage LoRAs, or download ComfyUI models.
- Do not edit through Jianying UI or Kdenlive for this feature.
- Do not delete intermediate clips after success; they are useful for manual inspection and reruns.
- Do not promise perfect identity consistency. The pipeline supplies stable scene segmentation and references; final quality depends on the ComfyUI workflow.

## User Interface

Create a new script entry point:

```powershell
uv run python video_replace.py `
  --video input/source.mp4 `
  --refs refs `
  --workflow comfy/workflows/realistic_replace.json `
  --output output/replaced.mp4 `
  --comfy-url http://127.0.0.1:8188
```

`--comfy-url` defaults to `http://127.0.0.1:8188`.

Reference assets are matched by scene number:

```text
refs/
  scene_001_person.png
  scene_001_background.png
  scene_002_person.png
  scene_002_background.png
  default_person.png
  default_background.png
```

For scene `N`, the resolver uses:

1. `scene_NNN_person.*` and `scene_NNN_background.*`
2. `default_person.*` and `default_background.*`
3. fail the scene with a clear report entry if either reference is still missing

Supported reference image extensions are `.png`, `.jpg`, `.jpeg`, `.webp`, and `.bmp`.

## Output Layout

Each run creates a deterministic job folder under `generated/video_replace/` using the source video path, size, modified time, workflow path, and output target hash:

```text
generated/video_replace/<job_id>/
  scenes/
    scene_001.mp4
    scene_002.mp4
  processed/
    scene_001.mp4
    scene_002.mp4
  scene_manifest.json
  comfy_report.json
  concat.txt
output/replaced.mp4
```

The manifest records source video metadata, scene start/end times, chosen references, source scene clip path, processed clip path, and status.

## Architecture

The implementation should be split into focused modules:

- `video_replace.py`: CLI entry point and top-level orchestration.
- `video_scene_splitter.py`: scene detection and FFmpeg-based cutting.
- `reference_resolver.py`: scene-number reference image matching.
- `comfy_client.py`: ComfyUI prompt submission, polling, and output discovery.
- `video_replace_pipeline.py`: job manifest, rerun behavior, and final concatenation.

The modules should use plain standard-library data structures where possible. If `PySceneDetect` is available, use it for scene detection. If it is not installed or fails, fall back to FFmpeg scene detection.

## Scene Detection

Primary strategy:

- Use `PySceneDetect` content-aware detection.
- Default threshold should be conservative enough to avoid over-cutting fast motion.
- Record detected scene boundaries in seconds.

Fallback strategy:

- Use FFmpeg `select=gt(scene,threshold)` to detect scene boundaries.
- Convert detected timestamps into contiguous scene ranges.

Guards:

- Always include a scene from `0.0` to video duration if no cuts are detected.
- Merge very short scenes below a configurable minimum duration, default `0.8s`.
- Scene ranges must be contiguous and cover the whole source video without gaps.

## Cutting And Merging

Cut source scenes with FFmpeg:

- Prefer stream copy for speed when boundaries permit.
- Fall back to re-encoding when stream copy fails.
- Preserve source audio in scene clips only if ComfyUI workflow expects video with audio; final audio handling is controlled separately.

Final merge:

- Concatenate processed scene clips in manifest order using FFmpeg concat demuxer.
- By default, preserve the original source audio by mapping it onto the final merged video.
- If processed clips already contain desired audio, expose a later option to keep processed audio, but v1 default is source audio.

The final output should be validated with `ffprobe`: file exists, has at least one video stream, duration is positive, and duration is close to the source duration.

## ComfyUI Workflow Integration

The user supplies `comfy/workflows/realistic_replace.json`. The pipeline treats it as a template and updates configured inputs per scene:

- source scene video path
- person reference image path
- background reference image path
- output directory or filename prefix

Because ComfyUI workflow node IDs vary, v1 should support a small mapping config in `config.json` or a sidecar JSON:

```json
{
  "comfy_url": "http://127.0.0.1:8188",
  "comfy_workflow_bindings": {
    "video_path": {"node": "VIDEO_INPUT_NODE_ID", "field": "video"},
    "person_image": {"node": "PERSON_IMAGE_NODE_ID", "field": "image"},
    "background_image": {"node": "BACKGROUND_IMAGE_NODE_ID", "field": "image"},
    "output_prefix": {"node": "OUTPUT_NODE_ID", "field": "filename_prefix"}
  }
}
```

If bindings are missing, the CLI should stop with a clear message explaining which binding is required. It should not guess arbitrary node IDs.

ComfyUI execution flow:

1. Load workflow JSON.
2. Patch bound node inputs for one scene.
3. Submit prompt to `/prompt`.
4. Poll `/history/{prompt_id}` until complete or timeout.
5. Locate generated video output from history metadata or configured output path.
6. Copy or move the result to `processed/scene_NNN.mp4`.

Failures are recorded per scene. Successful scenes are not rerun by default if their processed clip already exists and the manifest input hash matches.

## Error Handling

- Missing source video: fail before creating a job.
- Missing references for a scene: mark that scene failed and stop before ComfyUI submission.
- ComfyUI unavailable: fail fast with a connection error and suggest checking `--comfy-url`.
- Workflow binding missing: fail fast and name the missing binding.
- Scene processing failure: write `comfy_report.json`, keep successful processed clips, and do not merge unless all scenes succeeded.
- Merge failure: keep all processed clips and concat file for diagnosis.

## Reporting

`scene_manifest.json` includes:

- source video path, duration, fps, resolution
- scene index, start, end, duration
- reference image paths
- source scene clip path
- processed clip path
- status: `pending | skipped_existing | processing | succeeded | failed`
- error message when failed

`comfy_report.json` includes:

- ComfyUI URL
- workflow path and workflow hash
- per-scene prompt IDs
- per-scene elapsed time
- final merge command
- final output path and ffprobe validation

## Testing

Unit tests:

- Reference resolver chooses scene-specific images before defaults.
- Missing references produce clear failures.
- Scene range normalization covers the full source duration and merges very short scenes.
- ComfyUI client patches only configured workflow bindings.
- ComfyUI polling handles success, failure, and timeout.
- Manifest skips already processed scenes when inputs match.
- Concat file is written in scene order.

Integration-style tests with fake runners:

- FFmpeg commands are constructed without `shell=True`.
- A fake ComfyUI server response produces processed scene outputs.
- Final merge is skipped when any scene failed.

Manual validation:

- Run against a short video with 2-3 obvious scene cuts.
- Verify `scene_manifest.json` scene boundaries.
- Replace `comfy/workflows/realistic_replace.json` with the user's real workflow.
- Confirm final MP4 keeps source audio and all scenes appear in order.

## Open Configuration Defaults

Suggested defaults:

```json
{
  "comfy_url": "http://127.0.0.1:8188",
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
}
```

These values can live in `config.json`, but `video_replace.py` should also accept CLI overrides for the source video, refs directory, workflow path, output path, and ComfyUI URL.

## Acceptance Criteria

- A user can place reference images in `refs/` using scene-number names.
- A source video is split at scene boundaries into `generated/video_replace/<job_id>/scenes/`.
- Each scene uses the matching person/background references or defaults.
- Each scene is submitted to ComfyUI using a replaceable workflow JSON.
- Processed scenes are merged into one MP4.
- Original audio is preserved by default.
- Reports make every scene status traceable.
- A failed scene does not delete successful scenes.
