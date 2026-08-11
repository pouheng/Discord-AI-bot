"""
collide_pipeline.py — 對撞三階段流程（串接提示詞與引擎）

Phase A：預處理（LLM 分析討論串 → 判斷對抗雙方/招式/狀態）
Phase B：計算（collide_engine 純函式算 BP + LLM 產出公開思維鏈）
Phase C：文風輸出（確認後，依文風規則生成對撞文本）

資料來源透過 db_lookup callable 注入，實作於 collide_db.py（make_db_lookup）。
"""

import asyncio
import json
import os
from typing import Any, Awaitable, Callable, Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI

from collide_engine import (
    VERDICT_FUSION,
    compute_atk_bp,
    compute_domain_bp,
    compute_manamelee_bp,
    compute_melee_bp,
    decide_with_domain,
    domain_fusion_effects,
    domain_mod,
    hidden_score,
)
from collide_rules import (
    CLASH_PHASE_A_SYSTEM,
    CLASH_PHASE_B_SYSTEM,
    CLASH_PHASE_C_SYSTEM,
    RANGE_MOD_SYSTEM,
)

load_dotenv()
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_client = (
    AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
    if DEEPSEEK_API_KEY
    else None
)

# db_lookup: async (char_name, table) -> row dict or None
DbLookup = Callable[[str, str], Awaitable[Optional[dict]]]


class ClashError(Exception):
    pass


async def _call_llm(system: str, user: str, *, temperature: float = 0.0, max_tokens: int = 6000) -> str:
    if not _client:
        raise ClashError("AI 服務未啟用，請檢查 DEEPSEEK_API_KEY 設定")
    # deepseek-v4-flash 為 reasoning 型，reasoning_content 吃 token 預算，
    # 若 content 為空（預算被推論吃光）則放大預算重試，最多 3 次。
    last = ""
    for attempt in range(3):
        budget = min(max_tokens * (2 ** attempt), 32000)
        resp = await _client.chat.completions.create(
            model="deepseek-v4-flash",
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
            return content
        await asyncio.sleep(0.5)
    return last


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
            system, user, temperature=temperature, max_tokens=max_tokens * (2 ** attempt)
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
    return {
        "char": char,
        "skill": cand.get("skill"),
        "action_type": cand.get("action_type") or "skill",
        "is_player": cand.get("is_player", False),
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
        out["skill_data"] = sd
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

    def _one(
        actor: dict,
        skill: dict,
        status_m: float,
        form_m: float,
        variant_m: float,
        ambush_m: float,
    ) -> dict:
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
                "bp": bp, "kind": "melee",
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
                element_m=float(skill.get("element_mod") or 1.0),
                proficiency_m=float(skill.get("proficiency_mod") or 1.0),
                status_m=status_m,
                form_m=form_m,
                variant_m=variant_m,
                range_m=range_m,
                ambush_m=ambush_m,
            )
            return {
                "char": actor.get("name"), "skill": skill.get("name"), "bp": bp,
                "kind": "manamelee",
                "breakdown": {
                    **_base, "weapon_base": weapon, "str_hidden": str_h,
                    "int_hidden": int_h, "mp_invested": float(skill.get("mp_invested") or 0.0),
                    "element_mod": float(skill.get("element_mod") or 1.0),
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
                element_m=float(skill.get("element_mod") or 1.0),
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
                "kind": "magic",
                "breakdown": {
                    **_base, "skill_base": float(skill.get("power_base") or 1.0),
                    "stat_hidden": int_h,
                    "element_mod": float(skill.get("element_mod") or 1.0),
                    "domain_mod": float(actor.get("domain_mod") or 1.0),
                    "proficiency_mod": float(skill.get("proficiency_mod") or 1.0),
                },
            }

    a = _one(atk, a_skill, status_a, form_a, variant_a, ambush_a)
    d = _one(def_, d_skill, status_d, form_d, variant_d, ambush_d)
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

    domain_a = float(atk_actor.get("domain_mod") or 1.0)
    domain_d = float(def_actor.get("domain_mod") or 1.0)
    mp_invested = int((atk_actor.get("mp_invested") or 0) + (def_actor.get("mp_invested") or 0))
    verdict, fusion, gap = decide_with_domain(a["bp"], d["bp"], domain_a, domain_d, mp_invested)

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
    result = {
        "computation": {"attacker": a, "defender": d},
        "attacker": a["char"],
        "defender": d["char"],
        "verdict": verdict,
        "domain_fusion": fusion,
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
        "domain_fusion": bool(computation.get("domain_fusion")),
        "domain_override": computation.get("domain_override"),
        "domains": computation.get("domains"),
        "fusion_effects": computation.get("fusion_effects"),
        "remaining_damage": computation.get("remaining_damage", ""),
    }


async def phase_c(computation: dict, style_rules: str) -> str:
    """確認對撞結果後，依文風規則輸出正文文本（只送判定結果，不含計算細節）。"""
    prompt = (
        f"【對撞計算結果】\n{json.dumps(_clash_result_view(computation), ensure_ascii=False, indent=2)}\n\n"
        f"【文風規則】\n{style_rules}"
    )
    return await _call_llm(CLASH_PHASE_C_SYSTEM, prompt, temperature=0.9, max_tokens=6000)


# ── 主流程 ─────────────────────────────────────────────────

async def run_clash(history: str, style_rules: str, db_lookup: Optional[DbLookup] = None) -> dict:
    """完整對撞流程：Phase A → B → C。回傳含計算與最終文本的 dict。

    若 Phase A 判斷雙方皆為非對抗性動作（no_clash），直接回報「無對撞發生」，
    不進入計算與文風輸出。
    """
    a = await phase_a(history, db_lookup)
    if a.get("no_clash"):
        return {"phase_a": a, "phase_b": None, "phase_c": "無對撞發生", "no_clash": True}
    b = await phase_b(a, db_lookup)
    text = await phase_c(b, style_rules)
    return {"phase_a": a, "phase_b": b, "phase_c": text}


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
