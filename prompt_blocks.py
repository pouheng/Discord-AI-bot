"""prompt_blocks.py — Phase 2 提示詞的資料驅動引擎。

- 條目資料存於 prompt_blocks.json（唯一真相來源）。
- 每個條目：{id, title, kind, enabled, condition, content}
    kind=static  : content 為可編輯文字（可含 {{placeholder}}）。
    kind=dynamic : 執行期由 GENERATORS[id] 生成，content 不存。
- render_system(channel, ctx) 依序串接啟用且條件成立的條目，輸出 Phase 2 SYSTEM 字串。
- 儲存/版本歷史：save_blocks() 寫入並備份快照，保留 HISTORY_KEEP 版；rollback() 還原。
"""
import glob
import json
import os
import re
import tempfile
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BLOCKS_FILE = os.path.join(SCRIPT_DIR, "prompt_blocks.json")
HISTORY_DIR = os.path.join(SCRIPT_DIR, "prompt_blocks_history")
HISTORY_KEEP = 50

# ── 開場提示句（priming）──
# 存在的唯一位置：prompt_blocks.json 的 "priming"。程式用它
#   (1) 接在使用者訊息最後，引導模型直接進正文；
#   (2) 系統提示詞尾巴的 {{priming}} 佔位；
#   (3) 模型若把它複述出來，自動從回覆開頭刪掉。
DEFAULT_PRIMING = {
    "ooc": "喔好，收到了，我現在會開始思考、開始回應：",
    "ic": "開始回應：{{char_name}}的台詞",
}


def _priming_from(data, channel):
    p = data.get("priming")
    if isinstance(p, dict) and isinstance(p.get(channel), str):
        return p[channel]
    return DEFAULT_PRIMING.get(channel, "")


def get_priming(channel):
    """目前設定的開場提示句（'ooc' 或 'ic'）；可含 placeholder。"""
    return _priming_from(load_blocks(), channel)


def set_priming(data, channel, text):
    """在要寫回的 data 上設定 priming（不直接寫檔，交給 save_blocks）。"""
    p = data.get("priming")
    if not isinstance(p, dict):
        p = {}
    p[channel] = text or ""
    data["priming"] = p
    return data


# ── 已知條件旗標（給編輯器顯示與勾選；旗標值由 ctx 計算）──
FLAG_LABELS = {
    "has_server_rules": "有本伺服器規則",
    "has_lore": "有世界觀資料",
    "has_speculation": "有系統推測",
    "has_present_profiles": "有在場角色檔案",
    "has_quests": "有任務",
    "has_self_memories": "有自身記憶",
    "has_combined_memories": "有已知資訊",
    "has_char_name": "有角色名稱",
    "has_char_identity": "有角色描述",
    "has_ooc_persona": "有中之人設定",
    "has_ooc_chat_examples": "有中之人語料",
    "has_social_awareness": "有人際互動原則",
    "has_emojis": "有可用表情符號",
    "enable_ic_style": "Phase 1 判定為寫作任務",
}


def _flag_ok(flag, ctx):
    v = ctx.get(flag)
    if v is None:
        return False
    return bool(v)


def condition_ok(flags, ctx):
    return all(_flag_ok(f, ctx) for f in (flags or []))


# ── placeholder 代換 ──
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def fill_placeholders(content, ctx):
    def rep(m):
        key = m.group(1)
        if key in ctx and ctx[key] is not None:
            return str(ctx[key])
        return m.group(0)
    return _PLACEHOLDER_RE.sub(rep, content or "")


# ── 動態條目生成器 ──
_SPEC_CLOSE = (
    "（以上是系統對劇情的推測與運用建議，不是對話紀錄。⚠️ 嚴禁把這裡的內容當成"
    "「誰說了什麼」的事實：對話中每個人的發言歸屬，一律以實際對話紀錄為準；"
    "若此處與對話紀錄衝突，以對話紀錄為準。）"
)


