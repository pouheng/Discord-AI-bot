"""
完整整合測試 — 虛擬請求模擬所有模組
=====================================================================
使用方式：
    python -m unittest test_all.py -v          # 全部測試
    python -m unittest test_all.TestTarot -v   # 只測塔羅

涵蓋模組：
  ✓ main.py (database · phase 1/2/3 · on_message · logging · config)
  ✓ tarot.py (trigger · draw · custom card · spread · embed)
  ✓ autopilot.py (enable/disable · add/list · memory)
  ✓ say_cmd.py (register_say · imports)
  ✓ gods_eye.py (register_commands)
  ✗ editor.py / manager.py / db_viewer.py (Tkinter GUI，無法 CLI 測試)

注意：autopilot / gods_eye 中的函數名稱已根據實際程式碼更新
      （is_autopilot_enabled · get_autopilot_injection · register_commands）
=====================================================================
"""

import sys, os, json, re, random, asyncio, datetime, tempfile, unittest
from unittest.mock import MagicMock, AsyncMock, PropertyMock, patch, call

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# ──────────────────────── Mock 外部模組 ────────────────────────
_mock_discord = MagicMock()
_mock_discord_ext = MagicMock()
_mock_discord_ext_commands = MagicMock()
_mock_discord_app = MagicMock()
_mock_discord.Message = MagicMock
_mock_discord.File = MagicMock
_mock_discord.Embed = MagicMock
_mock_discord.ui = MagicMock()
_mock_discord.ui.Select = MagicMock
_mock_discord.ui.View = MagicMock
_mock_discord.ui.Button = MagicMock

class _MockModal:
    """Allow class definitions with keyword args (title=...)"""
    def __init_subclass__(cls, **kw): pass
    def __init__(self, *a, **kw): pass
_mock_discord.ui.Modal = _MockModal

_mock_discord.Interaction = MagicMock
_mock_discord.Thread = MagicMock

sys.modules["discord"] = _mock_discord
sys.modules["discord.ext"] = _mock_discord_ext
sys.modules["discord.ext.commands"] = _mock_discord_ext_commands
sys.modules["discord.app_commands"] = _mock_discord_app
sys.modules["openai"] = MagicMock()
sys.modules["aiosqlite"] = MagicMock()
sys.modules["aiofiles"] = MagicMock()
sys.modules["dotenv"] = MagicMock()

os.environ["DISCORD_TOKEN"] = "fake_token_123"
os.environ["DEEPSEEK_API_KEY"] = "fake_key_456"

import importlib

# ── 載入 main.py ──
_spec_main = importlib.util.spec_from_file_location(
    "main_module", os.path.join(SCRIPT_DIR, "main.py")
)
_main_mod = importlib.util.module_from_spec(_spec_main)
_mock_bot_instance = MagicMock()
_mock_bot_instance.user = MagicMock()
_mock_bot_instance.user.id = 123456
_mock_bot_instance.user.mentioned_in = MagicMock(return_value=False)
_mock_bot_instance.process_commands = AsyncMock()
_patches = [
    patch("discord.ext.commands.Bot", return_value=_mock_bot_instance),
    patch("builtins.print"),
]
for p in _patches:
    p.start()
_spec_main.loader.exec_module(_main_mod)
for p in _patches:
    p.stop()

import main as M
M.bot = _mock_bot_instance

# ── 載入 tarot.py ──
_spec_tarot = importlib.util.spec_from_file_location(
    "tarot_module", os.path.join(SCRIPT_DIR, "tarot.py")
)
_tarot_mod = importlib.util.module_from_spec(_spec_tarot)
with patch("builtins.print"):
    _spec_tarot.loader.exec_module(_tarot_mod)
import tarot

# ── 載入 autopilot.py（模擬 main module 作為相依） ──
sys.modules["main"] = _main_mod
_spec_auto = importlib.util.spec_from_file_location(
    "autopilot_module", os.path.join(SCRIPT_DIR, "autopilot.py")
)
_auto_mod = importlib.util.module_from_spec(_spec_auto)
with patch("builtins.print"):
    _spec_auto.loader.exec_module(_auto_mod)
