"""
collide_pipeline.py — 對撞三階段流程（串接提示詞與引擎）

Phase A：預處理（LLM 分析討論串 → 判斷對抗雙方/招式/狀態）
Phase B：計算（collide_engine 純函式算 BP + LLM 產出公開思維鏈）
Phase C：文風輸出（確認後，依文風規則生成對撞文本）

資料來源透過 db_lookup callable 注入，實作於 collide_db.py（make_db_lookup）。
"""

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from llm_models import chat_create, has_credentials

from collide_engine import (
    CLASH_OUTCOME_ACTIVE,
    CLASH_TIER_SPECS,
    VERDICT_DECISIVE,
    VERDICT_FUSION,
    VERDICT_SUPPRESS,
    _hidden_of,
    apply_effects,
    clash_outcome_label,
    compute_atk_bp,
    compute_domain_bp,
    compute_manamelee_bp,
    compute_melee_bp,
    compute_villain_guard,
    decide_with_domain,
    domain_fusion_effects,
    domain_mod,
    element_mod,
    format_clash_hp_bar,
    hidden_score,
    resolve_clash_node,
    retreat_floor_ticks,
)
from collide_rules import (
    CLASH_EFFECT_SYSTEM,
    CLASH_PHASE_A_SYSTEM,
    CLASH_PHASE_B_SYSTEM,
    CLASH_PHASE_C_SYSTEM,
    RANGE_MOD_SYSTEM,
)
import collide_db

load_dotenv()
# 客戶端改由 llm_models.get_client() 依來源即時提供（見下方呼叫點）。

# db_lookup: async (char_name, table) -> row dict or None
DbLookup = Callable[[str, str], Awaitable[Optional[dict]]]


class ClashError(Exception):
    pass


async def _call_llm(
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 6000,
    label: str = "",
) -> str:
    if not has_credentials():
        raise ClashError("AI 服務未啟用，請檢查 DEEPSEEK_API_KEY 設定")
    # deepseek-v4-flash 為 reasoning 型，reasoning_content 吃 token 預算，
    # 若 content 為空（預算被推論吃光）則放大預算重試，最多 3 次。
    last = ""
    for attempt in range(3):
        budget = min(max_tokens * (2 ** attempt), 32000)
        resp = await chat_create(
            "collide",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=budget,
        )
        content = (resp.choices[0].message.content or "").strip()
        last = content
        if content:
            _append_clash_log(label or "Phase", system, user, content)
            return content
        await asyncio.sleep(0.5)
    if last:
        _append_clash_log(label or "Phase", system, user, last)
    return last


def _append_clash_log(label: str, system: str, user: str, response: str) -> None:
    """把對撞 LLM 呼叫的 prompt 與輸出附加到 prompt_logs/clash_log.txt（供除錯）。"""
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "clash_log.txt")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {label}\n")
            f.write("-" * 70 + "\n【SYSTEM】\n" + system + "\n")
            f.write("-" * 70 + "\n【USER】\n" + user + "\n")
            f.write("-" * 70 + "\n【OUTPUT】\n" + response + "\n")
    except Exception:
        pass


async def _call_json(
    system: str,
    user: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 8000,
    retries: int = 2,
    label: str = "JSON",
) -> dict:
    """呼叫 LLM 並解析 JSON；空內容（_call_llm 內處理）或**解析失敗**時放大預算重試。

    deepseek-v4-flash 的 reasoning_content 可能把 content 截斷成不完整 JSON
    （"Unterminated string"），這類非空但壞掉的輸出需要重試才能救回。
    """
    last_raw = ""
    for attempt in range(retries + 1):
        raw = await _call_llm(
            system, user, temperature=temperature, max_tokens=max_tokens * (2 ** attempt), label=label
        )
        try:
            return _json_or_raise(raw, label)
        except ClashError:
            last_raw = raw
            await asyncio.sleep(0.5)
    raise ClashError(f"{label} 多次輸出無法解析為 JSON：{last_raw[:300]}")


# ── Phase A：預處理 ────────────────────────────────────────

async def phase_a(history: str, db_lookup: Optional[DbLookup] = None) -> dict:
    """分析討論串，判斷對抗雙方、招式、狀態、型態、主張。

    db_lookup 提供時，一併取得登記角色表（roster），讓 Phase A 能區分
    玩家角色與 NPC（NPC 動作可 AI 預設、不構成越權代操）。

    Returns:
        Phase A 結構化 JSON（見 collide_rules.CLASH_PHASE_A_SYSTEM）。
    """
    prompt = f"討論串內容：\n{history}"
    if db_lookup:
        try:
            roster = await db_lookup("", "roster")
            if roster:
                lines = [
                    f"- {r.get('name') or '?'}（種族 {r.get('race') or '未知'}"
                    f"{'｜反派' if r.get('is_villain') else ''}）"
                    for r in roster
                ]
                prompt += "\n\n【登記角色表（供判斷玩家/NPC，不得更改招式內容）】\n" + "\n".join(lines)
        except Exception:
            pass
        try:
            abilities = await db_lookup("", "abilities")
            if abilities:
                lines = [
                    f"- {a.get('name') or '?'}（{a.get('owner') or '通用'}｜{a.get('category') or '未分類'}）"
                    for a in abilities
                ]
                prompt += "\n\n【已登記能力表（供判斷未登記招式屬於哪個能力的擴展應用，不得更改招式內容）】\n" + "\n".join(lines)
        except Exception:
            pass
    return await _call_json(
        CLASH_PHASE_A_SYSTEM,
        prompt,
        max_tokens=8000,
        label="Phase A",
    )


