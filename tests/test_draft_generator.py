import tempfile
import unittest
from pathlib import Path

from draft_generator import (
    DEFAULT_FALLBACK_TRANSITIONS,
    ensure_assets,
    extract_text_from_html,
    pick_transition,
    split_into_cards,
)


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


if __name__ == "__main__":
    unittest.main()
