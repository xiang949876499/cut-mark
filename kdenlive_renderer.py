from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kdenlive_runtime import RuntimePaths


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
    probe: dict[str, Any]
    log_path: Path


class KdenliveRenderer:
    def __init__(self, runtime: RuntimePaths, *, runner=subprocess.run, log_dir: Path = Path("generated")) -> None:
        self.runtime = runtime
        self.runner = runner
        self.log_dir = Path(log_dir)

    def preflight(self, settings: RenderSettings) -> None:
        consumers = self.runner([str(self.runtime.melt_exe), "-query", "consumers"], capture_output=True, text=True, timeout=120)
        if getattr(consumers, "returncode", 1) != 0 or "avformat" not in getattr(consumers, "stdout", ""):
            raise RuntimeError("Kdenlive/MLT avformat consumer is unavailable")

        ffmpeg = self._ffmpeg_exe()
        encoders = self.runner([str(ffmpeg), "-hide_banner", "-encoders"], capture_output=True, text=True, timeout=120)
        encoder_output = f"{getattr(encoders, 'stdout', '')}\n{getattr(encoders, 'stderr', '')}"
        if getattr(encoders, "returncode", 1) != 0:
            raise RuntimeError("ffmpeg encoder query failed")
        for codec in (settings.video_codec, settings.audio_codec):
            if codec not in encoder_output:
                raise RuntimeError(f"ffmpeg encoder is unavailable: {codec}")

    def render(
        self,
        project_path: Path,
        output_path: Path,
        settings: RenderSettings,
        *,
        expect_audio: bool = False,
    ) -> RenderResult:
        self.preflight(settings)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            str(self.runtime.melt_exe),
            str(project_path),
            "-consumer",
            f"avformat:{output_path}",
            f"vcodec={settings.video_codec}",
            f"acodec={settings.audio_codec}",
            f"crf={settings.crf}",
            f"preset={settings.preset}",
            "movflags=+faststart",
        ]
        result = self.runner(command, capture_output=True, text=True, timeout=900)
        log_path = self._write_log(command, result)
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError(f"Kdenlive render failed: {getattr(result, 'stderr', '')}")

        valid, probe = self._probe_output(output_path, settings, expect_audio=expect_audio)
        return RenderResult(
            output_path=output_path,
            valid=valid,
            command=command,
            melt_returncode=getattr(result, "returncode", 0),
            probe=probe,
            log_path=log_path,
        )

    def _probe_output(self, output_path: Path, settings: RenderSettings, *, expect_audio: bool) -> tuple[bool, dict[str, Any]]:
        if not output_path.exists() or output_path.stat().st_size == 0:
            return False, {"error": "output file is missing or empty"}
        ffprobe = self._ffprobe_exe()
        command = [
            str(ffprobe),
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(output_path),
        ]
        result = self.runner(command, capture_output=True, text=True, timeout=120)
        if getattr(result, "returncode", 1) != 0:
            return False, {"error": getattr(result, "stderr", "")}
        try:
            raw_probe = json.loads(getattr(result, "stdout", "") or "{}")
        except json.JSONDecodeError as exc:
            return False, {"error": f"invalid ffprobe json: {exc}"}
        video_streams = [stream for stream in raw_probe.get("streams", []) if stream.get("codec_type") == "video"]
        audio_streams = [stream for stream in raw_probe.get("streams", []) if stream.get("codec_type") == "audio"]
        duration = float(raw_probe.get("format", {}).get("duration", 0) or 0)
        if not video_streams:
            return False, {"error": "no video stream"}
        video = video_streams[0]
        normalized = {
            "video": {
                "width": int(video.get("width", 0) or 0),
                "height": int(video.get("height", 0) or 0),
                "codec_name": video.get("codec_name"),
            },
            "audio": {
                "present": bool(audio_streams),
                "codec_name": audio_streams[0].get("codec_name") if audio_streams else None,
            },
            "duration": duration,
        }
        valid = (
            normalized["video"]["width"] == settings.width
            and normalized["video"]["height"] == settings.height
            and normalized["duration"] > 0
            and (not expect_audio or normalized["audio"]["present"])
        )
        return valid, normalized

    def _write_log(self, command: list[str], result) -> Path:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = self.log_dir / "kdenlive_render.log"
        log_path.write_text(
            "\n".join(
                [
                    "command=" + json.dumps(command, ensure_ascii=False),
                    f"returncode={getattr(result, 'returncode', None)}",
                    f"stdout={getattr(result, 'stdout', '')}",
                    f"stderr={getattr(result, 'stderr', '')}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return log_path

    def _ffmpeg_exe(self) -> Path | str:
        if self.runtime.ffmpeg_exe:
            return self.runtime.ffmpeg_exe
        found = shutil.which("ffmpeg")
        if not found:
            raise RuntimeError("ffmpeg executable was not found")
        return found

    def _ffprobe_exe(self) -> Path | str:
        if self.runtime.ffprobe_exe:
            return self.runtime.ffprobe_exe
        found = shutil.which("ffprobe")
        if not found:
            raise RuntimeError("ffprobe executable was not found")
        return found
