# -*- coding: utf-8 -*-
"""Rendering + output-boundary tests.

Design: strings stay FULL fidelity (░ + emoji, for Discord UTF-8); encoding
safety happens only at the output edge via collide_engine.enc_safe_for_stream.

Run:  python -m unittest test_clash_ui -v
"""
import unittest

import collide_engine
import collide_ui


class _Stream:
    """Minimal stand-in for a stdout-like object with an .encoding."""

    def __init__(self, encoding):
        self.encoding = encoding


class TestRenderClashBarFidelity(unittest.TestCase):
    def test_full_fidelity_keeps_shade(self):
        bar = collide_ui.render_clash_bar(120, 240)
        self.assertIn("█", bar)
        self.assertIn("░", bar)          # 全保真：不因 stdout 編碼而洗掉
        self.assertIn("12B/24B", bar)

    def test_reason_and_emoji_kept(self):
        bar = collide_ui.render_clash_bar(0, 240, "❌ 魔王 已被擊敗（HP 歸零）")
        self.assertIn("░", bar)
        self.assertIn("❌", bar)          # emoji 保留（給 Discord）

    def test_width_and_clamp(self):
        self.assertEqual(collide_ui.render_clash_bar(120, 240, width=4).split(" ")[0], "██░░")
        self.assertIn("24B/24B", collide_ui.render_clash_bar(999, 240))
        self.assertIn("0B/24B", collide_ui.render_clash_bar(-5, 240))


class TestOutputBoundary(unittest.TestCase):
    """enc_safe_for_stream：只在輸出邊界降級，字串本身不受影響。"""

    def test_cp950_stream_sanitized(self):
        text = collide_engine.format_clash_hp_bar(60, 240)
        out = collide_engine.enc_safe_for_stream(text, _Stream("cp950"))
        out.encode("cp950")              # 不得拋 UnicodeEncodeError
        self.assertNotIn("░", out)        # cp950 無 ░ → 降級
        self.assertIn("-", out)
        self.assertIn("█", out)           # cp950 有 █ → 保留

    def test_cp1252_stream_sanitized(self):
        text = collide_engine.format_clash_hp_bar(60, 240)
        out = collide_engine.enc_safe_for_stream(text, _Stream("cp1252"))
        out.encode("cp1252")
        self.assertNotIn("█", out)
        self.assertNotIn("░", out)

    def test_utf8_stream_unchanged(self):
        text = collide_engine.format_clash_hp_bar(60, 240)
        self.assertEqual(collide_engine.enc_safe_for_stream(text, _Stream("utf-8")), text)

    def test_terminal_composed_endgame_line_does_not_raise(self):
        # 殘留缺陷回歸：血條 + emoji 標籤的**實際輸出行**在 cp950 console 不得拋
        for outcome in ("defeated", "retreated", "sealed"):
            line = (
                f"{collide_engine.format_clash_hp_bar(0, 240)} "
                f"{collide_engine.clash_outcome_label(outcome, '魔王')}"
            )
            safe = collide_engine.enc_safe_for_stream(line, _Stream("cp950"))
            safe.encode("cp950")          # 修復前此處會拋

    def test_discord_payload_keeps_fidelity(self):
        # 回歸鎖：送 Discord 的字串仍含 ░ 與 emoji（不得為 console 而犧牲品質）
        line = (
            f"{collide_engine.format_clash_hp_bar(0, 240)} "
            f"{collide_engine.clash_outcome_label('defeated', '魔王')}"
        )
        self.assertIn("░", line)
        self.assertIn("❌", line)
        line.encode("utf-8")              # Discord 走 UTF-8


class TestOutcomeLabels(unittest.TestCase):
    def test_sealed_has_nonempty_label(self):
        self.assertTrue(collide_engine.clash_outcome_label("sealed", "魔王"))

    def test_active_is_empty(self):
        self.assertEqual(collide_engine.clash_outcome_label("active", "魔王"), "")

    def test_unknown_is_empty(self):
        self.assertEqual(collide_engine.clash_outcome_label("bogus", "魔王"), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
