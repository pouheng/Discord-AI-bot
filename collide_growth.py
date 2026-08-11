"""
collide_growth.py — 樓層成長與防呆機制

核心：防呆單位 = 樓層（thread_id），floor_event_id 由系統確定性生成（不依賴 LLM），
首次 /grow 為總結成長，後續為更新/重跑（覆寫既有 log，不新增不疊加）。

資料表：
- collide_floors          （樓層主表，summary_generated 標記）
- collide_growth_log      （成長審計，UNIQUE(thread_id, floor_event_id)）
"""

import sqlite3
from typing import Optional

# 成長事件類別與幅度範圍（草案）
GROWTH_CATEGORIES = {
    "mainline": {"label": "核心主線/重大事件", "exp_range": (10, 20)},
    "duel": {"label": "跨越階級生死決鬥", "exp_range": (5, 10)},
    "domain_practice": {"label": "結界戰鬥實踐", "exp_range": (5, 8)},
    "commission": {"label": "高難度委託/實戰演練", "exp_range": (1, 3)},
}


def make_floor_event_id(thread_id: str, category: str, target_name: str, field: str) -> str:
    """系統確定性生成 floor_event_id，不使用 LLM 產出的事件名稱。

    `{thread_id}#{category}#{target_name}#{field}`——同樓層同欄位必然相同鍵，
    防重複機制不依賴事件命名一致性。
    """
    return f"{thread_id}#{category}#{target_name}#{field}"


def floor_is_summarized(conn: sqlite3.Connection, thread_id: str) -> bool:
    """查詢該樓層是否已執行過 /grow。"""
    row = conn.execute(
        "SELECT summary_generated FROM collide_floors WHERE thread_id = ?", (thread_id,)
    ).fetchone()
    return bool(row and row[0])


def mark_floor_summarized(conn: sqlite3.Connection, thread_id: str, thread_name: str = "") -> None:
    """標記樓層已總結（首次 /grow 後呼叫）。"""
    conn.execute(
        """INSERT INTO collide_floors (thread_id, thread_name, status, summary_generated, summary_at, created_at)
           VALUES (?, ?, 'summarized', 1, datetime('now'), datetime('now'))
           ON CONFLICT(thread_id) DO UPDATE SET
             summary_generated = 1,
             status = 'summarized',
             summary_at = datetime('now')""",
        (thread_id, thread_name),
    )
    conn.commit()


def upsert_growth(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    category: str,
    target_name: str,
    field: str,
    old_value: str,
    new_value: str,
    delta: str,
    reason: str,
    source: str = "action_log",
    battle_id: int | None = None,
) -> str:
    """寫入一筆成長 log（防呆核心）。

    以 (thread_id, floor_event_id) 唯一鍵決定新增或覆寫：
    - 不存在 → INSERT（首次 /grow 套用成長）
    - 存在   → UPDATE 覆寫 new_value/delta/battle_id，source 標記 grow_repeat（更新/重跑）
    若 DB 層仍抓到重複鍵（多執行緒競態），會 raise，作為最後防線。
    """
    event_id = make_floor_event_id(thread_id, category, target_name, field)
    existing = conn.execute(
        "SELECT id FROM collide_growth_log WHERE thread_id = ? AND floor_event_id = ?",
        (thread_id, event_id),
    ).fetchone()

    if existing:
        conn.execute(
            """UPDATE collide_growth_log
               SET new_value = ?, delta = ?, reason = ?, source = ?, battle_id = ?, reviewed = 0
               WHERE id = ?""",
            (new_value, delta, reason, source, battle_id, existing[0]),
        )
        mode = "update"
    else:
        conn.execute(
            """INSERT INTO collide_growth_log
               (thread_id, floor_event_id, battle_id, category, target_name, field,
                old_value, new_value, delta, reason, source, reviewed, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, datetime('now'))""",
            (thread_id, event_id, battle_id, category, target_name, field,
             old_value, new_value, delta, reason, source),
        )
        mode = "insert"
    conn.commit()
    return mode


