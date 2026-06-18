import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from video_replace import load_video_replace_config, main, parse_args


class VideoReplaceCliTests(unittest.TestCase):
    def test_parse_args_has_comfy_default(self):
        args = parse_args(["--video", "input.mp4", "--refs", "refs", "--workflow", "workflow.json"])

        self.assertEqual(args.comfy_url, "http://127.0.0.1:8188")
        self.assertEqual(args.output_dir, "output")

    def test_load_video_replace_config_reads_nested_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "comfy_url": "http://localhost:9000",
                        "comfy_workflow_bindings": {"video_path": {"node": "1", "field": "path"}},
                        "scene_detection": {
                            "backend": "ffmpeg",
                            "threshold": 18,
                            "ffmpeg_scene_threshold": 0.25,
                            "min_scene_duration_sec": 1.2,
                        },
                        "video_replace": {"preserve_source_audio": False, "skip_existing_processed": False},
                    }
                ),
                encoding="utf-8",
            )

            config = load_video_replace_config(path, comfy_url_override=None)

        self.assertEqual(config.comfy_url, "http://localhost:9000")
        self.assertEqual(config.scene_backend, "ffmpeg")
        self.assertEqual(config.scene_threshold, 18)
        self.assertEqual(config.ffmpeg_scene_threshold, 0.25)
        self.assertEqual(config.min_scene_duration_sec, 1.2)
        self.assertFalse(config.preserve_source_audio)
        self.assertFalse(config.skip_existing_processed)

    def test_main_calls_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "input.mp4"
            refs = root / "refs"
            workflow = root / "workflow.json"
            video.write_bytes(b"video")
            refs.mkdir()
            workflow.write_text("{}", encoding="utf-8")

            with patch("video_replace.process_video_replacement") as process:
                process.return_value.output_path = root / "out.mp4"
                process.return_value.manifest_path = root / "manifest.json"
                process.return_value.report_path = root / "report.json"
                exit_code = main(
                    [
                        "--video",
                        str(video),
                        "--refs",
                        str(refs),
                        "--workflow",
                        str(workflow),
                        "--output-dir",
                        str(root / "output"),
                    ]
                )

        self.assertEqual(exit_code, 0)
        process.assert_called_once()


if __name__ == "__main__":
    unittest.main()
