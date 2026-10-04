"""
profiles.py — 統一角色檔案系統

PROFILE_FIELDS、PROFILE_LABELS、character_profiles 表的 CRUD 操作，
以及 person_aliases 別名對照表（把甲／草莓姊這類稱呼收斂到正名）。
main.py 和 autopilot.py 共用此模組。
"""

import datetime
import re

from db import get_db

PROFILE_FIELDS = ["gender_age", "intro", "appearance", "items", "experience"]
PROFILE_LABELS = {
    "gender_age": "性別/年齡",
    "intro": "一句話介紹",
    "appearance": "外貌特徵",
    "items": "持有的重要物品",
    "experience": "過往經歷",
}


async def get_character_profile(char_name: str) -> dict:
    """取得角色檔案，不存在的欄位回空字串"""
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT gender_age, intro, appearance, items, experience FROM character_profiles WHERE char_name = ?",
            (char_name,),
        )
        row = await cursor.fetchone()
    if row:
        return dict(zip(PROFILE_FIELDS, row))
    return {f: "" for f in PROFILE_FIELDS}


async def update_character_profile(char_name: str, field: str, value: str):
    """更新角色檔案的單一欄位，若不存在則新增"""
    if field not in PROFILE_FIELDS:
        print(f"[角色檔案] 未知欄位: {field}")
        return
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with get_db() as conn:
        cursor = await conn.execute(
            f"UPDATE character_profiles SET {field} = ?, updated_at = ? WHERE char_name = ?",
            (value, now_str, char_name),
        )
        if cursor.rowcount == 0:
            placeholders = ", ".join(PROFILE_FIELDS)
            values = {f: "" for f in PROFILE_FIELDS}
            values[field] = value
            await conn.execute(
                f"INSERT INTO character_profiles (char_name, {placeholders}, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    char_name,
                    values["gender_age"],
                    values["intro"],
                    values["appearance"],
                    values["items"],
                    values["experience"],
                    now_str,
                ),
            )
    print(f"[角色檔案] {char_name}.{field} -> {value[:80]}")


async def format_character_profile(char_name: str) -> str:
    """格式化角色檔案為可讀字串，供 Phase 2 注入"""
    profile = await get_character_profile(char_name)
    lines = [f"📋 {char_name} 的角色檔案"]
    for f in PROFILE_FIELDS:
        label = PROFILE_LABELS.get(f, f)
        val = profile.get(f, "")
        if val:
            lines.append(f"  {label}：{val}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines)


async def format_person_profiles(canonical: str) -> str:
    """格式化某人在場時的完整身分卡，供 Phase 2 注入。

    包含別名關係，讓模型在同一段裡同時知道『這個人叫什麼』與
    『他還叫什麼』。若 canonical 有對應的別名，改用正名去撈 profile，
    避免 profile 建在別名下而撈不到。

    找 profile 時不只比對別名表字串，而是把 character_profiles 裡的
    每個 char_name 都丟去解析，看解析結果是否等於本正名。因為舊資料
    常把 profile 建在『雪風れいな（雪糕）』『謝黛君（代謝君）』這種
    帶括號的複合名下，而那些字串不在別名表中，字串比對會漏掉。

    只有真的有內容的欄位才輸出，空殼列回空字串以免浪費 token。
    """
    canonical = (canonical or "").strip()
    if not canonical:
        return ""
    aliases = await get_person_aliases(canonical)
    accepted = {canonical} | set(aliases)
    # 候選名：正名 → 別名字串 → 舊的括號複合名
    # 括號複合名只認『去掉括號後剛好等於正名或別名』，或『括號內就是
    # 正名或別名』（雪風れいな（雪糕）、謝黛君（代謝君））。不用模糊比對：
    # 模糊比對會把『凌空的哥哥』判成凌空本人（它包含『凌空』），
    # 那是另一個人，profile 掛錯人比撈不到更糟。
    candidates = [canonical] + list(aliases)
    for name in await list_character_names():
        if name in accepted:
            continue
        base = re.sub(r"[（(].*?[）)].*$", "", name).strip()
        inner = " ".join(re.findall(r"[（(](.*?)[）)]", name))
        inner = inner.split()[-1] if inner.split() else ""
        if (base and base in accepted) or (inner and inner in accepted):
            candidates.append(name)

    for name in candidates:
        text = await format_character_profile(name)
        if text:
            header = f"📋 {canonical} 的角色檔案"
            others = [a for a in aliases if a != canonical]
            if others:
                header += f"（又稱 {'、'.join(others)}）"
            return text.replace(text.split("\n")[0], header, 1)
    return ""


async def list_character_names() -> list[str]:
    """回傳所有角色檔案的角色名稱列表"""
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT char_name FROM character_profiles ORDER BY char_name"
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]


# ── 別名對照 ──────────────────────────────────────────────
# 同一個人在對話裡會有多種稱呼（甲／草莓姊、雪糕／雪風鈴奈）。
# 括號在 topic 裡有兩種意義：真別名（甲（草莓姊））與子主題（凌空（狐族）），
# 無法從字串可靠反推，所以別名一律以這張表為準，不做自動折疊。
# 每個正名都會自映射一筆（甲 -> 甲），讓解析統一走表。


