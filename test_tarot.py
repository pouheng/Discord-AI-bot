"""離線塔羅模組測試：關鍵詞偵測、抽牌邏輯、自訂牌、文字生成"""

import sys, os, random, unittest
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

sys.modules["discord"] = MagicMock()
sys.modules["discord.ext"] = MagicMock()
sys.modules["discord.ext.commands"] = MagicMock()
sys.modules["discord.app_commands"] = MagicMock()
sys.modules["openai"] = MagicMock()
sys.modules["dotenv"] = MagicMock()

os.environ["DISCORD_TOKEN"] = "fake_token"
os.environ["DEEPSEEK_API_KEY"] = "fake_key"

import importlib
spec = importlib.util.spec_from_file_location(
    "tarot_module", os.path.join(SCRIPT_DIR, "tarot.py")
)
mod = importlib.util.module_from_spec(spec)
with patch("builtins.print"):
    spec.loader.exec_module(mod)

import tarot


class TestTarotTrigger(unittest.TestCase):
    def test_trigger_matches_chou_pai(self):
        self.assertTrue(tarot.check_tarot_trigger("幫我抽牌"))

    def test_trigger_matches_ta_luo(self):
        self.assertTrue(tarot.check_tarot_trigger("塔羅牌占卜"))

    def test_trigger_matches_english(self):
        self.assertTrue(tarot.check_tarot_trigger("draw a tarot card"))

    def test_trigger_no_false_positive(self):
        self.assertFalse(tarot.check_tarot_trigger("今天天氣真好"))

    def test_trigger_no_false_chou_shui_fei(self):
        self.assertFalse(tarot.check_tarot_trigger("我們去抽水肥"))


class TestTarotDraw(unittest.TestCase):
    def test_draw_one_card(self):
        cards = tarot.draw_cards(1)
        self.assertEqual(len(cards), 1)
        self.assertIsInstance(cards[0], tarot.TarotCard)

    def test_draw_three_cards(self):
        cards = tarot.draw_cards(3)
        self.assertEqual(len(cards), 3)
        for c in cards:
            self.assertIsInstance(c, tarot.TarotCard)

    def test_drawn_cards_have_reversed(self):
        random.seed(42)
        cards = tarot.draw_cards(3)
        for c in cards:
            self.assertIsInstance(c.reversed, bool)

    def test_all_cards_populated(self):
        self.assertEqual(len(tarot.CARD_LIST), 78)

    def test_major_arcana_count(self):
        major = [c for c in tarot.CARD_LIST if c.is_major]
        self.assertEqual(len(major), 22)


class TestTarotCustomCards(unittest.TestCase):
    def _reg(self, *args, **kw):
        with patch("builtins.print"):
            return tarot.register_custom_card(*args, **kw)

    def _rm(self, cid):
        with patch("builtins.print"):
            return tarot.remove_custom_card(cid)

    def test_register_custom_card(self):
        card = self._reg("test_custom_01", "Test Card", "測試牌", "up", "rev", "cups")
        self.assertEqual(card.card_id, "test_custom_01")
        self.assertIn("test_custom_01", tarot.CUSTOM_CARDS)
        self.assertIn("test_custom_01", tarot.ALL_CARDS)
        self._rm("test_custom_01")

    def test_register_duplicate_raises(self):
        self._reg("test_dup", "Dup", "重複", "up", "rev")
        with self.assertRaises(ValueError):
            self._reg("test_dup", "Dup2", "重複2", "up2", "rev2")
        self._rm("test_dup")

    def test_register_builtin_id_raises(self):
        with self.assertRaises(ValueError):
            self._reg("major_00", "Fool", "愚者", "up", "rev")

    def test_remove_custom_card(self):
        self._reg("test_remove_me", "Remove", "移除", "up", "rev")
        self.assertTrue(tarot.remove_custom_card("test_remove_me"))
        self.assertNotIn("test_remove_me", tarot.CUSTOM_CARDS)
        self.assertNotIn("test_remove_me", tarot.ALL_CARDS)

    def test_remove_nonexistent(self):
        self.assertFalse(tarot.remove_custom_card("nonexistent"))

    def test_custom_card_in_draw_pool(self):
        self._reg("custom_draw_pool", "Pool", "池子", "up", "rev")
        ids = [c.card_id for c in tarot.draw_cards(78)]
        self.assertIn("custom_draw_pool", ids)
        self._rm("custom_draw_pool")

    def test_custom_card_not_in_list_after_remove(self):
        self._reg("test_list_rm", "ListRm", "列表移除", "up", "rev")
        self.assertIn(tarot.CUSTOM_CARDS["test_list_rm"], tarot.CARD_LIST)
        tarot.remove_custom_card("test_list_rm")
        self.assertIsNone(tarot.CUSTOM_CARDS.get("test_list_rm"))


class TestTarotSpreads(unittest.TestCase):
    def test_spread_single(self):
        card = tarot.draw_cards(1)[0]
        desc = tarot.build_spread_description([card], "單張")
        self.assertIn(card.name_zh, desc)

    def test_spread_three(self):
        cards = tarot.draw_cards(3)
        desc = tarot.build_spread_description(cards, "三張")
        for w in ("過去", "現在", "未來"):
            self.assertIn(w, desc)


class TestTarotCardClass(unittest.TestCase):
    def test_display_name_upright(self):
        c = tarot.TarotCard("tc", "TC", "測試卡", "正位", "逆位")
        c.reversed = False
        self.assertIn("測試卡", c.get_display_name())

    def test_display_name_reversed(self):
        c = tarot.TarotCard("tc", "TC", "測試卡", "正位", "逆位")
        c.reversed = True
        self.assertIn("逆位", c.get_display_name())

    def test_meaning_upright(self):
        c = tarot.TarotCard("tc", "TC", "測試卡", "正位", "逆位")
        c.reversed = False
        self.assertEqual(c.get_meaning(), "正位")

    def test_meaning_reversed(self):
        c = tarot.TarotCard("tc", "TC", "測試卡", "正位", "逆位")
        c.reversed = True
        self.assertEqual(c.get_meaning(), "逆位")


class TestTarotEmbed(unittest.TestCase):
    def test_embed_single_smoke(self):
        card = tarot.draw_cards(1)[0]
        e = tarot.build_tarot_embed([card], "單張", "解讀", "user")
        self.assertIsNotNone(e)

    def test_embed_three_smoke(self):
        cards = tarot.draw_cards(3)
        e = tarot.build_tarot_embed(cards, "三張", "解讀", "user")
        self.assertIsNotNone(e)


if __name__ == "__main__":
    unittest.main()
