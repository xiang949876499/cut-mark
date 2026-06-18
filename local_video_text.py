#!/usr/bin/env python3
import argparse
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Iterable, Sequence


if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def collect_segment_text(segments: Iterable[object]) -> str:
    lines = []
    for segment in segments:
        text = str(getattr(segment, "text", "")).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def default_model_factory(model_name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("faster-whisper is required. Install it with: python -m pip install -r requirements.txt") from exc
    return WhisperModel(model_name, device="auto", compute_type="auto")


def extract_audio_to_wav(video_path: Path, audio_path: Path, *, runner=subprocess.run) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to extract audio from local videos")

    audio_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "wav",
        str(audio_path),
    ]
    result = runner(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        stderr = getattr(result, "stderr", "") or ""
        if "does not contain any stream" in stderr or "Stream map" in stderr:
            raise RuntimeError(f"No audio stream found in video: {video_path}")
        raise RuntimeError(f"ffmpeg failed to extract audio from {video_path}: {stderr.strip()}")
    return audio_path


def transcribe_video_file(
    video_path: Path,
    model_name: str,
    language: str,
    *,
    model_factory=default_model_factory,
    audio_extractor=extract_audio_to_wav,
) -> str:
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not video_path.is_file():
        raise ValueError(f"Video path is not a file: {video_path}")

    with tempfile.TemporaryDirectory(prefix="local_video_text_") as tmp:
        audio_path = audio_extractor(video_path, Path(tmp) / "audio.wav")
        model = model_factory(model_name)
        segments, _ = model.transcribe(str(audio_path), language=language)
        text = collect_segment_text(segments)
        if not text:
            raise RuntimeError("No speech text was extracted from the local video")
        return text


def write_text_output(text: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text.strip() + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract spoken text from a local video file.")
    parser.add_argument("video", type=Path, help="Local video file path, such as D:/videos/demo.mp4")
    parser.add_argument("--output", type=Path, default=Path("input/content.txt"), help="Output text path")
    parser.add_argument("--model", default="base", help="faster-whisper model name, default: base")
    parser.add_argument("--language", default="zh", help="Speech language code, default: zh")
    parser.add_argument("--print", action="store_true", help="Also print extracted text to stdout")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    try:
        text = transcribe_video_file(args.video, args.model, args.language)
        output_path = write_text_output(text, args.output)
        print(f"Extracted text saved: {output_path}")
        if args.print:
            print()
            print(text)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
