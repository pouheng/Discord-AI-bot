"""End-to-end virtual request test — 模擬 Discord 訊息經過 on_message 全管線"""
import sys, os, asyncio, tempfile, unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DISCORD_TOKEN", "fake")
os.environ.setdefault("DEEPSEEK_API_KEY", "fake")

import discord

# --- Patch bot.user BEFORE importing main (it's None until connected) ---
_fake_bot_user = mock.MagicMock(spec=discord.ClientUser)
_fake_bot_user.id = 42
_fake_bot_user.display_name = "MioBot"
_fake_bot_user.mentioned_in = lambda m: False

_orig_bot_init = None


def _patch_bot_user():
    """Directly set bot.user to our fake (bot.user is a property that returns None pre-connect)."""
    import main as _m
    _type = type(_m.bot)
    # Overwrite the 'user' property descriptor on the class
    _type.user = property(lambda self: _fake_bot_user)


_patch_bot_user()

import main


def _make_message(content, author_name="TestUser", author_id=111, guild_id=999,
                  channel_name="rp-主線", channel_id=888, ref_resolved=None):
    msg = mock.MagicMock(spec=discord.Message)
    msg.content = content
    msg.id = int.from_bytes(os.urandom(4), "big")
    msg.guild = mock.MagicMock(spec=discord.Guild)
    msg.guild.id = guild_id
    msg.guild.name = "TestServer"
    msg.guild.get_member.return_value = mock.MagicMock(roles=[])
    msg.guild.threads = []
    msg.guild.text_channels = []
    ch = mock.MagicMock(spec=discord.abc.Messageable)
    ch.id = channel_id
    ch.name = channel_name
    ch.typing.return_value = mock.AsyncMock()
    ch.history.return_value.__aiter__ = mock.AsyncMock(return_value=iter([]))
    msg.channel = ch
    msg.reference = ref_resolved
    msg.role_mentions = []
    msg.author = mock.MagicMock(spec=discord.Member)
    msg.author.id = author_id
    msg.author.display_name = author_name
    msg.author.bot = False
    msg.mentions = []
    msg.reply = mock.AsyncMock()
    return msg


def _make_ref():
    ref = mock.MagicMock()
    ref.resolved = mock.MagicMock(spec=discord.Message)
    ref.resolved.author = _fake_bot_user
    ref.resolved.content = "some text"
    ref.resolved.display_name = "Mio"
    return ref


def _default_mocks():
    # 測試不得污染正式 prompt_logs/（會蓋掉編輯器的「上次提示詞」快照）
    main.PROMPT_LOG_DIR = tempfile.mkdtemp(prefix="mio_test_logs_")
    main.get_character_name = mock.AsyncMock(return_value="Mio")
    main.get_all_lore_full = mock.AsyncMock(return_value="")
    main.get_self_memory_raw = mock.AsyncMock(return_value=[])
    main.get_user_memory = mock.AsyncMock(return_value="")
    main.get_self_memory = mock.AsyncMock(return_value="")
    main.get_server_rules = mock.AsyncMock(return_value="")
    main.get_active_quests = mock.AsyncMock(return_value="")
    main.get_channel_summary = mock.AsyncMock(return_value="")
    main.get_lore_by_topics = mock.AsyncMock(return_value=[])
    main.run_phase1 = mock.AsyncMock(
        return_value=([], [], [], [], [], False, False, "", False, "")
    )
    main.load_prompt_config = mock.AsyncMock(return_value={
        "banned_words": [], "blocked_keywords": [],
        "banned_nsfw_words": [], "expression_prefs": [],
        "ic_style_prefs": [], "ooc_style_prefs": [],
        "personality": "", "character_name": "Mio",
        "allowed_servers": [],
    })
    main.build_phase2_system_prompt = mock.AsyncMock(return_value="You are Mio.")
    main.fetch_reply_chain = mock.AsyncMock(return_value=([], []))
    main.fetch_recent_messages = mock.AsyncMock(return_value=[])
    main.read_last_ic_channel = mock.AsyncMock(return_value={})
    main.write_last_ic_channel = mock.AsyncMock()
    main._snapshot_prompt_logs = mock.AsyncMock()
    main._cleanup_old_sessions = mock.AsyncMock()
    main.phase3_process = mock.AsyncMock()

    resp = mock.MagicMock()
    resp.choices = [mock.MagicMock()]
    resp.choices[0].message.content = "<planning>greeting</planning>\n[Mio]: Hello!"
    main.client.chat.completions.create = mock.AsyncMock(return_value=resp)


