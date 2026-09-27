"""
collide_db.py — 招式對撞系統的資料層（能力原理 / 招式庫）

能力原理的單一事實來源：
- collide_abilities  能力（principle 能力原理 / range_limit / range_decay）
- collide_skills     招式（ability 掛所屬能力、application 擴展應用分類）

collide_abilities 同時鏡像到 world_lore（category=角色能力），讓 RP bot 的
Phase 1 recall 與 supplement 自動載入能力原理。

db_lookup 契約（collide_pipeline.DbLookup）：async (char_name, table) -> row dict or None
table 支援：character / ability / skill / domain
"""

import json
import os
import sqlite3
import tempfile

from collide_growth import init_schema as _init_growth_schema

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "rp_memory.db")
RACE_MOD_FILE = os.path.join(SCRIPT_DIR, "race_modifiers.json")

_race_mods_cache: dict | None = None
_race_mods_mtime: float | None = None

RACE_MOD_MIN = 0.8
RACE_MOD_MAX = 1.5


def load_race_modifiers() -> dict:
    """讀取種族修正係數表（race_modifiers.json）。未定義種族以 1.0 對待。

    回傳 {種族: {六維: 修正}}；`_pending_review` 等非 dict 欄位被濾除。
    以檔案修改時間（mtime）偵測外部變更：其他程序（bot/管理員）更新檔案後，
    本程序的 cache 會自動失效重讀，不需手動 invalidate。
    """
    global _race_mods_cache, _race_mods_mtime
    try:
        mtime = os.path.getmtime(RACE_MOD_FILE)
    except OSError:
        mtime = None
    if _race_mods_cache is not None and _race_mods_mtime == mtime:
        return _race_mods_cache
    try:
        with open(RACE_MOD_FILE, encoding="utf-8") as f:
            data = json.load(f)
        _race_mods_cache = {k: v for k, v in data.items() if isinstance(v, dict)}
        _race_mods_mtime = mtime
    except (FileNotFoundError, json.JSONDecodeError):
        _race_mods_cache = {}
        _race_mods_mtime = mtime
    return _race_mods_cache


def invalidate_race_mods_cache() -> None:
    """種族表寫入後清除快取，讓後續計算立刻讀到新值。"""
    global _race_mods_cache
    _race_mods_cache = None


def race_mods_for(race: str) -> dict:
    """該種族的六維修正 dict（key 依力量/敏捷/智力/體質/魅力/幸運；未定義回 {}）。"""
    return dict(load_race_modifiers().get(str(race or ""), {}) or {})


def race_mod_for(race: str) -> float:
    """單一修正值（六維同值的種族用；未定義回 1.0）。"""
    values = [float(v) for v in race_mods_for(race).values() if isinstance(v, (int, float))]
    return values[0] if values else 1.0


def race_defined(race: str) -> bool:
    """種族是否已列入種族表（即使修正全為 1.0 也算已定義）。"""
    return str(race or "") in load_race_modifiers()


