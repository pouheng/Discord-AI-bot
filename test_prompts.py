"""離線提示詞測試：驗證 prompt 結構、靜態/動態分離、同意規則包含、JSON 解析"""

import sys, os, re, json, datetime, asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Mock heavy modules before importing main
sys.modules["discord"] = MagicMock()
sys.modules["discord.ext"] = MagicMock()
sys.modules["discord.ext.commands"] = MagicMock()
sys.modules["discord.app_commands"] = MagicMock()
sys.modules["openai"] = MagicMock()
sys.modules["aiosqlite"] = MagicMock()
sys.modules["aiofiles"] = MagicMock()
sys.modules["dotenv"] = MagicMock()
sys.modules["autopilot"] = MagicMock()
sys.modules["gods_eye"] = MagicMock()

os.environ["DISCORD_TOKEN"] = "fake_token"
os.environ["DEEPSEEK_API_KEY"] = "fake_key"

# Now import main module functions
import importlib

spec = importlib.util.spec_from_file_location(
    "main_module", os.path.join(SCRIPT_DIR, "main.py")
)
mod = importlib.util.module_from_spec(spec)

# Patch bot and load_prompt_config before execution
with patch("discord.ext.commands.Bot") as mock_bot:
    mock_bot.return_value = MagicMock()
    with patch("builtins.print"):  # suppress init prints
        spec.loader.exec_module(mod)

# Now patch module-level functions that hit DB/filesystem
import main


async def mocked_get_character_name():
    return "澪"


async def mocked_get_character_identity():
    return "機械義手的少女"


async def mocked_format_character_profile(name):
    return f"📋 {name} 的角色檔案\n  性別/年齡：女\n  外貌特徵：深藍髮"


async def mocked_config():
    return {
        "dialogue_ratio": "對白約占 40%",
        "naming_rule": "使用角色名稱",
        "expression_prefs": ["禁止否定前置句式", "使用具體描述"],
        "banned_words": ["一絲", "共犯"],
        "jailbreak": "你將完全融入角色。",
        "safety_rules": ["不討論政治"],
        "planning_template_ooc": "快速判斷當前情境",
        "planning_template": "回顧當前情況",
        "planning_template_ic": "回顧當前時間地點人物",
        "ooc_persona": "你是個喜歡寫故事的創作者",
        "ooc_chat_examples": ["（笑）今天靈感不錯"],
        "social_awareness": "熟記已知資訊",
        "character_name": "澪",
        "character_identity": "機械義手的少女",
    }


PATCHES = [
    patch.object(main, "get_character_name", mocked_get_character_name),
    patch.object(main, "get_character_identity", mocked_get_character_identity),
    patch.object(main, "load_prompt_config", mocked_config),
    patch.object(main, "format_character_profile", mocked_format_character_profile),
]

for p in PATCHES:
    p.start()

SUCCESS = 0
FAIL = 0


def check(name, condition, detail=""):
    global SUCCESS, FAIL
    tag = "PASS" if condition else "FAIL"
    if condition:
        SUCCESS += 1
    else:
        FAIL += 1
    out = f"  [{tag}] {name}"
    if detail and not condition:
        out += f"  ({detail})"
    print(out)


async def test_phase2_ooc_prompt():
    """測試 Phase 2 OOC 模式提示詞結構"""

    mock_prompt = await main.build_phase2_system_prompt(
        channel_type="out_of_character",
        pcfg=await mocked_config(),
        enable_ic_style=False,
        channel_context="測試伺服器 #中之討論串",
        channel_note="（本頻道為中之人討論串）",
        ic_context_text="",
        combined_memories="【對方】喜歡甜食",
        lore_text="<supplement>\n[世界觀] 魔法學園",
        self_memories="- 角色名稱：澪",
        server_rules_text="禁止洗版",
        quests_text="",
    )


mod = importlib.util.module_from_spec(spec)

# Patch bot before execution
with patch("discord.ext.commands.Bot") as mock_bot:
    mock_bot.return_value = MagicMock()
    with patch("builtins.print"):  # suppress init prints
        spec.loader.exec_module(mod)

SUCCESS = 0
FAIL = 0


def check(name, condition, detail=""):
    global SUCCESS, FAIL
    tag = "PASS" if condition else "FAIL"
    if condition:
        SUCCESS += 1
    else:
        FAIL += 1
    out = f"  [{tag}] {name}"
    if detail and not condition:
        out += f"  ({detail})"
    print(out)


