import tempfile
import unittest
from pathlib import Path

from local_video_text import (
    collect_segment_text,
    extract_audio_to_wav,
    transcribe_video_file,
    write_text_output,
)


class LocalVideoTextTests(unittest.TestCase):
    def test_collect_segment_text_joins_non_empty_segments(self):
        class Segment:
            def __init__(self, text):
                self.text = text

        text = collect_segment_text([Segment(" 第一段 "), Segment(""), Segment("第二段")])

        self.assertEqual(text, "第一段\n第二段")

    def test_write_text_output_creates_parent_directory_and_newline(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "input" / "content.txt"

            returned_path = write_text_output("识别出来的文案", output_path)

            self.assertEqual(returned_path, output_path)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "识别出来的文案\n")

    def test_transcribe_video_file_uses_injected_model_factory(self):
        class FakeModel:
            def transcribe(self, video_path, language):
                self.video_path = video_path
                self.language = language
                return [type("Segment", (), {"text": "本地视频文案"})()], None

        created = {}

        def fake_model_factory(model_name):
            created["model_name"] = model_name
            created["model"] = FakeModel()
            return created["model"]

        def fake_audio_extractor(video_path, audio_path):
            audio_path.write_bytes(b"fake wav")
            return audio_path

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "video.mp4"
            video_path.write_bytes(b"fake video")

            text = transcribe_video_file(
                video_path,
                "base",
                "zh",
                model_factory=fake_model_factory,
                audio_extractor=fake_audio_extractor,
            )

            self.assertEqual(text, "本地视频文案")
            self.assertEqual(created["model_name"], "base")
            self.assertTrue(created["model"].video_path.endswith("audio.wav"))
            self.assertEqual(created["model"].language, "zh")

    def test_extract_audio_to_wav_reports_missing_audio_stream(self):
        def fake_runner(cmd, capture_output, text, timeout):
            return type("Result", (), {"returncode": 1, "stderr": "Output file does not contain any stream"})()

        with tempfile.TemporaryDirectory() as tmp:
            video_path = Path(tmp) / "video.mp4"
            audio_path = Path(tmp) / "audio.wav"
            video_path.write_bytes(b"fake video")

            with self.assertRaisesRegex(RuntimeError, "No audio stream"):
                extract_audio_to_wav(video_path, audio_path, runner=fake_runner)


if __name__ == "__main__":
    unittest.main()
