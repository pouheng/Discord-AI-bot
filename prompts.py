"""
prompts.py — 統一 Prompt 組裝工具

build_style_block() 從 config.json 組裝風格規則文字，
消除 _ai_edit_message、build_phase2_system_prompt、_review_edit_suggestion 三處重複。
"""


def build_style_block(pcfg: dict) -> str:
    """從 prompt config 組裝風格規則文字區塊。"""
    lines = []
    for k in ("dialogue_ratio", "naming_rule"):
        v = pcfg.get(k, "")
        if v:
            lines.append(f"• {v}")
    for pref in pcfg.get("expression_prefs", []):
        if isinstance(pref, str) and pref.strip():
            lines.append(f"• {pref.strip()}")
    banned = pcfg.get("banned_words", [])
    if banned:
        lines.append(f"• 禁止使用：{'、'.join(banned)}")
    return "\n".join(lines) if lines else "（無特殊文風規則）"


def build_banned_str(pcfg: dict) -> str:
    """組裝禁用詞彙字串。"""
    return "、".join(pcfg.get("banned_words", []))


def build_expr_str(pcfg: dict) -> str:
    """組裝表達偏好字串。"""
    return "\n".join(
        f"  {line}" for line in pcfg.get("expression_prefs", [])
    )


def build_safety_str(pcfg: dict, exclude_ooc: bool = False) -> str:
    """組裝安全規則字串。exclude_ooc=True 時過濾掉 NSFW 相關規則。"""
    rules = pcfg.get("safety_rules", [])
    if exclude_ooc:
        rules = [r for r in rules if "色情" not in r and "NSFW" not in r]
    return "\n".join(f"- {line}" for line in rules)