import autopilot

# ── 載入 gods_eye.py ──
_spec_ge = importlib.util.spec_from_file_location(
    "gods_eye_module", os.path.join(SCRIPT_DIR, "gods_eye.py")
)
_ge_mod = importlib.util.module_from_spec(_spec_ge)
with patch("builtins.print"):
    _spec_ge.loader.exec_module(_ge_mod)
import gods_eye

# ── 載入 say_cmd.py ──
_spec_say = importlib.util.spec_from_file_location(
    "say_cmd_module", os.path.join(SCRIPT_DIR, "say_cmd.py")
)
_say_mod = importlib.util.module_from_spec(_spec_say)
with patch("builtins.print"):
    _spec_say.loader.exec_module(_say_mod)
import say_cmd


# ═══════════════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════════════

def _silent(fn):
    """Decorator: run function with builtins.print patched out."""
    def wrapper(*args, **kw):
        with patch("builtins.print"):
            return fn(*args, **kw)
    return wrapper


def _make_mock_conn():
    """Create a mocked aiosqlite connection."""
    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(return_value=None)
    cursor.fetchall = AsyncMock(return_value=[])
    conn.execute = AsyncMock(return_value=cursor)
    conn.executemany = AsyncMock()
    conn.commit = AsyncMock()
    conn.close = AsyncMock()
    return conn


# ═══════════════════════════════════════════════════════════════
#  測試案例
# ═══════════════════════════════════════════════════════════════

class TestImports(unittest.TestCase):
    """確認所有模組載入成功"""
    def test_main_loaded(self):
        self.assertTrue(hasattr(M, "on_message"))
        self.assertTrue(hasattr(M, "load_prompt_config"))
        self.assertTrue(hasattr(M, "get_db"))
        self.assertTrue(hasattr(M, "_log_error"))

    def test_tarot_loaded(self):
        self.assertTrue(hasattr(tarot, "draw_cards"))
        self.assertTrue(hasattr(tarot, "check_tarot_trigger"))
        self.assertTrue(hasattr(tarot, "last_tarot"))

    def test_autopilot_loaded(self):
        self.assertTrue(hasattr(autopilot, "is_autopilot_enabled"))
        self.assertTrue(hasattr(autopilot, "register_commands"))

    def test_gods_eye_loaded(self):
        self.assertTrue(hasattr(gods_eye, "register_commands"))

    def test_say_cmd_loaded(self):
        self.assertTrue(hasattr(say_cmd, "register_say"))


class TestConfig(unittest.TestCase):
    """config.json 讀取與設定結構"""
    def test_load_prompt_config_returns_dict(self):
        async def run():
            cfg = await M.load_prompt_config()
            self.assertIsInstance(cfg, dict)
        asyncio.run(run())

    def test_config_has_required_keys(self):
        async def run():
            cfg = await M.load_prompt_config()
            for key in ["character_name", "character_identity", "ooc_persona",
                         "ooc_chat_examples", "safety_rules", "jailbreak",
                         "planning_template", "dialogue_ratio", "naming_rule",
                         "banned_words", "blocked_keywords", "allowed_servers",
                         "maint_threshold", "social_awareness", "available_emojis"]:
                self.assertIn(key, cfg, f"缺少設定鍵: {key}")
        asyncio.run(run())


