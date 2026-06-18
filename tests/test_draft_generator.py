import json
import os
import tempfile
import unittest
from pathlib import Path

from draft_generator import (
    DEFAULT_FALLBACK_TRANSITIONS,
    OperationPlan,
    build_draft,
    ensure_assets,
    extract_text_from_html,
    extract_video_text,
    load_config,
    make_draft_name_for_content,
    pick_transition,
    split_into_cards,
    write_video_text_input,
)
from operation_executor import ExecutionResult


class DraftGeneratorTests(unittest.TestCase):
    def test_plain_text_paragraphs_split_into_cards(self):
        text = "第一段介绍操作。\n\n第二段继续说明具体步骤。"

        cards = split_into_cards(text, max_chars=20)

        self.assertEqual([card.text for card in cards], ["第一段介绍操作。", "第二段继续说明具体步骤。"])

    def test_long_paragraph_splits_by_sentence(self):
        text = "第一句很短。第二句也很短。第三句继续说明。"

        cards = split_into_cards(text, max_chars=12)

        self.assertEqual([card.text for card in cards], ["第一句很短。", "第二句也很短。", "第三句继续说明。"])

    def test_html_extraction_removes_noise_and_keeps_body_text(self):
        html = """
        <html>
          <head><style>.hidden{}</style><script>alert(1)</script></head>
          <body>
            <nav>首页 导航</nav>
            <article>
              <h1>标题</h1>
              <p>第一段内容。</p>
              <p>第二段包含 <strong>重点</strong>。</p>
            </article>
            <footer>版权信息</footer>
          </body>
        </html>
        """

        text = extract_text_from_html(html)

        self.assertIn("标题", text)
        self.assertIn("第一段内容。", text)
        self.assertIn("第二段包含 重点。", text)
        self.assertNotIn("首页 导航", text)
        self.assertNotIn("版权信息", text)
        self.assertNotIn("alert", text)

    def test_transition_detection_prefers_transition_name_in_text(self):
        transition = pick_transition("这里使用信号故障切换到下一段。", DEFAULT_FALLBACK_TRANSITIONS, 0)

        self.assertEqual(transition, "信号故障")

    def test_transition_detection_falls_back_by_index(self):
        transition = pick_transition("没有写任何转场名称。", ["叠化", "右移"], 3)

        self.assertEqual(transition, "右移")

    def test_empty_asset_directory_generates_black_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            assets = root / "assets"
            generated = root / "generated"
            assets.mkdir()

            result = ensure_assets(assets, generated, (1080, 1920), "#000000")

            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "default_black.png")
            self.assertTrue(result[0].exists())
            self.assertGreater(result[0].stat().st_size, 0)

    def test_extract_video_text_returns_subtitle_text(self):
        def fake_extractor(url, config):
            return {"subtitle_text": "第一句文案。\n第二句文案。", "info": {"title": "测试视频"}}

        text = extract_video_text("https://example.com/video", extractor=fake_extractor, config={})

        self.assertEqual(text, "第一句文案。\n第二句文案。")

    def test_write_video_text_input_saves_extracted_text(self):
        def fake_extractor(url, config):
            return {"subtitle_text": "提取出来的视频文案。"}

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "input" / "extracted.txt"

            returned_path = write_video_text_input(
                "https://example.com/video",
                output_path,
                extractor=fake_extractor,
                config={},
            )

            self.assertEqual(returned_path, output_path)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "提取出来的视频文案。\n")

    def test_load_config_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.json"
            config_path.write_text(
                '{"draft_folder": "D:/drafts", "fallback_transitions": ["叠化"]}',
                encoding="utf-8-sig",
            )

            config = load_config(config_path)

            self.assertEqual(config.draft_folder, Path("D:/drafts"))
            self.assertEqual(config.fallback_transitions, ["叠化"])

    def test_content_hash_draft_name_changes_for_different_content(self):
        first = make_draft_name_for_content("web_transition_video", "first content")
        second = make_draft_name_for_content("web_transition_video", "second content")
        repeat = make_draft_name_for_content("web_transition_video", "first content")

        self.assertNotEqual(first, second)
        self.assertEqual(first, repeat)
        self.assertRegex(first, r"^web_transition_video_[0-9a-f]{8}$")

    def test_build_draft_does_not_create_empty_audio_track_when_audio_missing(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft_dir = root / "drafts"
            draft_dir.mkdir()
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "draft_folder": str(draft_dir),
                        "draft_name": "no_empty_audio",
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
            input_path = root / "content.txt"
            input_path.write_text("step one", encoding="utf-8")
            os.chdir(root)
            try:
                config = load_config(config_path)
                plan = OperationPlan([{"type": "add_audio", "line": "music", "line_index": 1}], [])
                result = ExecutionResult(
                    report=[
                        {
                            "operation": "add_audio",
                            "status": "needs_asset",
                            "message": "missing",
                            "line": "music",
                            "line_index": 1,
                            "details": {},
                        }
                    ],
                    warnings=[],
                    marker_times=[],
                    sticker_assets=[],
                    random_offsets=[],
                )
                build_draft(config, input_path, root / "assets", operation_plan=plan, execution_result=result)
            finally:
                os.chdir(old_cwd)

            draft_name = make_draft_name_for_content("no_empty_audio", "step one")
            content = json.loads((draft_dir / draft_name / "draft_content.json").read_text(encoding="utf-8"))
            self.assertNotIn("audio", [track.get("type") for track in content["tracks"]])

    def test_build_draft_can_hide_instruction_text_in_operations_mode(self):
        old_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            draft_dir = root / "drafts"
            draft_dir.mkdir()
            input_path = root / "content.txt"
            input_path.write_text("添加旋彩光圈贴纸\n右键新建复合片段", encoding="utf-8")
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "draft_folder": str(draft_dir),
                        "draft_name": "operation_visual",
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
            os.chdir(root)
            try:
                config = load_config(config_path)
                build_draft(config, input_path, root / "assets", include_text=False)
            finally:
                os.chdir(old_cwd)

            draft_name = make_draft_name_for_content("operation_visual", input_path.read_text(encoding="utf-8"))
            content = json.loads((draft_dir / draft_name / "draft_content.json").read_text(encoding="utf-8"))
            self.assertNotIn("text", [track.get("type") for track in content["tracks"]])


if __name__ == "__main__":
    unittest.main()
