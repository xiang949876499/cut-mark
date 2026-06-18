from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol, Tuple


UI_FAILED = "ui_failed"
UI_ATTEMPTED = "ui_attempted"
UI_EXECUTED = "ui_executed"
UI_SKIPPED = "ui_skipped"


@dataclass(frozen=True)
class RunConfig:
    plan_path: Path
    profile_path: Path
    output_dir: Path = Path("generated/ui_artifacts")
    report_path: Path = Path("generated/ui_report.json")
    dry_run: bool = False
    auto_confirm_profile: bool = False
    require_editor: bool = False


class ComputerDriver(Protocol):
    def find_window(self) -> Dict[str, object] | None: ...

    def screenshot(self, path: Path) -> None: ...

    def click(self, x: int, y: int, button: str = "left") -> None: ...

    def press_key(self, key: str) -> None: ...


class UiaComputerDriver:
    def __init__(self) -> None:
        self.window = None

    def find_window(self) -> Dict[str, object] | None:
        window = _find_uia_window()
        if not window:
            _launch_jianying_if_available()
            for _ in range(10):
                time.sleep(1)
                window = _find_uia_window()
                if window:
                    break
        if not window:
            return None
        self.window = window
        _activate_uia_window(window)
        rect = _window_rect(window)
        return {
            "title": getattr(window, "Name", ""),
            "app": _window_process_name(window),
            "width": max(1, rect[2] - rect[0]),
            "height": max(1, rect[3] - rect[1]),
            "rect": rect,
            "hwnd": _window_handle(window),
        }

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_active()
        rect = _window_rect(self.window) if self.window else (0, 0, 1, 1)
        try:
            from PIL import ImageGrab

            ImageGrab.grab(bbox=rect).save(path)
        except Exception:
            _write_tiny_png(path)

    def click(self, x: int, y: int, button: str = "left") -> None:
        self._ensure_active()
        rect = _window_rect(self.window) if self.window else (0, 0, 0, 0)
        try:
            import uiautomation as uia

            if button == "right":
                if hasattr(uia, "RightClick"):
                    uia.RightClick(rect[0] + x, rect[1] + y, waitTime=0.1)
                else:
                    uia.Click(rect[0] + x, rect[1] + y, waitTime=0.1)
            else:
                uia.Click(rect[0] + x, rect[1] + y, waitTime=0.1)
        except Exception as exc:
            raise RuntimeError(f"Click failed: {exc}") from exc

    def press_key(self, key: str) -> None:
        try:
            import uiautomation as uia

            self._ensure_active()
            uia.SendKeys(key)
        except Exception as exc:
            raise RuntimeError(f"Key press failed: {exc}") from exc

    def _ensure_active(self) -> None:
        if self.window is None:
            self.find_window()
        if not self.window:
            raise RuntimeError("Jianying window not found")
        if not _activate_uia_window(self.window):
            raise RuntimeError("Failed to activate Jianying window")
        if not _foreground_matches_window(self.window):
            foreground = _foreground_window_info()
            raise RuntimeError(f"Foreground window is not Jianying after activation: {foreground}")