class TestDatabase(unittest.TestCase):
    """資料庫 CRUD 操作"""
    async def _test_return_none_or_empty(self, func, *args, **kw):
        conn = _make_mock_conn()
        with patch("aiosqlite.connect", AsyncMock(return_value=conn)):
            return await func(*args, **kw)

    def test_get_character_name(self):
        r = asyncio.run(self._test_return_none_or_empty(M.get_character_name))
        self.assertIsNone(r)

    def test_get_server_rules(self):
        r = asyncio.run(self._test_return_none_or_empty(M.get_server_rules, 12345))
        self.assertEqual(r, "")

    def test_get_active_quests(self):
        r = asyncio.run(self._test_return_none_or_empty(M.get_active_quests))
        self.assertEqual(r, "")

    def test_get_channel_summary(self):
        r = asyncio.run(self._test_return_none_or_empty(M.get_channel_summary, "123"))
        self.assertEqual(r, "")

    def test_get_lore_catalog(self):
        r = asyncio.run(self._test_return_none_or_empty(M.get_lore_catalog))
        self.assertEqual(r, [])

    def test_list_character_names(self):
        r = asyncio.run(self._test_return_none_or_empty(M.list_character_names))
        self.assertEqual(r, [])

    def test_save_memory_constructs_insert_sql(self):
        async def run():
            conn = _make_mock_conn()
            with patch("aiosqlite.connect", AsyncMock(return_value=conn)):
                await M.save_user_memory("u1", "t", "c")
                sql = conn.execute.call_args[0][0]
                self.assertIn("INSERT INTO memories", sql)
        asyncio.run(run())

    def test_save_self_memory_constructs_insert_sql(self):
        async def run():
            conn = _make_mock_conn()
            with patch("aiosqlite.connect", AsyncMock(return_value=conn)):
                await M.save_self_memory("t", "c")
                sql = conn.execute.call_args[0][0]
                self.assertIn("INSERT INTO", sql)
        asyncio.run(run())


class TestTarot(unittest.TestCase):
    """塔羅牌模組全面測試"""
    def setUp(self):
        self._cleanup = []

    def tearDown(self):
        with patch("builtins.print"):
            for cid in self._cleanup:
                tarot.remove_custom_card(cid)

    def _reg(self, card_id, name_en="T", name_zh="測試", up="u", rev="r", suit=None):
        with patch("builtins.print"):
            c = tarot.register_custom_card(card_id, name_en, name_zh, up, rev, suit)
            self._cleanup.append(card_id)
            return c

    # -- trigger --
    def test_trigger_all_keywords(self):
        for kw in tarot.TRIGGER_KEYWORDS:
            self.assertTrue(tarot.check_tarot_trigger(kw), f"未命中: {kw}")

    def test_trigger_no_false_positive(self):
        self.assertFalse(tarot.check_tarot_trigger(""))
        self.assertFalse(tarot.check_tarot_trigger("  "))
        self.assertFalse(tarot.check_tarot_trigger("abcdefg"))
        self.assertFalse(tarot.check_tarot_trigger("三"))

    # -- draw --
    def test_draw_1_to_5(self):
        for n in range(1, 6):
            self.assertEqual(len(tarot.draw_cards(n)), n)

    def test_draw_all_unique(self):
        cards = tarot.draw_cards(5)
        ids = [c.card_id for c in cards]
        self.assertEqual(len(ids), len(set(ids)))

    def test_draw_78_returns_all(self):
        ids = [c.card_id for c in tarot.draw_cards(78)]
        self.assertEqual(len(set(ids)), 78)

    def test_drawn_cards_have_reversed(self):
        for c in tarot.draw_cards(10):
            self.assertIn(c.reversed, (True, False))

    # -- custom cards --
    def test_register_and_remove(self):
        self._reg("test_cr_01", "CR1", "測試一")
        self.assertIn("test_cr_01", tarot.ALL_CARDS)
        tarot.remove_custom_card("test_cr_01")
        self._cleanup.remove("test_cr_01")
        self.assertNotIn("test_cr_01", tarot.CUSTOM_CARDS)

    def test_register_duplicate_raises(self):
        self._reg("test_dup", "Dup", "重複")
        with self.assertRaises(ValueError):
            self._reg("test_dup", "Dup2", "重複二")

    def test_register_builtin_id_raises(self):
        with self.assertRaises(ValueError):
            self._reg("major_00", "Fool", "愚者")

    def test_custom_card_in_draw_pool(self):
        self._reg("test_draw_pool", "Pool", "池子")
        ids = [c.card_id for c in tarot.draw_cards(78)]
        self.assertIn("test_draw_pool", ids)

    # -- TarotCard --
    def test_card_display_name(self):
        c = tarot.TarotCard("x", "EN", "中文", "正", "逆")
        c.reversed = False
        self.assertIn("中文", c.get_display_name())
        c.reversed = True
        self.assertIn("逆位", c.get_display_name())

    def test_card_meaning(self):
        c = tarot.TarotCard("x", "E", "中", "正位意義", "逆位意義")
        c.reversed = False
        self.assertEqual(c.get_meaning(), "正位意義")
        c.reversed = True
        self.assertEqual(c.get_meaning(), "逆位意義")

    def test_card_has_image(self):
        c = tarot.TarotCard("x", "E", "中", "u", "r")
        self.assertFalse(c.has_image())

    # -- spreads --
    def test_spread_single(self):
        c = tarot.draw_cards(1)[0]
        d = tarot.build_spread_description([c], "單張")
        self.assertIn(c.name_zh, d)

    def test_spread_three(self):
        d = tarot.build_spread_description(tarot.draw_cards(3), "三張")
        for p in ("過去", "現在", "未來"):
            self.assertIn(p, d)

    def test_spread_cross(self):
        d = tarot.build_spread_description(tarot.draw_cards(5), "十字")
        for p in ("現狀", "阻礙", "潛意識", "建議", "發展"):
            self.assertIn(p, d)

    # -- embed (smoke: mock discord, only verify no crash) --
    def test_embed_single(self):
        e = tarot.build_tarot_embed(tarot.draw_cards(1), "單張", "x", "u")
        self.assertIsNotNone(e)

    def test_embed_three(self):
        e = tarot.build_tarot_embed(tarot.draw_cards(3), "三張", "x", "u")
        self.assertIsNotNone(e)

    def test_embed_cross(self):
        e = tarot.build_tarot_embed(tarot.draw_cards(5), "十字", "x", "u")
        self.assertIsNotNone(e)

    def test_embed_long_text_no_crash(self):
        e = tarot.build_tarot_embed(tarot.draw_cards(1), "單張", "x" * 2000, "u")
        self.assertIsNotNone(e)

    # -- register_tarot --
    def test_register_tarot_adds_command(self):
        mb = MagicMock()
        mb.tree = MagicMock()
        mb.tree.command = MagicMock(return_value=lambda f: f)
        with patch("builtins.print"):
            tarot.register_tarot(mb)
        mb.tree.command.assert_called_once()

    # -- generate_placeholder_images (smoke) --
    def test_generate_placeholder_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            orig = tarot.TAROT_IMAGE_DIR
            tarot.TAROT_IMAGE_DIR = tmp
            try:
                asyncio.run(tarot.generate_placeholder_images())
                self.assertGreater(len(os.listdir(tmp)), 0)
            finally:
                tarot.TAROT_IMAGE_DIR = orig


