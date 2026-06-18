import json
import tempfile
import unittest
from pathlib import Path

from draft_generator import DEFAULT_CONFIG, GeneratorConfig, OperationPlan, ensure_assets
from kdenlive_backend import build_timeline, generate_kdenlive
from kdenlive_renderer import RenderResult
from kdenlive_runtime import RuntimePaths
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
        self.assertEqual(stickers.clips[0].effects[0].service, "qtblend")
        self.assertIn("57.0%", stickers.clips[0].effects[0].properties["rect"])

    def test_black_fallback_assets_can_feed_timeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = ensure_assets(root / "assets", root / "generated", (1080, 1920), "#000000")

            timeline = build_timeline(
                _config(root),
                cards=["第一段"],
                assets=assets,
                operation_plan=OperationPlan([], []),
                execution_result=_empty_execution_result(),
                include_text=False,
            )

        main = next(track for track in timeline.tracks if track.id == "video-main")
        self.assertEqual(main.clips[0].source.name, "default_black.png")

    def test_operations_mode_can_skip_instruction_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            background = root / "background.png"
            background.write_bytes(b"image")

            timeline = build_timeline(
                _config(root),
                cards=["导入背景音乐"],
                assets=[background],
                operation_plan=OperationPlan([], []),
                execution_result=_empty_execution_result(),
                include_text=False,
            )

        self.assertNotIn("text", [track.role for track in timeline.tracks])

    def test_cards_mode_creates_text_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            background = root / "background.png"
            background.write_bytes(b"image")

            timeline = build_timeline(
                _config(root),
                cards=["第一段"],
                assets=[background],
                operation_plan=OperationPlan([], []),
                execution_result=_empty_execution_result(),
                include_text=True,
            )

        text_track = next(track for track in timeline.tracks if track.role == "text")
        self.assertEqual(text_track.clips[0].name, "第一段")

    def test_generate_kdenlive_writes_project_mp4_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "content.txt"
            input_path.write_text("第一段内容", encoding="utf-8")
            runtime = _FakeRuntime(root)
            renderer = _FakeRenderer()

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
            self.assertTrue((root / "generated" / "final_decision.txt").exists())


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


def _empty_execution_result() -> ExecutionResult:
    return ExecutionResult(
        report=[],
        warnings=[],
        marker_times=[],
        sticker_assets=[],
        random_offsets=[],
    )


class _FakeRuntime:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self) -> RuntimePaths:
        runtime = self.root / "runtime"
        runtime.mkdir()
        for name in ["kdenlive.exe", "melt.exe", "ffmpeg.exe", "ffprobe.exe"]:
            (runtime / name).write_bytes(b"")
        return RuntimePaths(
            runtime,
            runtime / "kdenlive.exe",
            runtime / "melt.exe",
            runtime / "ffmpeg.exe",
            runtime / "ffprobe.exe",
        )


class _FakeRenderer:
    def render(self, project_path, output_path, settings, *, expect_audio=False):
        Path(output_path).write_bytes(b"mp4")
        return RenderResult(
            output_path=Path(output_path),
            valid=True,
            command=["melt.exe", str(project_path)],
            melt_returncode=0,
            probe={"video": {"width": settings.width, "height": settings.height}, "audio": {"present": expect_audio}, "duration": 1.0},
            log_path=Path(output_path).parent / "render.log",
        )


if __name__ == "__main__":
    unittest.main()