@dataclass
class JianyingUIAutomation:
    root_provider: Optional[Callable[[], object]] = None
    computer_driver: Optional[ComputerDriver] = None

    def find_window(self):
        if self.root_provider:
            return self.root_provider()
        return _find_uia_window()

    def snapshot_controls(self, *, max_depth: int = 2) -> List[Dict[str, object]]:
        window = self.find_window()
        if not window:
            return []
        return [self._snapshot(window, depth=0, max_depth=max_depth)]

    def calibrate(self, profile_path: Path, *, output_dir: Path = Path("generated/ui_artifacts")) -> Dict[str, object]:
        driver = self._driver()
        window = driver.find_window()
        if not is_jianying_window(window):
            return _single_failure("calibrate", "Target window is not Jianying", window=window)
        output_dir.mkdir(parents=True, exist_ok=True)
        screenshot_path = output_dir / "calibrate_window.png"
        driver.screenshot(screenshot_path)
        width = int(window.get("width", 1))
        height = int(window.get("height", 1))
        profile = default_profile(width, height)
        profile["screenshot"] = str(screenshot_path)
        profile["editor_confirmed"] = _looks_like_editor_timeline(screenshot_path)
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": UI_EXECUTED, "message": "Calibration profile saved", "profile": str(profile_path), "window": window}

    def run(self, config: RunConfig) -> Dict[str, object]:
        plan = _load_json(config.plan_path)
        tasks = plan.get("tasks", [])
        report = {"version": 1, "tasks": []}
        driver = self._driver()
        window = driver.find_window()
        if not is_jianying_window(window):
            report["tasks"] = [
                _task_result(task, UI_FAILED, "Target window is not Jianying", {"window": window})
                for task in tasks
            ]
            return _write_report(report, config.report_path)
        if config.auto_confirm_profile and not config.profile_path.exists():
            self.calibrate(config.profile_path, output_dir=config.output_dir)
            profile = _load_json(config.profile_path) if config.profile_path.exists() else {}
            if config.require_editor and profile.get("editor_confirmed") is not True:
                return _write_report(_editor_not_ready_report(tasks, config.profile_path, profile), config.report_path)
            _confirm_profile(config.profile_path)
        if not config.profile_path.exists():
            report["tasks"] = [
                _task_result(task, UI_FAILED, "Calibration profile is missing", {"profile": str(config.profile_path)})
                for task in tasks
            ]
            return _write_report(report, config.report_path)

        profile = _load_json(config.profile_path)
        if config.auto_confirm_profile and profile.get("confirmed") is not True:
            self.calibrate(config.profile_path, output_dir=config.output_dir)
            profile = _load_json(config.profile_path)
            if config.require_editor and profile.get("editor_confirmed") is not True:
                return _write_report(_editor_not_ready_report(tasks, config.profile_path, profile), config.report_path)
            _confirm_profile(config.profile_path)
            profile = _load_json(config.profile_path)
        if profile.get("confirmed") is not True:
            report["tasks"] = [
                _task_result(task, UI_FAILED, "Calibration profile is not confirmed", {"profile": str(config.profile_path)})
                for task in tasks
            ]
            return _write_report(report, config.report_path)
        if config.require_editor and profile.get("editor_confirmed") is not True:
            if config.auto_confirm_profile:
                self.calibrate(config.profile_path, output_dir=config.output_dir)
                profile = _load_json(config.profile_path)
                if profile.get("editor_confirmed") is True:
                    _confirm_profile(config.profile_path)
                    profile = _load_json(config.profile_path)
            if profile.get("editor_confirmed") is not True:
                return _write_report(_editor_not_ready_report(tasks, config.profile_path, profile), config.report_path)
        config.output_dir.mkdir(parents=True, exist_ok=True)
        for index, task in enumerate(tasks, start=1):
            before = config.output_dir / f"task_{index:03d}_before.png"
            after = config.output_dir / f"task_{index:03d}_after.png"
            driver.screenshot(before)
            try:
                if config.dry_run:
                    result = _task_result(task, UI_SKIPPED, "Dry run; no UI action performed", {"before": str(before)})
                else:
                    action_result = self._execute_task(driver, window, profile, task)
                    driver.screenshot(after)
                    if action_result:
                        result = _task_result(
                            task,
                            action_result["status"],
                            action_result["message"],
                            {"before": str(before), "after": str(after), **action_result.get("details", {})},
                        )
                    else:
                        result = _task_result(task, UI_EXECUTED, "UI task executed", {"before": str(before), "after": str(after)})
            except Exception as exc:
                result = _task_result(task, UI_FAILED, str(exc), {"before": str(before)})
            report["tasks"].append(result)
            if result["status"] == UI_FAILED:
                break
        return _write_report(report, config.report_path)

    def attempt_ai_beat(self) -> Dict[str, object]:
        window = self.find_window()
        if not window:
            return {"status": UI_FAILED, "message": "Jianying window not found", "details": {}}
        return {
            "status": UI_ATTEMPTED,
            "message": "AI beat detection requires jianying_ui.py run with a calibrated profile",
            "details": {"window": getattr(window, "Name", "")},
        }

    def attempt_compound_clip(self) -> Dict[str, object]:
        window = self.find_window()
        if not window:
            return {"status": UI_FAILED, "message": "Jianying window not found", "details": {}}
        return {
            "status": UI_ATTEMPTED,
            "message": "Compound clip creation requires jianying_ui.py run with a calibrated profile",
            "details": {"window": getattr(window, "Name", "")},
        }

    def _driver(self) -> ComputerDriver:
        return self.computer_driver or UiaComputerDriver()

    def _execute_task(self, driver: ComputerDriver, window: Dict[str, object], profile: Dict[str, object], task: Dict[str, object]) -> Dict[str, object] | None:
        task_type = task.get("type")
        if task_type == "ai_beat":
            self._click_point(driver, window, profile, "timeline_center")
            self._click_point(driver, window, profile, "ai_beat_button")
            self._click_point(driver, window, profile, "ai_beat_option_2")
        elif task_type == "compound_clip":
            self._click_point(driver, window, profile, "timeline_center")
            self._click_point(driver, window, profile, "context_menu_compound_clip", button="right")
            self._click_point(driver, window, profile, "context_menu_compound_clip")
        elif task_type == "copy":
            self._click_point(driver, window, profile, "timeline_center")
            self._click_point(driver, window, profile, "context_menu_copy", button="right")
            self._click_point(driver, window, profile, "context_menu_copy")
        elif task_type == "select":
            self._click_point(driver, window, profile, "timeline_center")
            driver.press_key("^a")
        elif task_type == "split_at_marker":
            self._click_point(driver, window, profile, "timeline_center")
            driver.press_key("^b")
        else:
            return {
                "status": UI_FAILED,
                "message": f"UI task type is not implemented yet: {task_type}",
                "details": {},
            }
        time.sleep(0.2)
        return None

    def _click_point(self, driver: ComputerDriver, window: Dict[str, object], profile: Dict[str, object], point_name: str, *, button: str = "left") -> None:
        point = profile.get("points", {}).get(point_name)
        if not point:
            raise RuntimeError(f"Missing calibrated point: {point_name}")
        x, y = scale_point(point, profile.get("window", {}), window)
        driver.click(x, y, button=button)

    def _snapshot(self, control, *, depth: int, max_depth: int) -> Dict[str, object]:
        node = {
            "name": getattr(control, "Name", ""),
            "class_name": getattr(control, "ClassName", ""),
            "children": [],
        }
        if depth >= max_depth:
            return node
        try:
            children = control.GetChildren()
        except Exception:
            children = []
        node["children"] = [self._snapshot(child, depth=depth + 1, max_depth=max_depth) for child in children]
        return node