class TestAutopilot(unittest.TestCase):
    """NPC 托管模組"""
    def test_is_autopilot_enabled(self):
        async def run():
            val = await autopilot.is_autopilot_enabled()
            self.assertIsInstance(val, bool)
        asyncio.run(run())

    def test_get_active_autopilot_chars(self):
        async def run():
            chars = await autopilot.get_active_autopilot_chars()
            self.assertIsInstance(chars, list)
        asyncio.run(run())

    def test_get_autopilot_injection(self):
        async def run():
            msg = MagicMock()
            inj = await autopilot.get_autopilot_injection(msg, "ic")
            self.assertIsInstance(inj, str)
        asyncio.run(run())

    def test_register_commands(self):
        mb = MagicMock()
        mb.tree = MagicMock()
        mb.tree.command = MagicMock(return_value=lambda f: f)
        autopilot.register_commands(mb)
        self.assertTrue(mb.tree.command.called)

    def test_strip_japanese_original(self):
        result = autopilot.strip_japanese_original("テスト【original】")
        self.assertIsInstance(result, str)


class TestGodsEye(unittest.TestCase):
    """神之眼模組"""
    def test_register_commands(self):
        mb = MagicMock()
        mb.tree = MagicMock()
        mb.tree.command = MagicMock(return_value=lambda f: f)
        gods_eye.register_commands(mb)
        self.assertTrue(mb.tree.command.called)


class TestSayCmd(unittest.TestCase):
    """/say 指令模組"""
    def test_register_say(self):
        mb = MagicMock()
        mb.tree = MagicMock()
        mb.tree.command = MagicMock(return_value=lambda f: f)
        say_cmd.register_say(mb)
        self.assertTrue(mb.tree.command.called)