def _g_self_intro(ctx):
    s = f"你扮演的角色名稱是「{ctx.get('char_name','')}」。"
    if ctx.get("char_identity"):
        s += f"\n角色描述：{ctx['char_identity']}"
    s += "\n\n"
    return s


def _g_server_ooc(ctx):
    srv = ctx.get("server_rules_text", "")
    if srv:
        return "【本伺服器規則】\n" + srv + "\n\n"
    return "\n\n"


def _g_server_ic(ctx):
    srv = ctx.get("server_rules_text", "")
    if srv:
        return "【本伺服器規則】\n" + srv + "\n"
    return "\n"


def _g_scene(ctx):
    return (
        "【場景】\n"
        + ctx.get("channel_context", "") + "\n"
        + ctx.get("channel_note", "") + "\n"
        + ctx.get("ic_context_text", "") + "\n\n"
    )


def _g_lore_ooc(ctx):
    lore = ctx.get("lore_text", "")
    if lore:
        return (
            "【世界觀資料】\n" + lore
            + "\n（以上是世界觀資料。當你們討論劇情時可以參考這些內容。）"
        )
    return "【世界觀資料】"


def _g_lore_ic(ctx):
    header = (
        "【世界觀資料 - 若被問到相關內容，請直接引用以下資訊回答，"
        "不要自己編造不存在的情節】"
    )
    lore = ctx.get("lore_text", "")
    if lore:
        return (
            header + "\n\n" + lore
            + "\n\n（以上是世界觀資料。當對方要求你說明時，用自然的語氣轉述這些內容，"
            "但不要添加資料中沒有的細節。）"
        )
    return header


def _g_speculation(ctx):
    spec = ctx.get("lore_speculation", "")
    if spec:
        return (
            "\n\n【系統推測 — 非對話事實，不可當事實引用】\n" + spec + "\n" + _SPEC_CLOSE
        )
    return ""


def _g_alias(ctx):
    alias = ctx.get("alias_text") or "（無）"
    return "\n\n【角色身份對照表 — 同一個人，不是不同角色】\n" + alias + "\n"


def _g_present(ctx):
    present = ctx.get("present_text", "")
    if present:
        return "\n\n【在場角色檔案】\n" + present + "\n"
    return "\n"


def _g_known(ctx):
    return "【你對眼前人物的已知資訊】  \n" + ctx.get("combined_memories", "") + "\n\n"


def _g_self(ctx):
    s = "【你對自己角色的認知】"
    prof = ctx.get("char_profile", "")
    if prof:
        s += "\n" + prof + "\n"
    s += "\n"
    return s


def _g_self_mem(ctx):
    return ctx.get("self_memories", "") + "\n\n"


def _g_quests(ctx):
    return ctx.get("quests_text", "") + "\n\n"


def _g_identity_ic(ctx):
    cn = ctx.get("char_name", "")
    if not cn:
        return ""
    s = (
        "【你的身份 - 這是關於你自己的事，最優先遵守！】\n"
        f"你的名字是「{cn}」。在正文中你必須用「{cn}」自稱，絕不可使用「我」。\n"
        f"❌ 錯誤：我聽見了聲音 → ✅ 正確：{cn}聽見了聲音\n"
        f"❌ 錯誤：我想去學園 → ✅ 正確：{cn}想去學園\n"
        "這是關於你自己的核心設定，永遠不可違反。\n"
    )
    if ctx.get("char_identity"):
        s += f"\n角色描述：{ctx['char_identity']}\n"
    s += "\n"
    return s


GENERATORS = {
    "ooc_self_intro": _g_self_intro,
    "ooc_server": _g_server_ooc,
    "ooc_scene": _g_scene,
    "ooc_lore": _g_lore_ooc,
    "ooc_speculation": _g_speculation,
    "ooc_alias": _g_alias,
    "ooc_present": _g_present,
    "ooc_known": _g_known,
    "ooc_self": _g_self,
    "ooc_self_mem": _g_self_mem,
    "ic_identity": _g_identity_ic,
    "ic_server": _g_server_ic,
    "ic_scene": _g_scene,
    "ic_lore": _g_lore_ic,
    "ic_speculation": _g_speculation,
    "ic_alias": _g_alias,
    "ic_present": _g_present,
    "ic_known": _g_known,
    "ic_self": _g_self,
    "ic_self_mem": _g_self_mem,
    "ic_quests": _g_quests,
}