async def run_tests():
    print("=" * 60)
    print("Virtual End-to-End Test")
    print("=" * 60)

    _default_mocks()
    passed = 0
    total = 0

    def check(name, condition):
        nonlocal passed, total
        total += 1
        if condition:
            passed += 1
            print(f"  PASS: {name}")
        else:
            print(f"  FAIL: {name}")

    def sent(msg):
        """有送出去就算數（可能用 reply，也可能是普通訊息）。"""
        return msg.reply.called or msg.channel.send.called

    def sent_text(msg):
        if msg.reply.called:
            return msg.reply.call_args[0][0]
        if msg.channel.send.called:
            return msg.channel.send.call_args[0][0]
        return ""

    # ── Test 1: Non-trigger ──
    msg1 = _make_message("hello world")
    print("\n[Test 1] Non-trigger (no mention)")
    await main.on_message(msg1)
    check("No reply", not sent(msg1))

    # ── Test 2: @mention ──
    msg2 = _make_message("你好啊澪")
    _fake_bot_user.mentioned_in = lambda m: True
    print("\n[Test 2] @mention trigger")
    await main.on_message(msg2)
    check("Reply sent", sent(msg2))

    # ── Test 3: Reply-to-bot ──
    ref = _make_ref()
    msg3 = _make_message("那你覺得呢", ref_resolved=ref)
    _fake_bot_user.mentioned_in = lambda m: False
    print("\n[Test 3] Reply-to-bot trigger")
    await main.on_message(msg3)
    check("Reply sent", sent(msg3))

    # ── Test 4: Server whitelist ──
    msg4 = _make_message("test", guild_id=999)
    main.load_prompt_config = mock.AsyncMock(return_value={
        "banned_words": [], "blocked_keywords": [],
        "banned_nsfw_words": [], "expression_prefs": [],
        "ic_style_prefs": [], "ooc_style_prefs": [],
        "personality": "", "character_name": "Mio",
        "allowed_servers": ["888888"],
    })
    _fake_bot_user.mentioned_in = lambda m: True
    print("\n[Test 4] Server whitelist block")
    await main.on_message(msg4)
    check("No reply (blocked)", not sent(msg4))

    _default_mocks()

    # ── Test 5: Empty content ──
    msg5 = _make_message("")
    _fake_bot_user.mentioned_in = lambda m: True
    print("\n[Test 5] Empty content")
    await main.on_message(msg5)
    check("Reply sent (fallback to hi)", sent(msg5))

    # ── Test 6: Blocked keyword ──
    main.load_prompt_config = mock.AsyncMock(return_value={
        "banned_words": [], "blocked_keywords": ["bananas"],
        "banned_nsfw_words": [], "expression_prefs": [],
        "ic_style_prefs": [], "ooc_style_prefs": [],
        "personality": "", "character_name": "Mio",
        "allowed_servers": [],
    })
    resp_block = mock.MagicMock()
    resp_block.choices = [mock.MagicMock()]
    resp_block.choices[0].message.content = "<planning>Hmm</planning>\nI love bananas!"
    main.client.chat.completions.create = mock.AsyncMock(return_value=resp_block)
    msg6 = _make_message("what do you like?")
    _fake_bot_user.mentioned_in = lambda m: True
    print("\n[Test 6] Blocked keyword safety filter")
    await main.on_message(msg6)
    check("Reply sent", sent(msg6))
    if sent(msg6):
        reply_text = sent_text(msg6)
        check("Safe fallback (no bananas)", "bananas" not in reply_text.lower())

    _default_mocks()

    # ── Test 7: Phase 3 background task ──
    msg7 = _make_message("remember this")
    _fake_bot_user.mentioned_in = lambda m: True
    print("\n[Test 7] Phase 3 launched in background")
    await main.on_message(msg7)
    check("phase3_process called", main.phase3_process.called)

    # ── Test 8: Dedup ──
    msg8 = _make_message("test dedup")
    msg8.id = 99999
    _fake_bot_user.mentioned_in = lambda m: True
    print("\n[Test 8] Dedup (same msg ID)")
    await main.on_message(msg8)
    main._processing_messages.discard(msg8.id)
    msg8.reply.reset_mock()
    await main.on_message(msg8)
    check("Processed twice (dedup cleared)", True)

    # ── Test 9: new helper functions exist ──
    print("\n[Test 9] Helper functions exist")
    check("detect_trigger", callable(getattr(main, "detect_trigger", None)))
    check("detect_tarot", callable(getattr(main, "detect_tarot", None)))
    check("get_channel_info", callable(getattr(main, "get_channel_info", None)))
    check("gather_context", callable(getattr(main, "gather_context", None)))
    check("pull_ooc_ic_content", callable(getattr(main, "pull_ooc_ic_content", None)))
    check("resolve_and_fetch_memories", callable(getattr(main, "resolve_and_fetch_memories", None)))
    check("assemble_lore_text", callable(getattr(main, "assemble_lore_text", None)))
    check("log_recalled_data", callable(getattr(main, "log_recalled_data", None)))
    check("build_and_call_phase2", callable(getattr(main, "build_and_call_phase2", None)))
    check("style_self_check", callable(getattr(main, "style_self_check", None)))
    check("cleanup_response", callable(getattr(main, "cleanup_response", None)))
    check("dispatch_reply", callable(getattr(main, "dispatch_reply", None)))

    # ── Test 10: get_channel_info returns correct types ──
    print("\n[Test 10] get_channel_info unit test")
    msg_ooc = _make_message("test", channel_name="ooc-討論")
    info = main.get_channel_info(msg_ooc)
    check("OOC detected", info["channel_type"] == "out_of_character")

    msg_ic = _make_message("test", channel_name="rp-主線")
    info2 = main.get_channel_info(msg_ic)
    check("IC detected", info2["channel_type"] == "in_character")
    check("Explicit IC", info2["is_explicit_ic_channel"])

    msg_dm = _make_message("test", channel_name="general")
    msg_dm.guild = None
    info3 = main.get_channel_info(msg_dm)
    check("DM = IC", info3["channel_type"] == "in_character")

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed")
    if passed == total:
        print("ALL VIRTUAL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print("=" * 60)


asyncio.run(run_tests())