class TestLogging(unittest.TestCase):
    """日誌系統"""
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig = M.ERROR_LOG_DIR
        M.ERROR_LOG_DIR = self._tmp.name

    def tearDown(self):
        M.ERROR_LOG_DIR = self._orig
        self._tmp.cleanup()

    def test_log_error_writes_file(self):
        async def run():
            await M._log_error("test_event", "測試錯誤", extra="abc")
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            path = os.path.join(self._tmp.name, f"{today}.txt")
            self.assertTrue(os.path.exists(path))
            content = open(path, encoding="utf-8").read()
            self.assertIn("test_event", content)
            self.assertIn("extra=abc", content)
        asyncio.run(run())

    def test_log_error_handles_unicode(self):
        async def run():
            await M._log_error("中文事件", "中文錯誤訊息！")
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            path = os.path.join(self._tmp.name, f"{today}.txt")
            content = open(path, encoding="utf-8").read()
            self.assertIn("中文事件", content)
        asyncio.run(run())

    def test_log_error_no_crash_on_failure(self):
        async def run():
            with patch("os.makedirs", side_effect=PermissionError):
                await M._log_error("t", "should not crash")
        asyncio.run(run())

    def test_write_daily_log(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                orig = M.SCRIPT_DIR
                M.SCRIPT_DIR = tmp
                try:
                    await M._write_daily_log("2026-07-21", ["測試行"])
                    path = os.path.join(tmp, "knowledge_logs", "2026-07-21.txt")
                    self.assertTrue(os.path.exists(path))
                finally:
                    M.SCRIPT_DIR = orig
        asyncio.run(run())


class TestEditMessage(unittest.TestCase):
    """AI 編輯訊息"""
    def test_review_edit_suggestion_returns_dict(self):
        async def run():
            r = await M._review_edit_suggestion("原始", "建議")
            self.assertIsInstance(r, dict)
        asyncio.run(run())

    def test_ai_edit_message_returns_none(self):
        async def run():
            r = await M._ai_edit_message("原始", "找", "改")
            self.assertIsNone(r)
        asyncio.run(run())


class TestPipelinePhase1(unittest.TestCase):
    """Phase 1 記憶召回"""
    def test_run_phase1_returns_dict(self):
        async def run():
            result = await M.run_phase1(
                messages=[{"role": "user", "content": "你好"}],
                char_name="澪", identity="測試", config={},
                channel_context="測試頻道", reply_context="", char_profile="",
            )
            for key in ("recall", "lore_topics", "supplement"):
                self.assertIn(key, result)
        asyncio.run(run())

    def test_run_phase1_with_tarot_injection(self):
        async def run():
            with patch("builtins.print"):
                card = tarot.draw_cards(1)[0]
                tarot.last_tarot = {"cards": [card], "who_for": "u",
                                    "spread": "單張",
                                    "timestamp": datetime.datetime.now().timestamp(),
                                    "files": []}
            result = await M.run_phase1(
                messages=[{"role": "user", "content": "抽牌"}],
                char_name="澪", identity="測試", config={},
                channel_context="", reply_context="", char_profile="",
            )
            self.assertIsInstance(result, dict)
            tarot.last_tarot = None
        asyncio.run(run())


class TestPipelinePhase2(unittest.TestCase):
    """Phase 2 回覆生成"""
    def _recall(self):
        return {"recall": [], "lore_topics": [], "supplement": ""}

    def test_build_ic_prompt(self):
        async def run():
            p = await M.build_phase2_system_prompt(
                char_name="澪", identity="機械義手", config={},
                recall=self._recall(), channel_context="測試", reply_context="",
                is_ooc=False, msg_count=5, char_profile="",
                npc_injection="", server_rules="", self_memory="", quests="",
            )
            self.assertIsInstance(p, str)
            self.assertGreater(len(p), 100)
            self.assertIn("澪", p)
        asyncio.run(run())

    def test_build_ooc_prompt(self):
        async def run():
            cfg = {"ooc_persona": "中之人", "ooc_chat_examples": []}
            p = await M.build_phase2_system_prompt(
                char_name="澪", identity="機械義手", config=cfg,
                recall=self._recall(), channel_context="", reply_context="",
                is_ooc=True, msg_count=5, char_profile="",
                npc_injection="", server_rules="", self_memory="", quests="",
            )
            self.assertIn("中之人", p)
        asyncio.run(run())

    def test_tarot_injected_into_prompt(self):
        async def run():
            with patch("builtins.print"):
                card = tarot.draw_cards(1)[0]
                tarot.last_tarot = {"cards": [card], "who_for": "u",
                                    "spread": "單張",
                                    "timestamp": datetime.datetime.now().timestamp(),
                                    "files": []}
            p = await M.build_phase2_system_prompt(
                char_name="澪", identity="測試", config={},
                recall=self._recall(), channel_context="", reply_context="",
                is_ooc=False, msg_count=5, char_profile="",
                npc_injection="", server_rules="", self_memory="", quests="",
            )
            self.assertIn(card.name_zh, p)
            tarot.last_tarot = None
        asyncio.run(run())


class TestPipelinePhase3(unittest.TestCase):
    """Phase 3 記憶寫入"""
    def test_phase3_returns_list(self):
        async def run():
            r = await M.phase3_process(
                message=MagicMock(), raw_content="測試",
                char_name="澪",
                recall={"recall": [], "lore_topics": [], "supplement": ""},
                config={}, char_profile="", server_id="123",
            )
            self.assertIsInstance(r, list)
        asyncio.run(run())

    def test_phase3_with_learn_tag(self):
        async def run():
            r = await M.phase3_process(
                message=MagicMock(), raw_content="[LEARN 測試]",
                char_name="澪",
                recall={"recall": [], "lore_topics": [], "supplement": ""},
                config={}, char_profile="", server_id="123",
            )
            self.assertIsInstance(r, list)
        asyncio.run(run())


class TestOnMessageMentionGuard(unittest.TestCase):
    """on_message mention guard"""
    def setUp(self):
        self.msg = MagicMock()
        self.msg.author = MagicMock()
        self.msg.author.id = 99999
        self.msg.id = 88888
        self.msg.guild = MagicMock()
        self.msg.guild.id = 12345
        self.msg.content = "hi"
        self.msg.channel = MagicMock()
        self.msg.channel.send = AsyncMock()
        self.msg.channel.typing = MagicMock()
        self.msg.channel.typing.return_value.__aenter__ = AsyncMock()
        self.msg.channel.typing.return_value.__aexit__ = AsyncMock()
        self.msg.reference = None
        self.msg.role_mentions = []
        self.msg.attachments = []
        self.msg.author.display_name = "Tester"
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=False)
        _mock_bot_instance.process_commands = AsyncMock()
        M._processing_messages.clear()

    def test_no_mention_skips(self):
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=False)
        asyncio.run(M.on_message(self.msg))
        self.msg.channel.typing.assert_not_called()

    def test_mention_triggers(self):
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=True)
        asyncio.run(M.on_message(self.msg))
        self.msg.channel.typing.assert_called()

    def test_reply_to_bot_triggers(self):
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=False)
        replied = MagicMock()
        replied.author = MagicMock()
        replied.author.id = 123456
        replied.content = "原始"
        self.msg.reference = MagicMock()
        self.msg.reference.resolved = replied
        asyncio.run(M.on_message(self.msg))
        self.msg.channel.typing.assert_called()

    def test_bot_own_message_skipped(self):
        self.msg.author.id = 123456
        asyncio.run(M.on_message(self.msg))
        self.msg.channel.typing.assert_not_called()