# ── 儲存 / 讀取 / 歷史 ──
def load_blocks():
    if not os.path.exists(BLOCKS_FILE):
        return {"version": 1, "tabs": {"ooc": [], "ic": []},
                "priming": dict(DEFAULT_PRIMING)}
    with open(BLOCKS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("version", 1)
    data.setdefault("tabs", {"ooc": [], "ic": []})
    if not isinstance(data.get("priming"), dict):
        data["priming"] = dict(DEFAULT_PRIMING)
    return data


def _snapshot(data):
    os.makedirs(HISTORY_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(HISTORY_DIR, f"{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    _trim_history()
    return ts


def _trim_history():
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")))
    for old in files[:-HISTORY_KEEP]:
        try:
            os.remove(old)
        except OSError:
            pass


def save_blocks(data, note=""):
    """原子寫入 + 版本快照；回傳快照時間戳。"""
    os.makedirs(os.path.dirname(BLOCKS_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(BLOCKS_FILE), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BLOCKS_FILE)
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    ts = _snapshot(data)
    if note:
        note_path = os.path.join(HISTORY_DIR, f"{ts}.note")
        try:
            with open(note_path, "w", encoding="utf-8") as f:
                f.write(note)
        except OSError:
            pass
    return ts


def list_history():
    files = sorted(glob.glob(os.path.join(HISTORY_DIR, "*.json")), reverse=True)
    out = []
    for path in files:
        ts = os.path.basename(path)[:-5]
        note = ""
        note_path = os.path.join(HISTORY_DIR, f"{ts}.note")
        if os.path.exists(note_path):
            try:
                with open(note_path, "r", encoding="utf-8") as f:
                    note = f.read().strip()
            except OSError:
                pass
        out.append({"ts": ts, "note": note})
    return out


def load_history(ts):
    path = os.path.join(HISTORY_DIR, f"{ts}.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def rollback(ts):
    data = load_history(ts)
    return save_blocks(data, note=f"rollback→{ts}")


# ── 渲染 ──
LAST_SEGMENTS = []
LAST_CHANNEL = None


def render_system(channel, ctx):
    """channel: 'ooc'|'ic'。ctx 需含生成器/旗標/placeholder 對應的值。

    同時把本次渲染的每個區段記到 LAST_SEGMENTS（供 last_prompt_blocks.json 檢視）。
    """
    global LAST_SEGMENTS, LAST_CHANNEL
    data = load_blocks()
    tab = data.get("tabs", {}).get(channel, [])
    # priming 先填好 placeholder，再讓 {{priming}} 帶進條目（單次代換，
    # 不會回頭再掃一次，所以這裡要先自己填）。
    ctx = dict(ctx)
    ctx["priming"] = fill_placeholders(_priming_from(data, channel), ctx)
    parts = []
    segments = []
    for b in tab:
        enabled = b.get("enabled", True)
        cond_ok = condition_ok(b.get("condition", []), ctx)
        included = enabled and cond_ok
        rendered = ""
        if included:
            if b.get("kind") == "static":
                rendered = fill_placeholders(b.get("content", ""), ctx)
            else:
                gen = GENERATORS.get(b.get("id"))
                rendered = gen(ctx) if gen else ""
            parts.append(rendered)
        segments.append({
            "id": b.get("id"),
            "title": b.get("title", ""),
            "kind": b.get("kind", "static"),
            "enabled": enabled,
            "condition": b.get("condition", []),
            "included": included,
            "rendered": rendered,
        })
    LAST_SEGMENTS = segments
    LAST_CHANNEL = channel
    return "".join(parts)
