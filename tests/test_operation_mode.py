import tempfile
import unittest
from pathlib import Path

from draft_generator import (
    build_operation_plan,
    write_operation_artifacts,
)


class OperationModeTests(unittest.TestCase):
    def test_build_operation_plan_detects_executable_operations(self):
        text = "\n".join(
            [
                "首先导入背景音乐",
                "添加叠化转场",
                "缩放改成20",
                "添加圆形蒙版",
                "加上放大的出场动画",
            ]
        )

        plan = build_operation_plan(text)

        self.assertEqual(
            [op["type"] for op in plan.operations],
            ["add_audio", "add_transition", "set_scale", "add_mask", "add_animation"],
        )
        self.assertEqual(plan.operations[2]["value"], 0.2)
        self.assertEqual(plan.operations[3]["mask"], "circle")
        self.assertEqual(plan.warnings, [])

    def test_build_operation_plan_maps_former_manual_operations(self):
        text = "\n".join(
            [
                "点击AI卡点后选择踩节拍",
                "右键新建复合片段",
                "在第7个标记点进行分割",
                "添加旋彩光圈贴纸",
                "框选下方所有素材",
                "右键复制属性",
                "打上位置关键帧",
                "添加故障2特效",
                "其余光圈随机拖动摆放",
            ]
        )

        plan = build_operation_plan(text)

        self.assertEqual(plan.warnings, [])
        self.assertIn("ai_beat", [op["type"] for op in plan.operations])
        self.assertIn("compound_clip", [op["type"] for op in plan.operations])
        self.assertIn("split_at_marker", [op["type"] for op in plan.operations])
        self.assertIn("add_sticker", [op["type"] for op in plan.operations])
        self.assertIn("select", [op["type"] for op in plan.operations])
        self.assertIn("copy", [op["type"] for op in plan.operations])
        self.assertIn("keyframe", [op["type"] for op in plan.operations])
        self.assertIn("add_effect", [op["type"] for op in plan.operations])
        self.assertIn("random_place", [op["type"] for op in plan.operations])

    def test_build_operation_plan_does_not_treat_editing_directions_as_transitions(self):
        text = "\n".join(
            [
                "在第7个标记点进行分割",
                "向右移动三帧",
                "再给顶部加上故障2特效",
            ]
        )

        plan = build_operation_plan(text)

        self.assertNotIn("add_transition", [op["type"] for op in plan.operations])

    def test_write_operation_artifacts_saves_plan_and_empty_warnings(self):
        plan = build_operation_plan("首先导入背景音乐\n点击AI卡点")

        with tempfile.TemporaryDirectory() as tmp:
            paths = write_operation_artifacts(plan, Path(tmp))

            self.assertTrue(paths["plan"].exists())
            self.assertTrue(paths["warnings"].exists())
            self.assertIn('"type": "add_audio"', paths["plan"].read_text(encoding="utf-8"))
            self.assertEqual(paths["warnings"].read_text(encoding="utf-8"), "")


if __name__ == "__main__":
    unittest.main()
