import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import draft_generator
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

    def test_example_config_contains_supported_kdenlive_defaults(self):
        example = json.loads(Path("config.example.json").read_text(encoding="utf-8"))

        self.assertEqual(example["backend"], "kdenlive")
        self.assertEqual(example["kdenlive_version"], "26.04.2")
        self.assertTrue(example["auto_download_kdenlive"])
        self.assertEqual(example["render_video_codec"], "libx264")
        self.assertEqual(example["render_audio_codec"], "aac")

    def test_default_route_calls_kdenlive_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, input_path, assets = _write_cli_fixture(root)
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch("kdenlive_backend.generate_kdenlive", side_effect=_fake_kdenlive_result) as generate:
                    draft_generator.main(["--config", str(config_path), "--input", str(input_path), "--assets", str(assets)])
            finally:
                os.chdir(old_cwd)

        self.assertTrue(generate.called)
        self.assertTrue(generate.call_args.kwargs["render"])

    def test_kdenlive_project_route_skips_render(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, input_path, assets = _write_cli_fixture(root)
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch("kdenlive_backend.generate_kdenlive", side_effect=_fake_kdenlive_result) as generate:
                    draft_generator.main(
                        [
                            "--config",
                            str(config_path),
                            "--input",
                            str(input_path),
                            "--assets",
                            str(assets),
                            "--route",
                            "kdenlive-project",
                        ]
                    )
            finally:
                os.chdir(old_cwd)

        self.assertFalse(generate.call_args.kwargs["render"])

    def test_direct_route_still_calls_direct_renderer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, input_path, assets = _write_cli_fixture(root)
            output = root / "output" / "demo.mp4"
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch("video_renderer.render_direct_video") as render_direct:
                    render_direct.return_value = type("Result", (), {"output_path": output})()
                    draft_generator.main(
                        [
                            "--config",
                            str(config_path),
                            "--input",
                            str(input_path),
                            "--assets",
                            str(assets),
                            "--route",
                            "direct",
                        ]
                    )
            finally:
                os.chdir(old_cwd)

        render_direct.assert_called_once()

    def test_jianying_route_still_calls_build_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path, input_path, assets = _write_cli_fixture(root, draft_folder=root / "drafts")
            output = root / "output" / "demo.mp4"
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch("video_renderer.render_direct_video") as render_direct, patch("draft_generator.build_draft") as build_draft:
                    render_direct.return_value = type("Result", (), {"output_path": output})()
                    draft_generator.main(
                        [
                            "--config",
                            str(config_path),
                            "--input",
                            str(input_path),
                            "--assets",
                            str(assets),
                            "--route",
                            "jianying",
                        ]
                    )
            finally:
                os.chdir(old_cwd)

        build_draft.assert_called_once()


if __name__ == "__main__":
    unittest.main()


def _write_cli_fixture(root: Path, draft_folder: Path | None = None) -> tuple[Path, Path, Path]:
    assets = root / "assets"
    assets.mkdir()
    input_path = root / "content.txt"
    input_path.write_text("第一段内容", encoding="utf-8")
    config = {
        "draft_name": "demo",
        "resolution": [1080, 1920],
        "segment_duration_sec": 1.0,
        "max_chars_per_card": 72,
        "fallback_transitions": ["叠化"],
        "default_background_color": "#000000",
        "backend": "kdenlive",
        "kdenlive_version": "26.04.2",
        "auto_download_kdenlive": False,
    }
    if draft_folder:
        config["draft_folder"] = str(draft_folder)
    config_path = root / "config.json"
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return config_path, input_path, assets


def _fake_kdenlive_result(**kwargs):
    output_dir = kwargs["output_dir"]
    generated_dir = kwargs["generated_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_dir.mkdir(parents=True, exist_ok=True)
    project = output_dir / "demo.kdenlive"
    video = output_dir / "demo.mp4" if kwargs.get("render", True) else None
    project.write_text("<mlt/>", encoding="utf-8")
    if video:
        video.write_bytes(b"mp4")
    report = generated_dir / "kdenlive_report.json"
    report.write_text("{}", encoding="utf-8")
    return type("Result", (), {"project_path": project, "video_path": video, "report_path": report, "render_result": None})()
