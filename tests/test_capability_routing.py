import json
import tempfile
import unittest
from pathlib import Path

from draft_generator import DEFAULT_CONFIG, GeneratorConfig, OperationPlan, TextCard
from operation_executor import ExecutionResult
from video_renderer import (
    choose_route,
    evaluate_capabilities,
    make_render_output_path,
    render_direct_video,
    write_capability_report,
)


class CapabilityRoutingTests(unittest.TestCase):
    def test_ai_beat_is_critical_missing_and_routes_to_jianying(self):
        plan = OperationPlan([{"type": "ai_beat", "line": "AI卡点", "line_index": 1}], [])

        report = evaluate_capabilities(plan)

        self.assertEqual(report["selected_route"], "jianying_ui")
        self.assertEqual(report["items"][0]["status"], "jianying_ui_required")
        self.assertTrue(report["items"][0]["critical"])

    def test_plain_cards_route_to_direct_mp4(self):
        report = evaluate_capabilities(OperationPlan([], []))

        self.assertEqual(report["selected_route"], "direct_mp4")
        self.assertEqual(report["critical_missing"], [])

    def test_copy_and_compound_force_jianying_route(self):
        plan = OperationPlan(
            [
                {"type": "compound_clip", "line": "复合片段", "line_index": 2},
                {"type": "copy", "line": "复制属性", "line_index": 3},
            ],
            [],
        )

        report = evaluate_capabilities(plan)

        self.assertEqual(choose_route(report, requested="auto"), "jianying")
        self.assertEqual([item["operation"] for item in report["critical_missing"]], ["compound_clip", "copy"])

    def test_output_path_uses_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = make_render_output_path("web_transition_video", "first content", Path(tmp))
            second = make_render_output_path("web_transition_video", "second content", Path(tmp))

        self.assertNotEqual(first.name, second.name)
        self.assertRegex(first.name, r"^web_transition_video_[0-9a-f]{8}\.mp4$")

    def test_render_direct_video_writes_report_and_invokes_ffmpeg(self):
        calls = []

        def fake_runner(cmd, **kwargs):
            calls.append(cmd)
            Path(cmd[-1]).write_bytes(b"fake mp4")
            return type("Result", (), {"returncode": 0, "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "content.txt"
            input_path.write_text("第一段文字", encoding="utf-8")
            config = _config(root)
            result = render_direct_video(
                config,
                input_path,
                root / "assets",
                OperationPlan([], []),
                _empty_execution_result(),
                output_dir=root / "output",
                generated_dir=root / "generated",
                runner=fake_runner,
            )

            self.assertTrue(result.output_path.exists())
            self.assertTrue((root / "generated" / "render_report.json").exists())
            self.assertEqual(result.route, "direct_mp4")
            self.assertIn("ffmpeg", Path(calls[0][0]).name.lower())

    def test_operations_render_can_hide_instruction_text(self):
        def fake_runner(cmd, **kwargs):
            Path(cmd[-1]).write_bytes(b"fake mp4")
            return type("Result", (), {"returncode": 0, "stderr": ""})()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "content.txt"
            input_path.write_text("添加旋彩光圈贴纸\n右键新建复合片段", encoding="utf-8")

            render_direct_video(
                _config(root),
                input_path,
                root / "assets",
                OperationPlan([], []),
                _empty_execution_result(),
                output_dir=root / "output",
                generated_dir=root / "generated",
                runner=fake_runner,
                include_text=False,
            )

            report = json.loads((root / "generated" / "render_report.json").read_text(encoding="utf-8"))
            self.assertFalse(report["include_text"])

    def test_write_capability_report_saves_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_capability_report({"selected_route": "direct_mp4", "items": []}, Path(tmp))

            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["selected_route"], "direct_mp4")


def _config(root: Path) -> GeneratorConfig:
    return GeneratorConfig(
        draft_folder=root / "drafts",
        draft_name="web_transition_video",
        resolution=(320, 568),
        segment_duration_sec=0.25,
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


if __name__ == "__main__":
    unittest.main()
