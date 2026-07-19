"""
角色托管模組 — 模組化設計，獨立於 main.py

指令：
  /autopilot_on      — 開啟托管模式
  /autopilot_off     — 關閉托管模式
  /autopilot_add     — 新增托管角色（彈窗）
  /autopilot_list    — 檢視清單 + 切換各角色開關
  /dbnpc             — 檢視托管角色的記憶
  /dbnpc_teach       — 用自然語言教托管角色記住新資訊

資料表（rp_memory.db）：
  autopilot_config   — 托管模式全域開關
  autopilot_chars    — 角色定義（姓名/性別/性格/簡介/啟用狀態）

記憶（每個角色獨立 DB → auto_pilot_memories/{name}.db）：
  memories           — 角色對他人/世界的記憶
  self_memories      — 角色的自我認知
"""

import asyncio
import discord
from discord.ext import commands
from discord import app_commands, ui
from openai import AsyncOpenAI
import json
import datetime
import re
import sqlite3
import aiosqlite
import os
import aiofiles

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "rp_memory.db")
AUTOPILOT_MEMORY_DIR = os.path.join(SCRIPT_DIR, "auto_pilot_memories")

from contextlib import asynccontextmanager

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_deepseek_client = (
    AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
    if DEEPSEEK_API_KEY
    else None
)


@asynccontextmanager
async def _get_db():
    conn = await aiosqlite.connect(DB_FILE, timeout=10)
    try:
        yield conn
        await conn.commit()
    finally:
        await conn.close()


def _init_db():
    os.makedirs(AUTOPILOT_MEMORY_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS autopilot_config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS autopilot_chars (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            gender TEXT DEFAULT '',
            personality TEXT DEFAULT '',
            description TEXT DEFAULT '',
            ability TEXT DEFAULT '',
            active INTEGER DEFAULT 1,
            created_at TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)
    # 舊版相容：補 ability 欄位
    try:
        c.execute("ALTER TABLE autopilot_chars ADD COLUMN ability TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    conn.close()


_init_db()


# ─── 全域開關 ─────────────────────────────────


async def is_autopilot_enabled() -> bool:
    async with _get_db() as conn:
        cursor = await conn.execute(
            "SELECT value FROM autopilot_config WHERE key = 'enabled'"
        )
        row = await cursor.fetchone()
        return bool(row) and row[0] == "1"


async def set_autopilot_enabled(enabled: bool):
    async with _get_db() as conn:
        await conn.execute(
            "INSERT OR REPLACE INTO autopilot_config (key, value) VALUES ('enabled', ?)",
            ("1" if enabled else "0"),
        )


# ─── 角色 CRUD ─────────────────────────────────


async def add_autopilot_char(
    name: str, gender: str, personality: str, description: str, ability: str = ""
) -> int:
    async with _get_db() as conn:
        cursor = await conn.execute(
            "INSERT INTO autopilot_chars (name, gender, personality, description, ability) VALUES (?, ?, ?, ?, ?)",
            (name, gender, personality, description, ability),
        )
        char_id = cursor.lastrowid
    await _init_char_memory_db(name)
    return char_id


async def get_autopilot_chars() -> list[dict]:
    async with _get_db() as conn:
        cursor = await conn.execute(
            "SELECT id, name, gender, personality, description, ability, active FROM autopilot_chars ORDER BY id"
        )
        rows = await cursor.fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "gender": r[2],
            "personality": r[3],
            "description": r[4],
            "ability": r[5],
            "active": bool(r[6]),
        }
        for r in rows
    ]


async def get_active_autopilot_chars() -> list[dict]:
    chars = await get_autopilot_chars()
    return [c for c in chars if c["active"]]


async def set_autopilot_char_active(char_id: int, active: bool):
    async with _get_db() as conn:
        await conn.execute(
            "UPDATE autopilot_chars SET active = ? WHERE id = ?",
            (1 if active else 0, char_id),
        )


async def delete_autopilot_char(char_id: int):
    async with _get_db() as conn:
        await conn.execute("DELETE FROM autopilot_chars WHERE id = ?", (char_id,))


# ─── 各角色獨立記憶 ─────────────────────────


def _char_db_path(char_name: str) -> str:
    safe = re.sub(r'[\\/*?:"<>|]', "_", char_name)
    return os.path.join(AUTOPILOT_MEMORY_DIR, f"{safe}.db")