def is_jianying_window(window: Dict[str, object] | None) -> bool:
    if not window:
        return False
    text = f"{window.get('title', '')} {window.get('app', '')}"
    return ("剪映" in text or "Jianying" in text or "JianyingPro" in text) and "Codex" not in text


def scale_point(point: Dict[str, object], base_window: Dict[str, object], current_window: Dict[str, object]) -> Tuple[int, int]:
    base_width = max(1, int(base_window.get("width", current_window.get("width", 1))))
    base_height = max(1, int(base_window.get("height", current_window.get("height", 1))))
    current_width = max(1, int(current_window.get("width", base_width)))
    current_height = max(1, int(current_window.get("height", base_height)))
    x = int(round(float(point["x"]) * current_width / base_width))
    y = int(round(float(point["y"]) * current_height / base_height))
    return x, y


def default_profile(width: int, height: int) -> Dict[str, object]:
    return {
        "version": 1,
        "confirmed": False,
        "confirmation_note": "Set confirmed to true only after verifying the screenshot is the Jianying timeline.",
        "editor_confirmed": False,
        "window": {"width": width, "height": height},
        "points": {
            "timeline_center": {"x": int(width * 0.50), "y": int(height * 0.82)},
            "context_menu_compound_clip": {"x": int(width * 0.56), "y": int(height * 0.60)},
            "context_menu_copy": {"x": int(width * 0.55), "y": int(height * 0.56)},
            "ai_beat_button": {"x": int(width * 0.35), "y": int(height * 0.18)},
            "ai_beat_option_2": {"x": int(width * 0.38), "y": int(height * 0.24)},
        },
    }


