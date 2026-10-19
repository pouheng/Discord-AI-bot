"""make_prompt_blocks.py — 由現行硬編碼 Phase 2 prompt 產生 prompt_blocks.json 初始種子。

一次性遷移工具。做法：
1. 以固定 sentinel context 呼叫 legacy `build_phase2_system_prompt`，取得 OOC/IC 完整字串。
2. 依 marker 切段（marker = 該區段開始的獨特子字串，含前置空白）。
   - static 段：內容原樣保留（做 placeholder 代換）。
   - dynamic 段：內容不存（執行期由 prompt_blocks 的生成器產生）。
3. 寫出 prompt_blocks.json。

對拍由 test_parity.py 負責。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(SCRIPT_DIR, "prompt_blocks.json")

SENT = dict(
    channel_context="<<CHCTX>>",
    channel_note="<<CHNOTE>>",
    ic_context_text="<<ICCTX>>",
    combined_memories="<<KNOWN>>",
    lore_text="<<LORE>>",
    self_memories="<<SELF>>",
    server_rules_text="<<SRV>>",
    quests_text="<<QUESTS>>",
    lore_speculation="<<SPEC>>",
)

# (id, title, kind, marker) — marker=None 表示從字串開頭起算
OOC_DEFS = [
    ("ooc_identity", "身分（中之人）", "static", None),
    ("ooc_chat_rules", "中之人聊天守則", "static", "【中之人聊天守則"),
    ("ooc_self_intro", "角色名稱自我介紹", "dynamic", "你扮演的角色名稱是「"),
    ("ooc_persona", "你的中之人設定", "static", "\n【你的中之人設定】"),
    ("ooc_examples", "中之人聊天語料範例", "static", "\n【中之人聊天語料範例"),
    ("ooc_time", "時間感知", "static", "\n【⚠️ 時間感知"),
    ("ooc_safety", "安全規則", "static", "\n【安全規則"),
    ("ooc_server", "本伺服器規則", "dynamic", "【本伺服器規則】"),
    ("ooc_visual", "禁止視覺幻覺", "static", "【⚠️ 禁止視覺幻覺】"),
    ("ooc_emojis", "可用表情符號", "static", "【可用表情符號】"),
    ("ooc_no_self_answer", "禁止自問自答", "static", "【⚠️ 禁止自問自答】"),
    ("ooc_no_repeat", "避免重複結構", "static", "\n【⚠️ 避免重複結構】"),
    ("ooc_divider", "動態資料區分隔線", "static", "# ── 動態資料區"),
    ("ooc_scene", "場景", "dynamic", "【場景】"),
    ("ooc_lore", "世界觀資料", "dynamic", "【世界觀資料】"),
    ("ooc_speculation", "系統推測", "dynamic", "\n\n【系統推測"),
    ("ooc_alias", "角色身份對照表", "dynamic", "\n\n【角色身份對照表"),
    ("ooc_present", "在場角色檔案", "dynamic", "\n\n【在場角色檔案】"),
    ("ooc_known", "眼前人物已知資訊", "dynamic", "【你對眼前人物的已知資訊】"),
    ("ooc_self", "對自己角色的認知", "dynamic", "【你對自己角色的認知】"),
    ("ooc_self_mem", "自身記憶", "dynamic", "<<SELF>>"),
    ("ooc_tail", "Priming 尾巴", "static", "喔好，收到了"),
]

IC_DEFS = [
    ("ic_jailbreak", "角色扮演協議（jailbreak）", "static", None),
    ("ic_identity", "你的身份（IC）", "dynamic", "【你的身份"),
    ("ic_safety", "安全規則", "static", "\n【安全規則"),
    ("ic_server", "本伺服器規則", "dynamic", "【本伺服器規則】"),
    ("ic_social", "人際互動原則", "static", "【人際互動原則】"),
    ("ic_visual", "禁止視覺幻覺", "static", "【⚠️ 禁止視覺幻覺】"),
    ("ic_time", "時間感知", "static", "【⚠️ 時間感知"),
    ("ic_emojis", "可用表情符號", "static", "【可用表情符號】"),
    ("ic_style", "文風規則", "static", "【文風規則】"),
    ("ic_no_self_answer", "禁止自問自答", "static", "【⚠️ 禁止自問自答】"),
    ("ic_no_repeat", "避免重複結構", "static", "\n【⚠️ 避免重複結構】"),
    ("ic_divider", "動態資料區分隔線", "static", "# ── 動態資料區"),
    ("ic_scene", "場景", "dynamic", "【場景】"),
    ("ic_lore", "世界觀資料", "dynamic", "【世界觀資料"),
    ("ic_speculation", "系統推測", "dynamic", "\n\n【系統推測"),
    ("ic_alias", "角色身份對照表", "dynamic", "\n\n【角色身份對照表"),
    ("ic_present", "在場角色檔案", "dynamic", "\n\n【在場角色檔案】"),
    ("ic_known", "眼前人物已知資訊", "dynamic", "【你對眼前人物的已知資訊】"),
    ("ic_self", "對自己角色的認知", "dynamic", "【你對自己角色的認知】"),
    ("ic_self_mem", "自身記憶", "dynamic", "<<SELF>>"),
    ("ic_quests", "任務", "dynamic", "<<QUESTS>>"),
    ("ic_tail", "Priming 尾巴", "static", "開始回應："),
]


async def _capture(pcfg):
    async def const(v):
        return v

    async def name():
        return pcfg["character_name"]

    async def ident():
        return "<<IDENT>>"

    main.get_character_name = name
    main.get_character_identity = ident
    main.format_person_alias_map = lambda: const("<<ALIAS>>")
    main.format_person_profiles = lambda c: const("<<PROFILE>>")
    main.format_character_profile = lambda c: const("<<CHARPROFILE>>")
    main._present_persons_from_memories = lambda c, limit=4: const(["X"])

    out = {}
    for label, ctype in [("ooc", "out_of_character"), ("ic", "in_character")]:
        out[label] = await main.build_phase2_system_prompt(
            ctype, pcfg, False,
            SENT["channel_context"], SENT["channel_note"], SENT["ic_context_text"],
            SENT["combined_memories"], SENT["lore_text"], SENT["self_memories"],
            SENT["server_rules_text"], SENT["quests_text"], SENT["lore_speculation"],
        )
    return out


def _slice(text, defs):
    """依 marker 切段；回傳 [(id,title,kind,content)]。"""
    idxs = []
    for i, (bid, title, kind, marker) in enumerate(defs):
        if marker is None:
            idx = 0
        else:
            idx = text.find(marker)
            if idx < 0:
                raise SystemExit(f"找不到 marker: {bid} / {marker!r}")
        idxs.append(idx)
    blocks = []
    for i, (bid, title, kind, marker) in enumerate(defs):
        start = idxs[i]
        end = idxs[i + 1] if i + 1 < len(defs) else len(text)
        content = text[start:end]
        blocks.append({"id": bid, "title": title, "kind": kind, "content": content})
    return blocks


CONDITIONS = {
    "ooc_persona": ["has_ooc_persona"],
    "ooc_examples": ["has_ooc_chat_examples"],
    "ooc_emojis": ["has_emojis"],
    "ic_identity": ["has_char_name"],
    "ic_social": ["has_social_awareness"],
    "ic_emojis": ["has_emojis"],
}


def _subst(content, pcfg, char_name_only=False):
    content = content.replace("2026-10-18", "{{today}}")
    banned = "、".join(pcfg.get("banned_words", []))
    if banned:
        content = content.replace(banned, "{{banned_words}}")
    if char_name_only and pcfg.get("character_name"):
        content = content.replace(f"「{pcfg['character_name']}」", "「{{char_name}}」")
    return content


async def main_async():
    pcfg = await main.load_prompt_config()
    cap = await _capture(pcfg)

    data = {"version": 1, "tabs": {}}
    for label, defs in [("ooc", OOC_DEFS), ("ic", IC_DEFS)]:
        slices = _slice(cap[label], defs)
        out_blocks = []
        for order, b in enumerate(slices):
            is_static = b["kind"] == "static"
            content = b["content"] if is_static else ""
            if is_static:
                content = _subst(content, pcfg, char_name_only=(label == "ic" and b["id"] == "ic_style"))
            out_blocks.append({
                "id": b["id"],
                "title": b["title"],
                "kind": b["kind"],
                "enabled": True,
                "condition": CONDITIONS.get(b["id"], []),
                "content": content,
            })
        data["tabs"][label] = out_blocks

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[seed] 寫入 {OUT_FILE}")
    for label in ("ooc", "ic"):
        print(f"  {label}: {len(data['tabs'][label])} 條（static="
              f"{sum(1 for b in data['tabs'][label] if b['kind']=='static')}）")


if __name__ == "__main__":
    asyncio.run(main_async())
