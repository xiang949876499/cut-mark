import json
import sys
import tempfile
import unittest
from pathlib import Path

from jianying_ui import (
    JianyingUIAutomation,
    RunConfig,
    UiaComputerDriver,
    is_jianying_window,
    scale_point,
)


class FakeControl:
    def __init__(self, name, class_name="Control", children=None):
        self.Name = name
        self.ClassName = class_name
        self._children = children or []

    def GetChildren(self):
        return self._children


class FakeComputerDriver:
    def __init__(self, *, valid_window=True):
        self.valid_window = valid_window
        self.actions = []

    def find_window(self):
        if self.valid_window:
            return {"title": "剪映专业版", "app": "JianyingPro.exe", "width": 2000, "height": 1000}
        return {"title": "Codex", "app": "Codex.exe", "width": 2000, "height": 1000}

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake screenshot")

    def click(self, x, y, button="left"):
        self.actions.append(("click", x, y, button))

    def press_key(self, key):
        self.actions.append(("key", key))


class JianyingUITests(unittest.TestCase):
    def test_snapshot_controls_limits_depth(self):
        root = FakeControl("剪映专业版", "HomePage", [FakeControl("导出", "Button")])
        automation = JianyingUIAutomation(root_provider=lambda: root)

        snapshot = automation.snapshot_controls(max_depth=1)

        self.assertEqual(snapshot[0]["name"], "剪映专业版")
        self.assertEqual(snapshot[0]["children"][0]["name"], "导出")

    def test_window_gate_rejects_non_jianying_window(self):
        self.assertTrue(is_jianying_window({"title": "剪映专业版", "app": "JianyingPro.exe"}))
        self.assertFalse(is_jianying_window({"title": "Codex", "app": "Codex.exe"}))
        self.assertFalse(
            is_jianying_window(
                {
                    "title": "剪映下载 - Google 搜索 - Google Chrome",
                    "app": r"process:C:\Program Files\Google\Chrome\Application\chrome.exe",
                }
            )
        )

    def test_scale_point_uses_profile_base_size(self):
        point = scale_point({"x": 500, "y": 250}, {"width": 1000, "height": 500}, {"width": 2000, "height": 1000})

        self.assertEqual(point, (1000, 500))

    def test_window_gate_accepts_real_jianying_process_path(self):
        self.assertTrue(
            is_jianying_window(
                {
                    "title": "剪映专业版",
                    "app": r"process:C:\Users\zx\AppData\Local\JianyingPro\Apps\10.7.0.14095\JianyingPro.exe",
                }
            )
        )

    def test_run_fails_without_profile_and_does_not_click(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "ui_task_plan.json"
            plan.write_text(json.dumps({"version": 1, "tasks": [{"type": "ai_beat", "line_index": 1}]}), encoding="utf-8")
            driver = FakeComputerDriver()
            automation = JianyingUIAutomation(computer_driver=driver)

            report = automation.run(RunConfig(plan_path=plan, profile_path=root / "missing.json", output_dir=root / "artifacts"))

            self.assertEqual(report["tasks"][0]["status"], "ui_failed")
            self.assertEqual(driver.actions, [])

    def test_run_executes_with_valid_profile_and_records_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "ui_task_plan.json"
            profile = root / "profile.json"
            plan.write_text(json.dumps({"version": 1, "tasks": [{"type": "compound_clip", "line_index": 2}]}), encoding="utf-8")
            profile.write_text(
                json.dumps(
                    {
                        "confirmed": True,
                        "window": {"width": 1000, "height": 500},
                        "points": {
                            "timeline_center": {"x": 500, "y": 420},
                            "context_menu_compound_clip": {"x": 560, "y": 360},
                            "context_menu_copy": {"x": 550, "y": 330},
                            "ai_beat_button": {"x": 300, "y": 120},
                            "ai_beat_option_2": {"x": 330, "y": 180},
                        },
                    }
                ),
                encoding="utf-8",
            )
            driver = FakeComputerDriver()
            automation = JianyingUIAutomation(computer_driver=driver)

            report = automation.run(RunConfig(plan_path=plan, profile_path=profile, output_dir=root / "artifacts"))

            self.assertEqual(report["tasks"][0]["status"], "ui_executed")
            self.assertTrue(any(action[0] == "click" for action in driver.actions))
            self.assertTrue((root / "artifacts" / "task_001_before.png").exists())
            self.assertTrue((root / "artifacts" / "task_001_after.png").exists())

    def test_run_fails_unimplemented_ui_task_and_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "ui_task_plan.json"
            profile = root / "profile.json"
            plan.write_text(
                json.dumps({"version": 1, "tasks": [{"type": "add_audio", "line_index": 1}, {"type": "select", "line_index": 2}]}),
                encoding="utf-8",
            )
            profile.write_text(
                json.dumps(
                    {
                        "confirmed": True,
                        "window": {"width": 1000, "height": 500},
                        "points": {
                            "timeline_center": {"x": 500, "y": 420},
                            "context_menu_compound_clip": {"x": 560, "y": 360},
                            "context_menu_copy": {"x": 550, "y": 330},
                            "ai_beat_button": {"x": 300, "y": 120},
                            "ai_beat_option_2": {"x": 330, "y": 180},
                        },
                    }
                ),
                encoding="utf-8",
            )
            driver = FakeComputerDriver()
            automation = JianyingUIAutomation(computer_driver=driver)

            report = automation.run(RunConfig(plan_path=plan, profile_path=profile, output_dir=root / "artifacts"))

            self.assertEqual([task["status"] for task in report["tasks"]], ["ui_failed"])
            self.assertIn("not implemented", report["tasks"][0]["message"])
            self.assertEqual(driver.actions, [])

    def test_run_refuses_unconfirmed_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "ui_task_plan.json"
            profile = root / "profile.json"
            plan.write_text(json.dumps({"version": 1, "tasks": [{"type": "copy", "line_index": 1}]}), encoding="utf-8")
            profile.write_text(json.dumps({"confirmed": False, "window": {"width": 1000, "height": 500}, "points": {}}), encoding="utf-8")
            driver = FakeComputerDriver()
            automation = JianyingUIAutomation(computer_driver=driver)

            report = automation.run(RunConfig(plan_path=plan, profile_path=profile, output_dir=root / "artifacts"))

            self.assertEqual(report["tasks"][0]["status"], "ui_failed")
            self.assertIn("not confirmed", report["tasks"][0]["message"])
            self.assertEqual(driver.actions, [])

    def test_run_can_auto_confirm_profile_for_experimental_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "ui_task_plan.json"
            profile = root / "profile.json"
            plan.write_text(json.dumps({"version": 1, "tasks": [{"type": "select", "line_index": 1}]}), encoding="utf-8")
            profile.write_text(json.dumps({"confirmed": False, "window": {"width": 1000, "height": 500}, "points": {}}), encoding="utf-8")
            driver = FakeComputerDriver()
            automation = JianyingUIAutomation(computer_driver=driver)

            report = automation.run(
                RunConfig(
                    plan_path=plan,
                    profile_path=profile,
                    output_dir=root / "artifacts",
                    auto_confirm_profile=True,
                    require_editor=False,
                )
            )

            self.assertEqual(report["tasks"][0]["status"], "ui_executed")
            self.assertIn(("key", "^a"), driver.actions)
            saved_profile = json.loads(profile.read_text(encoding="utf-8"))
            self.assertTrue(saved_profile["confirmed"])

    def test_run_refuses_auto_confirm_when_editor_is_not_confirmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / "ui_task_plan.json"
            profile = root / "profile.json"
            plan.write_text(json.dumps({"version": 1, "tasks": [{"type": "select", "line_index": 1}]}), encoding="utf-8")
            profile.write_text(
                json.dumps(
                    {
                        "confirmed": False,
                        "editor_confirmed": False,
                        "window": {"width": 1000, "height": 500},
                        "points": {},
                    }
                ),
                encoding="utf-8",
            )
            driver = FakeComputerDriver()
            automation = JianyingUIAutomation(computer_driver=driver)

            report = automation.run(
                RunConfig(
                    plan_path=plan,
                    profile_path=profile,
                    output_dir=root / "artifacts",
                    auto_confirm_profile=True,
                    require_editor=True,
                )
            )

            self.assertEqual(report["tasks"][0]["status"], "ui_failed")
            self.assertIn("editor timeline", report["tasks"][0]["message"])
            self.assertEqual(driver.actions, [])

    def test_uia_driver_uses_right_click_api_for_right_button(self):
        calls = []

        class FakeUia:
            @staticmethod
            def Click(x, y, waitTime=0.0):
                calls.append(("click", x, y, waitTime))

            @staticmethod
            def RightClick(x, y, waitTime=0.0):
                calls.append(("right", x, y, waitTime))

        class Rect:
            left = 10
            top = 20
            right = 200
            bottom = 300

        class Window:
            BoundingRectangle = Rect()

            def SetActive(self):
                calls.append(("active",))

        old = sys.modules.get("uiautomation")
        sys.modules["uiautomation"] = FakeUia
        try:
            driver = UiaComputerDriver()
            driver.window = Window()
            driver.click(5, 6, button="right")
        finally:
            if old is None:
                sys.modules.pop("uiautomation", None)
            else:
                sys.modules["uiautomation"] = old

        self.assertIn(("right", 15, 26, 0.1), calls)


if __name__ == "__main__":
    unittest.main()
