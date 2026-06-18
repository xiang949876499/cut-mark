from __future__ import annotations

import copy
import json
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


REQUIRED_BINDINGS = ["video_path", "person_image", "background_image", "output_prefix"]
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


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

    def submit_and_wait(
        self,
        workflow: dict[str, Any],
        *,
        timeout_sec: float,
        poll_interval: float = 1.0,
    ) -> ComfyPromptResult:
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


def find_video_outputs(history: dict[str, Any]) -> list[str]:
    outputs: list[str] = []
    for prompt in history.values():
        for node_output in prompt.get("outputs", {}).values():
            for key in ["gifs", "videos"]:
                for item in node_output.get(key, []):
                    filename = item.get("filename")
                    if filename and Path(filename).suffix.lower() in VIDEO_EXTENSIONS:
                        outputs.append(filename)
    return outputs


def _request_json(method: str, url: str, payload: dict[str, Any] | None = None, timeout: float = 30) -> dict[str, Any]:
    data = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))
