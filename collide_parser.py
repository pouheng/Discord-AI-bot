"""
collide_parser.py — 自然語言 → 結構化資料的寫入解析器

流程：
1. 依目標類型組裝寫入規範 prompt（collide_rules.WRITE_RULES）
2. 呼叫 LLM 產出 JSON
3. 程式硬驗證（數值範圍 + enum + 必要欄位），不合格直接擋下，不靠 LLM 自覺
"""

import json
import os
import datetime
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from llm_models import chat_create, has_credentials

import collide_db
from collide_engine import (
    CLASH_TIER_SPECS,
    STAT_KEYS,
    _hidden_of,
    compute_villain_guard,
    retreat_floor_ticks,
)
from collide_rules import (
    ABILITY_CATEGORIES,
    BOUNDS,
    ENUMS,
    RACE_MOD_SYSTEM,
    RATING_CONST,
    SKILL_DERIVATION_SYSTEM,
    WRITE_RULES,
)

load_dotenv()
# 客戶端改由 llm_models.get_client() 依來源即時提供（見下方呼叫點）。

VALID_TARGETS = {"character", "ability", "skill", "domain", "clash_villain"}


def _log_parse_failure(target: str, raw: str, reason: str, text: str = "") -> None:
    """把解析失敗的原始 LLM 輸出寫入 error_logs/{date}.txt，供事後除錯。

    寫入失敗/無目錄權限時靜默（不影響主流程）。
    """
    try:
        log_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "error_logs"
        )
        os.makedirs(log_dir, exist_ok=True)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        path = os.path.join(log_dir, f"{today}.txt")
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(
                f"[{now}] [parse_failure] target={target} reason={reason}\n"
                f"INPUT: {text[:800]}\n"
                f"RAW  : {raw[:1200]}\n"
                f"{'-' * 40}\n"
            )
    except Exception:
        pass


class ParseError(Exception):
    """解析或驗證失敗。message 為可回報給使用者的說明。"""


