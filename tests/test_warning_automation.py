import json
import tempfile
import unittest
from pathlib import Path

from draft_generator import build_operation_plan, split_into_cards
from operation_executor import OperationExecutor, resolve_effect_or_filter


class WarningAutomationTests(unittest.TestCase):
    def test_utf8_tutorial_operations_are_detected_without_parse_warnings(self):
        text = "\n".join(
            [
                "首先導入背景音樂",
                "點擊AI卡點後選擇採捷拍2",
                "在第7個標記點出進行分割",
                "添加旋彩光圈貼紙",
                "然後全選光圈",
                "其餘光圈隨機拖動擺放",
                "勾選運動模糊看看效果",
                "最後給光圈加上流動煙霧的特效",
                "再給頂部加上故障2特效",
            ]
        )

        plan = build_operation_plan(text)

        self.assertEqual(plan.warnings, [])
        self.assertIn("add_audio", [op["type"] for op in plan.operations])
        self.assertIn("ai_beat", [op["type"] for op in plan.operations])
        self.assertIn("split_at_marker", [op["type"] for op in plan.operations])
        self.assertIn("add_sticker", [op["type"] for op in plan.operations])
        self.assertIn("select", [op["type"] for op in plan.operations])
        self.assertIn("random_place", [op["type"] for op in plan.operations])
        self.assertIn("add_effect", [op["type"] for op in plan.operations])

    def test_sticker_name_strips_leading_action_words(self):
        plan = build_operation_plan("首先偷入藍色球體貼紙後")

        sticker = [op for op in plan.operations if op["type"] == "add_sticker"][0]
        self.assertEqual(sticker["name"], "蓝色球体")

    def test_missing_audio_and_stickers_generate_placeholders(self):
        plan = build_operation_plan("首先導入背景音樂\n點擊AI卡點\n添加旋彩光圈貼紙")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = OperationExecutor(root, generated_dir=root / "generated").prepare(plan, card_count=8)

            self.assertEqual(result.warnings, [])
            self.assertTrue(Path(result.resolved_audio_path).exists())
            self.assertEqual(Path(result.resolved_audio_path).name, "default_beat.wav")
            self.assertGreaterEqual(len(result.marker_times), 9)
            self.assertEqual(result.sticker_assets[0]["status"], "generated")
            self.assertTrue(Path(result.sticker_assets[0]["path"]).exists())

    def test_effect_aliases_resolve_to_pyjianying_enums(self):
        smoke = resolve_effect_or_filter("流動煙霧", "add_effect")
        glitch = resolve_effect_or_filter("故障2", "add_effect")
        blur = resolve_effect_or_filter("運動模糊", "add_effect")

        self.assertEqual(smoke["member_name"], "流动烟雾")
        self.assertEqual(glitch["member_name"], "故障_II")
        self.assertEqual(blur["member_name"], "动感模糊")

    def test_current_input_can_prepare_without_warnings(self):
        input_path = Path("input/content.txt")
        if not input_path.exists():
            self.skipTest("input/content.txt is not present")
        text = input_path.read_text(encoding="utf-8")
        plan = build_operation_plan(text)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = OperationExecutor(root, generated_dir=root / "generated").prepare(
                plan,
                card_count=len(split_into_cards(text, 72)),
            )

            report_statuses = {item["status"] for item in result.report}
            self.assertEqual(result.warnings, [])
            self.assertNotIn("needs_asset", report_statuses)
            self.assertNotIn("unsupported", report_statuses)
            self.assertNotIn("skipped", report_statuses)
            self.assertGreater(len(result.marker_times), 0)

    def test_execution_result_report_is_json_serializable(self):
        plan = build_operation_plan("添加旋彩光圈貼紙\n最後給光圈加上流動煙霧的特效")

        with tempfile.TemporaryDirectory() as tmp:
            result = OperationExecutor(Path(tmp), generated_dir=Path(tmp) / "generated").prepare(plan, card_count=2)

        json.dumps(result.__dict__, ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