# ── Phase B：計算 ──────────────────────────────────────────

def _hidden_for(char: dict, which: str) -> float:
    """由角色資料取對應隱藏分數（魔法→智力）。"""
    return _stat_hidden(char, "智力")


def _stat_hidden(char: dict, stat: str) -> float:
    """角色某六維隱藏分數 = 評級 × 該項種族修正（_race_mods 六維各自獨立，
    未定義種族或退而求其次用單一 `_race_mod`）。"""
    mods = char.get("_race_mods") or {}
    race_mod = float(mods.get(stat) or char.get("_race_mod") or 1.0)
    rating = (char.get("stats") or {}).get(stat)
    return hidden_score(rating, race_mod) if rating else 0.0


def _resolve_action(
    phase_a: dict,
    side: str,
    db_lookup: Optional[DbLookup],
) -> dict:
    """依 Phase A 結果取角色與招式資料。玩家用其動作，NPC 用預設動作。"""
    cand = phase_a.get(f"{side}_candidate") or {}
    char = cand.get("char")
    if not char:
        raise ClashError(f"Phase A 未判斷出{side}方角色")
    abilities = phase_a.get("abilities") or {}
    applications = phase_a.get("applications") or {}
    estimates = phase_a.get("skill_estimates") or {}
    return {
        "char": char,
        "skill": cand.get("skill"),
        "action_type": cand.get("action_type") or "skill",
        "is_player": cand.get("is_player", False),
        "ability": (abilities.get(side) or "").strip() or None,
        "application": (applications.get(side) or "").strip() or None,
        "skill_estimate": estimates.get(side) or None,
    }


async def _resolve_skill_or_domain(
    actor: dict,
    db_lookup: Optional[DbLookup],
) -> dict:
    """依動作型別解析角色資料：skill 查招式，domain 查結界。

    For skill: attach `kind` field based on data:
    - 有元素、或屬魔法體系（超能系/自然系/療癒系/跨系融合）、或魔法型技能型別
      （個人魔法/通常魔法/術式）→ "magic" (compute_atk_bp)
    - 否則（無元素且無魔法體系的純物理）→ "melee" (compute_melee_bp)
    This ensures _one() selects the correct formula path.
    注意：超能系／療癒系招式不填元素，仍是魔法，不可因無元素而誤判為近戰。
    """
    out = dict(actor)
    if actor.get("action_type") == "domain":
        domain_row = (await db_lookup(actor["skill"], "domain")) if db_lookup else None
        dom = dict(domain_row or {})
        dom["_domain_mod"] = domain_mod(dom.get("awakening") or None, in_domain=True)
        out["domain"] = dom
        out["domain_mod"] = dom["_domain_mod"]
        return out
    skill_name = actor.get("skill")
    skill_row = (await db_lookup(skill_name, "skill")) if (db_lookup and skill_name) else None
    if skill_row:
        sd = dict(skill_row)
        # Derive kind: 有元素或魔法體系 → magic；純物理 → melee
        elem = str(sd.get("element") or "").strip()
        msys = str(sd.get("magic_system") or "").strip()
        stype = str(sd.get("skill_type") or "").strip()
        if elem or msys or stype in ("個人魔法", "通常魔法", "術式"):
            sd["kind"] = "magic"
        else:
            sd["kind"] = "melee"
        # 招式自身 range 留空時，距離判定器沿用所屬能力的極限距離與衰減規則
        ability_row = (await db_lookup(sd.get("ability"), "ability")) if db_lookup else None
        if ability_row:
            sd["_ability_range_limit"] = ability_row.get("range_limit") or ""
            sd["_ability_range_decay"] = ability_row.get("range_decay") or ""
            # 繼承所屬能力熟練度（Proficiency_Mod = 1 + 熟練度/100）
            sd["proficiency_mod"] = round(1.0 + int(ability_row.get("proficiency") or 0) / 100.0, 3)
        out["skill_data"] = sd
    elif actor.get("ability"):
        # 未登記招式但 Phase A 判定屬於某已登記能力的合理擴展：
        # 優先使用 Phase A 估算的完整數值（第一次對撞就能用真實威力）；
        # 估算缺失/異常時退回「變體」規則（繼承熟練度 + 該能力最低 power_base）。
        est = actor.get("skill_estimate")
        ability_row = (await db_lookup(actor["ability"], "ability")) if db_lookup else None
        prof_mod = 1.0
        if ability_row:
            prof_mod = round(1.0 + int(ability_row.get("proficiency") or 0) / 100.0, 3)
        if (
            isinstance(est, dict)
            and est.get("power_base") is not None
            and 1.0 <= float(est.get("power_base") or 0) <= 10.0
        ):
            sd = {k: est.get(k) for k in (
                "name", "ability", "owner", "application", "skill_type",
                "magic_system", "element", "mp_cost", "power_base", "effect", "range",
            )}
            sd["proficiency_mod"] = prof_mod
            elem = str(sd.get("element") or "").strip()
            msys = str(sd.get("magic_system") or "").strip()
            stype = str(sd.get("skill_type") or "").strip()
            sd["kind"] = "magic" if (elem or msys or stype in ("個人魔法", "通常魔法", "術式")) else "melee"
            out["skill_data"] = sd
            out["_unregistered"] = True
        elif ability_row:
            from collide_engine import variant_skill_base
            bases = [float(r["power_base"]) for r in (await db_lookup(actor["ability"], "ability_skills") or [])] if db_lookup else []
            sd = {
                "name": skill_name,
                "power_base": variant_skill_base(bases),
                "proficiency_mod": prof_mod,
                "kind": "magic",
                "_unregistered_variant": True,
            }
            out["skill_data"] = sd
            out["_unregistered"] = True
        else:
            out["skill_data"] = {}
    else:
        out["skill_data"] = {}
    return out