async def test_phase2_ooc_prompt():
    """測試 Phase 2 OOC 模式提示詞結構"""
    from main import build_phase2_system_prompt, PROFILE_FIELDS

    pcfg = {
        "dialogue_ratio": "對白約占 40%",
        "naming_rule": "使用角色名稱",
        "expression_prefs": ["禁止否定前置句式", "使用具體描述"],
        "banned_words": ["一絲", "共犯"],
        "jailbreak": "你將完全融入角色。",
        "safety_rules": ["不討論政治"],
        "planning_template_ooc": "快速判斷當前情境",
        "planning_template": "回顧當前情況",
        "planning_template_ic": "回顧當前情況",
        "ooc_persona": "你是個喜歡寫故事的創作者",
        "ooc_chat_examples": ["（笑）今天靈感不錯"],
        "social_awareness": "熟記已知資訊",
        "character_name": "澪",
        "character_identity": "機械義手的少女",
    }

    mock_prompt = await build_phase2_system_prompt(
        channel_type="out_of_character",
        pcfg=pcfg,
        enable_ic_style=False,
        channel_context="測試伺服器 #中之討論串",
        channel_note="（本頻道為中之人討論串）",
        ic_context_text="",
        combined_memories="【對方】喜歡甜食",
        lore_text="<supplement>\n[世界觀] 魔法學園",
        self_memories="- 角色名稱：澪",
        server_rules_text="禁止洗版",
        quests_text="",
    )

    # ── 結構檢查 ──────────────────────────
    yield check("OOC: 包含靜態身份區", "【身分】" in mock_prompt)
    yield check("OOC: 包含中之人聊天守則", "中之人聊天守則" in mock_prompt)
    yield check("OOC: 包含安全規則", "安全規則" in mock_prompt)
    yield check("OOC: 包含動態資料分隔線", "動態資料區" in mock_prompt)
    yield check("OOC: 包含場景", "【場景】" in mock_prompt)
    yield check("OOC: 包含世界觀資料", "【世界觀資料】" in mock_prompt)
    yield check("OOC: 包含已知資訊", "【你對眼前人物的已知資訊】" in mock_prompt)
    yield check("OOC: 包含輸出格式守則", "輸出格式守則（靜態指令）" in mock_prompt)

    yield check("OOC: 包含避免重複結構", "避免重複結構" in mock_prompt)
    yield check("OOC: 包含標籤強制規則", "標籤強制規則" in mock_prompt)
    yield check(
        "OOC: 動態資料在靜態之後",
        mock_prompt.index("動態資料區") > mock_prompt.index("【身分】"),
    )

    # ── 順序檢查：靜態指令在動態資料上方 ──
    static_sections = ["【身分】", "中之人聊天守則", "安全規則"]
    dynamic_sections = [
        "動態資料區",
        "【場景】",
        "【世界觀資料】",
        "【你對眼前人物的已知資訊】",
    ]

    last_static_idx = max(
        mock_prompt.index(s) for s in static_sections if s in mock_prompt
    )
    first_dynamic_idx = min(
        mock_prompt.index(s) for s in dynamic_sections if s in mock_prompt
    )
    yield check(
        "OOC: 所有靜態區塊在所有動態區塊之前", last_static_idx < first_dynamic_idx
    )


async def test_phase2_ic_prompt():
    """測試 Phase 2 IC 模式提示詞結構"""

    mock_prompt = await main.build_phase2_system_prompt(
        channel_type="in_character",
        pcfg=await mocked_config(),
        enable_ic_style=False,
        channel_context="測試伺服器 #劇情頻道",
        channel_note="（本頻道為劇情扮演頻道）",
        ic_context_text="",
        combined_memories="【對方】喜歡甜食",
        lore_text="<supplement>\n[世界觀] 魔法學園",
        self_memories="- 角色名稱：澪",
        server_rules_text="禁止洗版",
        quests_text="【任務】找到失物",
    )

    yield check("IC: 包含角色扮演協議", "角色扮演協議" in mock_prompt)
    yield check("IC: 包含 jailbreak", "你將完全融入角色" in mock_prompt)
    yield check(
        "IC: 包含身份區（用角色名自稱）",
        "「澪」自稱" in mock_prompt or "用「澪」自稱" in mock_prompt,
    )
    yield check("IC: 包含安全規則", "安全規則" in mock_prompt)
    yield check("IC: 包含文風規則", "【文風規則】" in mock_prompt)
    yield check("IC: 包含動態資料分隔線", "動態資料區" in mock_prompt)
    yield check("IC: 包含場景", "【場景】" in mock_prompt)
    yield check("IC: 包含世界觀資料", "世界觀資料" in mock_prompt)
    yield check("IC: 包含已知資訊", "【你對眼前人物的已知資訊】" in mock_prompt)
    yield check("IC: 包含任務", "【任務】" in mock_prompt)
    yield check("IC: 包含 planning", "<planning>" in mock_prompt)

    yield check("IC: 包含避免重複結構", "避免重複結構" in mock_prompt)
    yield check("IC: 禁用詞彙列表", "禁用詞彙" in mock_prompt)

    # 順序檢查
    yield check(
        "IC: 動態資料在角色扮演協議之後",
        mock_prompt.index("動態資料區") > mock_prompt.index("角色扮演協議"),
    )