class TestOnMessageTarotFlow(unittest.TestCase):
    """塔羅 on_message 整合"""
    def setUp(self):
        self.msg = MagicMock()
        self.msg.author = MagicMock()
        self.msg.author.id = 99999
        self.msg.id = 77777
        self.msg.guild = MagicMock()
        self.msg.guild.id = 12345
        self.msg.content = "幫我抽牌"
        self.msg.channel = MagicMock()
        self.msg.channel.send = AsyncMock()
        self.msg.channel.typing = MagicMock()
        self.msg.channel.typing.return_value.__aenter__ = AsyncMock()
        self.msg.channel.typing.return_value.__aexit__ = AsyncMock()
        self.msg.reference = None
        self.msg.role_mentions = []
        self.msg.attachments = []
        self.msg.author.display_name = "Tester"
        tarot.last_tarot = None
        M._processing_messages.clear()

    def test_tarot_sets_last_tarot(self):
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=True)
        asyncio.run(M.on_message(self.msg))
        self.assertIsNotNone(tarot.last_tarot)
        self.assertEqual(tarot.last_tarot["spread"], "單張")

    def test_tarot_three_card(self):
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=True)
        self.msg.content = "幫我抽三張"
        asyncio.run(M.on_message(self.msg))
        self.assertIsNotNone(tarot.last_tarot)
        self.assertEqual(tarot.last_tarot["spread"], "三張")

    def test_tarot_no_mention_does_nothing(self):
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=False)
        tarot.last_tarot = None
        asyncio.run(M.on_message(self.msg))
        self.assertIsNone(tarot.last_tarot)