def _shapeless_neighbor(a: str, b: str) -> bool:
    """判斷兩字是否為形近字（同部件 + 相近 codepoint），用來擋『澪』↔『澀』。

    模型偶爾會把視覺相近的字當成同一個字寫進別名表：
    澪(U+6FAA) 與 澀(U+6F80) 只差 42 個 codepoint、右側部件不同，
    卻會被當成別名登記，久了就長出一個幽靈角色。
    判斷只看 codepoint 距離，不做語意猜測，避免誤殺真正合法的別名。
    """
    if len(a) != 1 or len(b) != 1:
        return False
    return abs(ord(a) - ord(b)) <= 64


async def set_person_alias(alias: str, canonical: str, note: str = "") -> bool:
    """登記一筆別名對應；alias 等於 canonical 時記為自映射。回傳是否寫入成功。

    擋掉會製造幽靈角色的輸入：
      · alias 或 canonical 為空
      · alias 與 canonical 相同（自映射，這裡不負責）
      · alias 是 canonical 的形近字（如『澀』對『澪』，只差 42 個 codepoint）
    """
    alias = (alias or "").strip()
    canonical = (canonical or "").strip()
    if not alias or not canonical:
        return False
    if alias == canonical:
        return False
    if _shapeless_neighbor(alias, canonical):
        print(f"[別名] 擋下形近字：{alias} vs {canonical}"
              f"（codepoint 相差 {abs(ord(alias) - ord(canonical))}，"
              f"疑似誤字而非別名）")
        return False
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with get_db() as conn:
        await conn.execute(
            "INSERT INTO person_aliases (alias, canonical, note, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(alias) DO UPDATE SET canonical = excluded.canonical, "
            "note = excluded.note, updated_at = excluded.updated_at",
            (alias, canonical, note, now_str),
        )
    print(f"[別名] {alias} -> {canonical}")
    return True


async def ensure_person_canonical(char_name: str) -> None:
    """為正名補上自映射，讓所有解析都能統一走 person_aliases。"""
    char_name = (char_name or "").strip()
    if not char_name:
        return
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with get_db() as conn:
        await conn.execute(
            "INSERT OR IGNORE INTO person_aliases (alias, canonical, note, updated_at) "
            "VALUES (?, ?, '', ?)",
            (char_name, char_name, now_str),
        )


async def resolve_person_name(name: str) -> str:
    """把任意稱呼解析成正名；查不到就原樣回傳。

    容忍括號殘留（『甲（草莓姊）』先取『甲』再解析），並且對
    『草莓姊』這種帶親稱的常見稱呼做一次去除後綴的寬鬆比對。
    """
    name = (name or "").strip()
    if not name:
        return name
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT canonical FROM person_aliases WHERE alias = ?", (name,)
        )
        row = await cursor.fetchone()
        if row:
            return row[0] or name
        # 括號殘留：『甲（草莓姊）』→ 先試『甲』
        base = re.sub(r"[（(].*?[）)].*$", "", name).strip()
        if base and base != name:
            cursor = await conn.execute(
                "SELECT canonical FROM person_aliases WHERE alias = ?", (base,)
            )
            row = await cursor.fetchone()
            if row:
                return row[0] or base
        # 寬鬆比對：唯一命中才採用，避免『凌空』誤配到『凌空的哥哥』
        cursor = await conn.execute(
            "SELECT alias, canonical FROM person_aliases WHERE alias LIKE ? OR ? LIKE alias",
            (f"%{name}%", name),
        )
        hits = {r[0]: r[1] for r in await cursor.fetchall()}
        if len(hits) == 1:
            return next(iter(hits.values()))
    return name


async def is_known_person(name: str) -> bool:
    """判斷某個名稱是否已登記在 person_aliases（含正名本身）。

    呼叫端需要這個判斷，因為 resolve_person_name 對「正名回正名」與
    「完全沒這個人」都會原樣回傳同一個字串，無法區分：
    已知角色『甲』回『甲』，不該被當成查無此人的雜訊丟掉。
    """
    name = (name or "").strip()
    if not name:
        return False
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM person_aliases WHERE alias = ? OR canonical = ? LIMIT 1",
            (name, name),
        )
        return await cursor.fetchone() is not None


async def get_person_aliases(canonical: str) -> list[str]:
    """回傳某人的所有別名（含正名本身），供記憶召回比對。"""
    canonical = (canonical or "").strip()
    if not canonical:
        return []
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT alias FROM person_aliases WHERE canonical = ? ORDER BY alias",
            (canonical,),
        )
        return [r[0] for r in await cursor.fetchall()]


async def format_person_alias_map() -> str:
    """格式化別名對照表，供 Phase 1 / Phase 2 注入。

    沒有任何別名時回空字串，讓呼叫端可以跳過注入。
    """
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT alias, canonical FROM person_aliases WHERE alias != canonical "
            "ORDER BY canonical, alias"
        )
        rows = await cursor.fetchall()
    if not rows:
        return ""
    by_canon: dict[str, list[str]] = {}
    for alias, canonical in rows:
        by_canon.setdefault(canonical, []).append(alias)
    lines = ["【角色身份對照表】"]
    for canon, als in by_canon.items():
        lines.append(f"  {canon}：又稱 {'、'.join(als)}（同一個人，非不同角色）")
    return "\n".join(lines)
