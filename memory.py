"""memory.py — 記憶模組

擁有 `memories` 資料表（含欄位遷移與所有 SQL 存取），介面是記憶的
save / get / search / recall，實作吸收去重、語意合併、字首相似、
`__BOT__` 自記憶疊加與重複分組等邏輯。

對外只依賴 db（連線）與 profiles（人物別名解析）。
"""

import datetime
import difflib
import re

from db import get_db
from profiles import resolve_person_name, get_person_aliases, is_known_person


# 每人帶入的「角色自身記憶」筆數上限。原本硬編碼 3，且只比對單一顯示名。
PERSON_BOT_ROW_LIMIT = 6


# 角色名稱來源。由主程式（main）在定義 get_character_name 後注入，
# 讓本模組不必反向依賴 main（那是角色設定／config 的職責）。
character_name_provider = None


async def _get_character_name() -> str | None:
    if character_name_provider is None:
        return None
    try:
        return await character_name_provider()
    except Exception:
        return None


def create_memories_table(cursor):
    """建立 memories 表並補上歷史欄位（供 db.init_db 呼叫）。"""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            user_id TEXT,
            user_name TEXT,
            topic TEXT,
            content TEXT,
            context TEXT DEFAULT 'ic',
            mem_type TEXT DEFAULT ''
        )
    """
    )
    try:
        cursor.execute("ALTER TABLE memories ADD COLUMN context TEXT DEFAULT 'ic'")
    except Exception:
        pass
    try:
        cursor.execute("ALTER TABLE memories ADD COLUMN mem_type TEXT DEFAULT ''")
    except Exception:
        pass


async def get_user_memory(
    user_id: int, limit: int = 8, context_filter: str = None, user_name: str = None
) -> str:
    """從 SQLite 召回該使用者的記憶 + 角色自身關於此人的記憶

    個人記憶分兩段取：先取 topic 直接命中此人的（那些是真正屬於他的
    設定），不足 limit 才用一般最新記憶補齊。否則重度使用者的
    一般閒聊會把『人物:甲（草莓姊）』擠出窗口——吳神月有 41 筆，
    而甲的設定只佔其中 4 筆。
    """
    async with get_db() as conn:
        ctx_sql = " AND context = ?" if context_filter else ""
        ctx_args = (context_filter,) if context_filter else ()
        rows = []
        if user_name:
            import re as _re

            short_name = _re.sub(r"[（(].*?[）)]", "", user_name).strip()
            short_name = "".join(ch for ch in short_name if ord(ch) < 0x10000)
            short_name = short_name.strip()
            # 別名也要一起比對：玩家飾演的角色與其 Discord 顯示名未必相同
            # （例如顯示名『吳神月』飾演『甲（草莓姊）』），只比對顯示名會撈不到。
            needles = {short_name} if short_name else set()
            _canon = await resolve_person_name(short_name) if short_name else short_name
            if _canon:
                needles.add(_canon)
                needles.update(await get_person_aliases(_canon))
            needles = {n for n in needles if n and len(n) >= 2}
            if needles:
                clause = " OR ".join(["topic LIKE ?"] * len(needles))
                params = [f"%{n}%" for n in needles]
                cursor0 = await conn.execute(
                    f"SELECT timestamp, topic, content, context FROM memories "
                    f"WHERE user_id = ?{ctx_sql} AND ({clause}) "
                    f"ORDER BY timestamp DESC LIMIT ?",
                    [str(user_id)] + list(ctx_args) + params + [limit],
                )
                rows = await cursor0.fetchall()
                seen = {(r[1], r[2]) for r in rows}
                if len(rows) < limit:
                    cursor1 = await conn.execute(
                        f"SELECT timestamp, topic, content, context FROM memories "
                        f"WHERE user_id = ?{ctx_sql} "
                        f"ORDER BY timestamp DESC LIMIT ?",
                        [str(user_id)] + list(ctx_args) + [limit * 3],
                    )
                    for r in await cursor1.fetchall():
                        if len(rows) >= limit:
                            break
                        if (r[1], r[2]) not in seen:
                            seen.add((r[1], r[2]))
                            rows.append(r)
        if not rows:
            if context_filter:
                cursor = await conn.execute(
                    "SELECT timestamp, topic, content, context FROM memories WHERE user_id = ? AND context = ? ORDER BY timestamp DESC LIMIT ?",
                    (str(user_id), context_filter, limit),
                )
            else:
                cursor = await conn.execute(
                    "SELECT timestamp, topic, content, context FROM memories WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (str(user_id), limit),
                )
            rows = await cursor.fetchall()

        # 也撈角色自身記憶中關於此人的內容（透過名字匹配）
        bot_rows = []
        if user_name and needles:
            clause2 = " OR ".join(["(topic LIKE ? OR content LIKE ?)"] * len(needles))
            params2 = []
            for n in needles:
                params2.extend([f"%{n}%", f"%{n}%"])
            cursor2 = await conn.execute(
                f"SELECT timestamp, topic, content FROM memories "
                f"WHERE user_id = '__BOT__' AND ({clause2}) "
                f"ORDER BY timestamp DESC LIMIT ?",
                params2 + [PERSON_BOT_ROW_LIMIT],
            )
            bot_rows = await cursor2.fetchall()

    if not rows and not bot_rows:
        return "（無記錄）"

    memory_list = []
    for row in reversed(rows):
        timestamp, topic, content, ctx = row
        label = "【中之人】" if ctx == "ooc" else "【劇中】"
        memory_list.append(f"- [{timestamp}] {label} {topic}: {content}")
    if bot_rows:
        memory_list.append("（角色已知關於此人的資訊）")
        for timestamp, topic, content in reversed(bot_rows):
            memory_list.append(f"- [{timestamp}] {topic}: {content}")

    return "\n".join(memory_list)


async def get_person_memory(canonical: str, limit: int = 8) -> str:
    """撈某個 NPC 角色的記憶。

    以 person_aliases 收斂出該人的所有稱呼，全部拿去比對 topic / content，
    如此『人物:甲（草莓姊）』與『人物:甲』不會變成兩個人。
    找不到別名時退回單一正名比對。

    不限制 user_id：同一個人的知識會散在多個擁有者底下
    （Phase 3 的 learn 寫 __BOT__、mem 寫玩家 id），實測甲有 吳神月 4 筆、
    澪 15 筆，若只撈 __BOT__ 就會漏掉一半。mem 是玩家私人資訊，
    但與 learn 一起餵給 Phase 2 時有上下文可分辨。

    分兩段取：先取 topic 開頭就是正名／別名的（那些是真正屬於此人的設定，
    也是被拆組的核心記憶），不足 limit 才用全文提及的補齊。
    否則 ORDER BY timestamp DESC 會讓高頻提及者的舊記憶擠掉本體
    （實測凌空有 147 筆，獨立的『人物:煙霧之神』被擠在 50 名之外）。
    """
    canonical = (canonical or "").strip()
    if not canonical:
        return "（無記錄）"
    needles = {canonical}
    needles.update(await get_person_aliases(canonical))
    needles = {n for n in needles if n and len(n) >= 2}
    if not needles:
        return "（無記錄）"

    async with get_db() as conn:
        clause = " OR ".join(["(topic LIKE ? OR content LIKE ?)"] * len(needles))
        params = []
        for n in needles:
            params.extend([f"%{n}%", f"%{n}%"])
        # 第一段：topic 直接命中某人（人物:甲 / 角色能力:甲（草莓姊）的水柱）
        own_clause = " OR ".join(["topic LIKE ?"] * len(needles))
        own_params = [f"%{n}%" for n in needles]
        cur = await conn.execute(
            f"SELECT timestamp, topic, content, user_name FROM memories "
            f"WHERE {own_clause} ORDER BY timestamp DESC LIMIT ?",
            own_params + [limit],
        )
        rows = await cur.fetchall()
        seen = {(r[1], r[2]) for r in rows}
        # 第二段：不足則用全文提及的補齊
        if len(rows) < limit:
            cur2 = await conn.execute(
                f"SELECT timestamp, topic, content, user_name FROM memories "
                f"WHERE {clause} ORDER BY timestamp DESC LIMIT ?",
                params + [limit * 3],
            )
            for r in await cur2.fetchall():
                if len(rows) >= limit:
                    break
                if (r[1], r[2]) not in seen:
                    seen.add((r[1], r[2]))
                    rows.append(r)

    if not rows:
        return "（無記錄）"
    lines = []
    for ts, topic, content, uname in rows:
        who = f"（{uname}）" if uname and uname not in ("澪", "__BOT__") else ""
        lines.append(f"- [{ts}] {topic}{who}: {content}")
    return "\n".join(lines)


async def save_user_memory(
    user_id: int,
    user_name: str,
    topic: str,
    content: str,
    context: str = "ic",
    mem_type: str = "",
):
    """將新記憶寫入 SQLite（mem_type 須為「真實」否則略過）"""
    if mem_type != "真實":
        print(f"[記憶] 略過非真實記憶 ({mem_type or '無type'}): {topic}")
        return
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        async with get_db() as conn:
            await conn.execute(
                "INSERT INTO memories (timestamp, user_id, user_name, topic, content, context, mem_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_str, str(user_id), user_name, topic, content, context, mem_type),
            )
        print(f"[記憶] 寫入 {user_name} -> {topic}: {content}")
    except Exception as e:
        print(f"[錯誤] 寫入資料庫失敗: {e}")


async def _find_semantic_self_memory(
    conn, topic: str, content: str, limit: int = 300
):
    """語意防重：找「topic 措辭不同但內容同一事實」的既有記憶。

    判定（同時滿足才視為同一條，避免誤併不同主題）：
     1. topic 核心詞共享：英數 token >=1 或中文詞 >=2（_memory_keywords）
     2. content 佐證：content 也抽得出共享詞，或整體相似度足夠
    回傳 (id, existing_topic, existing_content) 或 None。
    """
    kw_new = _memory_keywords(topic)
    kw_c_new = _memory_keywords(content)
    cursor = await conn.execute(
        "SELECT id, topic, content FROM memories WHERE user_id = '__BOT__' "
        "AND topic != ? ORDER BY timestamp DESC LIMIT ?",
        (topic, limit),
    )
    rows = await cursor.fetchall()
    best = None
    best_score = 0.0
    for mid, etopic, econtent in rows:
        kw_old = _memory_keywords(etopic)
        en_new = {t for t in kw_new if re.search(r"[A-Za-z0-9]", t)}
        en_old = {t for t in kw_old if re.search(r"[A-Za-z0-9]", t)}
        zh_new = {t for t in kw_new if not re.search(r"[A-Za-z0-9]", t)}
        zh_old = {t for t in kw_old if not re.search(r"[A-Za-z0-9]", t)}
        en_share = en_new & en_old
        zh_share = zh_new & zh_old
        if not en_share and len(zh_share) < 2:
            continue
        s1 = content or ""
        s2 = econtent or ""
        if len(s1) < 8 or len(s2) < 8:
            continue
        import difflib as _diff

        sm = _diff.SequenceMatcher(None, s1, s2)
        ratio = sm.ratio()
        lm = sm.find_longest_match(0, len(s1), 0, len(s2))
        # content 佐證：content 共享詞（英數>=1 或中文>=2）、或相似度/公共子串足夠
        kw_c_old = _memory_keywords(econtent)
        en_c_share = {
            t
            for t in kw_c_new & kw_c_old
            if re.search(r"[A-Za-z0-9]", t)
        }
        zh_c_share = {
            t
            for t in kw_c_new & kw_c_old
            if not re.search(r"[A-Za-z0-9]", t)
        }
        content_ok = (
            len(en_c_share) >= 1
            or len(zh_c_share) >= 2
            or ratio >= 0.2
            or lm.size >= 8
        )
        if not content_ok:
            continue
        score = (
            0.5
            + len(en_share) * 0.2
            + len(zh_share) * 0.05
            + len(en_c_share) * 0.05
            + min(ratio, 0.3)
            + min(lm.size / 80.0, 0.1)
        )
        if score > best_score:
            best_score = score
            best = (mid, etopic, econtent)
    return best


def _prefix_similar(a: str, b: str, threshold: float = 0.7) -> bool:
    """兩字串由頭逐字比對，前綴相似度是否超過 threshold。

    用於自我記憶的「同 topic 近重複」判定（模型改寫同一件事時開頭多半相同）。
    """
    common_len = 0
    for i in range(min(len(a), len(b))):
        if a[i] == b[i]:
            common_len += 1
        else:
            break
    short_len = min(len(a), len(b))
    return short_len > 0 and common_len / short_len > threshold


async def save_self_memory(
    topic: str, content: str, mem_type: str = "真實", context: str = "ic"
):
    """將角色自身的新認知寫入 SQLite（去重 + 上限修剪）"""
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        async with get_db() as conn:
            # 去重：相同 topic 且內容相似度 > 70% 則更新取代新增
            cursor = await conn.execute(
                "SELECT id, content FROM memories WHERE user_id = '__BOT__' AND topic = ? ORDER BY timestamp DESC LIMIT 5",
                (topic,),
            )
            matched = False
            for row in await cursor.fetchall():
                existing_id, existing_content = row
                if not _prefix_similar(content, existing_content):
                    continue
                matched = True
                if topic.startswith("人物:") or topic.startswith("人物："):
                    merged = existing_content
                    if content not in existing_content:
                        merged = existing_content + "；" + content
                    await conn.execute(
                        "UPDATE memories SET content = ?, timestamp = ?, mem_type = ?, context = ? WHERE id = ?",
                        (merged, now_str, mem_type, context, existing_id),
                    )
                    print(f"[自我記憶] 疊加 -> {topic} ({mem_type})")
                else:
                    await conn.execute(
                        "UPDATE memories SET content = ?, timestamp = ?, mem_type = ?, context = ? WHERE id = ?",
                        (content, now_str, mem_type, context, existing_id),
                    )
                    print(f"[自我記憶] 去重更新 -> {topic} ({mem_type})")

            if not matched:
                # 語意防重：topic 措辭不同但講同一件事時（如「事件:凌空CNC購買計畫」
                # vs「事件:CNC機台購買計劃」），也要合併更新而非新增，否則會不斷堆積
                # 雷同記憶（9/15 的 76 條重複即敗破此處）。
                semantic_match = await _find_semantic_self_memory(
                    conn, topic, content, limit=300
                )
                if semantic_match:
                    mid, existing_topic, existing_content = semantic_match
                    if content not in existing_content:
                        new_body = existing_content + "；" + content
                    else:
                        new_body = existing_content
                    await conn.execute(
                        "UPDATE memories SET content = ?, timestamp = ?, mem_type = ?, context = ? WHERE id = ?",
                        (new_body, now_str, mem_type, context, mid),
                    )
                    print(
                        f"[自我記憶] 語意合併 -> {existing_topic} (+{topic[:40]}) ({mem_type})"
                    )
                else:
                    char_name = await _get_character_name() or "角色"
                    await conn.execute(
                        "INSERT INTO memories (timestamp, user_id, user_name, topic, content, mem_type, context) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (now_str, "__BOT__", char_name, topic, content, mem_type, context),
                    )
                    print(
                        f"[自我記憶] 寫入 -> {topic}: {content[:80]}... ({mem_type}, {context})"
                    )
    except Exception as e:
        print(f"[錯誤] 寫入自我記憶失敗: {e}")


async def get_self_memory(limit: int = 12) -> str:
    """召回角色自身的記憶（最近 limit 條）"""
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT timestamp, topic, content FROM memories WHERE user_id = '__BOT__' ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()

    if not rows:
        return "（尚無自我認知）"

    memory_list = []
    for row in reversed(rows):
        timestamp, topic, content = row
        memory_list.append(f"- [{timestamp}] {topic}: {content}")

    return "\n".join(memory_list)


async def get_self_memory_raw(limit: int = 50) -> list[dict]:
    """召回角色自身記憶原始資料 [{id, timestamp, topic, content}]"""
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT id, timestamp, topic, content FROM memories WHERE user_id = '__BOT__' ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()
    return [
        {"id": r[0], "timestamp": r[1], "topic": r[2], "content": r[3]} for r in rows
    ]


async def _update_memory(
    user_id: str,
    topic: str,
    new_content: str = None,
    new_type: str = None,
    new_topic: str = None,
):
    """更新現有記憶的內容或類型（用於 Phase 3 修正）"""
    if not topic:
        return
    try:
        async with get_db() as conn:
            # 內容／topic 被改動時必須同步刷新 timestamp，否則修正後的記憶仍帶著
            # 舊時間，召回時的 ORDER BY timestamp DESC 會把它當成陳舊資料。
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if new_topic and new_topic != topic:
                # 重新命名 topic（同時可更新內容與類型）
                if new_content and new_type:
                    await conn.execute(
                        "UPDATE memories SET topic = ?, content = ?, mem_type = ?, timestamp = ? WHERE user_id = ? AND topic = ?",
                        (new_topic, new_content, new_type, now_str, user_id, topic),
                    )
                elif new_content:
                    await conn.execute(
                        "UPDATE memories SET topic = ?, content = ?, timestamp = ? WHERE user_id = ? AND topic = ?",
                        (new_topic, new_content, now_str, user_id, topic),
                    )
                elif new_type:
                    await conn.execute(
                        "UPDATE memories SET topic = ?, mem_type = ?, timestamp = ? WHERE user_id = ? AND topic = ?",
                        (new_topic, new_type, now_str, user_id, topic),
                    )
                else:
                    await conn.execute(
                        "UPDATE memories SET topic = ?, timestamp = ? WHERE user_id = ? AND topic = ?",
                        (new_topic, now_str, user_id, topic),
                    )
            elif new_content and new_type:
                await conn.execute(
                    "UPDATE memories SET content = ?, mem_type = ?, timestamp = ? WHERE user_id = ? AND topic = ?",
                    (new_content, new_type, now_str, user_id, topic),
                )
            elif new_content:
                await conn.execute(
                    "UPDATE memories SET content = ?, timestamp = ? WHERE user_id = ? AND topic = ?",
                    (new_content, now_str, user_id, topic),
                )
            elif new_type:
                await conn.execute(
                    "UPDATE memories SET mem_type = ? WHERE user_id = ? AND topic = ?",
                    (new_type, user_id, topic),
                )
            affected = conn.total_changes
        if affected:
            old = f"{topic}->{new_topic}" if new_topic else topic
            print(f"[記憶修正] {user_id}/{old} 已更新 (rows={affected})")
    except Exception as e:
        print(f"[記憶修正] 失敗: {e}")


def _memory_keywords(topic: str) -> set[str]:
    """從 topic 去前綴後抽「關鍵詞」：英數 token + 2-6 字中文詞（濾掉過度常見詞）。

    用於把「措辭不同但共享核心名詞」的記憶聚在一起
    （例：「中之人經歷:凌空考慮購入CNC機台」vs「事件:CNC 機台討論」都含 CNC）。
    """
    import re as _re

    _STOP = {
        "凌空", "星狐", "澪", "bot", "角色", "人物", "設定", "方面",
        "中之人", "相關", "曾經", "目前", "已經", "尚未", "本次", "前面",
        "討論", "問題", "事件", "計畫", "預算", "購買", "計劃", "談論",
        "裝置", "系統", "機台", "開發", "設計", "使用", "利用", "擁有",
        "工作室", "公司", "有", "是", "那", "這", "自己", "覺得", "認為",
        "為何", "如何", "是否", "結果", "情況", "開始", "出現", "提及",
        "東西", "設備", "事情", "內容", "資訊", "過程", "部分", "來源",
    }
    t = _re.sub(r"^(事件|人物|物品|場景|世界觀|任務|角色背景|中之人經歷|角色能力|角色名稱|角色身份)[：:]", "", topic.strip())
    toks: set[str] = set()
    for m in _re.finditer(r"[A-Za-z0-9\u00e0-\u00ff]{2,}", t):
        toks.add(m.group().lower())
    # 中文採 2-3 字滑窗（非整串貪婪），避開把「凌空的老婆騎」整串當單一 token
    zh_chars = _re.findall(r"[\u3400-\u9fff]", t)
    if len(zh_chars) >= 2:
        for size in (3, 2):
            for start in range(0, len(zh_chars) - size + 1):
                gram = "".join(zh_chars[start : start + size])
                if gram not in _STOP:
                    toks.add(gram)
    return toks


def _group_similar_memories(memories: list[dict]) -> list[list[dict]]:
    """依 topic 相似度把記憶聚成候選重複組。

    回傳成「候選重複組」：每組包含 >= 2 條主題高度相似的記憶。
    判斷準則（兩輪）：
      1. 去前綴（事件:/人物:/物品: 等）後比對主體字串，相似度 > 0.66 判定為候選重複。
      2. 對第 1 輪沒被併走的殘餘，比對「共享關鍵詞」>= 2 個即併入同組，
         用來抓住措辭不同但核心名詞相同的重複（CNC／震動／切削液 等）。
    只做建議，是否合併仍由 AI 決定。
    """
    import difflib

    def _body(topic: str) -> str:
        t = re.sub(r"^(事件|人物|物品|場景|世界觀|任務|角色背景|中之人經歷|角色能力|角色名稱|角色身份)[：:]", "", topic.strip())
        return t.strip() or topic.strip()

    pairs = [(m, _body(m["topic"])) for m in memories]
    groups: list[list[dict]] = []
    used: set[int] = set()
    # 第一輪：序列相似度聚類
    for i, (mi, bi) in enumerate(pairs):
        if i in used:
            continue
        group = [mi]
        used.add(i)
        for j, (mj, bj) in enumerate(pairs):
            if j in used or j == i:
                continue
            if not bi or not bj:
                continue
            short, long = sorted((bi, bj), key=len)
            sim = difflib.SequenceMatcher(None, bi, bj).ratio()
            # 兩條都只有純中文（無英數縮寫/術語，如 CNC、CAN）時，字面相似度可能誤導
            # （「凌空的電動叉車」vs「凌空的老婆騎電動自行車」sim≈0.67）；
            # 純中文採較嚴的模糊帶判定：sim≥0.78 直接併，0.60≤sim<0.78 需 LCS≥4 才併。
            has_en = bool(re.search(r"[A-Za-z0-9]", bi) or re.search(r"[A-Za-z0-9]", bj))
            if has_en:
                threshold, fuzzy = 0.66, False
            elif sim >= 0.78:
                threshold, fuzzy = sim, False
            else:
                threshold, fuzzy = 0.60, True
            if sim <= threshold:
                continue
            lm = difflib.SequenceMatcher(None, short, long).find_longest_match(
                0, len(short), 0, len(long)
            )
            if fuzzy and lm.size < 4:
                continue
            # 長度懸殊時，須共享英數核心詞或 LCS 才算同主題，
            # 避免「凌空的電動叉車」誤併「凌空的老婆騎電動自行車」這類純字面重複
            if len(short) * 2 < len(long):
                kw_i = _memory_keywords(bi)
                kw_j = _memory_keywords(bj)
                en_i = {t for t in kw_i if re.search(r"[A-Za-z0-9]", t)}
                en_j = {t for t in kw_j if re.search(r"[A-Za-z0-9]", t)}
                if not (en_i & en_j) and lm.size < 3:
                    continue
            group.append(mj)
            used.add(j)
        if len(group) >= 2:
            groups.append(group)

    # 第二輪：共享關鍵詞聚類（抓措辭不同的同主題重複）
    def _shared_strength(tk1: set[str], tk2: set[str]) -> bool:
        """兩條記憶是否構成同主題：共享英文/數字核心詞 >=1，或共享 >=2 個 3 字以上中文詞。"""
        en1 = {t for t in tk1 if re.search(r"[A-Za-z0-9]", t)}
        en2 = {t for t in tk2 if re.search(r"[A-Za-z0-9]", t)}
        zh1 = {t for t in tk1 if not re.search(r"[A-Za-z0-9]", t) and len(t) >= 3}
        zh2 = {t for t in tk2 if not re.search(r"[A-Za-z0-9]", t) and len(t) >= 3}
        if en1 & en2:
            return True
        return len(zh1 & zh2) >= 2

    kws = [(m, _memory_keywords(m["topic"])) for m in memories]
    for i, (mi, ki) in enumerate(kws):
        if i in used:
            continue
        if not ki:
            continue
        group = [mi]
        used.add(i)
        for j, (mj, kj) in enumerate(kws):
            if j in used or j == i or not kj:
                continue
            if _shared_strength(ki, kj):
                group.append(mj)
                used.add(j)
        if len(group) >= 2:
            groups.append(group)
    return groups


def _auto_merge_dup_groups(groups: list[list[dict]], strict_category: bool = True) -> list[dict]:
    """程式端自動合併候選重複組（毋需 AI）。

    每組取內容最長的一條為基底，其餘按「[topic] content」串接保留下來，
    產生成與 AI 相同格式的 merge action。回傳空清單代表沒有值得合併的組。
    預設只合併同類別（事件:/人物:/場景: 等前綴一致）的組，避免跨類別誤合併；
    strict_category=False 時解除限制（供內容高度重複的跨類別組使用）。
    """

    def _category(topic: str) -> str:
        m = re.match(r"^(事件|人物|物品|場景|世界觀|任務|角色背景|中之人經歷|角色能力|角色名稱|角色身份)[：:]", topic.strip())
        return m.group(1) if m else "無前綴"

    def _body(topic: str) -> str:
        t = re.sub(r"^(事件|人物|物品|場景|世界觀|任務|角色背景|中之人經歷|角色能力|角色名稱|角色身份)[：:]", "", topic.strip())
        return t.strip() or topic.strip()

    actions = []
    for g in groups:
        if len(g) < 2:
            continue
        cats = {_category(m["topic"]) for m in g}
        bodies = {_body(m["topic"]) for m in g}
        if strict_category and len(cats) > 1 and len(bodies) > 1:
            # 跨類別：去前綴後主題本體完全相同（如 事件:頭盔印製 vs 任務:頭盔印製）、
            # 共享英文/數字核心詞（如 CNC、CAN、Golf——這類縮寫本身就代表同一事物）、
            # 共享 ≥2 個中文關鍵詞、或任兩條 body 有 >=3 字的最長公共子串
            # （如「呼叫系統」「工作室」——跨類別但講同一件事）。
            bodies_l = list(bodies)
            kw_sets = [_memory_keywords(m["topic"]) for m in g]
            kw_sets = [s for s in kw_sets if s]
            shared_kw = set.intersection(*kw_sets) if kw_sets else set()
            en_sets = [
                set(re.findall(r"[A-Za-z0-9\u00e0-\u00ff]{2,}", _body(m["topic"])))
                for m in g
            ]
            en_sets = [s for s in en_sets if s]
            shared_en = set.intersection(*en_sets) if en_sets else set()
            max_lcs = 0
            try:
                import difflib as _dl
                for a_i in range(len(bodies_l)):
                    for b_i in range(a_i + 1, len(bodies_l)):
                        aa, bb = bodies_l[a_i], bodies_l[b_i]
                        lm = _dl.SequenceMatcher(None, aa, bb).find_longest_match(
                            0, len(aa), 0, len(bb)
                        )
                        max_lcs = max(max_lcs, lm.size)
            except Exception:
                pass
            if (
                len(bodies) == 1
                or len(shared_en) >= 1
                or len(shared_kw) >= 2
                or max_lcs >= 3
            ):
                pass  # 任一成立 → 允許合併
            else:
                continue
        base = max(g, key=lambda m: len(m["content"]))
        others = [m for m in g if m["id"] != base["id"]]
        parts = [base["content"]]
        for m in others:
            parts.append(f"[{m['topic']}] {m['content']}")
        new_content = "；".join(p for p in parts if p)
        actions.append(
            {
                "action": "merge",
                "ids": [m["id"] for m in g],
                "new_topic": base["topic"],
                "new_content": new_content[:500],
            }
        )
    return actions


def _group_content_dups(memories: list[dict]) -> list[list[dict]]:
    """依 content 相似度聚類（允許跨類別）。

    只挑「內容高度重複」的對：sim >= 0.65 且短文在長文中保留率 >= 0.5。
    用於抓同類別偵測漏掉的跨類別重複（同一事實被重複記錄在不同類別下）。
    """
    import difflib

    groups: list[list[dict]] = []
    used: set[int] = set()
    for i, mi in enumerate(memories):
        if i in used:
            continue
        group = [mi]
        used.add(i)
        for j, mj in enumerate(memories):
            if j in used or j == i:
                continue
            ca, cb = mi["content"], mj["content"]
            if not ca or not cb:
                continue
            short, long = sorted((ca, cb), key=len)
            sim = difflib.SequenceMatcher(None, ca, cb).ratio()
            if sim < 0.65:
                continue
            lmatch = difflib.SequenceMatcher(None, short, long).find_longest_match(0, len(short), 0, len(long)).size
            if lmatch / len(short) >= 0.5:
                group.append(mj)
                used.add(j)
        if len(group) >= 2:
            groups.append(group)
    return groups


async def search_memories_by_keyword(keywords: list[str], limit: int = 6) -> str:
    """關鍵字檢索記憶（Phase 1 失效時的兜底）。

    以 LIKE 比對 topic/content，跨 user_id（含 __BOT__）搜尋，
    讓『非最近但字面相關』的記憶（例如使用者點名問的舊設定）仍能被帶進來。
    回傳格式化字串；無命中回傳空字串。
    """
    kws = [k.strip() for k in keywords if k and len(k.strip()) >= 2]
    if not kws:
        return ""
    clauses = " OR ".join(["(topic LIKE ? OR content LIKE ?)"] * len(kws))
    params: list = []
    for k in kws:
        params.extend([f"%{k}%", f"%{k}%"])
    try:
        async with get_db() as conn:
            cursor = await conn.execute(
                f"""SELECT timestamp, topic, content, context, user_name
                    FROM memories WHERE {clauses}
                    ORDER BY timestamp DESC LIMIT ?""",
                (*params, limit),
            )
            rows = await cursor.fetchall()
    except Exception as e:
        print(f"[關鍵字召回] 搜尋失敗: {e}")
        return ""
    if not rows:
        return ""
    lines = ["（依關鍵字額外召回）"]
    for timestamp, topic, content, ctx, uname in rows:
        label = "【中之人】" if ctx == "ooc" else "【劇中】"
        who = f"{uname} " if uname else ""
        lines.append(f"- [{timestamp}] {who}{label} {topic}: {content}")
    print(f"[關鍵字召回] 命中 {len(rows)} 條: {kws}")
    return "\n".join(lines)


_DATE_RE = re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")


def _norm_date(value) -> str | None:
    """把 '2026-9-28' / '2026/09/28' 正規化為 '2026-09-28'；不成格式回 None。"""
    if not value or not isinstance(value, str):
        return None
    s = value.strip().replace("/", "-").replace(".", "-")
    if not _DATE_RE.match(s):
        return None
    try:
        y, m, d = (int(p) for p in s.split("-"))
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return None


def parse_date_range(range_obj) -> tuple[str, str] | None:
    """從 Phase 1 的 recall_date_range 解析出 (from, to)；無法解析回 None。

    容忍 'to' 缺漏（單日錨點），也容忍 from/to 顛倒（自動對調）。
    """
    if not range_obj:
        return None
    if isinstance(range_obj, str):
        # 模型常把物件回成字串（lore_notes 也有同型問題），先試著解回 dict
        s = range_obj.strip()
        if s.startswith("{"):
            try:
                import json as _json

                range_obj = _json.loads(s)
            except (ValueError, TypeError):
                return None
        else:
            range_obj = {"from": s}
    if not isinstance(range_obj, dict):
        return None
    d_from = _norm_date(range_obj.get("from") or range_obj.get("start"))
    d_to = _norm_date(range_obj.get("to") or range_obj.get("end"))
    if d_from and d_to and d_from > d_to:
        d_from, d_to = d_to, d_from
    if d_from and not d_to:
        d_to = d_from
    if d_to and not d_from:
        d_from = d_to
    if not d_from or not d_to:
        return None
    return d_from, d_to


async def fetch_memories_by_date_range(
    date_from: str, date_to: str, max_entries: int = 60, content_limit: int = 0
) -> str:
    """依日期／區間撈出記憶全文，供「總結某日」這類整段回顧請求使用。

    一般召迴只給每個參與者最近幾條 + Phase 1 挑的 supplement，覆蓋率取決於
    模型挑選；要求「總結某天」時使用者要的是「那天全部」，不能靠挑選。
    故此路徑直接撈時間範圍內的記憶，略過 supplement 篩選。

    date_from/date_to 為 YYYY-MM-DD（含起訖兩天）。max_entries 為保險絲，
    content_limit > 0 時逐條截斷 content，寬區間用它控制注入量。
    """
    span_days = (
        datetime.date.fromisoformat(date_to) - datetime.date.fromisoformat(date_from)
    ).days + 1
    if content_limit <= 0:
        # 單日或兩天以內：內容不截斷，確保摘要不會漏掉細節
        content_limit = 600 if span_days <= 2 else 300
    try:
        async with get_db() as conn:
            cursor = await conn.execute(
                "SELECT timestamp, topic, content, context, user_id FROM memories "
                "WHERE timestamp >= ? AND timestamp <= ? "
                "ORDER BY timestamp ASC LIMIT ?",
                (f"{date_from} 00:00:00", f"{date_to} 23:59:59", max_entries),
            )
            rows = await cursor.fetchall()
            total = (
                await (await conn.execute(
                    "SELECT COUNT(*) FROM memories "
                    "WHERE timestamp >= ? AND timestamp <= ?",
                    (f"{date_from} 00:00:00", f"{date_to} 23:59:59"),
                )).fetchone()
            )[0]
    except Exception as e:
        print(f"[日期召回] 搜尋失敗: {e}")
        return ""

    if not rows:
        return ""

    label = date_from if date_from == date_to else f"{date_from} ~ {date_to}"
    lines = [f"（{label} 全期間記憶，共 {total} 筆）"]
    for timestamp, topic, content, ctx, uid in rows:
        c_label = "【中之人】" if ctx == "ooc" else "【劇中】"
        who = "" if uid == "__BOT__" else "使用者 "
        body = content if len(content) <= content_limit else content[:content_limit] + "…"
        lines.append(f"- [{timestamp}] {who}{c_label} {topic}: {body}")
    if total > len(rows):
        lines.append(f"（僅列出前 {len(rows)} 筆，共 {total} 筆）")
    print(f"[日期召回] {date_from}~{date_to} 命中 {len(rows)}/{total} 筆")
    return "\n".join(lines)


async def resolve_and_fetch_memories(recall_user_ids: list, recall_candidates: list,
                                     channel_type: str) -> tuple[list, str]:
    """名稱→ID 解析 + 從 SQLite 撈使用者記憶。回傳 (resolved_ids, combined_memories)。

    解析順序：純數字 ID → Discord 顯示名（精確／包含）→ person_aliases 別名。
    最後一步是關鍵：recall_candidates 永遠只有 Discord 使用者，NPC 不在其中，
    若不查別名表，Phase 1 回的 NPC 名（例如「草莓姊」）會被整個丟棄。
    """
    resolved_ids = []
    npc_names = []   # 解析不到 Discord id、但認得出是誰的 NPC
    for raw_id in recall_user_ids[:3]:
        try:
            int(raw_id)
            resolved_ids.append(raw_id)
        except ValueError:
            matched = next(
                (c for c in recall_candidates if c["name"].lower() == raw_id.lower()),
                None,
            )
            if not matched:
                raw_lower = raw_id.lower()
                for c in recall_candidates:
                    c_lower = c["name"].lower()
                    if raw_lower in c_lower or c_lower in raw_lower:
                        matched = c
                        break
            if matched:
                resolved_ids.append(matched["id"])
                continue
            # Discord 對不上 → 試別名表（NPC 走這條）
            canonical = await resolve_person_name(raw_id)
            if await is_known_person(raw_id):
                npc_names.append((raw_id, canonical))
                print(f"[Phase 1] '{raw_id}' 解析為角色 {canonical}（別名對照）")
            else:
                print(f"[Phase 1] 無法解析 '{raw_id}' 為有效 ID，跳過。")

    recalled_memories = []
    for uid in resolved_ids:
        if not uid.isdigit():
            continue
        name = next((c["name"] for c in recall_candidates if c["id"] == uid), uid)
        mem = await get_user_memory(
            int(uid), limit=8,
            context_filter="ic" if channel_type == "in_character" else None,
            user_name=name,
        )
        recalled_memories.append(f"【{name}】\n{mem}")
        print(f"[召回] {name} (id={uid})")

    for raw, canonical in npc_names:
        mem = await get_person_memory(canonical)
        if mem and mem != "（無記錄）":
            recalled_memories.append(f"【{canonical}】（{raw}）\n{mem}")
            print(f"[召回] {canonical} via 別名 '{raw}'")

    combined_memories = "\n\n".join(recalled_memories)
    return resolved_ids, combined_memories


# 記憶 topic 的人物前綴（人物:甲 / 角色能力:甲（草莓姊）的水柱 / 角色背景:...）
_PERSON_TOPIC_RE = re.compile(r"^(?:人物|角色能力|角色背景)[:：]\s*(.+)$")
# combined_memories 每個區塊的標題：【吳神月】 / 【甲】（草莓姊）
_PERSON_LABEL_RE = re.compile(r"^【([^】]+)】", re.M)


async def _collect_known_person_names() -> dict[str, str]:
    """建立 {可辨識名稱: 正名} 對照，來源為 person_aliases 與記憶 topic。

    topic 是關鍵：像『羊君』這種沒跟任何人互為別名的獨立角色，永遠不會
    出現在 person_aliases，但會有『人物:羊君』的記憶；不從 topic 撈的話，
    這類角色就算被對話點名也召不回來（2026-10-09 實測羊君被忘記即此因）。
    """
    mapping: dict[str, str] = {}
    try:
        async with get_db() as conn:
            cursor = await conn.execute("SELECT alias, canonical FROM person_aliases")
            for alias, canonical in await cursor.fetchall():
                alias = (alias or "").strip()
                canonical = (canonical or alias).strip()
                if alias:
                    mapping[alias] = canonical
                if canonical:
                    mapping.setdefault(canonical, canonical)
            cursor = await conn.execute(
                "SELECT DISTINCT topic FROM memories "
                "WHERE topic LIKE '人物:%' OR topic LIKE '人物：%' "
                "OR topic LIKE '角色能力:%' OR topic LIKE '角色能力：%' "
                "OR topic LIKE '角色背景:%' OR topic LIKE '角色背景：%'"
            )
            for (topic,) in await cursor.fetchall():
                m = _PERSON_TOPIC_RE.match((topic or "").strip())
                if not m:
                    continue
                # 去括號複合名（『甲（草莓姊）』→『甲』），只用主名比對
                base = re.sub(r"[（(].*?[）)].*$", "", m.group(1)).strip()
                if len(base) >= 2:
                    mapping.setdefault(base, base)
    except Exception as e:
        print(f"[人物召回] 讀取人物清單失敗: {e}")
    return mapping


async def recall_mentioned_persons_memories(
    text: str, combined_memories: str, limit: int = 3
) -> str:
    """掃描對話文字中提及的已知角色，撈其記憶並回傳可注入的區塊。

    治本於「被提到的人就該被召回」：不再只靠 Phase 1 點名（Phase 1 常因
    看不到該角色記憶而漏），也不要求角色先登記進 person_aliases。
    長名優先、每人最多一筆，已在 combined_memories 出現過的對象跳過。
    """
    text = text or ""
    if not text.strip():
        return ""
    mapping = await _collect_known_person_names()
    if not mapping:
        return ""
    bot_name = await _get_character_name()
    already = set(_PERSON_LABEL_RE.findall(combined_memories or ""))
    # 只比對長度 >= 2 的名稱，避免『甲』『乙』這種單字暱稱亂命中
    hits = [
        (name, canonical)
        for name, canonical in mapping.items()
        if len(name) >= 2 and name in text and name not in already
        and canonical != bot_name
    ]
    if not hits:
        return ""
    hits.sort(key=lambda x: len(x[0]), reverse=True)
    chosen: list[tuple[str, str]] = []
    seen_canon: set[str] = set()
    for name, canonical in hits:
        if canonical in seen_canon or canonical in already:
            continue
        seen_canon.add(canonical)
        chosen.append((name, canonical))
        if len(chosen) >= limit:
            break
    blocks = []
    for name, canonical in chosen:
        mem = await get_person_memory(canonical)
        if mem and mem != "（無記錄）":
            blocks.append(f"【{canonical}】（對話提及：{name}）\n{mem}")
            print(f"[人物召回] 對話提及 '{name}' → 召回 {canonical}")
    return "\n\n".join(blocks)