class TestOnMessageErrorHandling(unittest.TestCase):
    """on_message 異常處理"""
    def setUp(self):
        self.msg = MagicMock()
        self.msg.author = MagicMock()
        self.msg.author.id = 99999
        self.msg.id = 66666
        self.msg.guild = MagicMock()
        self.msg.guild.id = 12345
        self.msg.content = "hi"
        self.msg.channel = MagicMock()
        self.msg.channel.send = AsyncMock()
        self.msg.channel.typing = MagicMock()
        self.msg.channel.typing.return_value.__aenter__ = AsyncMock(side_effect=Exception("模擬錯誤"))
        self.msg.channel.typing.return_value.__aexit__ = AsyncMock()
        self.msg.reference = None
        self.msg.role_mentions = []
        self.msg.attachments = []
        self.msg.author.display_name = "Tester"
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=True)
        M._processing_messages.clear()

    def test_error_caught_by_handler(self):
        try:
            asyncio.run(M.on_message(self.msg))
        except Exception:
            self.fail("on_message 應捕捉內部異常")


class TestProfileAndIdentity(unittest.TestCase):
    """角色檔案"""
    def test_format_character_profile(self):
        async def run():
            r = await M.format_character_profile("澪")
            self.assertIsInstance(r, str)
        asyncio.run(run())

    def test_get_character_identity(self):
        async def run():
            r = await M.get_character_identity()
            self.assertIsInstance(r, str)
        asyncio.run(run())

    def test_list_character_names(self):
        async def run():
            r = await M.list_character_names()
            self.assertIsInstance(r, list)
        asyncio.run(run())


class TestKnowledge(unittest.TestCase):
    """知識管理"""
    def test_get_all_lore_full(self):
        async def run():
            r = await M.get_all_lore_full()
            self.assertIsInstance(r, str)
        asyncio.run(run())

    def test_get_lore_by_topics(self):
        async def run():
            r = await M.get_lore_by_topics(["測試"])
            self.assertIsInstance(r, list)
        asyncio.run(run())


class TestFetchMessages(unittest.TestCase):
    """訊息擷取"""
    def test_fetch_recent_messages(self):
        async def run():
            mc = AsyncMock()
            m = MagicMock()
            m.content = "測試"
            m.author.id = 1
            m.author.display_name = "T"
            mc.history.return_value.__aenter__ = AsyncMock(return_value=[m] * 3)
            r = await M.fetch_recent_messages(mc, 3)
            self.assertIsInstance(r, list)
        asyncio.run(run())

    def test_fetch_reply_chain(self):
        async def run():
            m = MagicMock()
            m.content = "測試"
            m.author.id = 1
            r = await M.fetch_reply_chain(m, 3)
            self.assertIsInstance(r, list)
        asyncio.run(run())