def _strip_json(text: str) -> str:
    """移除 LLM 輸出中的 ```json ... ``` 包裝。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


_BATCH_WRAP_KEYS = {
    "entries": None,  # 元素已含 type
    "skills": "skill",
    "abilities": "ability",
    "characters": "character",
    "domains": "domain",
}


def _normalize_batch(parsed) -> list:
    """把各種批次形狀轉成「每筆含 type」的陣列；不合法直接拋 ParseError。

    接受：`[{type:...}]`、`{"type": ...}`（單筆）、
    `{"entries": [...]}`、`{"skills": [...]}` / `{"abilities": [...]}` /
    `{"characters": [...]}` / `{"domains": [...]}`（由複數鍵補 type）。
    """
    if isinstance(parsed, dict):
        for key, t in _BATCH_WRAP_KEYS.items():
            v = parsed.get(key)
            if isinstance(v, list):
                parsed = v
                if t:
                    parsed = [
                        {**e, "type": t} if isinstance(e, dict) else e
                        for e in parsed
                    ]
                break
        else:
            if parsed.get("type"):
                parsed = [parsed]
            else:
                raise ParseError("單筆資料須含 type（character/ability/skill/domain）")
    if not isinstance(parsed, list) or not parsed:
        raise ParseError("批次寫入需提供非空 JSON 陣列（每筆含 type 欄位）")
    return parsed


def _extract_batch(parsed) -> tuple[str | None, list | None]:
    """LLM 對單一目標指令回傳批次包裝（如 {"skills": [...]}）時，回傳 (type, list)。

    一般單筆物件回傳 (None, None)。
    """
    if isinstance(parsed, dict):
        for key, t in _BATCH_WRAP_KEYS.items():
            v = parsed.get(key)
            if isinstance(v, list) and v:
                return t, v
    return None, None


def _check_bounds(data: dict, target: str) -> list[str]:
    """對已解析的資料做數值範圍與 enum 硬驗證，回傳錯誤清單。"""
    errors: list[str] = []
    if target == "character":
        stats = data.get("stats") or {}
        for key, val in stats.items():
            if val not in RATING_CONST:
                errors.append(f"六維評級「{key}」的值「{val}」不合法（僅 E/D/C/B/A/S）")
        for field in ("max_mp", "martial", "exp"):
            _check_int(data, field, BOUNDS[field], errors)
        for form, mult in (data.get("special_forms") or {}).items():
            if not (isinstance(mult, (int, float)) and 1.1 <= mult <= 1.5):
                errors.append(f"特殊型態「{form}」的倍率 {mult} 超出 1.1~1.5")
        for flag in ("is_teacher", "is_villain", "dynamic_difficulty"):
            if flag in data and not isinstance(data[flag], bool):
                errors.append(f"{flag} 必須是布林（true/false）")
        if data.get("dynamic_difficulty") and not data.get("calibrate_roster"):
            errors.append("dynamic_difficulty 為 true 時 calibrate_roster（該篇章預定上場角色）不可為空")
        roster = data.get("calibrate_roster")
        if roster is not None:
            if not isinstance(roster, list) or not all(isinstance(r, str) and r.strip() for r in roster):
                errors.append("calibrate_roster 必須是非空字串的陣列（預定上場角色名）")
    elif target == "ability":
        if not data.get("name"):
            errors.append("能力名稱不可為空")
        if not data.get("principle"):
            errors.append("能力原理（principle）不可為空")
        category = str(data.get("category") or "").strip()
        if category and category not in ABILITY_CATEGORIES:
            errors.append(
                f"能力分類「{category}」不在建議分類（{'/'.join(ABILITY_CATEGORIES)}）中，可自訂或改用複合型"
            )
    elif target == "skill":
        for field in ("mp_cost",):
            _check_int(data, field, BOUNDS[field], errors)
        if not isinstance(data.get("power_base"), (int, float)):
            errors.append("招式威力基數 power_base 必須是數字")
        else:
            lo, hi = BOUNDS["power_base"]
            if not (lo <= data["power_base"] <= hi):
                errors.append(f"招式威力基數 {data['power_base']} 超出 {lo}~{hi}（10.0 為表/裡世界天花板）")
        stype = str(data.get("skill_type") or "").strip()
        if stype not in ENUMS["skill_type"]:
            errors.append(f"skill_type「{data.get('skill_type')}」不合法（僅 {'/'.join(ENUMS['skill_type'])}）")
        msys = str(data.get("magic_system") or "").strip()
        elem = str(data.get("element") or "").strip()
        if stype == "個人魔法":
            if msys not in ENUMS["magic_system"]:
                errors.append(f"個人魔法必須屬於魔法體系（magic_system）之一：{'/'.join(ENUMS['magic_system'])}")
            if msys == "自然系" and not elem:
                errors.append("自然系招式必須填寫元素（element）；超能系／療癒系／跨系融合不需元素")
        elif msys:
            errors.append("僅個人魔法會劃分魔法體系（magic_system）；通常魔法為三大系之外的通用魔法，magic_system 應留空")
        if not data.get("name"):
            errors.append("招式名稱不可為空")
        if stype == "個人魔法" and not data.get("ability"):
            errors.append("個人魔法必須屬於已登記能力（ability）；通常魔法為通用魔法不需掛能力")
        if not str(data.get("application") or "").strip():
            errors.append("擴展應用分類（application）不可為空（例如「擴展一：以造成傷害為前提的能力放出」）")
    elif target == "domain":
        if not isinstance(data.get("precision"), (int, float)):
            errors.append("結界術精度 precision 必須是數字")
        else:
            lo, hi = BOUNDS["precision"]
            if not (lo <= data["precision"] <= hi):
                errors.append(f"結界術精度 {data['precision']} 超出 {lo}~{hi}")
        if data.get("awakening") not in ENUMS["awakening"]:
            errors.append(f"覺醒進度「{data.get('awakening')}」不合法（僅 {'/'.join(ENUMS['awakening'])}）")
        if not data.get("name"):
            errors.append("結界名稱不可為空")
    elif target == "clash_villain":
        if not str(data.get("name") or "").strip():
            errors.append("反派角色名不可為空")
        tier = str(data.get("tier") or "").strip()
        if tier not in CLASH_TIER_SPECS:
            errors.append(f"階級「{tier}」不合法（僅 {'/'.join(CLASH_TIER_SPECS)}）")
        if data.get("dynamic_difficulty") is True:
            errors.append(
                "dynamic_difficulty 必須為 false：自動偵測的反派 guard 純粹由體質隱藏分數 × "
                "Domain_Mod 計算，不進入 calibrate_roster 校準流程"
            )
        dm = data.get("domain_mod")
        if dm is not None and not (isinstance(dm, (int, float)) and 1.0 <= dm <= 1.5):
            errors.append(f"domain_mod {dm} 超出 1.0~1.5（未開結界 1.0）")
        gv = data.get("guard_value")
        if gv is not None and not (isinstance(gv, (int, float)) and gv > 0):
            errors.append(f"guard_value {gv!r} 必須是正數")
        for field in ("max_hp_ticks", "cap_ticks"):
            if data.get(field) is not None:
                _check_int(data, field, (1, 10**6), errors)
    return errors


def _check_int(data: dict, field: str, bounds: tuple, errors: list[str]):
    val = data.get(field)
    if not isinstance(val, int) or isinstance(val, bool):
        errors.append(f"{field} 必須是整數（收到 {val!r}）")
        return
    lo, hi = bounds
    if not (lo <= val <= hi):
        errors.append(f"{field} {val} 超出 {lo}~{hi}")


def validate(data: dict, target: str, known_abilities: set | None = None) -> list[str]:
    """公開驗證介面：回傳錯誤清單（空 = 通過）。

    known_abilities: 能力庫名稱集合（collide_db.list_abilities）。提供時，
    招式必須掛在已登記的能力下，否則視為錯誤（能力原理須先建檔）。
    """
    if target not in VALID_TARGETS:
        return [f"不支援的目標類型：{target}"]
    errors = _check_bounds(data, target)
    if target == "skill" and known_abilities:
        ability = str(data.get("ability") or "").strip()
        if ability and ability not in known_abilities:
            errors.append(
                f"所屬能力「{ability}」未在能力庫（collide_abilities）登記，請先新增該能力（含能力原理）"
            )
    return errors


async def parse_natural_language(text: str, target: str, *, retries: int = 2) -> dict:
    """將自然語言描述解析為結構化 JSON，並做硬驗證。

    Args:
        text: 管理員的自然語言描述。
        target: character / ability / skill / domain。
        retries: 驗證失敗時的重試次數。

    Returns:
        結構化 dict（含 LLM 標記的 _errors 欄位則一併保留）。

    Raises:
        ParseError: LLM 無法產出合法 JSON 或多次驗證失敗。
    """
    if target not in VALID_TARGETS:
        raise ParseError(f"不支援的目標類型：{target}")
    if not has_credentials():
        raise ParseError("AI 解析服務未啟用，請檢查 DEEPSEEK_API_KEY 設定")

    system_prompt = WRITE_RULES[target]
    user_prompt = f"請解析以下描述：\n{text}"

    last_error = "未知錯誤"
    last_raw = ""
    for attempt in range(retries + 1):
        try:
            resp = await chat_create(
                "collide",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=4000,
            )
            raw = (resp.choices[0].message.content or "").strip()
            parsed = json.loads(_strip_json(raw))
        except json.JSONDecodeError as e:
            last_error = f"LLM 輸出無法解析為 JSON：{e}"
            last_raw = raw
            _log_parse_failure(target, raw, f"JSON decode: {e}", text=text)
            continue
        except Exception as e:
            raise ParseError(f"AI 解析呼叫失敗：{e}")

        if not isinstance(parsed, dict):
            last_error = "LLM 輸出不是 JSON 物件"
            last_raw = raw
            _log_parse_failure(target, raw, "not a JSON object", text=text)
            continue

        batch_type, batch_list = _extract_batch(parsed)
        if batch_list is not None:
            # LLM 把多筆資料一次回傳（批次格式）→ 單筆指令無法處理，直接給指引
            raise ParseError(
                "LLM 回傳了多筆資料（批次格式）。此指令一次只處理一筆，"
                "請改用 /writebatch 批次寫入，或一次只描述一個"
                f"{target}。"
            )

        errors = _check_bounds(parsed, target)
        if errors:
            parsed["_errors"] = errors
            last_error = "；".join(errors)
            last_raw = raw
            _log_parse_failure(target, raw, "validation failed: " + "；".join(errors), text=text)
            continue
        return parsed

    raise ParseError(f"多次驗證失敗（{retries + 1} 次）：{last_error}")


# ── 種族修正設計（AI 填入種族表，待人工審查）──────────────

async def propose_race_mods(race: str, *, context: str = "") -> dict:
    """請 AI 依種族特徵設計六維修正係數。

    Args:
        race: 種族名。
        context: 角色描述等背景文字（供 AI 參考，可空）。

    Returns:
        {"race": str, "mods": {六維: float}, "reason": str}

    Raises:
        ParseError: AI 多次輸出非合法 JSON。
    """
    if not has_credentials():
        raise ParseError("AI 解析服務未啟用，請檢查 DEEPSEEK_API_KEY 設定")
    system = RACE_MOD_SYSTEM.format(
        race_min=collide_db.RACE_MOD_MIN,
        race_max=collide_db.RACE_MOD_MAX,
        existing_table=json.dumps(collide_db.load_race_modifiers(), ensure_ascii=False, indent=2),
    )
    user = f"請設計以下種族的六維修正：{race}\n參考背景：{context[:300] if context else '（無）'}"
    last_error = "未知錯誤"
    for attempt in range(2):
        try:
            resp = await chat_create(
                "collide",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                max_tokens=800,
            )
            raw = (resp.choices[0].message.content or "").strip()
            parsed = json.loads(_strip_json(raw))
        except json.JSONDecodeError as e:
            last_error = f"LLM 輸出無法解析為 JSON：{e}"
            continue
        except Exception as e:
            raise ParseError(f"AI 種族修正設計呼叫失敗：{e}")
        if not isinstance(parsed, dict):
            last_error = "LLM 輸出不是 JSON 物件"
            continue
        mods = {}
        for s in STAT_KEYS:
            v = parsed.get("mods", {}).get(s)
            if isinstance(v, (int, float)):
                v = round(float(v), 1)
                v = min(max(v, collide_db.RACE_MOD_MIN), collide_db.RACE_MOD_MAX)
            else:
                v = 1.0
            mods[s] = v
        return {
            "race": str(parsed.get("race") or race),
            "mods": mods,
            "reason": str(parsed.get("reason") or ""),
        }
    raise ParseError(f"種族修正設計多次失敗（2 次）：{last_error}")


async def ensure_race_mods(race: str, *, context: str = "") -> dict | None:
    """確保種族已定義：未定義時請 AI 設計六維修正並寫入種族表（立即生效＋標記待審）。

    Returns:
        None: 種族已定義（無需處理）。
        {"race", "mods", "reason", "pending": True, "saved": bool}: 新提案已填入。
        {"race", "error": str}: AI 設計失敗（不阻擋角色寫入，僅回報）。
    """
    race = str(race or "").strip()
    if not race or collide_db.race_defined(race):
        return None
    try:
        prop = await propose_race_mods(race, context=context)
    except Exception as e:
        return {"race": race, "error": f"種族修正設計失敗：{e}"}
    mods = {s: round(float(prop["mods"].get(s) or 1.0), 1) for s in STAT_KEYS}
    saved = collide_db.save_race_mods(race, mods, pending_review=True)
    return {
        "race": race,
        "mods": mods,
        "reason": prop.get("reason", ""),
        "pending": True,
        "saved": saved,
    }


# ── 擴展推導檢查 + 整合寫入 ────────────────────────────────

async def check_skill_derivation(skill: dict, ability: dict, *, retries: int = 1) -> dict:
    """判斷招式是否為其能力原理的合理擴展應用。

    輸入能力必須含 principle；回傳 {"valid": bool, "reason": str}。
    招式與能力無關機制、或違背能力限制 → valid=false。
    """
    if not has_credentials():
        raise ParseError("AI 解析服務未啟用，請檢查 DEEPSEEK_API_KEY 設定")
    payload = json.dumps(
        {"ability": ability, "skill": skill}, ensure_ascii=False, indent=2
    )
    last_error = "未知錯誤"
    for attempt in range(retries + 1):
        try:
            resp = await chat_create(
                "collide",
                messages=[
                    {"role": "system", "content": SKILL_DERIVATION_SYSTEM},
                    {"role": "user", "content": payload},
                ],
                temperature=0.0,
                max_tokens=1500,
            )
            raw = (resp.choices[0].message.content or "").strip()
            parsed = json.loads(_strip_json(raw))
        except json.JSONDecodeError as e:
            last_error = f"LLM 輸出無法解析為 JSON：{e}"
            continue
        except Exception as e:
            raise ParseError(f"AI 推導檢查呼叫失敗：{e}")

        if not isinstance(parsed, dict):
            last_error = "LLM 輸出不是 JSON 物件"
            continue
        return {"valid": bool(parsed.get("valid")), "reason": str(parsed.get("reason") or "")}

    raise ParseError(f"推導檢查多次失敗（{retries + 1} 次）：{last_error}")


def _ability_names(conn) -> set[str]:
    """能力庫現有名稱集合（供招式驗證）。"""
    try:
        return {a["name"] for a in collide_db.list_abilities(conn)}
    except Exception:
        return set()


async def write_ability(text: str, conn=None) -> dict:
    """把自然語言能力描述解析、驗證並寫入 collide_abilities，再鏡像 world_lore。

    回傳 {"ability": row, "status": "insert"/"update"}。
    """
    data = await parse_natural_language(text, "ability")
    own = conn is None
    conn = conn or collide_db.open_conn()
    collide_db.init_schema(conn)
    try:
        existing = collide_db.get_ability(conn, data["name"], str(data.get("owner") or ""))
        row = collide_db.upsert_ability(conn, data)
        collide_db.mirror_abilities_to_world_lore(conn)
        conn.commit()
        return {"ability": row, "status": "update" if existing else "insert"}
    finally:
        if own:
            conn.close()


async def write_skill(text: str, conn=None, *, require_derivation: bool = False) -> dict:
    """把自然語言招式描述解析、驗證並寫入 collide_skills。

    驗證包含：
    1. 硬驗證（數值/欄位/所屬能力須在能力庫登記）
    2. 擴展推導檢查：招式 application 是否可由能力 principle 合理推導
       結果附於回傳 {"derivation": {"valid":…, "reason":…}}；require_derivation=True 時
       不合理直接擋下（ParseError）。

    回傳 {"skill": row, "status": "insert"/"update", "derivation": {...}}。
    """
    own = conn is None
    conn = conn or collide_db.open_conn()
    collide_db.init_schema(conn)
    try:
        names = _ability_names(conn)
        data = await parse_natural_language(text, "skill")
        errors = validate(data, "skill", known_abilities=names)
        if errors:
            raise ParseError("；".join(errors))
        ability_name = str(data.get("ability") or "").strip()
        if ability_name:
            ability = collide_db.get_ability_named(conn, ability_name)
            derivation = await check_skill_derivation(data, ability or {"name": ability_name})
        else:
            derivation = {"valid": None, "reason": "通常魔法（通用魔法）不需能力推導"}
        if require_derivation and derivation.get("valid") is False:
            raise ParseError(f"招式「{data['name']}」不符能力原理推導：{derivation['reason']}")
        existing = collide_db.get_skill(conn, data["name"], str(data.get("owner") or ""))
        row = collide_db.upsert_skill(conn, data)
        conn.commit()
        return {
            "skill": row,
            "status": "update" if existing else "insert",
            "derivation": derivation,
        }
    finally:
        if own:
            conn.close()


async def write_character(text: str, conn=None) -> dict:
    """把自然語言角色描述解析、驗證並寫入 collide_characters。

    若角色種族未定義，自動請 AI 設計六維修正並填入種族表（立即生效、待人工審查），
    提案資訊附於回傳 {"race_mod_proposal": {...}}。

    回傳 {"character": row, "status": "insert"/"update"}。
    """
    data = await parse_natural_language(text, "character")
    own = conn is None
    conn = conn or collide_db.open_conn()
    collide_db.init_schema(conn)
    try:
        existing = collide_db.get_character(conn, data["name"])
        row = collide_db.upsert_character(conn, data)
        conn.commit()
        result = {"character": row, "status": "update" if existing else "insert"}
        proposal = await ensure_race_mods(str(data.get("race") or ""), context=text)
        if proposal:
            result["race_mod_proposal"] = proposal
        return result
    finally:
        if own:
            conn.close()


async def write_domain(text: str, conn=None) -> dict:
    """把自然語言結界描述解析、驗證並寫入 collide_domains。

    回傳 {"domain": row, "status": "insert"/"update"}。
    """
    data = await parse_natural_language(text, "domain")
    own = conn is None
    conn = conn or collide_db.open_conn()
    collide_db.init_schema(conn)
    try:
        existing = collide_db.get_domain(conn, data["name"], str(data.get("owner") or ""))
        row = collide_db.upsert_domain(conn, data)
        conn.commit()
        return {"domain": row, "status": "update" if existing else "insert"}
    finally:
        if own:
            conn.close()


async def write_clash_villain(text: str, conn=None, *, source: str = "gm") -> dict:
    """把自然語言反派描述解析、驗證並建立血條對戰（collide_clash_battles）。

    自動偵測路徑的門檻：
    - `dynamic_difficulty` 一律強制為 0（guard 純粹由體質隱藏分數 × Domain_Mod 計算），
      不進入 calibrate_roster 校準流程（spec 9 邊界）。
    - 反派必須已存在於角色表 collide_characters（guard 需要其體質隱藏分數）。

    source：gm（管理員註冊）/ auto_detect（自動偵測，pipeline 使用）。

    回傳 {"clash_battle": battle_row, "guard_value": float, "status": "insert"}。
    """
    data = await parse_natural_language(text, "clash_villain")
    # 自動偵測路徑門檻：無論 LLM 怎麼輸出，一律強制 dynamic_difficulty = 0
    data["dynamic_difficulty"] = False

    own = conn is None
    conn = conn or collide_db.open_conn()
    collide_db.init_schema(conn)
    try:
        name = str(data["name"]).strip()
        char = collide_db.get_character(conn, name)
        if not char:
            raise ParseError(
                f"反派「{name}」不存在於角色表（collide_characters），"
                "請先用 /create 建立角色資料（guard 需取該角色體質隱藏分數）"
            )
        char["_race_mods"] = collide_db.race_mods_for(str(char.get("race") or ""))
        constitution_hidden = _hidden_of(char, "體質")
        domain_mod = float(data.get("domain_mod") or 1.0)
        # 共識 A4：guard 純粹由 體質隱藏 × Domain_Mod 機械計算，
        # 解析注入的 guard_value 一律忽略（自動偵測與 gm 註冊皆然），不可覆寫。
        guard_value = compute_villain_guard(constitution_hidden, domain_mod)

        tier = str(data["tier"]).strip()
        spec = CLASH_TIER_SPECS[tier]
        max_hp_ticks = int(data.get("max_hp_ticks") or spec["max_hp_ticks"])
        cap_ticks = int(data.get("cap_ticks") or spec["cap_ticks"])
        floor_ticks = retreat_floor_ticks(max_hp_ticks)

        battle_id = _new_clash_battle_id(conn)
        row = collide_db.create_clash_battle(
            conn,
            battle_id=battle_id,
            villain_id=int(char["id"]),
            villain_name=name,
            tier=tier,
            max_hp_ticks=max_hp_ticks,
            cap_ticks=cap_ticks,
            guard_value=guard_value,
            retreat_floor_ticks=floor_ticks,
            source=source,
            dynamic_difficulty=0,
        )
        return {
            "clash_battle": row,
            "guard_value": guard_value,
            "constitution_hidden": constitution_hidden,
            "status": "insert",
        }
    finally:
        if own:
            conn.close()


def _new_clash_battle_id(conn) -> str:
    """產生唯一 battle_id：clash-<時間戳>-<亂數>；與既有 battle 撞號則重試。"""
    import uuid

    for _ in range(5):
        ts = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        bid = f"clash-{ts}-{uuid.uuid4().hex[:6]}"
        if not collide_db.get_clash_battle(conn, bid):
            return bid
    return f"clash-{uuid.uuid4().hex[:12]}"


async def write_entries(text: str, conn=None, *, require_derivation: bool = False, skip_derivation: bool = False) -> dict:
    """批次寫入：一次接受招式/角色/能力/結界多筆資料。

    輸入可以是：
    - JSON 陣列：[{"type": "skill", ...}, {"type": "character", ...}, ...]
    - 單一 JSON 物件（含 type 欄位）：{"type": "skill", ...}
    - `{"entries": [...]}` 包裝（元素已含 type）
    - 複數鍵包裝：`{"skills": [...]}` / `{"abilities": [...]}` /
      `{"characters": [...]}` / `{"domains": [...]}`（元素由複數鍵自動補 type）

    每筆資料不再呼叫 LLM 重新解析（已是結構化 JSON），只做程式硬驗證後寫入。
    處理順序：ability → domain → character → skill（能力先建檔，招式才能掛在已登記能力下）。

    回傳 {"entries": [{"type", "status", "row", (skill 另含 "derivation", character 另含
    可能有的 "race_mod_proposal")}, ...]}。
    """
    own = conn is None
    conn = conn or collide_db.open_conn()
    collide_db.init_schema(conn)
    try:
        try:
            parsed = _normalize_batch(json.loads(_strip_json(text)))
        except json.JSONDecodeError as e:
            raise ParseError(f"批次寫入需提供 JSON 陣列（每筆含 type 欄位），解析失敗：{e}")

        order = {"ability": 0, "domain": 1, "character": 2, "skill": 3}
        entries = []
        for e in parsed:
            if not isinstance(e, dict):
                raise ParseError("陣列元素必須是 JSON 物件")
            t = str(e.get("type") or "")
            if t not in VALID_TARGETS:
                raise ParseError(f"不支援的目標類型：{t}")
            entries.append((order[t], t, {k: v for k, v in e.items() if k != "type"}))
        entries.sort(key=lambda x: x[0])

        ability_names = _ability_names(conn)
        results: list[dict] = []
        for _, t, data in entries:
            name = str(data.get("name") or "")
            errors = validate(data, t, known_abilities=ability_names)
            if errors:
                raise ParseError(f"「{name}」（{t}）驗證失敗：{'；'.join(errors)}")
            if t == "ability":
                existing = collide_db.get_ability(conn, name, str(data.get("owner") or ""))
                row = collide_db.upsert_ability(conn, data)
                ability_names.add(name)  # 讓同批後續的招式可掛上
                results.append({"type": t, "status": "update" if existing else "insert", "row": row})
            elif t == "domain":
                existing = collide_db.get_domain(conn, name, str(data.get("owner") or ""))
                row = collide_db.upsert_domain(conn, data)
                results.append({"type": t, "status": "update" if existing else "insert", "row": row})
            elif t == "character":
                existing = collide_db.get_character(conn, name)
                row = collide_db.upsert_character(conn, data)
                entry = {"type": t, "status": "update" if existing else "insert", "row": row}
                proposal = await ensure_race_mods(
                    str(data.get("race") or ""),
                    context=json.dumps(data, ensure_ascii=False),
                )
                if proposal:
                    entry["race_mod_proposal"] = proposal
                results.append(entry)
            else:  # skill
                ability_name = str(data.get("ability") or "").strip()
                if skip_derivation or not ability_name:
                    derivation = {"valid": None, "reason": "通用魔法或批次錄入，略過推導檢查"}
                else:
                    ability = collide_db.get_ability_named(conn, ability_name)
                    derivation = await check_skill_derivation(data, ability or {"name": ability_name})
                if require_derivation and derivation.get("valid") is False:
                    raise ParseError(f"招式「{name}」不符能力原理推導：{derivation['reason']}")
                existing = collide_db.get_skill(conn, name, str(data.get("owner") or ""))
                row = collide_db.upsert_skill(conn, data)
                results.append({
                    "type": t, "status": "update" if existing else "insert",
                    "row": row, "derivation": derivation,
                })
        collide_db.mirror_abilities_to_world_lore(conn)
        conn.commit()
        return {"entries": results}
    finally:
        if own:
            conn.close()


async def write_entries_nl(text: str, *, retries: int = 2, require_derivation: bool = False) -> dict:
    """自然語言批次寫入：一次可輸入角色/能力/招式/結界多筆。

    例：「新增角色凌空（人類，力量B…）；新增能力蒼雷，凌空的能力，原理是…；
    新增招式蒼雷轟，屬蒼雷的擴展應用…」

    LLM 依各類寫入規範把整段描述拆成含 type 的 JSON 陣列，再走 write_entries 硬驗證與寫入。
    若 LLM 產出非合法 JSON/多次驗證失敗，raw 輸出寫入 error_logs 供除錯。
    """
    if not has_credentials():
        raise ParseError("AI 解析服務未啟用，請檢查 DEEPSEEK_API_KEY 設定")

    system_prompt = (
        "你是招式對撞系統的批次資料解析器。管理員可能在一段自然語言中同時描述"
        "角色（character）、能力（ability）、招式（skill）、結界（domain）多筆資料。\n"
        "請把整段描述拆解成 JSON 陣列，每個元素為一個物件，必須含 type 欄位"
        "（character/ability/skill/domain），其餘欄位依對應類型的寫入規範填寫。\n"
        "輸出只能是 JSON 陣列（不要 ```json 包裝、不要額外文字）。\n\n"
        f"【各類欄位規範】\n{WRITE_RULES['character']}\n\n{WRITE_RULES['ability']}\n\n"
        f"{WRITE_RULES['skill']}\n\n{WRITE_RULES['domain']}"
    )
    user_prompt = f"請解析以下描述（可能含多筆資料）：\n{text}"

    last_error = "未知錯誤"
    last_raw = ""
    for attempt in range(retries + 1):
        try:
            resp = await chat_create(
                "collide",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=6000,
            )
            raw = (resp.choices[0].message.content or "").strip()
            try:
                parsed = _normalize_batch(json.loads(_strip_json(raw)))
            except json.JSONDecodeError as e:
                last_error = f"LLM 輸出無法解析為 JSON：{e}"
                last_raw = raw
                _log_parse_failure("batch_nl", raw, f"JSON decode: {e}", text=text)
                continue
            except ParseError as e:
                last_error = str(e)
                last_raw = raw
                _log_parse_failure("batch_nl", raw, f"batch normalize: {e}", text=text)
                continue
        except Exception as e:
            raise ParseError(f"AI 批次解析呼叫失敗：{e}")

        if not all(isinstance(e, dict) and e.get("type") in VALID_TARGETS for e in parsed):
            last_error = "陣列元素缺少 type（character/ability/skill/domain）"
            last_raw = raw
            _log_parse_failure("batch_nl", raw, "missing type", text=text)
            continue
        try:
            return await write_entries(
                json.dumps(parsed, ensure_ascii=False),
                require_derivation=require_derivation,
            )
        except ParseError as e:
            last_error = str(e)
            last_raw = raw
            _log_parse_failure("batch_nl", raw, f"write_entries failed: {e}", text=text)
            continue

    raise ParseError(f"批次解析多次失敗（{retries + 1} 次）：{last_error}")


async def quick_test():
    """手動測試（不呼叫 API）：驗證驗證器邏輯。"""
    bad_char = {
        "name": "測試",
        "stats": {"力量": "X", "敏捷": "B"},
        "max_mp": 500,
        "martial": 101,
        "exp": 10,
        "special_forms": {"獸王": 9.9},
    }
    print("bad_char errors:", validate(bad_char, "character"))
    good_char = {
        "name": "凌空",
        "stats": {"力量": "B", "敏捷": "A", "智力": "C", "體質": "B", "魅力": "D", "幸運": "E"},
        "max_mp": 45,
        "martial": 60,
        "exp": 15,
    }
    print("good_char errors:", validate(good_char, "character"))
    bad_villain = {
        "name": "黑龍",
        "stats": {"力量": "S", "敏捷": "B", "智力": "C", "體質": "S", "魅力": "D", "幸運": "E"},
        "max_mp": 80, "martial": 70, "exp": 30,
        "is_villain": True,
        "dynamic_difficulty": True,  # 缺 calibrate_roster
        "is_teacher": "yes",         # 非布林
    }
    print("bad_villain errors:", validate(bad_villain, "character"))
    good_villain = {
        "name": "黑龍",
        "stats": {"力量": "S", "敏捷": "B", "智力": "C", "體質": "S", "魅力": "D", "幸運": "E"},
        "max_mp": 80, "martial": 70, "exp": 30,
        "is_villain": True,
        "dynamic_difficulty": True,
        "calibrate_arc": "第一篇",
        "calibrate_roster": ["凌空", "澪", "羊君"],
    }
    print("good_villain errors:", validate(good_villain, "character"))
    bad_skill = {"name": "", "ability": "", "application": "", "skill_type": "禁咒", "magic_system": "錯", "mp_cost": 99, "power_base": 12.0}
    print("bad_skill errors:", validate(bad_skill, "skill"))
    good_skill = {"name": "火焰分身", "ability": "蒼炎", "application": "擴展二：不以造成傷害為前提的火焰放出", "skill_type": "個人魔法", "magic_system": "自然系", "element": "火", "mp_cost": 8, "power_base": 3.0}
    print("good_skill errors:", validate(good_skill, "skill"))
    print("good_skill(known) errors:", validate(good_skill, "skill", known_abilities={"蒼炎"}))
    no_elem_heal = {"name": "治癒之光", "ability": "聖光", "application": "治療・回復", "skill_type": "個人魔法", "magic_system": "療癒系", "element": "", "mp_cost": 5, "power_base": 3.0}
    print("療癒系無元素 errors:", validate(no_elem_heal, "skill"))
    no_elem_psi = {"name": "心電感應", "ability": "念力", "application": "感官干擾", "skill_type": "個人魔法", "magic_system": "超能系", "element": "", "mp_cost": 6, "power_base": 4.0}
    print("超能系無元素 errors:", validate(no_elem_psi, "skill"))
    nat_no_elem = {**good_skill, "element": ""}
    print("自然系無元素 errors:", validate(nat_no_elem, "skill"))
    common_magic = {"name": "貫通魔法", "ability": "貫通魔法", "application": "攻擊型-放出", "skill_type": "通常魔法", "magic_system": "", "element": "", "mp_cost": 8, "power_base": 4.5}
    print("通常魔法無系 errors:", validate(common_magic, "skill"))
    common_magic_with_sys = {**common_magic, "magic_system": "超能系"}
    print("通常魔法帶系 errors:", validate(common_magic_with_sys, "skill"))
    personal_no_sys = {**good_skill, "magic_system": ""}
    print("個人魔法無系 errors:", validate(personal_no_sys, "skill"))
    common_no_ability = {**common_magic, "ability": ""}
    print("通常魔法無能力 errors:", validate(common_no_ability, "skill"))
    personal_no_ability = {**good_skill, "ability": ""}
    print("個人魔法無能力 errors:", validate(personal_no_ability, "skill"))
    orphan_skill = {**good_skill, "ability": "未建檔能力"}
    print("orphan_skill errors:", validate(orphan_skill, "skill", known_abilities={"蒼炎"}))
    print("bad_ability errors:", validate({"name": "蒼炎", "principle": "", "category": "攻擊型"}, "ability"))
    print("good_ability errors:", validate({"name": "蒼炎", "principle": "壓縮雷元素形成電擊", "category": "攻擊型"}, "ability"))
    print("custom_category errors:", validate({"name": "蒼炎", "principle": "壓縮雷元素形成電擊", "category": "精神系"}, "ability"))


if __name__ == "__main__":
    import asyncio

    asyncio.run(quick_test())