async def _init_char_memory_db(char_name: str):
    db_path = _char_db_path(char_name)
    conn = await aiosqlite.connect(db_path, timeout=10)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            topic TEXT,
            content TEXT,
            mem_type TEXT DEFAULT '',
            context TEXT DEFAULT 'ic'
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS self_memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            topic TEXT,
            content TEXT,
            mem_type TEXT DEFAULT ''
        )
    """)
    await conn.commit()
    await conn.close()


async def save_char_memory(
    char_name: str, topic: str, content: str, mem_type: str = "", context: str = "ic"
):
    db_path = _char_db_path(char_name)
    if not os.path.exists(db_path):
        await _init_char_memory_db(char_name)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(db_path, timeout=10) as conn:
        await conn.execute(
            "INSERT INTO memories (timestamp, topic, content, mem_type, context) VALUES (?, ?, ?, ?, ?)",
            (now_str, topic, content, mem_type, context),
        )
        await conn.commit()


async def save_char_self_memory(
    char_name: str, topic: str, content: str, mem_type: str = ""
):
    db_path = _char_db_path(char_name)
    if not os.path.exists(db_path):
        await _init_char_memory_db(char_name)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(db_path, timeout=10) as conn:
        await conn.execute(
            "INSERT INTO self_memories (timestamp, topic, content, mem_type) VALUES (?, ?, ?, ?)",
            (now_str, topic, content, mem_type),
        )
        await conn.commit()


async def get_char_memories(char_name: str, limit: int = 10) -> str:
    db_path = _char_db_path(char_name)
    if not os.path.exists(db_path):
        return ""
    async with aiosqlite.connect(db_path, timeout=10) as conn:
        cursor = await conn.execute(
            "SELECT timestamp, topic, content FROM memories ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    if not rows:
        return ""
    return "\n".join(f"[{r[0]}] {r[1]}: {r[2][:80]}" for r in rows)


async def get_char_self_memories(char_name: str, limit: int = 10) -> str:
    db_path = _char_db_path(char_name)
    if not os.path.exists(db_path):
        return ""
    async with aiosqlite.connect(db_path, timeout=10) as conn:
        cursor = await conn.execute(
            "SELECT timestamp, topic, content FROM self_memories ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    if not rows:
        return ""
    return "\n".join(f"[{r[0]}] {r[1]}: {r[2][:80]}" for r in rows)


# ─── 在場角色掃描 ──────────────────────────


async def _detect_present_chars(message: discord.Message) -> list[dict]:
    """機械式掃描最近 50 則訊息，找出哪些托管角色被提及"""
    active = await get_active_autopilot_chars()
    if not active:
        return []
    present_names = set()
    present = []
    try:
        async for msg in message.channel.history(limit=50):
            content = msg.content or ""
            for char in active:
                if char["name"] in content and char["name"] not in present_names:
                    present_names.add(char["name"])
                    present.append(char)
    except Exception:
        pass
    return present


# ─── Phase 2 提示詞注入 ────────────────────


async def _main_char_is_present(message: discord.Message) -> bool:
    """掃描最近訊息檢查主角（config.json character_name）是否在場"""
    main_name = ""
    cfg_path = os.path.join(SCRIPT_DIR, "config.json")
    try:
        async with aiofiles.open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.loads(await f.read())
            main_name = cfg.get("character_name", "").strip()
    except Exception:
        pass
    if not main_name:
        return True  # 不知道名字就當作在場
    try:
        async for msg in message.channel.history(limit=50):
            if main_name in (msg.content or ""):
                return True
    except Exception:
        pass
    return False


async def get_autopilot_injection(message: discord.Message, channel_type: str) -> str:
    """回傳要附加到 Phase 2 system prompt 的區塊（無則回空字串）"""
    if channel_type != "in_character":
        return ""
    if not await is_autopilot_enabled():
        return ""

    chars = await _detect_present_chars(message)
    if not chars:
        return ""

    main_present = await _main_char_is_present(message)

    lines = [
        "\n\n【⚠️ 托管模式啟用 — 在場角色扮演】",
        "以下角色目前也在這個場景中。請視情況扮演這些角色來推進場景，不強制每個角色都要說話。",
        "",
        "【格式要求】",
        "角色說話 → 角色名：「日文原文」（中文翻譯）",
        "旁白描述 → 旁白：「中文內容」",
        "場景/行動描述獨立佔一行，不加括號，直接描述。",
        "",
    ]
    if not main_present:
        lines.append(
            "【澪不在場】澪目前不在這個場景中。你不需要扮演澪，只需演出以下托管角色。\n"
        )
    else:
        lines.append("你只需要演出托管角色即可，不需要強制讓澪出場。\n")
    for char in chars:
        lines.append(f"• {char['name']}（{char['gender']} | {char['personality']}）")
        lines.append(f"  簡介：{char['description']}")
        if char.get("ability"):
            lines.append(f"  能力：{char['ability']}")
        mem = await get_char_memories(char["name"], limit=5)
        if mem:
            lines.append(f"  記憶：\n{mem}")
        sm = await get_char_self_memories(char["name"], limit=5)
        if sm:
            lines.append(f"  自我認知：\n{sm}")
        profile_text = await _format_char_profile(char["name"])
        if profile_text:
            lines.append(f"  角色檔案：\n{profile_text}")
        lines.append("")
    lines.append("【托管角色記憶標籤】")
    lines.append("若托管角色學到新資訊，使用以下格式標記：")
    lines.append(
        '[LEARN_CHAR:{"char":"角色名","topic":"主題","content":"內容","type":"真實/玩笑"}]'
    )
    lines.append('[MEM_CHAR:{"char":"角色名","topic":"主題","content":"內容"}]')
    lines.append(
        '[PROFILE_CHAR:{"char":"角色名","field":"gender_age/intro/appearance/items/experience","value":"..."}]'
    )
    return "\n".join(lines)


# ─── 日文原文隱藏（去八股文）──


def strip_japanese_original(text: str) -> str:
    """將「日文原文」（中文翻譯）→（中文翻譯），隱藏日文只留中文"""
    return re.sub(r"「[^」]*?」\s*（([^）]*?)）", r"「\1」", text, flags=re.DOTALL)


# ─── 角色檔案（character_profiles 表操作，與 main.py 共用）──

PROFILE_FIELDS = ["gender_age", "intro", "appearance", "items", "experience"]
PROFILE_LABELS = {
    "gender_age": "性別/年齡",
    "intro": "一句話介紹",
    "appearance": "外貌特徵",
    "items": "持有的重要物品",
    "experience": "過往經歷",
}


async def _get_char_profile_db(char_name: str) -> dict:
    conn = await aiosqlite.connect(DB_FILE, timeout=10)
    cursor = await conn.execute(
        "SELECT gender_age, intro, appearance, items, experience FROM character_profiles WHERE char_name = ?",
        (char_name,),
    )
    row = await cursor.fetchone()
    await conn.close()
    if row:
        return dict(zip(PROFILE_FIELDS, row))
    return {f: "" for f in PROFILE_FIELDS}


async def _update_char_profile_db(char_name: str, field: str, value: str):
    if field not in PROFILE_FIELDS or not value:
        return
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = await aiosqlite.connect(DB_FILE, timeout=10)
    cursor = await conn.execute(
        f"UPDATE character_profiles SET {field} = ?, updated_at = ? WHERE char_name = ?",
        (value, now_str, char_name),
    )
    if cursor.rowcount == 0:
        values = {f: "" for f in PROFILE_FIELDS}
        values[field] = value
        await conn.execute(
            f"INSERT INTO character_profiles (char_name, gender_age, intro, appearance, items, experience, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
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
    await conn.commit()
    await conn.close()
    print(f"[托管角色檔案] {char_name}.{field} -> {value[:60]}")


async def _format_char_profile(char_name: str) -> str:
    profile = await _get_char_profile_db(char_name)
    lines = []
    for f in PROFILE_FIELDS:
        label = PROFILE_LABELS.get(f, f)
        val = profile.get(f, "")
        if val:
            lines.append(f"  {label}：{val}")
    if not lines:
        return ""
    return "\n".join(lines)


# ─── Phase 3 記憶後處理（解析 LEARN_CHAR / MEM_CHAR 標籤）──


async def process_autopilot_memories(message: discord.Message, raw_response: str):
    if not await is_autopilot_enabled():
        return

    for tag, handler, needs_content in [
        (
            r"\[LEARN_CHAR:(\{.*?\})\]",
            lambda d: save_char_self_memory(
                d["char"],
                d.get("topic", "未分類"),
                d.get("content", ""),
                d.get("type", ""),
            ),
            True,
        ),
        (
            r"\[MEM_CHAR:(\{.*?\})\]",
            lambda d: save_char_memory(
                d["char"], d.get("topic", "未分類"), d.get("content", "")
            ),
            True,
        ),
        (
            r"\[PROFILE_CHAR:(\{.*?\})\]",
            lambda d: _update_char_profile_db(
                d["char"], d.get("field", ""), d.get("value", "")
            ),
            False,
        ),
    ]:
        for match in re.findall(tag, raw_response, re.DOTALL):
            try:
                data = json.loads(match)
                if not data.get("char"):
                    continue
                if needs_content and not data.get("content"):
                    continue
                await handler(data)
                label = data.get("topic", data.get("field", "?"))

                print(
                    f"[托管記憶] {data['char']}: {label} = {(data.get('content') or data.get('value', ''))[:60]}"
                )
            except (json.JSONDecodeError, Exception):
                pass


# ─── Discord UI ─────────────────────────────


class _AutopilotSelect(ui.Select):
    def __init__(self, chars: list[dict], user_id: int):
        self._user_id = user_id
        options = []
        for char in chars:
            status = "🟢 開啟" if char["active"] else "🔴 關閉"
            desc = (
                f"{char['gender']} | {char['personality'][:50]}"
                if char.get("personality")
                else char["gender"]
            )
            options.append(
                discord.SelectOption(
                    label=char["name"],
                    description=desc,
                    emoji="🟢" if char["active"] else "🔴",
                    value=str(char["id"]),
                )
            )
        super().__init__(
            placeholder="選擇角色切換托管狀態",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("這不是你的面板", ephemeral=True)
            return
        char_id = int(interaction.data["values"][0])
        view: AutopilotListView = self.view
        char = next((c for c in view.chars if c["id"] == char_id), None)
        if not char:
            return
        new_active = not char["active"]
        await set_autopilot_char_active(char_id, new_active)
        char["active"] = new_active
        embed = _build_embed(view.chars, await is_autopilot_enabled())
        # 重建選單（刷新選項狀態）
        new_select = _AutopilotSelect(view.chars, self._user_id)
        view.clear_items()
        view.add_item(new_select)
        view.add_item(_CloseButton(self._user_id))
        await interaction.response.edit_message(embed=embed, view=view)


class _CloseButton(ui.Button):
    def __init__(self, user_id: int):
        super().__init__(label="關閉", style=discord.ButtonStyle.danger, emoji="❌")
        self._user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("這不是你的面板", ephemeral=True)
            return
        await interaction.message.delete()


class AutopilotListView(ui.View):
    def __init__(self, chars: list[dict], user_id: int):
        super().__init__(timeout=120)
        self.chars = chars
        self.user_id = user_id
        self.add_item(_AutopilotSelect(chars, user_id))
        self.add_item(_CloseButton(user_id))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            if hasattr(self, "_message") and self._message:
                await self._message.edit(view=self)
        except Exception:
            pass


def _build_embed(chars: list[dict], enabled: bool) -> discord.Embed:
    embed = discord.Embed(
        title="🤖 托管角色清單", color=0x9B59B6, timestamp=datetime.datetime.now()
    )
    if not chars:
        embed.description = "尚無任何托管角色。使用 `/autopilot_add` 新增。"
        embed.set_footer(text=f"托管模式：{'🟢 已開啟' if enabled else '🔴 已關閉'}")
        return embed
    for char in chars:
        status = "🟢 開啟" if char["active"] else "🔴 關閉"
        value = f"性別：{char['gender']}\n性格：{char['personality']}"
        if char.get("ability"):
            value += f"\n能力：{char['ability'][:200]}"
        value += f"\n簡介：{char.get('description', '')[:200]}"
        embed.add_field(
            name=f"{status} {char['name']}", value=value or "（無資料）", inline=False
        )
    embed.set_footer(text=f"托管模式：{'🟢 已開啟' if enabled else '🔴 已關閉'}")
    return embed


# ─── NPC 記憶檢視 ─────────────────────────


async def _get_char_all_memories(char_name: str) -> list[dict]:
    """合併角色的 memories + self_memories，按時間排序"""
    result = []
    db_path = _char_db_path(char_name)
    if not os.path.exists(db_path):
        return result
    async with aiosqlite.connect(db_path, timeout=10) as conn:
        for table, label in [("memories", "記憶"), ("self_memories", "自我認知")]:
            cursor = await conn.execute(
                f"SELECT timestamp, topic, content, mem_type FROM {table} ORDER BY timestamp DESC"
            )
            for row in await cursor.fetchall():
                result.append(
                    {
                        "ts": row[0] or "?",
                        "topic": row[1] or "",
                        "content": row[2] or "",
                        "type": row[3] or "",
                        "table": label,
                    }
                )
    result.sort(key=lambda r: r["ts"], reverse=True)
    return result


_PER_PAGE = 5


def _build_npc_memory_embed(
    char_name: str, memories: list[dict], page: int, total_pages: int
) -> discord.Embed:
    embed = discord.Embed(
        title=f"📖 {char_name} 的記憶",
        color=0x9B59B6,
        timestamp=datetime.datetime.now(),
    )
    if not memories:
        embed.description = "（尚無任何記憶）"
        embed.set_footer(text=f"第 {page + 1}/{total_pages} 頁")
        return embed

    start = page * _PER_PAGE
    batch = memories[start : start + _PER_PAGE]
    for m in batch:
        tag = f"[{m['table']}]"
        if m["type"]:
            tag += f" ({m['type']})"
        val = m["content"][:400]
        if len(m["content"]) > 400:
            val += "…"
        embed.add_field(
            name=f"{tag} {m['topic']}  ({m['ts']})",
            value=val or "（空）",
            inline=False,
        )
    embed.set_footer(text=f"共 {len(memories)} 條  |  第 {page + 1}/{total_pages} 頁")
    return embed


class _NPCMemoryPagination(ui.View):
    def __init__(
        self, char_name: str, memories: list[dict], all_chars: list[dict], user_id: int
    ):
        super().__init__(timeout=120)
        self.char_name = char_name
        self.memories = memories
        self.all_chars = all_chars
        self.user_id = user_id
        self.page = 0
        self.total_pages = max((len(memories) + _PER_PAGE - 1) // _PER_PAGE, 1)
        self._message = None
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        self.add_item(_NPCBackSelect(self.all_chars, self.user_id))
        self.add_item(_CloseButton(self.user_id))
        if self.total_pages > 1:
            self.add_item(_PrevButton(self.user_id, self.page <= 0))
            self.add_item(_NextButton(self.user_id, self.page >= self.total_pages - 1))

    async def _refresh(self, interaction: discord.Interaction):
        self._update_buttons()
        embed = _build_npc_memory_embed(
            self.char_name, self.memories, self.page, self.total_pages
        )
        await interaction.response.edit_message(embed=embed, view=self)


class _NPCBackSelect(ui.Select):
    def __init__(self, chars: list[dict], user_id: int):
        self._user_id = user_id
        options = [
            discord.SelectOption(
                label=c["name"],
                value=str(c["id"]),
                description=f"{c['gender']} | {c['personality'][:40]}",
            )
            for c in chars
        ]
        super().__init__(
            placeholder="切換角色", options=options, min_values=1, max_values=1
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("這不是你的面板", ephemeral=True)
            return
        char_id = int(interaction.data["values"][0])
        view: _NPCMemoryPagination = self.view
        char = next((c for c in view.all_chars if c["id"] == char_id), None)
        if not char:
            return
        view.char_name = char["name"]
        view.memories = await _get_char_all_memories(char["name"])
        view.page = 0
        view.total_pages = max((len(view.memories) + _PER_PAGE - 1) // _PER_PAGE, 1)
        await view._refresh(interaction)


class _PrevButton(ui.Button):
    def __init__(self, user_id: int, disabled: bool):
        super().__init__(
            label="◀ 上一頁", style=discord.ButtonStyle.secondary, disabled=disabled
        )
        self._user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("這不是你的面板", ephemeral=True)
            return
        view: _NPCMemoryPagination = self.view
        if view.page > 0:
            view.page -= 1
            await view._refresh(interaction)


class _NextButton(ui.Button):
    def __init__(self, user_id: int, disabled: bool):
        super().__init__(
            label="下一頁 ▶", style=discord.ButtonStyle.secondary, disabled=disabled
        )
        self._user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("這不是你的面板", ephemeral=True)
            return
        view: _NPCMemoryPagination = self.view
        if view.page < view.total_pages - 1:
            view.page += 1
            await view._refresh(interaction)


# ─── Modal ─────────────────────────────────


class _AddAutopilotModal(ui.Modal):
    def __init__(self):
        super().__init__(title="新增托管角色")
        self._name = ui.TextInput(
            label="姓名 *", placeholder="例如：張三", max_length=50, required=True
        )
        self._gender = ui.TextInput(
            label="性別 *", placeholder="例如：男性", max_length=20, required=True
        )
        self._personality = ui.TextInput(
            label="性格 *",
            placeholder="例如：開朗、直率",
            max_length=200,
            required=True,
        )
        self._ability = ui.TextInput(
            label="能力",
            placeholder="能力名稱、原理、能做到什麼程度",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False,
        )
        self._desc = ui.TextInput(
            label="簡介 *",
            placeholder="角色的背景故事、外觀特徵等",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=True,
        )
        for item in (
            self._name,
            self._gender,
            self._personality,
            self._ability,
            self._desc,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            char_id = await add_autopilot_char(
                name=self._name.value,
                gender=self._gender.value,
                personality=self._personality.value,
                description=self._desc.value,
                ability=self._ability.value,
            )
            await interaction.followup.send(
                f"✅ 托管角色「{self._name.value}」已新增！（ID: {char_id}）",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(f"❌ 新增失敗：{e}", ephemeral=True)


# ─── 斜線指令註冊 ─────────────────────────


def register_commands(bot: commands.Bot):
    @bot.tree.command(
        name="autopilot_on", description="開啟托管模式 — AI 將同時扮演在場的托管角色"
    )
    async def _on(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await set_autopilot_enabled(True)
        await interaction.followup.send(
            "✅ 托管模式已開啟！AI 將同時扮演在場的托管角色。", ephemeral=True
        )

    @bot.tree.command(name="autopilot_off", description="關閉托管模式")
    async def _off(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        await set_autopilot_enabled(False)
        await interaction.followup.send("✅ 托管模式已關閉。", ephemeral=True)

    @bot.tree.command(
        name="autopilot_add", description="新增托管角色（姓名／性別／性格／能力／簡介）"
    )
    async def _add(interaction: discord.Interaction):
        await interaction.response.send_modal(_AddAutopilotModal())

    @bot.tree.command(
        name="autopilot_list", description="檢視全部托管角色清單與開關狀態"
    )
    async def _list(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        chars = await get_autopilot_chars()
        enabled = await is_autopilot_enabled()
        embed = _build_embed(chars, enabled)
        if chars:
            view = AutopilotListView(chars, interaction.user.id)
            msg = await interaction.followup.send(embed=embed, view=view)
            view._message = msg
        else:
            await interaction.followup.send(embed=embed)

    @bot.tree.command(name="dbnpc", description="檢視托管角色的記憶（可選角色）")
    async def _dbnpc(interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        chars = await get_autopilot_chars()
        if not chars:
            await interaction.followup.send("尚無任何托管角色。", ephemeral=True)
            return

        if len(chars) == 1:
            char = chars[0]
            memories = await _get_char_all_memories(char["name"])
            total = max((len(memories) + _PER_PAGE - 1) // _PER_PAGE, 1)
            embed = _build_npc_memory_embed(char["name"], memories, 0, total)
            view = _NPCMemoryPagination(
                char["name"], memories, chars, interaction.user.id
            )
            msg = await interaction.followup.send(embed=embed, view=view)
            view._message = msg
        else:
            options = [
                discord.SelectOption(
                    label=c["name"],
                    value=str(c["id"]),
                    description=f"{c['gender']} | {c.get('personality', '')[:40]}",
                )
                for c in chars
            ]
            select = ui.Select(placeholder="選擇要檢視的角色", options=options)

            async def select_cb(sel_interaction: discord.Interaction):
                if sel_interaction.user.id != interaction.user.id:
                    await sel_interaction.response.send_message(
                        "這不是你的面板", ephemeral=True
                    )
                    return
                char_id = int(sel_interaction.data["values"][0])
                char = next(c for c in chars if c["id"] == char_id)
                memories = await _get_char_all_memories(char["name"])
                total = max((len(memories) + _PER_PAGE - 1) // _PER_PAGE, 1)
                embed = _build_npc_memory_embed(char["name"], memories, 0, total)
                view = _NPCMemoryPagination(
                    char["name"], memories, chars, interaction.user.id
                )
                await sel_interaction.response.edit_message(embed=embed, view=view)

            select.callback = select_cb
            view = ui.View(timeout=120)
            view.add_item(select)
            view.add_item(_CloseButton(interaction.user.id))
            msg = await interaction.followup.send(
                embed=discord.Embed(
                    title="📖 選擇托管角色",
                    description="下拉選單選擇要檢視記憶的角色",
                    color=0x9B59B6,
                ),
                view=view,
            )
            view._message = msg

    async def _char_autocomplete(
        interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice]:
        chars = await get_autopilot_chars()
        return [
            app_commands.Choice(name=c["name"], value=c["name"])
            for c in chars
            if current.lower() in c["name"].lower()
        ][:25]

    @bot.tree.command(
        name="dbnpc_teach",
        description="用自然語言教托管角色記住新資訊（AI 自動填表）",
    )
    @app_commands.autocomplete(character=_char_autocomplete)
    async def _teach(interaction: discord.Interaction, character: str, text: str):
        await interaction.response.defer(ephemeral=True)
        chars = await get_autopilot_chars()
        char = next((c for c in chars if c["name"] == character), None)
        if not char:
            await interaction.followup.send(
                f"找不到角色「{character}」", ephemeral=True
            )
            return

        existing_mem = await get_char_memories(character, limit=10)
        existing_self = await get_char_self_memories(character, limit=10)

        prompt = f"""你是一個記憶管理員。使用者的自然語言描述如下，請從中提取結構化記憶。

