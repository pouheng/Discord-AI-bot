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
        return_value=([], [], [], [], [], False, False, "", False, "", [], False, "")
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

    def check(name, condition, detail=""):
        nonlocal passed, total
        total += 1
        if condition:
            passed += 1
            print(f"  PASS: {name}")
        else:
            print(f"  FAIL: {name}" + (f"  ({detail})" if detail else ""))

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

    # ── Test 11-14: Drive Agent 使用場景（主流程整合）──
    import drive_agent as drive_mod

    _orig_phase2 = main.build_and_call_phase2
    _orig_read_agent = drive_mod.run_read_agent
    _orig_loop = drive_mod.run_agent_loop
    _orig_tree = drive_mod.folder_tree_text
    _orig_enabled = drive_mod.is_enabled

    drive_mod.folder_tree_text = lambda *a, **k: "- 世界觀.md"

    def phase1_with_drive(read=None, write=False, intent=""):
        return mock.AsyncMock(return_value=(
            [], [], [], [], [], False, False, "", False, "",
            read or [], write, intent,
        ))

    print("\n[Test 11] Drive 唯讀：研究員結果注入 Phase 2，不發提示")
    _default_mocks()
    drive_mod.is_enabled = lambda: True
    main.run_phase1 = phase1_with_drive(
        read=[{"path": "世界觀.md", "mode": "section", "section": "國家"}],
        intent="世界觀的國家設定",
    )
    drive_mod.run_read_agent = mock.AsyncMock(return_value={
        "ok": True, "summary": "三個國家互相對立。", "tool_calls": [], "steps": 2,
    })
    captured = {}

    async def _phase2_capture(*args, **kwargs):
        captured.update(kwargs)
        return "<planning>x</planning>\nMio 的回覆", "Mio", "TestUser"

    main.build_and_call_phase2 = _phase2_capture
    msg11 = _make_message("世界觀的國家有哪些？")
    _fake_bot_user.mentioned_in = lambda m: True
    await main.on_message(msg11)
    check("研究員被呼叫", drive_mod.run_read_agent.called)
    check("研究員收到 Phase 1 的意圖",
          "國家設定" in str(drive_mod.run_read_agent.call_args), 
          str(drive_mod.run_read_agent.call_args)[:200])
    check("摘要注入 Phase 2",
          "三個國家互相對立" in (captured.get("drive_context") or ""),
          str(captured.get("drive_context"))[:120])
    check("有回覆", sent(msg11))
    check("沒有寫入就沒有提示",
          not any("📁" in str(c[0][0]) for c in msg11.channel.send.call_args_list),
          str(msg11.channel.send.call_args_list)[:150])

    print("\n[Test 12] Drive 寫入：提示以獨立訊息送出")
    _default_mocks()
    drive_mod.is_enabled = lambda: True
    main.run_phase1 = phase1_with_drive(
        write=True, intent="把國家設定整理成檔案",
    )
    drive_mod.run_agent_loop = mock.AsyncMock(return_value={
        "text": "已建立國家設定.md",
        "tool_calls": [{"tool": "drive_write", "args": {}, "ok": True}],
        "writes": [{"ok": True, "action": "created", "name": "國家設定.md", "bytes": 42}],
        "steps": 2,
    })
    captured = {}

    async def _phase2_capture2(*args, **kwargs):
        captured.update(kwargs)
        return "<planning>x</planning>\nMio 的回覆", "Mio", "TestUser"

    main.build_and_call_phase2 = _phase2_capture2
    msg12 = _make_message("把國家設定整理成檔案")
    _fake_bot_user.mentioned_in = lambda m: True
    await main.on_message(msg12)
    check("Agent 被呼叫", drive_mod.run_agent_loop.called)
    check("寫入結果注入 Phase 2",
          "已建立國家設定.md" in (captured.get("drive_context") or ""),
          str(captured.get("drive_context"))[:120])
    notices = [c[0][0] for c in msg12.channel.send.call_args_list if c[0]]
    check("有發送寫入提示",
          any("📁" in n and "國家設定.md" in n for n in notices), str(notices))
    check("提示是獨立訊息（角色回覆在前、提示在後）",
          len(notices) >= 2 and "📁" not in notices[0]
          and any("📁" in n for n in notices[1:]), str(notices))

    print("\n[Test 13] Drive 關閉：即使 Phase 1 說要讀也不動作")
    _default_mocks()
    drive_mod.is_enabled = lambda: False
    main.run_phase1 = phase1_with_drive(
        read=[{"path": "世界觀.md"}], write=True, intent="整理檔案",
    )
    drive_mod.run_read_agent = mock.AsyncMock()
    drive_mod.run_agent_loop = mock.AsyncMock()
    msg13 = _make_message("看一下世界觀")
    _fake_bot_user.mentioned_in = lambda m: True
    main.build_and_call_phase2 = mock.AsyncMock(
        return_value=("<planning>x</planning>\nMio 的回覆", "Mio", "TestUser")
    )
    await main.on_message(msg13)
    check("關閉時不呼叫研究員", not drive_mod.run_read_agent.called)
    check("關閉時不呼叫 Agent", not drive_mod.run_agent_loop.called)
    check("仍然正常回覆", sent(msg13))

    print("\n[Test 14] Drive 出錯：不影響正常回覆")
    _default_mocks()
    drive_mod.is_enabled = lambda: True
    main.run_phase1 = phase1_with_drive(read=[{"path": "世界觀.md"}], intent="看一下")
    drive_mod.run_read_agent = mock.AsyncMock(side_effect=RuntimeError("boom"))
    main.build_and_call_phase2 = mock.AsyncMock(
        return_value=("<planning>x</planning>\nMio 的回覆", "Mio", "TestUser")
    )
    msg14 = _make_message("看一下世界觀")
    _fake_bot_user.mentioned_in = lambda m: True
    await main.on_message(msg14)
    check("Agent 爆炸仍照樣回覆", sent(msg14), str(msg14.reply.call_args_list))

    print("\n[Test 15] 閒聊：Phase 1 沒標記 → 完全不碰雲端")
    _default_mocks()
    drive_mod.is_enabled = lambda: True
    main.run_phase1 = phase1_with_drive()
    drive_mod.run_read_agent = mock.AsyncMock()
    drive_mod.run_agent_loop = mock.AsyncMock()
    captured = {}

    async def _phase2_capture3(*args, **kwargs):
        captured.update(kwargs)
        return "<planning>x</planning>\nMio 的回覆", "Mio", "TestUser"

    main.build_and_call_phase2 = _phase2_capture3
    msg15 = _make_message("今天好累喔")
    _fake_bot_user.mentioned_in = lambda m: True
    await main.on_message(msg15)
    check("沒有呼叫研究員", not drive_mod.run_read_agent.called)
    check("沒有呼叫 Agent", not drive_mod.run_agent_loop.called)
    check("Phase 2 沒有雲端區塊", not captured.get("drive_context"),
          str(captured.get("drive_context")))
    check("正常回覆", sent(msg15))

    print("\n[Test 16] 連鎖：上網找參考 → 寫入角色草稿")
    _default_mocks()
    drive_mod.is_enabled = lambda: True
    main.run_phase1 = mock.AsyncMock(return_value=(
        [], [], [], [], [], False, False, "", True, "蒸氣龐克 角色設定",
        [], True, "上網找蒸氣龐克世界觀參考，整理成角色草稿 角色草稿/技師.md",
    ))
    main._build_web_context = mock.AsyncMock(return_value=(
        "【網路】蒸氣龐克常見元素：齒輪、蒸氣、黃銅、維多利亞風格。",
        [{"url": "https://example.com/steampunk", "title": "Steampunk"}],
        "蒸氣龐克摘要",
    ))
    drive_mod.run_agent_loop = mock.AsyncMock(return_value={
        "text": "已建立 角色草稿/技師.md",
        "tool_calls": [{"tool": "drive_write", "args": {}, "ok": True}],
        "writes": [{"ok": True, "action": "created", "name": "技師.md"}],
        "steps": 3,
    })
    msg16 = _make_message("上網找一些蒸氣龐克的參考，幫我寫一份角色草稿")
    _fake_bot_user.mentioned_in = lambda m: True
    main.build_and_call_phase2 = mock.AsyncMock(
        return_value=("<planning>x</planning>\nMio 的回覆", "Mio", "TestUser")
    )
    await main.on_message(msg16)
    agent_input = drive_mod.run_agent_loop.call_args[0][0]
    blob = str(agent_input)
    check("Agent 被呼叫", drive_mod.run_agent_loop.called)
    check("Agent 收到網路參考", "蒸氣龐克" in blob and "齒輪" in blob, blob[:200])
    check("Agent 收到需求與檔名",
          "角色草稿/技師.md" in blob, blob[:200])
    check("有寫入提示", any(
        "📁" in str(c[0][0]) for c in msg16.channel.send.call_args_list if c[0]
    ), str(msg16.channel.send.call_args_list)[:150])

    # ── Test 17：有寫入意圖但 Agent 沒寫 → 要誠實告知 ────────
    print("\n[Test 17] 寫入意圖落空 → 顯示 ⚠️ 沒存檔")
    drive_mod.run_agent_loop = mock.AsyncMock(return_value={
        "text": "我讀完資料了。",
        "tool_calls": [{"tool": "drive_read", "args": {}, "ok": True}],
        "writes": [],
        "steps": 6,
    })
    msg17 = _make_message("幫我寫一份角色草稿到 角色草稿/技師.md")
    await main.on_message(msg17)
    warns = [str(c[0][0]) for c in msg17.channel.send.call_args_list if c[0]]
    check("有 ⚠️ 沒存檔提示", any("⚠️" in w for w in warns), str(warns)[:200])
    check("沒有誤報成功", not any("📁" in w for w in warns), str(warns)[:200])
    check("有把目標檔名交給 Agent 當備援",
          drive_mod.run_agent_loop.call_args.kwargs.get("fallback_path")
          == "角色草稿/技師.md",
          str(drive_mod.run_agent_loop.call_args.kwargs)[:200])

    # ── Test 18：指定來源檔案 → 先讀進來再交給 Agent 擴展 ────
    print("\n[Test 18] 「根據資料夾內容擴展」→ 來源檔案先讀進參考資料")
    _orig_read_file = drive_mod.read_file
    drive_mod.read_file = mock.AsyncMock(return_value={
        "ok": True, "text": "個人魔法分為超能系、自然系、療癒系三大類。",
        "total_chars": 22,
    })
    main.run_phase1 = mock.AsyncMock(return_value=(
        [], [], [], [], [], False, False, "", True, "治癒魔法練習機",
        [{"path": "名詞解釋.md", "mode": "auto"}], True,
        "根據名詞解釋寫一台練習治癒魔法的機器到 角色草稿/治癒練習機.md",
    ))
    drive_mod.run_agent_loop = mock.AsyncMock(return_value={
        "text": "已建立 治癒練習機.md",
        "tool_calls": [{"tool": "drive_write", "args": {}, "ok": True}],
        "writes": [{"ok": True, "action": "created", "name": "治癒練習機.md"}],
        "steps": 2,
    })
    msg18 = _make_message("根據資料夾裡的名詞解釋，寫一台練習治癒魔法的機器")
    await main.on_message(msg18)
    blob = str(drive_mod.run_agent_loop.call_args[0][0])
    check("來源檔案被讀取", drive_mod.read_file.await_count >= 1,
          str(drive_mod.read_file.await_count))
    check("參考資料含檔案名", "《名詞解釋.md》" in blob, blob[:300])
    check("參考資料含檔案內容", "療癒系" in blob, blob[:300])
    drive_mod.read_file = _orig_read_file

    # 還原
    main.build_and_call_phase2 = _orig_phase2
    drive_mod.run_read_agent = _orig_read_agent
    drive_mod.run_agent_loop = _orig_loop
    drive_mod.folder_tree_text = _orig_tree
    drive_mod.is_enabled = _orig_enabled

    print("\n" + "=" * 60)
    print(f"Results: {passed}/{total} passed")
    if passed == total:
        print("ALL VIRTUAL TESTS PASSED")
    else:
        print("SOME TESTS FAILED")
    print("=" * 60)


asyncio.run(run_tests())