def _looks_like_editor_timeline(screenshot_path: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False
    try:
        image = Image.open(screenshot_path).convert("RGB")
    except Exception:
        return False

    width, height = image.size
    if width < 200 or height < 200:
        return False

    top = image.crop((0, 0, width, int(height * 0.18)))
    left = image.crop((0, 0, int(width * 0.42), int(height * 0.36)))
    timeline = image.crop((0, int(height * 0.58), width, height))

    timeline_pixels = list(timeline.getdata())
    top_pixels = list(top.getdata())
    left_pixels = list(left.getdata())
    if not timeline_pixels or not top_pixels or not left_pixels:
        return False

    def dark_ratio(pixels: List[Tuple[int, int, int]]) -> float:
        return sum(1 for red, green, blue in pixels if red < 75 and green < 75 and blue < 75) / len(pixels)

    def accent_ratio(pixels: List[Tuple[int, int, int]]) -> float:
        return sum(
            1
            for red, green, blue in pixels
            if (green > 100 and blue > 90 and red < 120) or (blue > 120 and green > 120 and red < 90)
        ) / len(pixels)

    return dark_ratio(timeline_pixels) > 0.72 and (
        accent_ratio(top_pixels) > 0.002 or accent_ratio(left_pixels) > 0.002
    )


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safe Jianying UI automation helper.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    calibrate = subparsers.add_parser("calibrate")
    calibrate.add_argument("--profile", type=Path, default=Path("generated/jianying_ui_profile.json"))
    calibrate.add_argument("--artifacts", type=Path, default=Path("generated/ui_artifacts"))
    run = subparsers.add_parser("run")
    run.add_argument("--plan", type=Path, default=Path("generated/ui_task_plan.json"))
    run.add_argument("--profile", type=Path, default=Path("generated/jianying_ui_profile.json"))
    run.add_argument("--artifacts", type=Path, default=Path("generated/ui_artifacts"))
    run.add_argument("--report", type=Path, default=Path("generated/ui_report.json"))
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--auto-confirm-profile", action="store_true")
    run.add_argument("--require-editor", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    automation = JianyingUIAutomation()
    if args.command == "calibrate":
        result = automation.calibrate(args.profile, output_dir=args.artifacts)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "run":
        result = automation.run(
            RunConfig(
                plan_path=args.plan,
                profile_path=args.profile,
                output_dir=args.artifacts,
                report_path=args.report,
                dry_run=args.dry_run,
                auto_confirm_profile=args.auto_confirm_profile,
                require_editor=args.require_editor,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


def _find_uia_window():
    try:
        import uiautomation as uia
    except ImportError:
        return None
    root = uia.GetRootControl()
    for window in root.GetChildren():
        name = getattr(window, "Name", "")
        class_name = getattr(window, "ClassName", "")
        if "剪映" in name or "Jianying" in name or "HomePage" in class_name or "MainWindow" in class_name:
            return window
    return None


def _window_rect(window) -> Tuple[int, int, int, int]:
    if not window:
        return (0, 0, 1, 1)
    rect = getattr(window, "BoundingRectangle", None)
    if rect is None:
        return (0, 0, 1, 1)
    return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))


def _window_handle(window) -> int | None:
    if not window:
        return None
    handle = getattr(window, "NativeWindowHandle", None)
    if handle:
        return int(handle)
    return None


def _foreground_window_info() -> Dict[str, object]:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow())
        length = int(user32.GetWindowTextLengthW(hwnd))
        buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buffer, length + 1)
        return {"hwnd": hwnd, "title": buffer.value}
    except Exception:
        return {"hwnd": None, "title": ""}


def _foreground_matches_window(window) -> bool:
    expected = _window_handle(window)
    if not expected:
        return True
    return _foreground_window_info().get("hwnd") == expected


def _activate_uia_window(window) -> bool:
    if not window:
        return False
    handle = _window_handle(window)
    if handle:
        try:
            import ctypes

            user32 = ctypes.windll.user32
            user32.ShowWindow(handle, 9)
            user32.SetForegroundWindow(handle)
            time.sleep(0.2)
            if _foreground_matches_window(window):
                return True
        except Exception:
            pass
    for method_name in ("SetActive", "SetFocus"):
        method = getattr(window, method_name, None)
        if not method:
            continue
        try:
            method()
            time.sleep(0.2)
            return True
        except Exception:
            continue
    return False


def _window_process_name(window) -> str:
    process_id = getattr(window, "ProcessId", None)
    if not process_id:
        return "JianyingPro.exe" if "剪映" in getattr(window, "Name", "") else ""
    return "JianyingPro.exe"


def _load_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_report(report: Dict[str, object], path: Path) -> Dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _confirm_profile(path: Path) -> None:
    if not path.exists():
        return
    profile = _load_json(path)
    profile["confirmed"] = True
    profile["auto_confirmed"] = True
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_result(task: Dict[str, object], status: str, message: str, details: Dict[str, object]) -> Dict[str, object]:
    return {
        "type": task.get("type"),
        "status": status,
        "message": message,
        "line": task.get("line", ""),
        "line_index": task.get("line_index"),
        "details": details,
    }


