import json
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from draft_generator import build_operation_plan
from operation_executor import (
    OperationExecutor,
    build_full_ui_task_plan,
    generate_audio_markers,
    load_manifest,
    random_positions,
    write_ui_task_plan,
)


class OperationExecutorTests(unittest.TestCase):
    def test_executor_auto_executes_known_operations(self):
        plan = build_operation_plan("点击AI卡点后选择踩节拍\n在第7个标记点进行分割\n右键新建复合片段")

        with tempfile.TemporaryDirectory() as tmp:
            result = OperationExecutor(Path(tmp), generated_dir=Path(tmp) / "generated").prepare(plan, card_count=8)

        statuses = [item["status"] for item in result.report]
        self.assertNotIn("unsupported", statuses)
        self.assertNotIn("skipped", statuses)
        self.assertNotIn("needs_asset", statuses)
        self.assertEqual(result.warnings, [])
        self.assertGreaterEqual(len(result.marker_times), 9)

    def test_executor_generates_audio_when_audio_missing(self):
        plan = build_operation_plan("首先导入背景音乐")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = OperationExecutor(root, generated_dir=root / "generated").prepare(plan, card_count=3)

            audio = [item for item in result.report if item["operation"] == "add_audio"][0]
            self.assertEqual(audio["status"], "executed")
            self.assertTrue((root / "generated" / "default_beat.wav").exists())
            self.assertEqual(Path(result.resolved_audio_path).name, "default_beat.wav")

    def test_executor_resolves_local_sticker_asset(self):
        plan = build_operation_plan("添加旋彩光圈贴纸")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sticker_dir = root / "stickers"
            sticker_dir.mkdir()
            (sticker_dir / "旋彩光圈.png").write_bytes(b"fake image")

            result = OperationExecutor(root, seed=42).prepare(plan)

        sticker_ops = [item for item in result.report if item["operation"] == "add_sticker"]
        self.assertEqual(sticker_ops[0]["status"], "executed")
        self.assertTrue(sticker_ops[0]["details"]["path"].endswith("旋彩光圈.png"))

    def test_executor_resolves_manifest_sticker_id(self):
        plan = build_operation_plan("添加旋彩光圈贴纸")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "stickers.json"
            manifest.write_text(json.dumps({"旋彩光圈": {"resource_id": "sticker-123"}}, ensure_ascii=False), encoding="utf-8")

            result = OperationExecutor(root, sticker_manifest=manifest, seed=42, backend="jianying").prepare(plan)

        sticker_ops = [item for item in result.report if item["operation"] == "add_sticker"]
        self.assertEqual(sticker_ops[0]["status"], "executed")
        self.assertEqual(sticker_ops[0]["details"]["resource_id"], "sticker-123")

    def test_random_positions_are_seeded(self):
        self.assertEqual(random_positions(3, seed=7), random_positions(3, seed=7))
        self.assertNotEqual(random_positions(3, seed=7), random_positions(3, seed=8))

    def test_generate_audio_markers_from_wav(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "test.wav"
            with wave.open(str(wav_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(16000)
                handle.writeframes(b"\x00\x00" * 16000 * 3)

            markers = generate_audio_markers(wav_path, segment_duration_sec=1.0)

        self.assertEqual(markers, [0.0, 1.0, 2.0, 3.0])

    def test_generate_audio_markers_detects_pulse_beats(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "beats.wav"
            sample_rate = 16000
            samples = [0] * (sample_rate * 2)
            for beat_second in (0.5, 1.0, 1.5):
                start = int(beat_second * sample_rate)
                for index in range(start, start + 800):
                    samples[index] = 24000
            with wave.open(str(wav_path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))

            markers = generate_audio_markers(wav_path, segment_duration_sec=1.0)

        self.assertEqual(markers[0], 0.0)
        self.assertTrue(any(abs(marker - 0.5) < 0.08 for marker in markers))
        self.assertTrue(any(abs(marker - 1.0) < 0.08 for marker in markers))
        self.assertTrue(any(abs(marker - 1.5) < 0.08 for marker in markers))

    def test_load_manifest_accepts_missing_file(self):
        self.assertEqual(load_manifest(Path("missing.json")), {})

    def test_experimental_ui_mode_is_not_needed_when_local_markers_work(self):
        class FakeUI:
            def attempt_ai_beat(self):
                return {"status": "ui_attempted", "message": "fake ai", "details": {"ok": True}}

        plan = build_operation_plan("点击AI卡点")

        with tempfile.TemporaryDirectory() as tmp:
            result = OperationExecutor(Path(tmp), generated_dir=Path(tmp) / "generated", ui_mode="experimental", ui_driver=FakeUI()).prepare(plan, card_count=2)

        self.assertEqual(result.report[0]["status"], "executed")
        self.assertGreater(len(result.marker_times), 0)

    def test_executor_collects_optional_ui_tasks(self):
        plan = build_operation_plan("点击AI卡点\n右键新建复合片段\n右键复制属性")

        with tempfile.TemporaryDirectory() as tmp:
            result = OperationExecutor(Path(tmp), generated_dir=Path(tmp) / "generated").prepare(plan, card_count=3)

            task_types = [task["type"] for task in result.ui_tasks]
            self.assertEqual(task_types, ["ai_beat", "compound_clip", "copy"])
            self.assertEqual(result.ui_tasks[0]["status"], "ui_required")
            self.assertEqual(result.ui_tasks[0]["line_index"], 1)

    def test_write_ui_task_plan_saves_tasks(self):
        plan = build_operation_plan("点击AI卡点\n右键复制属性")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = OperationExecutor(root, generated_dir=root / "generated").prepare(plan, card_count=2)
            path = write_ui_task_plan(result.ui_tasks, root / "generated")

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([task["type"] for task in saved["tasks"]], ["ai_beat", "copy"])
            self.assertEqual(saved["version"], 1)

    def test_full_ui_task_plan_includes_every_operation(self):
        plan = build_operation_plan(
            "\n".join(
                [
                    "导入背景音乐",
                    "点击AI卡点",
                    "在第7个标记点分割",
                    "添加旋彩光圈贴纸",
                    "缩放改成20",
                    "全选光圈",
                    "随机拖动摆放",
                    "右键新建复合片段",
                    "添加运动模糊特效",
                    "右键复制属性",
                ]
            )
        )

        tasks = build_full_ui_task_plan(plan.operations)

        self.assertEqual([task["type"] for task in tasks], [op["type"] for op in plan.operations])
        self.assertTrue(all(task["status"] == "ui_required" for task in tasks))
        self.assertIn("experimental", {task["ui_kind"] for task in tasks if task["type"] in {"ai_beat", "compound_clip", "add_effect"}})

    def test_kdenlive_backend_does_not_require_pyjianyingdraft(self):
        plan = type(
            "Plan",
            (),
            {"operations": [{"type": "add_effect", "name": "杩愬姩妯＄硦", "selector": "all_video_segments"}]},
        )()

        with tempfile.TemporaryDirectory() as tmp:
            result = OperationExecutor(
                Path(tmp),
                generated_dir=Path(tmp) / "generated",
                backend="kdenlive",
            ).prepare(plan, card_count=2)

        self.assertEqual(result.effects[0]["name"], "杩愬姩妯＄硦")

    def test_kdenlive_ignores_jianying_sticker_resource_id_and_generates_png(self):
        plan = type(
            "Plan",
            (),
            {"operations": [{"type": "add_sticker", "name": "鏃嬗僵鍏夊湀"}]},
        )()

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = root / "stickers.json"
            manifest.write_text(
                json.dumps({"鏃嬪僵鍏夊湀": {"resource_id": "jianying-only-id"}}, ensure_ascii=False),
                encoding="utf-8",
            )
            result = OperationExecutor(
                root,
                generated_dir=root / "generated",
                sticker_manifest=manifest,
                backend="kdenlive",
            ).prepare(plan, card_count=2)

        self.assertTrue(result.sticker_assets[0]["path"].endswith(".png"))
        self.assertNotIn("resource_id", result.sticker_assets[0])


if __name__ == "__main__":
    unittest.main()