async def _resolve_range_mods(atk: dict, def_: dict, distance: dict) -> tuple[dict, dict]:
    """以招式自身範圍 vs 交戰距離判定雙方 Range_Mod（LLM 距離判定器）。

    近戰型招式在交戰距離超出近身範圍時給出極低 Range_Mod（距離不符，無法有效命中）。
    """
    def _side(actor: dict) -> dict:
        sd = actor.get("skill_data") or {}
        return {
            "char": actor.get("char") or actor.get("name"),
            "action_type": actor.get("action_type"),
            "skill": actor.get("skill"),
            "skill_range": sd.get("range") or "",
            "ability_range_limit": sd.get("_ability_range_limit") or "",
            "ability_range_decay": sd.get("_ability_range_decay") or "",
            "weapon_category": actor.get("weapon_category") or "bare",
        }

    ctx = {
        "distance": {"desc": (distance or {}).get("desc", "")},
        "sides": {"attacker": _side(atk), "defender": _side(def_)},
    }
    judge = await _call_json(
        RANGE_MOD_SYSTEM,
        json.dumps(ctx, ensure_ascii=False, indent=2),
        max_tokens=4000,
        label="距離判定",
    )
    rm = judge.get("range_mod") or {}
    reason = judge.get("reason") or {}
    atk["range_mod"] = max(0.0, float(rm.get("attacker") or 1.0))
    def_["range_mod"] = max(0.0, float(rm.get("defender") or 1.0))
    atk["range_reason"] = str(reason.get("attacker") or "")
    def_["range_reason"] = str(reason.get("defender") or "")
    return atk, def_


# ── 招式效果解析（Phase A2，全機械套用）────────────────────

async def parse_effects(
    atk_actor: dict,
    def_actor: dict,
    statuses: dict,
) -> dict:
    """把雙方招式 effect 文字與討論串宣告的殘存狀態解析成結構化效果。

    回傳 {"attacker": [...], "defender": [...]}（依 Phase A 側別）。
    快速路徑：雙方 effect 皆空且無殘存狀態 → 不呼叫 LLM，直接回空陣列。
    """
    def side_info(actor: dict) -> dict:
        sd = actor.get("skill_data") or {}
        return {
            "char": actor.get("char") or actor.get("name") or "",
            "skill": actor.get("skill") or "",
            "action_type": actor.get("action_type") or "skill",
            "kind": sd.get("kind") or "magic",
            "effect_text": str(sd.get("effect") or "").strip(),
        }

    ctx = {
        "attacker": side_info(atk_actor),
        "defender": side_info(def_actor),
        "statuses": {
            side: [s for s in (statuses.get(side) or []) if isinstance(s, str) and s.strip()]
            for side in ("attacker", "defender")
        },
    }
    has_text = bool(ctx["attacker"]["effect_text"] or ctx["defender"]["effect_text"])
    has_status = bool(ctx["statuses"]["attacker"] or ctx["statuses"]["defender"])
    if not has_text and not has_status:
        return {"attacker": [], "defender": []}
    parsed = await _call_json(
        CLASH_EFFECT_SYSTEM,
        json.dumps(ctx, ensure_ascii=False, indent=2),
        max_tokens=3000,
        label="效果解析",
    )
    return {
        side: [e for e in (parsed.get(side) or []) if isinstance(e, dict)]
        for side in ("attacker", "defender")
    }


def _effects_by_char(phase_a: dict, effects_by_side: dict, atk_actor: dict, def_actor: dict) -> dict:
    """把 Phase A 側別的效果依角色名重新對應（以解析後的完整角色名為鍵）。

    優先取解析後 actor 的 char/name（模糊匹配後的全名），退回 Phase A candidate 名。
    """
    out: dict[str, list[dict]] = {}
    for side, fx in effects_by_side.items():
        actor = atk_actor if side == "attacker" else def_actor
        char = (actor.get("name") or actor.get("char") or "").strip()
        if not char:
            cand = phase_a.get(f"{side}_candidate") or {}
            char = (cand.get("char") or "").strip()
        if char:
            out[char] = [e for e in fx if isinstance(e, dict)]
    return out