def pending_review_races() -> list[str]:
    """AI 填入、尚未人工審查的種族清單（race_modifiers.json 的 _pending_review）。"""
    try:
        with open(RACE_MOD_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [str(r) for r in (data.get("_pending_review") or [])]
    except Exception:
        return []


def _write_race_file(data: dict) -> bool:
    """寫回 race_modifiers.json（先備份 .bak）。回傳是否成功。"""
    try:
        import shutil
        if os.path.exists(RACE_MOD_FILE):
            shutil.copy2(RACE_MOD_FILE, RACE_MOD_FILE + ".bak")
        with open(RACE_MOD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        return False
    invalidate_race_mods_cache()
    return True


def mark_race_reviewed(race: str) -> bool:
    """把種族從待審查清單移除（管理員人工確認/修改後）。"""
    race = str(race or "").strip()
    if not race:
        return False
    try:
        with open(RACE_MOD_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    pending = data.get("_pending_review") or []
    if race not in pending:
        return True
    pending = [r for r in pending if r != race]
    if pending:
        data["_pending_review"] = pending
    else:
        data.pop("_pending_review", None)
    return _write_race_file(data)


def delete_race(race: str) -> bool:
    """從種族表刪除一個種族（含自待審查清單移除）。回傳是否成功。"""
    race = str(race or "").strip()
    if not race:
        return False
    try:
        with open(RACE_MOD_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return False
    if race not in data:
        return False
    data.pop(race, None)
    pending = data.get("_pending_review") or []
    if race in pending:
        pending = [r for r in pending if r != race]
        if pending:
            data["_pending_review"] = pending
        else:
            data.pop("_pending_review", None)
    return _write_race_file(data)


def save_race_mods(race: str, mods: dict, *, pending_review: bool = False) -> bool:
    """把種族的六維修正寫入 race_modifiers.json（立即生效）。

    pending_review=True 時一併加入 `_pending_review` 清單（待人工審查）。
    寫入前先備份（.bak），避免寫一半壞檔。回傳是否寫入成功。
    """
    race = str(race or "").strip()
    if not race:
        return False
    try:
        with open(RACE_MOD_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    clean = {
        str(k).strip(): round(float(v), 1)
        for k, v in (mods or {}).items()
        if isinstance(v, (int, float))
    }
    data[race] = clean
    if pending_review:
        pending = data.setdefault("_pending_review", [])
        if race not in pending:
            pending.append(race)
    try:
        import shutil
        if os.path.exists(RACE_MOD_FILE):
            shutil.copy2(RACE_MOD_FILE, RACE_MOD_FILE + ".bak")
        with open(RACE_MOD_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        return False
    invalidate_race_mods_cache()
    return True

SCHEMA = """
CREATE TABLE IF NOT EXISTS world_lore (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    topic TEXT,
    content TEXT
);
CREATE TABLE IF NOT EXISTS collide_abilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    owner TEXT DEFAULT '',
    category TEXT DEFAULT '',
    principle TEXT NOT NULL,
    range_limit TEXT DEFAULT '',
    range_decay TEXT DEFAULT '',
    proficiency INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(name, owner)
);
CREATE TABLE IF NOT EXISTS collide_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    ability TEXT NOT NULL,
    owner TEXT DEFAULT '',
    application TEXT NOT NULL,
    skill_type TEXT DEFAULT '',
    magic_system TEXT DEFAULT '',
    element TEXT DEFAULT '',
    range TEXT DEFAULT '',
    mp_cost INTEGER DEFAULT 0,
    power_base REAL NOT NULL,
    effect TEXT DEFAULT '',
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(name, owner)
);
CREATE TABLE IF NOT EXISTS collide_characters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    affiliation TEXT DEFAULT '',
    race TEXT DEFAULT '',
    school_rank TEXT DEFAULT '',
    stats TEXT DEFAULT '{}',
    special_tags TEXT DEFAULT '[]',
    special_forms TEXT DEFAULT '{}',
    weapon TEXT DEFAULT '',
    weapon_base REAL,
    max_mp INTEGER DEFAULT 40,
    martial INTEGER DEFAULT 0,
    exp INTEGER DEFAULT 0,
    is_teacher INTEGER DEFAULT 0,
    is_villain INTEGER DEFAULT 0,
    dynamic_difficulty INTEGER DEFAULT 0,
    calibrate_arc TEXT DEFAULT '',
    calibrate_roster TEXT DEFAULT '[]',
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS collide_domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    owner TEXT DEFAULT '',
    domain_type TEXT DEFAULT '',
    awakening TEXT DEFAULT '',
    precision REAL DEFAULT 0,
    rule_self TEXT DEFAULT '',
    rule_enemy TEXT DEFAULT '',
    rule_environment TEXT DEFAULT '',
    cooldown TEXT DEFAULT '',
    sustain_mp REAL DEFAULT 0,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(name, owner)
);
CREATE TABLE IF NOT EXISTS collide_battles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT,
    battle_time TEXT,
    attacker TEXT,
    attacker_skill TEXT,
    defender TEXT,
    defender_skill TEXT,
    atk_output REAL,
    def_output REAL,
    element_mod REAL,
    verdict TEXT,
    result TEXT,
    remaining_damage TEXT,
    phase_c TEXT,
    skills_meta TEXT,
    domain_fusion INTEGER DEFAULT 0,
    chain_of_thought TEXT,
    effects TEXT,
    confirmed INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    undone_by TEXT,
    undone_reason TEXT,
    created_at TEXT
);
"""


def open_conn(db_file: str | None = None) -> sqlite3.Connection:
    """開啟連線（row_factory 回傳 dict 列），供 collide_parser 等外部使用。

    db_file 省略時動態讀取模組 DB_FILE（測試可 patch 後生效）。
    """
    if db_file is None:
        db_file = DB_FILE
    conn = sqlite3.connect(db_file, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """建立能力與招式表，並一併建立樓層/成長審計表（防呆）。"""
    conn.executescript(SCHEMA)
    _init_growth_schema(conn)
    _migrate_collide_columns(conn)
    conn.commit()


def _migrate_collide_columns(conn: sqlite3.Connection) -> None:
    """為舊資料庫補齊新欄位（CREATE IF NOT EXISTS 不會改既有表）。"""
    tables = {
        "collide_abilities": [("category", "TEXT DEFAULT ''"), ("proficiency", "INTEGER DEFAULT 0")],
        "collide_skills": [("range", "TEXT DEFAULT ''")],
        "collide_domains": [],
        "collide_battles": [
            ("phase_c", "TEXT DEFAULT ''"),
            ("skills_meta", "TEXT DEFAULT '{}'"),
            ("effects", "TEXT DEFAULT '{}'"),
        ],
    }
    for table, cols in tables.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col, ddl in cols:
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


# ── 能力（collide_abilities）──────────────────────────────

def get_ability(conn: sqlite3.Connection, name: str, owner: str = "") -> dict | None:
    row = conn.execute(
        "SELECT * FROM collide_abilities WHERE name = ? AND owner = ?",
        (name, owner),
    ).fetchone()
    return dict(row) if row else None


def list_abilities(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM collide_abilities ORDER BY owner, name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_ability_named(conn: sqlite3.Connection, name: str) -> dict | None:
    """依能力名稱查詢（不限 owner）。重名時取第一筆，供招式推導檢查使用。"""
    row = conn.execute(
        "SELECT * FROM collide_abilities WHERE name = ? ORDER BY owner, name LIMIT 1",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def upsert_ability(conn: sqlite3.Connection, data: dict) -> dict:
    """新增或更新能力（UNIQUE(name, owner) 同鍵覆寫）。回傳該列。"""
    name = data["name"].strip()
    owner = str(data.get("owner") or "").strip()
    conn.execute(
        """
        INSERT INTO collide_abilities (name, owner, category, principle, range_limit, range_decay, proficiency, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(name, owner) DO UPDATE SET
            category = excluded.category,
            principle = excluded.principle,
            range_limit = excluded.range_limit,
            range_decay = excluded.range_decay,
            proficiency = excluded.proficiency,
            updated_at = datetime('now')
        """,
        (
            name,
            owner,
            str(data.get("category") or "").strip(),
            str(data.get("principle") or "").strip(),
            str(data.get("range_limit") or "").strip(),
            str(data.get("range_decay") or "").strip(),
            int(data.get("proficiency") or 0),
        ),
    )
    return get_ability(conn, name, owner)


# ── 招式（collide_skills）─────────────────────────────────

def get_skill(conn: sqlite3.Connection, name: str, owner: str = "") -> dict | None:
    row = conn.execute(
        "SELECT * FROM collide_skills WHERE name = ? AND owner = ?",
        (name, owner),
    ).fetchone()
    return dict(row) if row else None


def get_skill_named(conn: sqlite3.Connection, name: str) -> dict | None:
    """依招式名稱查詢（不限 owner）。重名時取第一筆，供 db_lookup 使用。"""
    row = conn.execute(
        "SELECT * FROM collide_skills WHERE name = ? ORDER BY owner, name LIMIT 1",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def list_skills(conn: sqlite3.Connection, ability: str | None = None) -> list[dict]:
    if ability:
        rows = conn.execute(
            "SELECT * FROM collide_skills WHERE ability = ? ORDER BY owner, name",
            (ability,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM collide_skills ORDER BY ability, owner, name"
        ).fetchall()
    return [dict(r) for r in rows]


def skills_of_ability(conn: sqlite3.Connection, ability: str) -> list[dict]:
    """某能力下的全部招式（供未登記招式的「合理變體」取最低 power_base）。"""
    return list_skills(conn, ability=ability)


def upsert_skill(conn: sqlite3.Connection, data: dict) -> dict:
    """新增或更新招式（UNIQUE(name, owner) 同鍵覆寫）。回傳該列。"""
    name = data["name"].strip()
    owner = str(data.get("owner") or "").strip()
    conn.execute(
        """
        INSERT INTO collide_skills (name, ability, owner, application, skill_type, magic_system,
                                    element, range, mp_cost, power_base, effect, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(name, owner) DO UPDATE SET
            ability = excluded.ability,
            application = excluded.application,
            skill_type = excluded.skill_type,
            magic_system = excluded.magic_system,
            element = excluded.element,
            range = excluded.range,
            mp_cost = excluded.mp_cost,
            power_base = excluded.power_base,
            effect = excluded.effect,
            updated_at = datetime('now')
        """,
        (
            name,
            str(data.get("ability") or "").strip(),
            owner,
            str(data.get("application") or "").strip(),
            str(data.get("skill_type") or "").strip(),
            str(data.get("magic_system") or "").strip(),
            str(data.get("element") or "").strip(),
            str(data.get("range") or "").strip(),
            int(data.get("mp_cost") or 0),
            float(data.get("power_base") or 0.0),
            str(data.get("effect") or "").strip(),
        ),
    )
    return get_skill(conn, name, owner)


# ── 角色（collide_characters）─────────────────────────────

def _json_dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def get_character(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM collide_characters WHERE name = ?", (name,)
    ).fetchone()
    if not row:
        return None
    out = dict(row)
    for key, empty in (
        ("stats", {}), ("special_tags", []), ("special_forms", {}), ("calibrate_roster", []),
    ):
        val = out.get(key)
        if isinstance(val, str):
            out[key] = json.loads(val) if val else empty
    return out


def list_characters(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM collide_characters ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_character(conn: sqlite3.Connection, data: dict) -> dict:
    """新增或更新角色（name 為主鍵）。stats/special_tags/special_forms 以 JSON 儲存。"""
    name = data["name"].strip()
    conn.execute(
        """
        INSERT INTO collide_characters (name, affiliation, race, school_rank, stats, special_tags,
                                        special_forms, weapon, weapon_base, max_mp, martial, exp,
                                        is_teacher, is_villain, dynamic_difficulty, calibrate_arc,
                                        calibrate_roster, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(name) DO UPDATE SET
            affiliation = excluded.affiliation,
            race = excluded.race,
            school_rank = excluded.school_rank,
            stats = excluded.stats,
            special_tags = excluded.special_tags,
            special_forms = excluded.special_forms,
            weapon = excluded.weapon,
            weapon_base = excluded.weapon_base,
            max_mp = excluded.max_mp,
            martial = excluded.martial,
            exp = excluded.exp,
            is_teacher = excluded.is_teacher,
            is_villain = excluded.is_villain,
            dynamic_difficulty = excluded.dynamic_difficulty,
            calibrate_arc = excluded.calibrate_arc,
            calibrate_roster = excluded.calibrate_roster,
            updated_at = datetime('now')
        """,
        (
            name,
            str(data.get("affiliation") or "").strip(),
            str(data.get("race") or "").strip(),
            str(data.get("school_rank") or "").strip(),
            _json_dumps(data.get("stats") or {}),
            _json_dumps(data.get("special_tags") or []),
            _json_dumps(data.get("special_forms") or {}),
            str(data.get("weapon") or "").strip(),
            data.get("weapon_base"),
            int(data.get("max_mp") or 40),
            int(data.get("martial") or 0),
            int(data.get("exp") or 0),
            1 if data.get("is_teacher") else 0,
            1 if data.get("is_villain") else 0,
            1 if data.get("dynamic_difficulty") else 0,
            str(data.get("calibrate_arc") or "").strip(),
            _json_dumps(data.get("calibrate_roster") or []),
        ),
    )
    return get_character(conn, name)


# ── 結界（collide_domains）────────────────────────────────

def get_domain(conn: sqlite3.Connection, name: str, owner: str = "") -> dict | None:
    row = conn.execute(
        "SELECT * FROM collide_domains WHERE name = ? AND owner = ?",
        (name, owner),
    ).fetchone()
    return dict(row) if row else None


def get_domain_named(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM collide_domains WHERE name = ? ORDER BY owner, name LIMIT 1",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def list_domains(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM collide_domains ORDER BY owner, name"
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_domain(conn: sqlite3.Connection, data: dict) -> dict:
    """新增或更新結界（UNIQUE(name, owner) 同鍵覆寫）。回傳該列。"""
    name = data["name"].strip()
    owner = str(data.get("owner") or "").strip()
    conn.execute(
        """
        INSERT INTO collide_domains (name, owner, domain_type, awakening, precision, rule_self,
                                     rule_enemy, rule_environment, cooldown, sustain_mp,
                                     created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        ON CONFLICT(name, owner) DO UPDATE SET
            domain_type = excluded.domain_type,
            awakening = excluded.awakening,
            precision = excluded.precision,
            rule_self = excluded.rule_self,
            rule_enemy = excluded.rule_enemy,
            rule_environment = excluded.rule_environment,
            cooldown = excluded.cooldown,
            sustain_mp = excluded.sustain_mp,
            updated_at = datetime('now')
        """,
        (
            name,
            owner,
            str(data.get("domain_type") or "").strip(),
            str(data.get("awakening") or "").strip(),
            float(data.get("precision") or 0.0),
            str(data.get("rule_self") or "").strip(),
            str(data.get("rule_enemy") or "").strip(),
            str(data.get("rule_environment") or "").strip(),
            str(data.get("cooldown") or "").strip(),
            float(data.get("sustain_mp") or 0.0),
        ),
    )
    return get_domain(conn, name, owner)


# ── 對撞紀錄（collide_battles）────────────────────────────

def insert_battle(conn: sqlite3.Connection, data: dict) -> dict:
    """新增一筆對撞紀錄（預設 status=pending）。回傳該列（含 id）。"""
    conn.execute(
        """
        INSERT INTO collide_battles (
            thread_id, battle_time, attacker, attacker_skill,
            defender, defender_skill, atk_output, def_output,
            element_mod, verdict, result, remaining_damage,
            phase_c, skills_meta, domain_fusion, chain_of_thought, effects, confirmed, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (
            str(data.get("thread_id") or "").strip(),
            str(data.get("battle_time") or "").strip(),
            str(data.get("attacker") or "").strip(),
            str(data.get("attacker_skill") or "").strip(),
            str(data.get("defender") or "").strip(),
            str(data.get("defender_skill") or "").strip(),
            data.get("atk_output"),
            data.get("def_output"),
            data.get("element_mod"),
            str(data.get("verdict") or "").strip(),
            _json_dumps(data.get("result") or {}),
            str(data.get("remaining_damage") or "").strip(),
            str(data.get("phase_c") or "").strip(),
            _json_dumps(data.get("skills_meta") or {}),
            1 if data.get("domain_fusion") else 0,
            str(data.get("chain_of_thought") or "").strip(),
            _json_dumps(data.get("effects") or {}),
            1 if data.get("confirmed") else 0,
            str(data.get("status") or "pending").strip(),
        ),
    )
    conn.commit()
    return get_battle(conn, conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def get_battle(conn: sqlite3.Connection, battle_id: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM collide_battles WHERE id = ?", (battle_id,)
    ).fetchone()
    return dict(row) if row else None


def list_battles(conn: sqlite3.Connection, *, status: str | None = None) -> list[dict]:
    if status:
        rows = conn.execute(
            "SELECT * FROM collide_battles WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM collide_battles ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def update_battle_status(
    conn: sqlite3.Connection,
    battle_id: int,
    status: str,
    *,
    undone_by: str = "",
    undone_reason: str = "",
) -> dict | None:
    """變更對撞紀錄狀態（pending→active→undone）。回傳更新後該列。"""
    conn.execute(
        """
        UPDATE collide_battles SET status = ?, undone_by = ?, undone_reason = ?
        WHERE id = ?
        """,
        (status, undone_by, undone_reason, battle_id),
    )
    conn.commit()
    return get_battle(conn, battle_id)


def growth_log_for_battle(conn: sqlite3.Connection, battle_id: int) -> list[dict]:
    """列出某場對撞衍生的所有成長紀錄（依 battle_id 關聯）。"""
    try:
        rows = conn.execute(
            "SELECT * FROM collide_growth_log WHERE battle_id = ? ORDER BY id",
            (battle_id,),
        ).fetchall()
    except Exception:
        return []
    return [dict(r) for r in rows]


# ── world_lore 鏡像 ───────────────────────────────────────

def _world_lore_topic(ability: dict) -> str:
    owner = str(ability.get("owner") or "").strip()
    return f"{owner}:{ability['name']}" if owner else ability["name"]


def _world_lore_content(ability: dict) -> str:
    """能力分類 + 原理 + 距離/衰減，組成 world_lore 條目內容（category=角色能力）。"""
    lines = [f"能力：{ability['name']}"]
    if ability.get("owner"):
        lines.append(f"擁有者：{ability['owner']}")
    if ability.get("category"):
        lines.append(f"能力分類：{ability['category']}")
    lines.append(f"能力原理：{ability['principle']}")
    if ability.get("range_limit"):
        lines.append(f"極限有效距離：{ability['range_limit']}")
    if ability.get("range_decay"):
        lines.append(f"距離衰減：{ability['range_decay']}")
    return "\n".join(lines)


def mirror_abilities_to_world_lore(conn: sqlite3.Connection) -> int:
    """把全部能力鏡像進 world_lore（category=角色能力），供 RP bot 召回。回傳條目數。"""
    count = 0
    for ability in list_abilities(conn):
        topic = _world_lore_topic(ability)
        content = _world_lore_content(ability)
        existing = conn.execute(
            "SELECT id FROM world_lore WHERE topic = ?",
            (topic,),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE world_lore SET content = ? WHERE id = ?",
                (content, existing[0]),
            )
        else:
            conn.execute(
                "INSERT INTO world_lore (category, topic, content) VALUES (?, ?, ?)",
                ("角色能力", topic, content),
            )
        count += 1
    conn.commit()
    return count


# ── clash db_lookup ───────────────────────────────────────

def fuzzy_name_match(query: str, names: list[str]) -> str | None:
    """模糊比對登錄名稱：先精確（不分大小寫），再子字串（後綴優先），最後反向包含。

    用途：玩家/討論串不會每次都輸出完整登記名（如「神月」→「吳神月」）；
    大小寫不敏感（如「WiFi」→「Wifi」），回傳登錄原名（保留原大小寫）。
    """
    q = (query or "").strip()
    if not q:
        return None
    for n in names:
        if n == q:
            return n
    ql = q.lower()
    hits = [n for n in names if ql in n.lower()]
    if hits:
        suffix = [n for n in hits if n.lower().endswith(ql)]
        if suffix:
            return min(suffix, key=len)
        return min(hits, key=len)
    reverse = [n for n in names if n.lower() in ql]
    if reverse:
        return max(reverse, key=len)
    return None


def make_db_lookup(conn: sqlite3.Connection):
    """回傳符合 collide_pipeline.DbLookup 契約的 async callable。

    用法：db_lookup = collide_db.make_db_lookup(collide_db.open_conn())
         result = await run_clash(history, style_rules, db_lookup)
    """
    _NAMED_GETTERS = {
        "ability": get_ability_named,
        "skill": get_skill_named,
        "domain": get_domain_named,
    }
    _NAMED_TABLES = {
        "ability": "collide_abilities",
        "skill": "collide_skills",
        "domain": "collide_domains",
    }

    def _resolve_named(table: str, name: str) -> dict | None:
        getter = _NAMED_GETTERS[table]
        row = getter(conn, name)
        if row:
            return row
        real = fuzzy_name_match(
            name,
            [r[0] for r in conn.execute(f"SELECT name FROM {_NAMED_TABLES[table]}")],
        )
        if real and real != name:
            return getter(conn, real)
        return None

    def _resolve_character(name: str) -> dict | None:
        row = get_character(conn, name)
        if not row:
            real = fuzzy_name_match(
                name,
                [r[0] for r in conn.execute("SELECT name FROM collide_characters")],
            )
            if real and real != name:
                row = get_character(conn, real)
        if row:
            out = dict(row)
            out["_race_mods"] = race_mods_for(out.get("race") or "")
            return out
        if conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='character_profiles'"
        ).fetchone():
            row = conn.execute(
                "SELECT * FROM character_profiles WHERE char_name = ?",
                (name,),
            ).fetchone()
            if not row:
                real = fuzzy_name_match(
                    name,
                    [r[0] for r in conn.execute("SELECT char_name FROM character_profiles")],
                )
                if real and real != name:
                    row = conn.execute(
                        "SELECT * FROM character_profiles WHERE char_name = ?",
                        (real,),
                    ).fetchone()
            if row:
                out = dict(row)
                out["name"] = out["char_name"]
                out["_race_mods"] = {}
                return out
        return None

    async def lookup(char_name: str, table: str) -> dict | None:
        if table == "character":
            return _resolve_character(char_name)
        if table == "roster":
            return [
                {
                    "name": r["name"],
                    "race": r["race"] or "",
                    "school_rank": r["school_rank"] or "",
                    "is_villain": bool(r["is_villain"]),
                }
                for r in conn.execute(
                    "SELECT name, race, school_rank, is_villain FROM collide_characters ORDER BY name"
                )
            ]
        if table == "ability_skills":
            rows = conn.execute(
                "SELECT power_base FROM collide_skills WHERE ability = ?",
                (char_name,),
            ).fetchall()
            return [{"power_base": float(r[0])} for r in rows]
        if table == "abilities":
            rows = conn.execute(
                "SELECT name, owner, category, principle FROM collide_abilities ORDER BY owner, name"
            ).fetchall()
            return [dict(r) for r in rows]
        if table in _NAMED_GETTERS:
            return _resolve_named(table, char_name)
        return None

    return lookup


def quick_test():
    """手動測試（不呼叫 API）：驗證 schema、CRUD、鏡像與 lookup。"""
    tmp = os.path.join(tempfile.gettempdir(), "collide_db_test.db")
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = open_conn(tmp)
    init_schema(conn)

    a1 = upsert_ability(conn, {
        "name": "蒼炎", "owner": "神月",
        "principle": "以魔力為燃料的火焰放出能力，可塑形、附著與散布，受限於魔力存量；擴展方向為火焰的形狀、附著目標與非傷害性放出。",
        "range_limit": "極限 80 公尺", "range_decay": "每超 10 公尺威力 -5%",
    })
    assert a1["name"] == "蒼炎" and a1["principle"]
    a1b = upsert_ability(conn, {"name": "蒼炎", "owner": "神月", "principle": "更新後的原理"})
    assert a1b["principle"] == "更新後的原理", "同鍵應覆寫不新增"
    assert len(list_abilities(conn)) == 1, "覆寫不該新增列"

    s1 = upsert_skill(conn, {
        "name": "火龍彈", "ability": "蒼炎", "owner": "神月",
        "application": "擴展一：以造成傷害為前提的火焰放出",
        "skill_type": "個人魔法", "magic_system": "自然系", "element": "火",
        "range": "極限 80 公尺",
        "mp_cost": 12, "power_base": 7.5,
    })
    assert s1["ability"] == "蒼炎" and s1["power_base"] == 7.5
    assert s1["range"] == "極限 80 公尺", "招式自身距離應可寫入"
    assert len(skills_of_ability(conn, "蒼炎")) == 1

    n = mirror_abilities_to_world_lore(conn)
    assert n == 1, "應鏡像 1 條能力"
    row = conn.execute(
        "SELECT content FROM world_lore WHERE category = '角色能力' AND topic = '神月:蒼炎'"
    ).fetchone()
    assert row and "能力原理" in row[0], "world_lore 應含能力原理"

    lookup = make_db_lookup(conn)
    import asyncio

    async def _run():
        assert (await lookup("蒼炎", "ability"))["name"] == "蒼炎"
        assert (await lookup("火龍彈", "skill"))["power_base"] == 7.5
        assert await lookup("不存在", "ability") is None
        assert await lookup("任何人", "domain") is None
        assert (await lookup("火龍", "skill"))["name"] == "火龍彈", "模糊：子字串應解析"

    asyncio.run(_run())

    ch = upsert_character(conn, {
        "name": "凌空", "race": "劍士", "stats": {"力量": "B", "敏捷": "A"},
        "max_mp": 45, "martial": 60, "exp": 15, "is_villain": False,
    })
    assert ch["name"] == "凌空" and ch["stats"]["敏捷"] == "A"
    assert len(list_characters(conn)) == 1
    assert get_character(conn, "凌空")["max_mp"] == 45

    d1 = upsert_domain(conn, {
        "name": "龍王禁地", "owner": "神月", "domain_type": "封閉式結界",
        "awakening": "中期覺醒", "precision": 1.2,
    })
    assert d1["awakening"] == "中期覺醒"
    assert get_domain_named(conn, "龍王禁地")["owner"] == "神月"

    wsy = upsert_character(conn, {
        "name": "吳神月", "race": "半龍", "stats": {"力量": "B", "敏捷": "A"},
        "max_mp": 60, "martial": 70, "exp": 30, "is_villain": False,
    })
    assert wsy["name"] == "吳神月" and len(list_characters(conn)) == 2

    async def _fuzzy():
        ch = await lookup("神月", "character")
        assert ch and ch["name"] == "吳神月", "模糊：簡稱應解析為登記全名"
        assert await lookup("水牢", "character") is None, "不應誤匹配到角色"
        d = await lookup("禁地", "domain")
        assert d and d["name"] == "龍王禁地", "模糊：結界簡稱"
        assert await lookup("神月", "ability") is None, "不應誤匹配到能力"

    asyncio.run(_fuzzy())

    # collide_battles 生命週期
    b = insert_battle(conn, {
        "thread_id": "T1", "attacker": "神月", "attacker_skill": "火龍彈",
        "defender": "甲", "defender_skill": "普通攻擊",
        "atk_output": 7.5, "def_output": 3.1, "verdict": "優勢壓制",
        "status": "pending",
    })
    assert b["id"] and b["status"] == "pending"
    b2 = update_battle_status(conn, b["id"], "active")
    assert b2["status"] == "active"
    b3 = update_battle_status(conn, b["id"], "undone", undone_by="admin", undone_reason="誤判")
    assert b3["status"] == "undone" and b3["undone_by"] == "admin"
    assert len(list_battles(conn, status="undone")) == 1
    assert get_battle(conn, b["id"])["verdict"] == "優勢壓制"

    conn.close()
    os.remove(tmp)
    print("collide_db quick_test: ALL PASSED")


if __name__ == "__main__":
    quick_test()