def apply_growth(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    thread_name: str,
    growths: list[dict],
) -> dict:
    """套用一樓層的全部成長項目。

    Args:
        growths: 每筆為
            {category, target_name, field, old_value, new_value, delta, reason, battle_id?}

    Returns:
        {"mode": "first"|"repeat", "inserted": n, "updated": m,
         "summary_generated": bool}
    """
    first = not floor_is_summarized(conn, thread_id)
    inserted = updated = 0
    for g in growths:
        g = dict(g)
        if not first:
            g.setdefault("source", "grow_repeat")
        mode = upsert_growth(conn, thread_id=thread_id, **g)
        if mode == "insert":
            inserted += 1
        else:
            updated += 1
    mark_floor_summarized(conn, thread_id, thread_name)
    return {
        "mode": "first" if first else "repeat",
        "inserted": inserted,
        "updated": updated,
        "summary_generated": True,
    }


def growth_log_for_floor(conn: sqlite3.Connection, thread_id: str) -> list[dict]:
    """查詢一樓層全部成長 log（/clash log 用）。"""
    rows = conn.execute(
        "SELECT * FROM collide_growth_log WHERE thread_id = ? ORDER BY id", (thread_id,)
    ).fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM collide_growth_log LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def list_growth_log(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    """列出全部成長 log（管理頁面用，最新在前）。"""
    rows = conn.execute(
        "SELECT * FROM collide_growth_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM collide_growth_log LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def list_action_log(conn: sqlite3.Connection, limit: int = 500) -> list[dict]:
    """列出全部 Action Log（管理頁面用，最新在前）。"""
    rows = conn.execute(
        "SELECT * FROM collide_action_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM collide_action_log LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def list_floors(conn: sqlite3.Connection) -> list[dict]:
    """列出全部樓層（管理頁面用）。"""
    rows = conn.execute("SELECT * FROM collide_floors ORDER BY id DESC").fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM collide_floors LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def review_growth(
    conn: sqlite3.Connection,
    log_id: int,
    *,
    reviewed_by: str,
    approved: bool = False,
    new_value: str | None = None,
    note: str = "",
) -> dict:
    """管理員人工審查成長 log。

    - approved=True：標記 reviewed，不變更數值。
    - new_value 提供：修正數值。除標記原 log 已審查外，<strong>新增一筆 source=manual 的 log</strong>
      記錄修正（old=原 new_value，new=修正值），全程可追溯。
    回傳 {"original": {...}, "correction": {...} | None}。
    """
    row = conn.execute(
        "SELECT * FROM collide_growth_log WHERE id = ?", (log_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"成長 log #{log_id} 不存在")
    cols = [c[0] for c in conn.execute("SELECT * FROM collide_growth_log LIMIT 0").description]
    original = dict(zip(cols, row))
    now = "datetime('now')"

    conn.execute(
        f"""UPDATE collide_growth_log SET
              reviewed = 1, reviewed_by = ?, reviewed_note = ?, created_at = {now}
            WHERE id = ?""",
        (reviewed_by, note, log_id),
    )
    correction = None
    if new_value is not None and new_value != original.get("new_value"):
        conn.execute(
            """INSERT INTO collide_growth_log
               (thread_id, floor_event_id, battle_id, category, target_name, field,
                old_value, new_value, delta, reason, source, reviewed, reviewed_by, reviewed_note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1, ?, ?, datetime('now'))""",
            (
                original["thread_id"],
                f"{original['thread_id']}#manual#{original['target_name']}#{original['field']}#{log_id}",
                original.get("battle_id"),
                original.get("category"),
                original.get("target_name"),
                original.get("field"),
                original.get("new_value"),
                new_value,
                f"{original.get('new_value')}→{new_value}",
                "管理員人工修正",
                reviewed_by,
                note,
            ),
        )
        correction_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        correction = dict(zip(cols, conn.execute(
            "SELECT * FROM collide_growth_log WHERE id = ?", (correction_id,)
        ).fetchone()))
    conn.commit()
    return {"original": original, "correction": correction}


# ── 背景能力使用 Log（熟練度成長依據）──────────────────────

ABILITY_USAGE_SOURCES = ("clash", "thread_scan", "manual")


def record_ability_usage(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    actor: str,
    ability: str,
    source: str = "thread_scan",
    detail: str = "",
) -> str:
    """記錄一筆「本樓層使用過的能力」（熟練度成長依據）。

    UNIQUE(thread_id, actor, ability)：重複掃描覆寫 detail，不新增。
    source：clash（對撞觸發）/ thread_scan（討論串掃描）/ manual（人工補登）。
    """
    if source not in ABILITY_USAGE_SOURCES:
        source = "thread_scan"
    existing = conn.execute(
        "SELECT id FROM collide_ability_usage WHERE thread_id = ? AND actor = ? AND ability = ?",
        (thread_id, actor, ability),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE collide_ability_usage SET source = ?, detail = ?, created_at = datetime('now') WHERE id = ?",
            (source, detail, existing[0]),
        )
        return "update"
    conn.execute(
        "INSERT INTO collide_ability_usage (thread_id, actor, ability, source, detail, created_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (thread_id, actor, ability, source, detail),
    )
    return "insert"


def record_ability_usage_bulk(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    usages: list[dict],
) -> dict:
    """批量記錄（/grow 掃描結果一次寫入）。每筆為 {actor, ability, source?, detail?}。

    回傳 {"inserted": n, "updated": m}。
    """
    inserted = updated = 0
    for u in usages:
        mode = record_ability_usage(
            conn,
            thread_id=thread_id,
            actor=u["actor"],
            ability=u["ability"],
            source=u.get("source", "thread_scan"),
            detail=u.get("detail", ""),
        )
        if mode == "insert":
            inserted += 1
        else:
            updated += 1
    return {"inserted": inserted, "updated": updated}


def abilities_used_in_floor(conn: sqlite3.Connection, thread_id: str) -> list[dict]:
    """查詢一樓層使用過的能力集合（熟練度成長 = 此集合，不只 clash）。"""
    rows = conn.execute(
        "SELECT * FROM collide_ability_usage WHERE thread_id = ? ORDER BY actor, ability",
        (thread_id,),
    ).fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM collide_ability_usage LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


# ── 樓層 Action Log（普通攻擊等動作記錄）──────────────────

ACTION_LOG_TYPES = ("clash", "skill_use", "summarize", "grow", "auto")


def record_action_log(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    action_type: str,
    actor: str,
    action_detail: str = "",
    related_values: Optional[dict] = None,
) -> None:
    """記錄一筆樓層 AI Action Log（全部可查）。

    無招式的普通攻擊也記錄：action_type = "skill_use"、
    action_detail = "普通攻擊（無招式）"——供武藝值（Martial）成長判斷。
    """
    if action_type not in ACTION_LOG_TYPES:
        action_type = "auto"
    import json as _json

    related = _json.dumps(related_values, ensure_ascii=False) if related_values else None
    conn.execute(
        "INSERT INTO collide_action_log (thread_id, action_type, actor, action_detail, related_values, created_at)"
        " VALUES (?, ?, ?, ?, ?, datetime('now'))",
        (thread_id, action_type, actor, action_detail, related),
    )
    conn.commit()


def record_basic_attack(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    actor: str,
    source: str = "clash",
    detail: str = "",
) -> str:
    """記錄一筆「普通攻擊（無招式）」。

    1) 寫入 collide_action_log（action_type=skill_use）。
    2) 寫入 collide_ability_usage（ability=「普通攻擊」，UNIQUE 防重複覆寫）——
       /grow 依此判斷該樓層的武藝值（Martial）成長。

    Returns:
        "insert" / "update"（ability_usage 的模式）
    """
    record_action_log(
        conn,
        thread_id=thread_id,
        action_type="skill_use",
        actor=actor,
        action_detail="普通攻擊（無招式）",
        related_values={"source": source, "detail": detail},
    )
    return record_ability_usage(
        conn,
        thread_id=thread_id,
        actor=actor,
        ability="普通攻擊",
        source=source,
        detail=detail or "無招式普通攻擊",
    )


# ── 未來功能參考 Log（三方以上混戰等 v1 不支援事件）────────

FUTURE_EVENT_TYPES = ("multiway_clash", "other")


def record_future_log(
    conn: sqlite3.Connection,
    *,
    thread_id: str,
    thread_name: str = "",
    event_type: str = "multiway_clash",
    involved: str = "",
    summary: str = "",
    suggestion: str = "",
) -> str:
    """記錄 v1 不支援、但需留存供未來設計參考的事件（如三方以上混戰）。

    UNIQUE(thread_id, event_type, involved, summary)：相同事件重複回報不新增。
    """
    if event_type not in FUTURE_EVENT_TYPES:
        event_type = "other"
    existing = conn.execute(
        "SELECT id FROM collide_future_log WHERE thread_id = ? AND event_type = ? AND involved = ? AND summary = ?",
        (thread_id, event_type, involved, summary),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE collide_future_log SET thread_name = ?, suggestion = ?, created_at = datetime('now') WHERE id = ?",
            (thread_name, suggestion, existing[0]),
        )
        return "update"
    conn.execute(
        "INSERT INTO collide_future_log (thread_id, thread_name, event_type, involved, summary, suggestion, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
        (thread_id, thread_name, event_type, involved, summary, suggestion),
    )
    return "insert"


def init_schema(conn: sqlite3.Connection) -> None:
    """建立樓層與成長審計表（供 collide_db.py 呼叫，確保防呆鍵唯一）。"""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS collide_floors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT UNIQUE,
            thread_name TEXT,
            status TEXT DEFAULT 'open',
            summary_generated INTEGER DEFAULT 0,
            summary_at TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS collide_growth_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            floor_event_id TEXT,
            battle_id INTEGER,
            category TEXT,
            target_name TEXT,
            field TEXT,
            old_value TEXT,
            new_value TEXT,
            delta TEXT,
            reason TEXT,
            source TEXT,
            reviewed INTEGER DEFAULT 0,
            reviewed_by TEXT,
            reviewed_note TEXT,
            created_at TEXT,
            UNIQUE(thread_id, floor_event_id)
        );
        CREATE TABLE IF NOT EXISTS collide_ability_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            actor TEXT,
            ability TEXT,
            source TEXT,
            detail TEXT,
            created_at TEXT,
            UNIQUE(thread_id, actor, ability)
        );
        CREATE TABLE IF NOT EXISTS collide_action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            action_type TEXT,
            actor TEXT,
            action_detail TEXT,
            related_values TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS collide_future_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id TEXT,
            thread_name TEXT,
            event_type TEXT,
            involved TEXT,
            summary TEXT,
            suggestion TEXT,
            created_at TEXT,
            UNIQUE(thread_id, event_type, involved, summary)
        );
        """
    )
    conn.commit()
    _migrate_columns(conn)


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """為既有資料庫補齊成長溯源欄位（battle_id），避免 CREATE IF NOT EXISTS 不修改舊表。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(collide_growth_log)")}
    if "battle_id" not in cols:
        conn.execute(
            "ALTER TABLE collide_growth_log ADD COLUMN battle_id INTEGER"
        )
        conn.commit()


if __name__ == "__main__":
    import os
    import tempfile

    tmp = os.path.join(tempfile.gettempdir(), "collide_growth_test.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    init_schema(db)

    # 背景能力使用 Log（討論串掃描，熟練度成長依據）
    print("usage:", record_ability_usage_bulk(
        db,
        thread_id="T1",
        usages=[
            {"actor": "凌空", "ability": "蒼雷", "source": "thread_scan", "detail": "日常釋放"},
            {"actor": "凌空", "ability": "蒼雷", "source": "clash", "detail": "對撞觸發"},  # 覆寫，不新增
            {"actor": "黑龍", "ability": "滅世炎", "source": "thread_scan"},
        ],
    ))
    print("abilities:", [(a["actor"], a["ability"], a["source"]) for a in abilities_used_in_floor(db, "T1")])

    # 普通攻擊記錄（無招式 → 供 Martial 成長判斷）
    print("basic_attack:", record_basic_attack(db, thread_id="T1", actor="凌空", source="clash", detail="空手揍人"))
    print("basic_attack repeat:", record_basic_attack(db, thread_id="T1", actor="凌空", source="clash", detail="再次空手"))
    print("ability_usage has 普通攻擊:", [a for a in abilities_used_in_floor(db, "T1") if a["ability"] == "普通攻擊"])

    # 三方以上混戰 → collide_future_log
    print("future:", record_future_log(
        db, thread_id="T1", thread_name="團戰測試", event_type="multiway_clash",
        involved="凌空、黑龍、阿明", summary="三方混戰", suggestion="拆成 凌空vs黑龍、凌空vs阿明 兩組 1v1",
    ))
    print("future repeat:", record_future_log(
        db, thread_id="T1", thread_name="團戰測試重跑", event_type="multiway_clash",
        involved="凌空、黑龍、阿明", summary="三方混戰", suggestion="同上",
    ))
    rows = db.execute("SELECT thread_id, event_type, involved FROM collide_future_log").fetchall()
    print("future rows:", rows)
    rows2 = db.execute("SELECT actor, action_type, action_detail FROM collide_action_log").fetchall()
    print("action rows:", rows2)

    # 首次 /grow
    r1 = apply_growth(
        db,
        thread_id="T1",
        thread_name="凌空 vs 黑龍決鬥",
        growths=[
            {"category": "character", "target_name": "凌空", "field": "exp",
             "old_value": "15", "new_value": "25", "delta": "+10", "reason": "跨越階級生死決鬥"},
            {"category": "character", "target_name": "凌空", "field": "proficiency",
             "old_value": "0", "new_value": "5", "delta": "+5", "reason": "結界戰鬥實踐"},
        ],
    )
    print("first:", r1, "count =", len(growth_log_for_floor(db, "T1")))

    # 再次 /grow（更新/重跑）：同成長鍵覆寫，不新增
    r2 = apply_growth(
        db,
        thread_id="T1",
        thread_name="凌空 vs 黑龍決鬥（重跑）",
        growths=[
            {"category": "character", "target_name": "凌空", "field": "exp",
             "old_value": "25", "new_value": "28", "delta": "+3", "reason": "高難度委託"},
            {"category": "character", "target_name": "凌空", "field": "proficiency",
             "old_value": "5", "new_value": "6", "delta": "+1", "reason": "高難度委託"},
        ],
    )
    print("repeat:", r2, "count =", len(growth_log_for_floor(db, "T1")))

    for row in growth_log_for_floor(db, "T1"):
        print("  log:", row["floor_event_id"], "|", row["old_value"], "->", row["new_value"],
              "|", row["delta"], "|", row["source"], "| battle_id:", row["battle_id"])

    # battle_id 溯源：clash 來源成長帶 battle_id，非 clash 來源留空
    r3 = apply_growth(
        db,
        thread_id="T2",
        thread_name="神月 vs 甲",
        growths=[
            {"category": "character", "target_name": "神月", "field": "exp",
             "old_value": "40", "new_value": "50", "delta": "+10", "reason": "決鬥",
             "battle_id": 42},
            {"category": "character", "target_name": "神月", "field": "proficiency",
             "old_value": "0", "new_value": "5", "delta": "+5", "reason": "主線事件"},
        ],
    )
    logs = growth_log_for_floor(db, "T2")
    assert logs[0]["battle_id"] == 42, "clash 來源成長應帶 battle_id"
    assert logs[1]["battle_id"] is None, "非 clash 來源應留空"
    print("battle_id test: PASS")

    db.close()