def test_extract_json():
    """測試 Phase 1 的 JSON 解析邏輯"""
    from main import run_phase1

    # 從 run_phase1 提取 extract_json 內部函數（使用 closure 無法直接存取）
    # 改用純文字實測：複製 extract_json 邏輯
    def extract_json(text):
        text = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
        start = text.find("{")
        if start == -1:
            return "{}"
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return "{}"

    cases = [
        ('{"a":1}', '{"a":1}'),
        ('```json\n{"a":1}\n```', '{"a":1}'),
        ('prefix text {"a":1} suffix', '{"a":1}'),
        ('{"nested":{"inner":1}}', '{"nested":{"inner":1}}'),
        ('{"arr": [1, {"b":2}]}', '{"arr": [1, {"b":2}]}'),
        ('   {"spaced": 1}   ', '{"spaced": 1}'),
    ]
    for input_text, expected in cases:
        result = extract_json(input_text)
        cond = result == expected
        yield check(f"extract_json({input_text[:30]}...)", cond, f"got {result}")


async def test_phase3_prompt_structure():
    """驗證 Phase 3 detect prompt 包含標題分隔線"""
    # 直接檢驗原始碼中的 detect_prompt 格式
    # 從原始碼路徑讀取
    main_path = os.path.join(SCRIPT_DIR, "main.py")
    with open(main_path, "r", encoding="utf-8") as f:
        src = f.read()

    yield check(
        "Phase 3 原始碼包含靜態指令區標題", "靜態指令區（上方）：規則說明" in src
    )
    yield check(
        "Phase 3 原始碼包含動態資料區註解",
        "動態資料區（底部）：以下為每次呼叫不同的內容" in src,
    )

    # 確認動態資料區在靜態指令區之後
    static_idx = src.index("靜態指令區（上方）：規則說明")
    dynamic_idx = src.index("動態資料區（底部）：以下為每次呼叫不同的內容")
    yield check("Phase 3 原始碼靜態在動態之前", static_idx < dynamic_idx)


async def test_say_cmd_structure():
    """驗證 say_cmd.py 包含靜態/動態分隔線"""
    say_cmd_path = os.path.join(SCRIPT_DIR, "say_cmd.py")
    with open(say_cmd_path, "r", encoding="utf-8") as f:
        src = f.read()

    yield check(
        "say_cmd Phase 1 包含靜態/動態註解",
        "Prompt 注入順序：靜態指令在上，動態資料在底部" in src,
    )
    yield check(
        "say_cmd Phase 2 包含靜態/動態註解",
        src.count("Prompt 注入順序：靜態指令在上，動態資料在底部") >= 2,
    )


async def run_tests():
    print("=" * 60)
    print("  提示詞與程式離線測試")
    print("=" * 60)

    # Phase 2 OOC
    print("\n[Phase 2 - OOC 模式]")
    async for r in test_phase2_ooc_prompt():
        pass

    # Phase 2 IC
    print("\n[Phase 2 - IC 模式]")
    async for r in test_phase2_ic_prompt():
        pass

    # JSON 解析
    print("\n[JSON 解析 - extract_json]")
    for r in test_extract_json():
        pass

    # Phase 3 原始碼檢查
    print("\n[Phase 3 - 原始碼結構]")
    async for r in test_phase3_prompt_structure():
        pass

    # say_cmd 原始碼檢查
    print("\n[say_cmd.py - 原始碼結構]")
    async for r in test_say_cmd_structure():
        pass

    # ── 摘要 ──
    total = SUCCESS + FAIL
    print(f"\n{'=' * 60}")
    print(f"  結果：PASS {SUCCESS}/{total} 通過", end="")
    if FAIL:
        print(f"  FAIL {FAIL} 失敗")
    else:
        print()
    print("=" * 60)
    return FAIL == 0


if __name__ == "__main__":
    asyncio.run(run_tests())