目標角色：{character}
        角色基本資料：{char["gender"]} | {char["personality"]} | {char.get("ability", "")} | {char["description"]}

使用者說：
{text}

{"【現有記憶】\n" + existing_mem if existing_mem else ""}
{"【現有自我認知】\n" + existing_self if existing_self else ""}

請判斷這些資訊屬於：
1. 角色對他人/世界的記憶（memories）— 例如認識了誰、某個地點的資訊
2. 角色對自己的認知（self_memories）— 例如自己的背景、能力、喜好

輸出 JSON，格式如下，不要加其他文字：
{{"actions": [
  {{"type":"memory","topic":"分類:具體名稱","content":"詳細內容","mem_type":"真實/玩笑"}},
  {{"type":"self","topic":"分類:具體名稱","content":"詳細內容","mem_type":"真實/玩笑"}}
]}}
若內容不需要寫入則回傳 {{"actions":[]}}"""

        try:
            if not _deepseek_client:
                await interaction.followup.send("❌ API 金鑰未設定", ephemeral=True)
                return
            resp = await _deepseek_client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": "你只輸出 JSON，不要加任何解釋。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=1000,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or '{"actions":[]}'
            cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip()
            result = json.loads(cleaned)
            actions = result.get("actions", [])

            if not actions:
                await interaction.followup.send(
                    f"🤔 「{character}」不需要新增任何記憶（AI 判定無新資訊）",
                    ephemeral=True,
                )
                return

            saved = []
            for act in actions:
                t = act.get("type", "")
                topic = act.get("topic", "未分類")
                content = act.get("content", "")
                mem_type = act.get("mem_type", "")
                if not content:
                    continue
                if t == "self":
                    await save_char_self_memory(character, topic, content, mem_type)
                    saved.append(f"[自我] {topic}")
                else:
                    await save_char_memory(character, topic, content, mem_type)
                    saved.append(f"[記憶] {topic}")

            embed = discord.Embed(
                title=f"✅ {character} 已學習",
                color=0x9B59B6,
                description=f"從您的描述中提取了 {len(saved)} 條記憶：\n"
                + "\n".join(f"• {s}" for s in saved),
                timestamp=datetime.datetime.now(),
            )
            embed.add_field(name="原始描述", value=text[:500], inline=False)
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f"❌ 處理失敗：{e}", ephemeral=True)
