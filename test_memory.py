"""離線測試：記憶模組（memory.py）。

對暫存 SQLite 檔直接驗證 memories 表的 save / get / search / recall
行為：去重、語意合併、字首相似、__BOT__ 自記憶疊加、關鍵字與日期檢索、
schema 由記憶模組擁有（db.init_db 委派）。
"""

import asyncio
import os
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import db
import memory

SUCCESS = 0
FAIL = 0
TMP = os.path.join(tempfile.gettempdir(), "test_memory_module.db")


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


async def reset():
    if os.path.exists(TMP):
        os.remove(TMP)
    db.DB_FILE = TMP
    db.init_db()
    async with db.get_db() as conn:
        await conn.execute("DELETE FROM memories")
        await conn.execute("DELETE FROM person_aliases")


async def fetch_all():
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT user_id, user_name, topic, content, mem_type, context FROM memories ORDER BY id"
        )
        return await cur.fetchall()


async def fake_char_name():
    return "澪"


async def run():
    memory.character_name_provider = fake_char_name

    # ── schema 由記憶模組擁有 ──
    await reset()
    async with db.get_db() as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memories'"
        )
        check("db.init_db 建立 memories 表", (await cur.fetchone()) is not None)
        cur = await conn.execute("PRAGMA table_info(memories)")
        cols = {r[1] for r in await cur.fetchall()}
        check(
            "memories 欄位齊全（含 context/mem_type）",
            {"id", "timestamp", "user_id", "user_name", "topic", "content",
             "context", "mem_type"} <= cols,
            str(cols),
        )

    # ── save_user_memory ──
    await reset()
    await memory.save_user_memory(1, "甲", "人物:甲", "半龍半人", mem_type="真實")
    await memory.save_user_memory(1, "甲", "雜談", "應該被略過", mem_type="")
    rows = await fetch_all()
    check("非真實 mem_type 不寫入", len(rows) == 1, str(rows))
    check("真實記憶寫入 user_id/user_name", rows and rows[0][0] == "1" and rows[0][1] == "甲")

    out = await memory.get_user_memory(1, user_name="甲")
    check("get_user_memory 召回", "半龍半人" in out, out)
    check("get_user_memory 無資料回無記錄",
          (await memory.get_user_memory(999)) == "（無記錄）")

    # ── 自記憶：字首相似去重 ──
    await reset()
    await memory.save_self_memory("事件:購買CNC機台", "決定購入 CNC 機台")
    await memory.save_self_memory("事件:購買CNC機台", "決定購入 CNC 機台")
    rows = await fetch_all()
    check("同 topic 近重複 → 更新不新增", len(rows) == 1, str(rows))
    check("自記憶 user_id=__BOT__ 且帶角色名",
          rows[0][0] == "__BOT__" and rows[0][1] == "澪", str(rows))

    # ── 自記憶：人物: 疊加 ──
    await reset()
    await memory.save_self_memory("人物:甲", "甲喜歡草莓")
    await memory.save_self_memory("人物:甲", "甲喜歡草莓")
    check("人物: 同內容不重複疊加", len(await fetch_all()) == 1)
    await memory.save_self_memory("人物:甲", "甲喜歡草莓球")
    rows = await fetch_all()
    check("人物: 前綴相似內容疊加",
          len(rows) == 1 and "草莓球" in rows[0][3], str(rows))

    # ── 自記憶：語意合併（topic 措辭不同、共享核心詞）──
    await reset()
    await memory.save_self_memory("事件:凌空CNC購買計畫", "凌空打算買 CNC 機台")
    await memory.save_self_memory("事件:CNC機台購買計劃", "凌空打算買 CNC 機台")
    rows = await fetch_all()
    check("語意合併 → 只留一條", len(rows) == 1, str(rows))

    # ── get_self_memory / raw ──
    await reset()
    await memory.save_self_memory("角色名稱", "我叫「澪」")
    txt = await memory.get_self_memory()
    check("get_self_memory 有內容", "澪" in txt, txt)
    raw = await memory.get_self_memory_raw()
    check("get_self_memory_raw 回 dict 列表",
          bool(raw) and set(raw[0]) == {"id", "timestamp", "topic", "content"}, str(raw))
    check("get_self_memory 空表回提示",
          "尚無自我認知" in (await _empty_self()))

    # ── _update_memory ──
    await reset()
    await memory.save_self_memory("事件:舊名", "內容")
    await memory._update_memory("__BOT__", "事件:舊名", new_topic="事件:新名",
                                new_content="新內容")
    raw = await memory.get_self_memory_raw()
    check("_update_memory 改 topic 與內容",
          raw[0]["topic"] == "事件:新名" and raw[0]["content"] == "新內容", str(raw))

    # ── search_memories_by_keyword ──
    await reset()
    await memory.save_self_memory("事件:煙火大會", "夏天在河邊看煙火")
    hit = await memory.search_memories_by_keyword(["煙火"])
    check("關鍵字檢索命中", "煙火" in hit, hit)
    check("關鍵字檢索無命中回空",
          (await memory.search_memories_by_keyword(["不存在詞"])) == "")

    # ── fetch_memories_by_date_range ──
    await reset()
    await memory.save_self_memory("事件:今天", "今天發生的事")
    async with db.get_db() as conn:
        await conn.execute("UPDATE memories SET timestamp = '2026-09-28 12:00:00'")
    day = await memory.fetch_memories_by_date_range("2026-09-28", "2026-09-28")
    check("日期召回命中", "今天" in day, day)

    # ── parse_date_range ──
    check("parse_date_range 正規化斜線",
          memory.parse_date_range({"from": "2026/9/28"}) == ("2026-09-28", "2026-09-28"))
    check("parse_date_range 顛倒自動對調",
          memory.parse_date_range({"from": "2026-10-01", "to": "2026-09-28"})
          == ("2026-09-28", "2026-10-01"))
    check("parse_date_range 無效回 None", memory.parse_date_range("亂七八糟") is None)

    # ── _collect_known_person_names 從 topic 撈角色 ──
    await reset()
    await memory.save_self_memory("人物:羊君", "羊君是原創角色")
    mapping = await memory._collect_known_person_names()
    check("人物清單從 topic 撈出", mapping.get("羊君") == "羊君", str(mapping))

    # ── recall_mentioned_persons_memories（端到端）──
    out = await memory.recall_mentioned_persons_memories("羊君走了過來", "")
    check("對話提及 → 召回記憶", "【羊君】" in out and "原創角色" in out, out)

    print(f"\n{SUCCESS} passed, {FAIL} failed")
    return FAIL


async def _empty_self():
    await reset()
    return await memory.get_self_memory()


if __name__ == "__main__":
    code = asyncio.run(run())
    try:
        os.remove(TMP)
    except OSError:
        pass
    sys.exit(code)
