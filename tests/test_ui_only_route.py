import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import draft_generator


class UiOnlyRouteTests(unittest.TestCase):
    def test_ui_only_route_writes_full_ui_plan_without_building_draft(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "input").mkdir()
            (root / "assets").mkdir()
            config_path = root / "config.json"
            input_path = root / "input" / "content.txt"
            config_path.write_text(
                json.dumps(
                    {
                        "draft_folder": str(root / "drafts"),
                        "draft_name": "ui_only_test",
                        "resolution": [1080, 1920],
                        "segment_duration_sec": 1.0,
                        "max_chars_per_card": 72,
                        "fallback_transitions": ["叠化"],
                        "default_background_color": "#000000",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            input_path.write_text("导入背景音乐\n点击AI卡点\n添加旋彩光圈贴纸\n右键新建复合片段", encoding="utf-8")
            old_cwd = Path.cwd()
            os.chdir(root)
            try:
                with patch("draft_generator.build_draft") as build_draft:
                    draft_generator.main(
                        [
                            "--config",
                            str(config_path),
                            "--input",
                            str(input_path),
                            "--assets",
                            str(root / "assets"),
                            "--mode",
                            "operations",
                            "--route",
                            "ui-only",
                        ]
                    )
            finally:
                os.chdir(old_cwd)

            build_draft.assert_not_called()
            ui_plan = json.loads((root / "generated" / "ui_task_plan.json").read_text(encoding="utf-8"))
            self.assertEqual([task["type"] for task in ui_plan["tasks"]], ["add_audio", "ai_beat", "add_sticker", "compound_clip"])
            decision = (root / "generated" / "final_decision.txt").read_text(encoding="utf-8")
            self.assertIn("route=ui-only", decision)
            self.assertIn("only generated UI task plan", decision)


if __name__ == "__main__":
    unittest.main()
