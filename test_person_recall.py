"""離線測試：對話提及已知角色時的自動召回（羊君案例，2026-10-09）。

重現問題：羊君只有『人物:羊君』記憶、不在 person_aliases；
Phase 1 看不到、也無法透過別名解析，於是 bot 忘記羊君。
驗證修法：recall_mentioned_persons_memories 能依對話文字召回其記憶，
且不誤召單字暱稱、不與既有區塊重複、不召回 bot 自己。
"""

import sys, os, asyncio
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# 在 import main 之前 mock 重型模組（與 test_prompts.py 相同手法）
for name in [
    "discord", "discord.ext", "discord.ext.commands", "discord.app_commands",
    "openai", "aiosqlite", "aiofiles", "dotenv", "autopilot", "gods_eye",
]:
    sys.modules[name] = MagicMock()

os.environ["DISCORD_TOKEN"] = "fake_token"
os.environ["DEEPSEEK_API_KEY"] = "fake_key"

import importlib

spec = importlib.util.spec_from_file_location(
    "main_module", os.path.join(SCRIPT_DIR, "main.py")
)
mod = importlib.util.module_from_spec(spec)
with patch("discord.ext.commands.Bot") as mock_bot:
    mock_bot.return_value = MagicMock()
    with patch("builtins.print"):
        spec.loader.exec_module(mod)

import main
import memory

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


# ── 注入測試替身 ────────────────────────────────
KNOWN = {
    "羊君": "羊君",            # 獨立角色（只靠 topic）
    "吳神月": "吳神月",        # 正名
    "神月": "吳神月",          # 別名 -> 正名
    "凌空": "凌空",            # 這裡當成 bot 自己的名字
    "甲": "甲",                # 單字暱稱，應被忽略
    "小明": "小明",            # 名稱是另一位角色的子字串，但兩者不同人
    "小明同學": "小明同學",
}
MEM = {
    "羊君": "- [2026-09-12] 人物:羊君: 羊君是吳神月的原創角色，暈船搞笑擔當。",
    "吳神月": "- [..] 人物:吳神月: 半龍半人，戰鬥狂。",
    "小明": "- [..] 人物:小明: 喜歡冒險。",
    "小明同學": "- [..] 人物:小明同學: 擅長魔法。",
}


async def fake_collect():
    return dict(KNOWN)


async def fake_char_name():
    return "凌空"


async def fake_get_person_memory(canonical, limit=8):
    return MEM.get(canonical, "（無記錄）")


memory._collect_known_person_names = fake_collect
memory.get_person_memory = fake_get_person_memory
memory.character_name_provider = fake_char_name


async def run():
    # 1) 對話提到羊君 -> 召回其記憶
    out = await main.recall_mentioned_persons_memories(
        "羊君要跟他度過晚餐，被一巴掌呼倒在地", ""
    )
    check("提及羊君 → 召回記憶", "【羊君】" in out and "暈船搞笑擔當" in out, out)

    # 2) 不召回 bot 自己的名字
    out = await main.recall_mentioned_persons_memories("凌空路過打招呼", "")
    check("不召回 bot 自己（凌空）", out == "", out)

    # 3) 已在 combined_memories 出現者跳過（避免重複注入）
    out = await main.recall_mentioned_persons_memories(
        "羊君又來了", "【羊君】（對話提及：羊君）\n- 舊內容"
    )
    check("已存在區塊 → 跳過", out == "", out)

    # 4) 單字暱稱（甲）不誤召
    out = await main.recall_mentioned_persons_memories("甲來了", "")
    check("單字暱稱不誤召", out == "", out)

    # 5) 別名與正名同時出現 -> 只召回一次（以正名去重）
    out = await main.recall_mentioned_persons_memories("吳神月，不對，神月說話了", "")
    check("別名/正名去重", out.count("【吳神月】") == 1, out)

    # 6) 空字串不爆
    out = await main.recall_mentioned_persons_memories("", "")
    check("空輸入安全", out == "", out)

    # 7) 不同角色的名稱有包含關係時，兩人都應召回
    out = await main.recall_mentioned_persons_memories(
        "小明同學和小明一起出發", ""
    )
    check(
        "不同人物的子字串名稱都召回",
        "【小明】" in out and "【小明同學】" in out,
        out,
    )

    print(f"\n{SUCCESS} passed, {FAIL} failed")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
