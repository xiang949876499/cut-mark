import os
import tempfile
import unittest
from pathlib import Path

from draft_generator import DEFAULT_CONFIG, GeneratorConfig, OperationPlan
from kdenlive_backend import generate_kdenlive
from operation_executor import ExecutionResult


def integration_config(root: Path) -> GeneratorConfig:
    return GeneratorConfig(
        draft_folder=None,
        draft_name="integration_video",
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
        auto_download_kdenlive=True,
        render_video_codec="libx264",
        render_audio_codec="aac",
        render_crf=20,
        render_preset="medium",
        render_fps=30,
        render_with_unsupported_operations=True,
    )


def empty_execution_result() -> ExecutionResult:
    return ExecutionResult(
        report=[],
        warnings=[],
        marker_times=[],
        sticker_assets=[],
        random_offsets=[],
    )


@unittest.skipUnless(
    os.environ.get("RUN_KDENLIVE_INTEGRATION") == "1",
    "set RUN_KDENLIVE_INTEGRATION=1 to download and render with Kdenlive",
)
class KdenliveIntegrationTests(unittest.TestCase):
    def test_download_build_render_and_probe_vertical_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "content.txt"
            input_path.write_text("测试片段", encoding="utf-8")

            result = generate_kdenlive(
                config=integration_config(root),
                input_path=input_path,
                assets_dir=root / "assets",
                operation_plan=OperationPlan([], []),
                execution_result=empty_execution_result(),
                output_dir=root / "output",
                generated_dir=root / "generated",
            )

            self.assertTrue(result.project_path.exists())
            self.assertTrue(result.video_path.exists())
            self.assertTrue(result.render_result.valid)
            self.assertEqual(result.render_result.probe["video"]["width"], 1080)
            self.assertEqual(result.render_result.probe["video"]["height"], 1920)


if __name__ == "__main__":
    unittest.main()
