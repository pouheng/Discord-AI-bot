"""
profiles.py — 統一角色檔案系統

PROFILE_FIELDS、PROFILE_LABELS 以及 character_profiles 表的 CRUD 操作。
main.py 和 autopilot.py 共用此模組。
"""

import datetime
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


async def list_character_names() -> list[str]:
    """回傳所有角色檔案的角色名稱列表"""
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT char_name FROM character_profiles ORDER BY char_name"
        )
        rows = await cursor.fetchall()
    return [r[0] for r in rows]