def compute_actor_bp(
    actor: dict,
    skill: dict,
    status_m: float,
    form_m: float,
    variant_m: float,
    ambush_m: float,
    elem_m: float,
) -> dict:
    """單一角色最終 BP。與 compute_sides 同公式，供多打一逐人計算。"""
    from collide_engine import weapon_base as _wb
    weapon_cat = actor.get("weapon_category") or "bare"
    weapon_override = actor.get("weapon_base")
    weapon = _wb(weapon_cat, weapon_override)
    range_m = float(actor.get("range_mod") or 1.0)
    exp = int(actor.get("exp") or 0)
    _base = {
        "exp_mod": round(1.0 + exp / 500.0, 3),
        "status_mod": status_m,
        "form_mod": form_m,
        "variant_mod": variant_m,
        "range_mod": range_m,
        "ambush_mod": ambush_m,
    }
    if actor.get("action_type") == "domain":
        dom = actor.get("domain") or {}
        domain_m = float(dom.get("_domain_mod") or 1.0)
        precision = float(dom.get("precision") or 1.0)
        int_h = _stat_hidden(actor, "智力")
        bp = compute_domain_bp(
            precision,
            int_h,
            exp,
            domain_m=domain_m,
            status_m=status_m,
            form_m=form_m,
            variant_m=variant_m,
            range_m=range_m,
            ambush_m=ambush_m,
        )
        name = dom.get("name") or actor.get("skill")
        return {
            "char": actor.get("name"), "skill": name, "bp": bp, "kind": "domain",
            "domain_mod": domain_m, "precision": precision,
            "breakdown": {
                **_base, "stat_hidden": int_h,
                "domain_mod": domain_m, "precision": precision,
            },
        }
    str_h = _stat_hidden(actor, "力量")
    if actor.get("action_type") == "melee" or not skill or skill.get("kind") == "melee":
        bp = compute_melee_bp(
            weapon,
            str_h,
            int(actor.get("martial") or 0),
            exp,
            domain_m=float(actor.get("domain_mod") or 1.0),
            status_m=status_m,
            form_m=form_m,
            variant_m=variant_m,
            range_m=range_m,
            ambush_m=ambush_m,
        )
        return {
            "char": actor.get("name"), "skill": skill.get("name") if skill else "普通攻擊",
            "bp": bp, "kind": "melee", "element": "",
            "breakdown": {
                **_base, "weapon_base": weapon, "str_hidden": str_h,
                "martial": int(actor.get("martial") or 0),
            },
        }
    elif skill.get("kind") == "manamelee":
        int_h = _stat_hidden(actor, "智力")
        bp = compute_manamelee_bp(
            weapon,
            str_h,
            int_h,
            float(skill.get("mp_invested") or 0.0),
            int(actor.get("martial") or 0),
            exp,
            element_m=elem_m,
            proficiency_m=float(skill.get("proficiency_mod") or 1.0),
            status_m=status_m,
            form_m=form_m,
            variant_m=variant_m,
            range_m=range_m,
            ambush_m=ambush_m,
        )
        return {
            "char": actor.get("name"), "skill": skill.get("name"), "bp": bp,
            "kind": "manamelee", "element": str(skill.get("element") or "").strip(),
            "breakdown": {
                **_base, "weapon_base": weapon, "str_hidden": str_h,
                "int_hidden": int_h, "mp_invested": float(skill.get("mp_invested") or 0.0),
                "element_mod": elem_m,
                "proficiency_mod": float(skill.get("proficiency_mod") or 1.0),
                "martial": int(actor.get("martial") or 0),
            },
        }
    else:
        int_h = _stat_hidden(actor, "智力")
        bp = compute_atk_bp(
            float(skill.get("power_base") or 1.0),
            int_h,
            exp,
            element_m=elem_m,
            domain_m=float(actor.get("domain_mod") or 1.0),
            proficiency_m=float(skill.get("proficiency_mod") or 1.0),
            status_m=status_m,
            form_m=form_m,
            variant_m=variant_m,
            range_m=range_m,
            ambush_m=ambush_m,
        )
        return {
            "char": actor.get("name"), "skill": skill.get("name"), "bp": bp,
            "kind": "magic", "element": str(skill.get("element") or "").strip(),
            "breakdown": {
                **_base, "skill_base": float(skill.get("power_base") or 1.0),
                "stat_hidden": int_h,
                "element_mod": elem_m,
                "domain_mod": float(actor.get("domain_mod") or 1.0),
                "proficiency_mod": float(skill.get("proficiency_mod") or 1.0),
            },
        }


def compute_sides(
    atk: dict,
    def_: dict,
    status_a: float,
    status_d: float,
    form_a: float,
    form_d: float,
    variant_a: float,
    variant_d: float,
    ambush_a: float = 1.0,
    ambush_d: float = 1.0,
) -> tuple[dict, dict]:
    """計算雙方最終 BP（同一公式）。Atk 較大者為攻，較小者為守。

    ambush_a / ambush_d：偷襲劣勢倍率（被偷襲方 <1.0，如 0.8；無偷襲為 1.0）。
    """
    a_skill = atk.get("skill_data") or {}
    d_skill = def_.get("skill_data") or {}
    # 元素相剋機械計算：本方攻擊元素 vs 對方招式元素
    # （例：水剋火 → 火攻水 0.8、水攻火 1.2；任一方無元素則 1.0）
    a_elem = str(a_skill.get("element") or "").strip()
    d_elem = str(d_skill.get("element") or "").strip()
    a_elem_m = element_mod(a_elem, d_elem)
    d_elem_m = element_mod(d_elem, a_elem)

    def _one(
        actor: dict,
        skill: dict,
        status_m: float,
        form_m: float,
        variant_m: float,
        ambush_m: float,
        elem_m: float,
    ) -> dict:
        return compute_actor_bp(
            actor, skill, status_m, form_m, variant_m, ambush_m, elem_m
        )

    a = _one(atk, a_skill, status_a, form_a, variant_a, ambush_a, a_elem_m)
    d = _one(def_, d_skill, status_d, form_d, variant_d, ambush_d, d_elem_m)
    if d["bp"] > a["bp"]:
        a, d = d, a  # 威力大者為攻
    return a, d


