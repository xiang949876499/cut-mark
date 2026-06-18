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
                return type("Result", (), {"returncode": 0, "stdout": "libx264\naac\n", "stderr": ""})()
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
                                {"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "h264"},
                                {"codec_type": "audio", "codec_name": "aac"},
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
            result = KdenliveRenderer(runtime, runner=fake_runner, log_dir=root / "generated").render(
                project,
                root / "demo.mp4",
                RenderSettings(width=1080, height=1920, fps=30),
                expect_audio=True,
            )

        self.assertTrue(result.valid)
        render_call = next(command for command in calls if "-consumer" in command)
        self.assertEqual(Path(render_call[0]).name, "melt.exe")
        self.assertIn("vcodec=libx264", render_call)
        self.assertIn("acodec=aac", render_call)

    def test_melt_failure_preserves_project_and_writes_log(self):
        def fake_runner(command, **kwargs):
            executable = Path(command[0]).name.lower()
            if executable == "melt.exe" and command[1:] == ["-query", "consumers"]:
                return type("Result", (), {"returncode": 0, "stdout": "avformat\n", "stderr": ""})()
            if executable == "ffmpeg.exe":
                return type("Result", (), {"returncode": 0, "stdout": "libx264\naac\n", "stderr": ""})()
            return type("Result", (), {"returncode": 2, "stdout": "out", "stderr": "boom"})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo.kdenlive"
            project.write_text("<mlt/>", encoding="utf-8")
            runtime = RuntimePaths(root, root / "kdenlive.exe", root / "melt.exe", root / "ffmpeg.exe", root / "ffprobe.exe")
            renderer = KdenliveRenderer(runtime, runner=fake_runner, log_dir=root / "generated")

            with self.assertRaises(RuntimeError):
                renderer.render(project, root / "demo.mp4", RenderSettings(width=1080, height=1920, fps=30))

            project_text = project.read_text(encoding="utf-8")
            log = (root / "generated" / "kdenlive_render.log").read_text(encoding="utf-8")

        self.assertEqual(project_text, "<mlt/>")
        self.assertIn("returncode=2", log)
        self.assertIn("stderr=boom", log)

    def test_probe_failure_preserves_mp4_and_returns_invalid(self):
        def fake_runner(command, **kwargs):
            executable = Path(command[0]).name.lower()
            if executable == "melt.exe" and command[1:] == ["-query", "consumers"]:
                return type("Result", (), {"returncode": 0, "stdout": "avformat\n", "stderr": ""})()
            if executable == "ffmpeg.exe":
                return type("Result", (), {"returncode": 0, "stdout": "libx264\naac\n", "stderr": ""})()
            if executable == "melt.exe":
                consumer = next(item for item in command if item.startswith("avformat:"))
                Path(consumer.split(":", 1)[1]).write_bytes(b"mp4")
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "bad probe"})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "demo.kdenlive"
            project.write_text("<mlt/>", encoding="utf-8")
            runtime = RuntimePaths(root, root / "kdenlive.exe", root / "melt.exe", root / "ffmpeg.exe", root / "ffprobe.exe")
            result = KdenliveRenderer(runtime, runner=fake_runner, log_dir=root / "generated").render(
                project,
                root / "demo.mp4",
                RenderSettings(width=1080, height=1920, fps=30),
            )

            self.assertTrue((root / "demo.mp4").exists())
            self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()