class TestBotLifecycle(unittest.TestCase):
    """Bot 生命週期"""
    def test_on_ready(self):
        async def run():
            with patch("tarot.ensure_images", AsyncMock()):
                with patch("tarot.register_tarot"):
                    await M.on_ready()
        asyncio.run(run())

    def test_init_db(self):
        with patch("sqlite3.connect"):
            M.init_db()


class TestHelpers(unittest.TestCase):
    """輔助函式"""
    def test_cleanup_old_sessions(self):
        async def run():
            with patch("os.path.isdir", return_value=False):
                await M._cleanup_old_sessions()
        asyncio.run(run())

    def test_snapshot_prompt_logs(self):
        async def run():
            with patch("os.path.isdir", return_value=True), \
                 patch("os.listdir", return_value=["last_system.txt"]), \
                 patch("os.makedirs"), patch("aiofiles.open"):
                await M._snapshot_prompt_logs("/tmp/session")
        asyncio.run(run())

    def test_save_item(self):
        async def run():
            conn = _make_mock_conn()
            with patch("aiosqlite.connect", AsyncMock(return_value=conn)):
                await M._save_item({"name": "道具", "description": "說明"})
                self.assertIn("INSERT INTO items", conn.execute.call_args[0][0])
        asyncio.run(run())

    def test_extract_json(self):
        text = '{"a": 1, "b": "hello"}'
        result = M.extract_json(text)
        self.assertEqual(result, {"a": 1, "b": "hello"})

    def test_extract_json_with_code_fence(self):
        text = '```json\n{"a": 1}\n```'
        result = M.extract_json(text)
        self.assertEqual(result, {"a": 1})

    def test_extract_json_nested(self):
        text = '{"nested": {"inner": [1, 2]}}'
        result = M.extract_json(text)
        self.assertEqual(result["nested"]["inner"], [1, 2])


class TestCrossModuleIntegration(unittest.TestCase):
    """跨模組整合"""
    def test_tarot_injection_in_phase2(self):
        async def run():
            with patch("builtins.print"):
                card = tarot.draw_cards(1)[0]
                tarot.last_tarot = {"cards": [card], "who_for": "u",
                                    "spread": "單張",
                                    "timestamp": datetime.datetime.now().timestamp(),
                                    "files": []}
            p = await M.build_phase2_system_prompt(
                char_name="澪", identity="測試", config={},
                recall={"recall": [], "lore_topics": [], "supplement": ""},
                channel_context="", reply_context="",
                is_ooc=False, msg_count=5, char_profile="",
                npc_injection="", server_rules="", self_memory="", quests="",
            )
            self.assertIn(card.name_zh, p)
            tarot.last_tarot = None
        asyncio.run(run())

    def test_all_modules_importable(self):
        self.assertTrue(hasattr(tarot, "register_tarot"))
        self.assertTrue(hasattr(autopilot, "register_commands"))
        self.assertTrue(hasattr(gods_eye, "register_commands"))
        self.assertTrue(hasattr(say_cmd, "register_say"))

    def test_config_influences_prompt(self):
        async def run():
            cfg = {"ooc_persona": "獨特人格", "ooc_chat_examples": []}
            p = await M.build_phase2_system_prompt(
                char_name="Bot", identity="AI", config=cfg,
                recall={"recall": [], "lore_topics": [], "supplement": ""},
                channel_context="", reply_context="",
                is_ooc=True, msg_count=5, char_profile="",
                npc_injection="", server_rules="", self_memory="", quests="",
            )
            self.assertIn("獨特人格", p)
        asyncio.run(run())

    def test_on_message_calls_process_commands_for_non_mention(self):
        self.msg = MagicMock()
        self.msg.author = MagicMock()
        self.msg.author.id = 99999
        self.msg.id = 55555
        self.msg.guild = MagicMock()
        self.msg.guild.id = 12345
        self.msg.content = "一般訊息"
        self.msg.channel = MagicMock()
        self.msg.reference = None
        self.msg.role_mentions = []
        _mock_bot_instance.user.mentioned_in = MagicMock(return_value=False)
        _mock_bot_instance.process_commands = AsyncMock()
        M._processing_messages.clear()

        asyncio.run(M.on_message(self.msg))
        _mock_bot_instance.process_commands.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