async def phase_b(
    phase_a: dict,
    db_lookup: Optional[DbLookup],
    *,
    atk_override: Optional[dict] = None,
    def_override: Optional[dict] = None,
) -> dict:
    """計算雙方 BP、判定攻守與階級，並請 LLM 產出公開思維鏈。"""
    atk_actor = atk_override or _resolve_action(phase_a, "attacker", db_lookup)
    def_actor = def_override or _resolve_action(phase_a, "defender", db_lookup)

    atk_char = (await db_lookup(atk_actor["char"], "character")) if db_lookup else atk_actor
    def_char = (await db_lookup(def_actor["char"], "character")) if db_lookup else def_actor
    atk_char = {**(atk_char or {}), "name": atk_char.get("name") if atk_char else atk_actor["char"]}
    def_char = {**(def_char or {}), "name": def_char.get("name") if def_char else def_actor["char"]}

    atk_actor = await _resolve_skill_or_domain({**atk_actor, **atk_char}, db_lookup)
    def_actor = await _resolve_skill_or_domain({**def_actor, **def_char}, db_lookup)

    status = phase_a.get("status") or {}
    form_mods = phase_a.get("form_mods") or {}
    variant_mods = phase_a.get("variant_mods") or {}
    distance = phase_a.get("distance") or {}
    distance_rm = distance.get("range_mod") or {}
    ambush = phase_a.get("ambush") or {}
    ambush_active = bool(ambush.get("active"))

    # 偷襲劣勢：target 為被偷襲方，套用 0.8
    ambush_a = ambush_d = 1.0
    if ambush_active:
        tgt = (ambush.get("target") or "").strip()
        if tgt == atk_actor["char"]:
            ambush_a = 0.8
        elif tgt == def_actor["char"]:
            ambush_d = 0.8
        else:
            ambush_a = 0.8  # 無法對應時預設劣勢於攻擊方候選（Phase A 應更正）

    atk_actor, def_actor = await _resolve_range_mods(atk_actor, def_actor, distance)

    a, d = compute_sides(
        atk_actor,
        def_actor,
        float(status.get("attacker", {}).get("status_mod", 1.0)),
        float(status.get("defender", {}).get("status_mod", 1.0)),
        float(form_mods.get("attacker", 1.0)),
        float(form_mods.get("defender", 1.0)),
        float(variant_mods.get("attacker", 1.0)),
        float(variant_mods.get("defender", 1.0)),
        ambush_a=ambush_a,
        ambush_d=ambush_d,
    )

    # 招式效果（Phase A2 解析 → 全機械套用，數值決定勝負）
    status_declared = phase_a.get("statuses") or {}
    try:
        effects_by_side = await parse_effects(atk_actor, def_actor, status_declared)
    except ClashError as _e:
        effects_by_side = {"attacker": [], "defender": []}
        phase_a.setdefault("warnings", []).append(f"招式效果解析失敗，本次以無效果計算：{_e}")
    effects_by_char = _effects_by_char(phase_a, effects_by_side, atk_actor, def_actor)
    atk_fx = effects_by_char.get(a["char"], [])
    def_fx = effects_by_char.get(d["char"], [])
    a_bp, d_bp, fx_log = apply_effects(
        a["bp"], d["bp"], atk_fx, def_fx,
        atk_kind=a.get("kind", "magic"), def_kind=d.get("kind", "magic"),
        atk_element=a.get("element") or "", def_element=d.get("element") or "",
    )
    flipped = d_bp > a_bp
    if flipped:
        a, d = d, a
        a_bp, d_bp = d_bp, a_bp
    a["bp"] = a_bp
    d["bp"] = d_bp

    domain_a = float(atk_actor.get("domain_mod") or 1.0)
    domain_d = float(def_actor.get("domain_mod") or 1.0)
    mp_invested = int((atk_actor.get("mp_invested") or 0) + (def_actor.get("mp_invested") or 0))
    verdict, fusion, gap = decide_with_domain(a["bp"], d["bp"], domain_a, domain_d, mp_invested)

    # 留手規則：贏方若主張「留手」（只想擊退/點到為止/不傷性命），
    # 即使數值足以碾壓，判定降為優勢壓制——出招者成功留手，達成其主張的目標。
    # 反向（誇大戰果）不成立：機械數值為準。
    intended = phase_a.get("intended_outcomes") or {}
    winner_force = None
    winner_desc = ""
    _resolved_side_char = {
        "attacker": atk_actor.get("name") or atk_actor.get("char"),
        "defender": def_actor.get("name") or def_actor.get("char"),
    }
    for side in ("attacker", "defender"):
        if _resolved_side_char.get(side) == a["char"]:
            wi = intended.get(side) or {}
            winner_force = str(wi.get("force") or "").strip()
            winner_desc = str(wi.get("desc") or "").strip()
            break
    holding_back = False
    if winner_force == "留手" and verdict == VERDICT_DECISIVE:
        verdict = VERDICT_SUPPRESS
        holding_back = True

    fusion_effects = None
    domains_view = None
    if verdict == VERDICT_FUSION:
        da = atk_actor.get("domain") if atk_actor.get("action_type") == "domain" else None
        db_ = def_actor.get("domain") if def_actor.get("action_type") == "domain" else None
        fusion_effects = domain_fusion_effects(da, db_)
        domains_view = {"attacker": da or {}, "defender": db_ or {}}

    _rmap = {
        atk_actor["char"]: {"mod": atk_actor.get("range_mod", 1.0), "reason": atk_actor.get("range_reason", "")},
        def_actor["char"]: {"mod": def_actor.get("range_mod", 1.0), "reason": def_actor.get("range_reason", "")},
    }
    _tags_map = {}
    for _ch in (atk_char, def_char):
        if _ch:
            _tags_map[_ch.get("name")] = [t for t in (_ch.get("special_tags") or []) if isinstance(t, str) and t]
    result = {
        "computation": {"attacker": a, "defender": d},
        "attacker": a["char"],
        "defender": d["char"],
        "verdict": verdict,
        "domain_fusion": fusion,
        "special_tags": {
            "attacker": _tags_map.get(a["char"], []),
            "defender": _tags_map.get(d["char"], []),
        },
        "ambush": {"active": ambush_active, "target": (ambush.get("target") if ambush_active else None),
                    "desc": (ambush.get("desc") if ambush_active else None)},
        "distance": {"desc": distance.get("desc"), "range_mod": distance_rm},
        "range": {
            "attacker": _rmap.get(a["char"], {"mod": 1.0, "reason": ""}),
            "defender": _rmap.get(d["char"], {"mod": 1.0, "reason": ""}),
        },
        "domain_override": {
            "applied": gap >= 0.3,
            "mp_invested": mp_invested,
            "effective_gap": gap,
            "result": "融合" if fusion else "覆蓋",
        },
        "domains": domains_view,
        "fusion_effects": fusion_effects,
        "chain_of_thought": "",
        "intended_outcomes": intended,
        "holding_back": holding_back,
        "winner_intent": {"force": winner_force or "", "desc": winner_desc or ""},
        "effects": {
            "attacker": effects_by_char.get(a["char"], []),
            "defender": effects_by_char.get(d["char"], []),
            "applied": fx_log,
            "flipped": flipped,
            "declared_statuses": {
                side: [s for s in (status_declared.get(side) or []) if isinstance(s, str) and s]
                for side in ("attacker", "defender")
            },
        },
        "warnings": phase_a.get("warnings") or [],
    }

    explained = await _call_json(
        CLASH_PHASE_B_SYSTEM,
        json.dumps(result, ensure_ascii=False, indent=2),
        max_tokens=10000,
        label="Phase B",
    )
    result["chain_of_thought"] = explained.get("chain_of_thought") or ""
    if explained.get("remaining_damage"):
        result["remaining_damage"] = explained["remaining_damage"]
    if explained.get("result"):
        result["result"] = explained["result"]
    if explained.get("warnings"):
        result["warnings"].extend(explained["warnings"])
    return result