def _single_failure(task_type: str, message: str, **details) -> Dict[str, object]:
    return {"version": 1, "tasks": [_task_result({"type": task_type}, UI_FAILED, message, details)]}


def _editor_not_ready_report(tasks: List[Dict[str, object]], profile_path: Path, profile: Dict[str, object]) -> Dict[str, object]:
    message = "Jianying window is open, but the editor timeline is not confirmed. Open the target draft/editor timeline and rerun."
    return {
        "version": 1,
        "tasks": [
            _task_result(
                task,
                UI_FAILED,
                message,
                {
                    "profile": str(profile_path),
                    "screenshot": profile.get("screenshot", ""),
                    "editor_confirmed": profile.get("editor_confirmed", False),
                },
            )
            for task in tasks
        ],
    }


def _write_tiny_png(path: Path) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
        b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def is_jianying_window(window: Dict[str, object] | None) -> bool:
    if not window:
        return False
    text = f"{window.get('title', '')} {window.get('app', '')}"
    return ("剪映" in text or "鍓槧" in text or "Jianying" in text or "JianyingPro" in text) and "Codex" not in text


def _find_uia_window():
    try:
        import uiautomation as uia
    except ImportError:
        return None
    root = uia.GetRootControl()
    for window in root.GetChildren():
        name = getattr(window, "Name", "")
        class_name = getattr(window, "ClassName", "")
        process_name = _window_process_name(window)
        identity = f"{name} {class_name} {process_name}"
        if "Codex" in identity:
            continue
        if "剪映" in identity or "鍓槧" in identity or "Jianying" in identity or "JianyingPro" in identity:
            return window
    return None


def _window_process_name(window) -> str:
    process_id = getattr(window, "ProcessId", None)
    if process_id:
        try:
            import csv
            import subprocess

            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
                text=True,
                encoding="mbcs",
                errors="replace",
            ).strip()
            rows = list(csv.reader(output.splitlines()))
            if rows and rows[0]:
                return rows[0][0]
        except Exception:
            pass
    name = getattr(window, "Name", "")
    return "JianyingPro.exe" if ("剪映" in name or "鍓槧" in name or "Jianying" in name) else ""


def is_jianying_window(window: Dict[str, object] | None) -> bool:
    if not window:
        return False
    title = str(window.get("title", ""))
    app = str(window.get("app", ""))
    process_ok = "JianyingPro.exe" in app or app == "JianyingPro.exe"
    title_ok = "剪映" in title or "Jianying" in title or "鍓槧" in title
    blocked = "Chrome" in app or "Codex" in app or "Codex" in title
    return process_ok and title_ok and not blocked


def _find_uia_window():
    try:
        import uiautomation as uia
    except ImportError:
        return None
    root = uia.GetRootControl()
    for window in root.GetChildren():
        name = getattr(window, "Name", "")
        process_name = _window_process_name(window)
        if is_jianying_window({"title": name, "app": process_name}):
            return window
    return None


def _window_process_name(window) -> str:
    process_id = getattr(window, "ProcessId", None)
    if process_id:
        try:
            import csv
            import subprocess

            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {process_id}", "/FO", "CSV", "/NH"],
                text=True,
                encoding="mbcs",
                errors="replace",
            ).strip()
            rows = list(csv.reader(output.splitlines()))
            if rows and rows[0]:
                return rows[0][0]
        except Exception:
            pass
    name = getattr(window, "Name", "")
    return "JianyingPro.exe" if ("剪映" in name or "鍓槧" in name or "Jianying" in name) else ""


def _launch_jianying_if_available() -> None:
    exe = _default_jianying_exe()
    if not exe:
        return
    try:
        import subprocess

        subprocess.Popen([str(exe)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return


def _default_jianying_exe() -> Path | None:
    candidates = [
        Path.home() / "AppData/Local/JianyingPro/Apps/JianyingPro.exe",
        Path.home() / "AppData/Local/JianyingPro/Apps/10.7.0.14095/JianyingPro.exe",
    ]
    for path in candidates:
        if path.exists():
            return path
    return None


if __name__ == "__main__":
    main()