# ── Phase C：文風輸出 ──────────────────────────────────────

def _clash_result_view(computation: dict) -> dict:
    """Phase C 只需要判定結果，不含計算細節（BP/breakdown/chain_of_thought）。"""
    comp = computation.get("computation") or {}
    pick = lambda side: (
        {"char": comp[side].get("char"), "skill": comp[side].get("skill")}
        if comp.get(side) else None
    )
    return {
        "verdict": computation.get("verdict", ""),
        "result": computation.get("result") or {},
        "attacker": pick("attacker"),
        "defender": pick("defender"),
        "special_tags": computation.get("special_tags") or {},
        "domain_fusion": bool(computation.get("domain_fusion")),
        "domain_override": computation.get("domain_override"),
        "domains": computation.get("domains"),
        "fusion_effects": computation.get("fusion_effects"),
        "remaining_damage": computation.get("remaining_damage", ""),
        "effects": computation.get("effects") or {},
    }


async def phase_c(computation: dict, style_rules: str) -> str:
    """確認對撞結果後，依文風規則輸出正文文本（只送判定結果，不含計算細節）。"""
    prompt = (
        f"【對撞計算結果】\n{json.dumps(_clash_result_view(computation), ensure_ascii=False, indent=2)}\n\n"
        f"【文風規則】\n{style_rules}"
    )
    return await _call_llm(CLASH_PHASE_C_SYSTEM, prompt, temperature=0.9, max_tokens=6000, label="Phase C")


# ── 主流程 ─────────────────────────────────────────────────

# ── 主流程 ─────────────────────────────────────────────────

def _new_clash_battle_id(conn) -> str:
    """產生唯一 battle_id（與 collide_parser 同款）：clash-<時間戳>-<亂數>。"""
    import uuid

    for _ in range(5):
        ts = datetime.now().strftime("%Y%m%d%H%M%S")
        bid = f"clash-{ts}-{uuid.uuid4().hex[:6]}"
        if not collide_db.get_clash_battle(conn, bid):
            return bid
    return f"clash-{uuid.uuid4().hex[:12]}"


def _new_clash_node_id() -> str:
    """草稿期固定節點識別：同一草稿重複確認 → 同 node_id → Ledger 複合唯一鍵擋下。"""
    import uuid

    return f"d{uuid.uuid4().hex[:8]}"


def _clash_reason_line(
    bar: str,
    *,
    total_delta: int,
    chip: bool,
    at_retreat_floor: bool,
    outcome: str,
    villain: str,
) -> str:
    """血條 + 一行理由（spec 5.6.4 / 6.5）：終局用 outcome 標註，進行中依節點 delta。"""
    label = clash_outcome_label(outcome, villain)
    if not label:
        if total_delta > 0:
            kind = "全員僵持 chip" if chip else "破防"
            label = f"{kind} -{total_delta} ticks"
        else:
            label = "未造成有效傷害"
        if at_retreat_floor:
            label += "（已達擊退線，可宣告擊退）"
    return f"{bar} {label}".strip()


async def _find_clash_villain(a: dict, db_lookup: Optional[DbLookup]) -> dict | None:
    """從 Phase A 候選找出登記反派（is_villain）。反派置於 defender_candidate
    （兼容 villain_candidate）；回傳其角色列（含 id）或 None。"""
    for cand in (a.get("villain_candidate"), a.get("defender_candidate"), a.get("attacker_candidate")):
        if not isinstance(cand, dict):
            continue
        name = str(cand.get("char") or "").strip()
        if not name:
            continue
        ch = await db_lookup(name, "character") or {}
        if ch.get("is_villain"):
            return {**ch, "name": ch.get("name") or name}
    return None


async def _resolve_combatant_bp(combatant: dict, db_lookup: Optional[DbLookup]) -> dict | None:
    """單一玩家 combatant → 最終 BP（沿用 compute_actor_bp 同公式；供多打一）。

    多打一 v1：逐玩家各自解析招式（已登記或 Phase A 估算）與角色列 → BP；
    未提供之狀態/型態/變體/距離修正視為中性 1.0（Phase A 可選擇性覆寫）。
    """
    if not isinstance(combatant, dict):
        return None
    name = str(combatant.get("char") or "").strip()
    if not name:
        return None
    # 契約：combatant = {"char", "action": {"skill", "action_type", "holding_back", ...}}
    # 兼容扁平的 {"char", "skill", "action_type"} 形式。
    action = combatant.get("action")
    action_d = action if isinstance(action, dict) else {}
    skill_name = combatant.get("skill") or action_d.get("skill") or ""
    action_type = combatant.get("action_type") or action_d.get("action_type") or "skill"
    ch = await db_lookup(name, "character") or {}
    actor = {
        "char": name,
        "name": ch.get("name") or name,
        "skill": skill_name,
        "action_type": action_type,
        **ch,
    }
    for k in ("status_mod", "form_mod", "variant_mod", "range_mod", "domain_mod"):
        if combatant.get(k) is not None:
            actor[k] = combatant[k]
    actor = await _resolve_skill_or_domain(actor, db_lookup)
    skill = actor.get("skill_data") or {}
    elem = str(skill.get("element") or "").strip()
    opp = str(combatant.get("opponent_element") or "").strip()
    elem_m = element_mod(elem, opp) if (elem and opp) else 1.0
    res = compute_actor_bp(
        actor,
        skill,
        float(actor.get("status_mod") or 1.0),
        float(actor.get("form_mod") or 1.0),
        float(actor.get("variant_mod") or 1.0),
        float(combatant.get("ambush_mod") or 1.0),
        elem_m,
    )
    return {
        "char": str(res.get("char") or name),
        "bp": float(res.get("bp") or 0.0),
        "kind": res.get("kind"),
        "skill": res.get("skill"),
        "holding_back": bool(combatant.get("holding_back") or action_d.get("holding_back")),
    }


async def _settle_clash_after_phase_b(
    a: dict, b: dict, conn, db_lookup: Optional[DbLookup]
) -> dict | None:
    """Clash v2 結算投影（A3 運行時接線）：Phase B 後偵測反派並計算血條扣減。

    反派 = 登記反派（is_villain，優先 Phase A defender_candidate）。玩家 BP：
    **多打一優先**——Phase A 的 `combatants`（有序，僅玩家本人貼出的行動）逐人
    解析後各自算 BP，N 個一次送進 resolve_clash_node（引擎聚合）；無 combatants
    時退回單挑（Phase B 非反派側）。留手**逐玩家**各自宣告（不再有單一贏方假設）。

    找 active battle（無則依 parser 同款前例自動建立：guard = 體質隱藏 ×
    Domain_Mod 純計算、dynamic_difficulty=0、tier 依角色檔或預設「重點」）。

    本函式**不寫 Ledger**——只在草稿期回傳投影（hp_bar / reason_line 與
    player_bps 等 commit 所需資料）；實際原子寫入由確認端
    collide_cmds._commit_clash_settlement 執行，重試/取消草稿不會重複扣血。
    無反派對撞回 None。
    """
    if conn is None or db_lookup is None:
        return None

    villain_row = await _find_clash_villain(a, db_lookup)
    if villain_row is None:
        # 容錯：候選未標 is_villain 時，退回掃描 Phase B 兩側
        comp0 = b.get("computation") or {}
        for side in (comp0.get("attacker") or {}, comp0.get("defender") or {}):
            nm = str(side.get("char") or "").strip()
            if not nm:
                continue
            ch = await db_lookup(nm, "character") or {}
            if ch.get("is_villain"):
                villain_row = {**ch, "name": ch.get("name") or nm}
                break
    if villain_row is None:
        return None
    v_id = villain_row.get("id")
    if v_id is None:
        return None

    villain_name = str(villain_row.get("name") or "")

    # 玩家 BP：多打一優先（Phase A combatants，逐玩家各自宣告留手）；
    # 否則退回單挑（Phase B 非反派側，沿用舊行為）。
    player_entries = []
    combatants = a.get("combatants")
    has_combatants = isinstance(combatants, list) and bool(combatants)
    if has_combatants:
        for c in combatants:
            # 防 Phase A 目標混入：必須明確指向本節點反派；缺欄位也不能猜測歸屬。
            tgt = str((c or {}).get("target") or "").strip()
            if not tgt or not villain_name or tgt != villain_name:
                continue
            entry = await _resolve_combatant_bp(c, db_lookup)
            if entry:
                player_entries.append(entry)
    if not has_combatants:
        winner = str(b.get("attacker") or "")
        overall_hb = bool(b.get("holding_back"))
        comp = b.get("computation") or {}
        for side in (comp.get("attacker") or {}, comp.get("defender") or {}):
            nm = str(side.get("char") or "").strip()
            if not nm:
                continue
            ch = await db_lookup(nm, "character") or {}
            if ch.get("is_villain"):
                continue
            player_entries.append({
                "char": str(ch.get("name") or nm),
                "bp": float(side.get("bp") or 0.0),
                "holding_back": overall_hb if nm == winner else False,
            })

    if not player_entries:
        if not has_combatants:
            return None
        # 有 combatants 卻無一筆對上本節點反派 → **出聲**：回傳可見 warning，
        # 絕不靜默、也絕不退回單挑（那會把傷害扣在錯的規則／對象上）。
        warning = (
            f"⚠️ 多打一參與者無法對應本節點反派「{villain_name or '（未知）'}」"
            "（Phase A 可能未吐 target、或目標不符）；本節點未產生血條、未扣血。"
        )
        log_warning = warning.encode("ascii", "backslashreplace").decode("ascii")
        logging.getLogger("collide.pipeline").warning(log_warning)
        return {
            "warning": warning,
            "aggressors": [],
            "player_bps": [],
            "villain": villain_name,
        }

    battle = collide_db.find_active_clash_for_villain(conn, v_id)
    if battle is None:
        v_name = villain_name or "反派"
        guard = compute_villain_guard(
            _hidden_of(villain_row, "體質"), float(villain_row.get("domain_mod") or 1.0)
        )
        tier = str(villain_row.get("tier") or villain_row.get("clash_tier") or "重點").strip()
        spec = CLASH_TIER_SPECS.get(tier, CLASH_TIER_SPECS["重點"])
        battle = collide_db.create_clash_battle(
            conn,
            battle_id=_new_clash_battle_id(conn),
            villain_id=int(v_id),
            villain_name=v_name,
            tier=tier,
            max_hp_ticks=int(spec["max_hp_ticks"]),
            cap_ticks=int(spec["cap_ticks"]),
            guard_value=guard,
            retreat_floor_ticks=retreat_floor_ticks(int(spec["max_hp_ticks"])),
            source="auto_detect",
            dynamic_difficulty=0,
        )

    player_bps = [e["bp"] for e in player_entries]
    holding_back = [bool(e.get("holding_back")) for e in player_entries]
    aggressors = [e["char"] for e in player_entries]

    settled = resolve_clash_node(
        player_bps,
        float(battle["guard_value"]),
        cap_ticks=int(battle["cap_ticks"]),
        current_hp_ticks=int(battle["current_hp_ticks"]),
        retreat_floor=int(battle["retreat_floor_ticks"]),
        holding_back=holding_back,
    )
    bar = format_clash_hp_bar(
        int(settled["new_hp_ticks"]), int(battle["max_hp_ticks"])
    )
    vname = str(battle.get("villain_name") or villain_row.get("name") or "反派")
    ostate = str(battle.get("outcome") or CLASH_OUTCOME_ACTIVE)
    return {
        "battle_id": battle["battle_id"],
        "node_id": _new_clash_node_id(),
        "villain": vname,
        "tier": battle.get("tier", ""),
        "player_bps": player_bps,
        "holding_back": holding_back,
        "aggressors": aggressors,
        "guard_value": float(battle["guard_value"]),
        "cap_ticks": int(battle["cap_ticks"]),
        "retreat_floor_ticks": int(battle["retreat_floor_ticks"]),
        "verdicts": settled["verdicts"],
        "total_delta": int(settled["total_delta"]),
        "chip": bool(settled["chip"]),
        "at_retreat_floor": bool(settled["at_retreat_floor"]),
        "hp_bar": bar,
        "reason_line": _clash_reason_line(
            bar,
            total_delta=int(settled["total_delta"]),
            chip=bool(settled["chip"]),
            at_retreat_floor=bool(settled["at_retreat_floor"]),
            outcome=ostate,
            villain=vname,
        ),
    }


async def run_clash(
    history: str,
    style_rules: str,
    db_lookup: Optional[DbLookup] = None,
    *,
    conn=None,
) -> dict:
    """完整對撞流程：Phase A → B → C。回傳含計算與最終文本的 dict。

    若 Phase A 判斷雙方皆為非對抗性動作（no_clash），直接回報「無對撞發生」，
    不進入計算與文風輸出。

    conn 提供時，Phase B 後自動執行 Clash v2 結算投影（偵測反派 → 血條扣減
    計算，見 _settle_clash_after_phase_b），結果掛在回傳 dict 的 "clash"；
    實際 Ledger 寫入於草稿確認時由 collide_cmds 執行。
    """
    a = await phase_a(history, db_lookup)
    if a.get("no_clash"):
        return {"phase_a": a, "phase_b": None, "phase_c": "無對撞發生", "no_clash": True}
    b = await phase_b(a, db_lookup)
    result = {"phase_a": a, "phase_b": b}
    if conn is not None:
        result["clash"] = await _settle_clash_after_phase_b(a, b, conn, db_lookup)
    result["phase_c"] = await phase_c(b, style_rules)
    return result


def _json_or_raise(raw: str, phase: str) -> dict:
    import json as _json

    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = _json.loads(text)
    except _json.JSONDecodeError as e:
        raise ClashError(f"{phase} 輸出無法解析為 JSON：{e}")
    if not isinstance(parsed, dict):
        raise ClashError(f"{phase} 輸出不是 JSON 物件")
    return parsed
