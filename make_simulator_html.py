"""
make_simulator_html.py — 從正式版規則/引擎產生「對撞模擬器」（單一自足 HTML）

保證「與正式上線時一樣的版本」：
- 提示詞直接取自 collide_rules.py（WRITE_RULES / CLASH_PHASE_* / CALIBRATE_SYSTEM / RULES_TEXT）
- 常數表直接取自 collide_rules.py（ELEMENT_GRID / RATING_CONST / DOMAIN_MOD_TIERS / EXP_TIERS ...）
- JS 引擎（simulator_engine.js）為 collide_engine.py 的逐函式鏡像；建置時以 Node 跑自測向量，
  與 Python 引擎輸出逐一比對（不一致即建置失敗）

用法：
    python make_simulator_html.py
輸出：simulator.html（可直接於瀏覽器開啟）
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import collide_engine as CE          # noqa: E402
import collide_rules as CR           # noqa: E402

ENGINE_JS = ROOT / "simulator_engine.js"
TEMPLATE = ROOT / "simulator_template.html"
OUT = ROOT / "simulator.html"
NODE_BIN = os.environ.get("NODE_BIN", "node")


# ─────────────────────────────────────────────────────────────
# 1. 組裝 SIM_DATA（與正式版同一來源）
# ─────────────────────────────────────────────────────────────

def build_sim_data() -> dict:
    grid = {k: dict(v) for k, v in CR.ELEMENT_GRID.items()}
    return {
        "_build": {
            "generated_by": "make_simulator_html.py",
            "sources": ["collide_rules.py", "collide_engine.py", "simulator_engine.js"],
            "timestamp": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
            "element_pairs": len(CR.STRONG_ELEMENT_PAIRS),
        },
        "RATING_CONST": CR.RATING_CONST,
        "RATING_ORDER": CR.RATING_ORDER,
        "HIDDEN_SCORE_MAX": CR.HIDDEN_SCORE_MAX,
        "STAT_KEYS": CE.STAT_KEYS,
        "RATING_REVERSE": CE.RATING_REVERSE,
        "WEAPON_BASE_TABLE": CE.WEAPON_BASE_TABLE,
        "WEAPON_LABELS": {"bare": "徒手", "light": "輕武器", "one_hand": "單手武器", "heavy": "重型武器"},
        "ELEMENT_GRID": grid,
        "ELEMENT_LIST": CR.ELEMENT_LIST,
        "DOMAIN_MOD_TIERS": CR.DOMAIN_MOD_TIERS,
        "EXP_TIERS": [list(t) for t in CR.EXP_TIERS],
        "BOUNDS": CR.BOUNDS,
        "ENUMS": CR.ENUMS,
        "ABILITY_CATEGORIES": CR.ABILITY_CATEGORIES,
        "APPLICATION_HINTS": CR.APPLICATION_HINTS,
        "MP_INVESTED_MAX": CE.MP_INVESTED_MAX,
        "MP_INVESTED_STEP": CE.MP_INVESTED_STEP,
        "PROMPTS": {
            "phase_a": CR.CLASH_PHASE_A_SYSTEM,
            "phase_b": CR.CLASH_PHASE_B_SYSTEM,
            "phase_c": CR.CLASH_PHASE_C_SYSTEM,
            "range_judge": CR.RANGE_MOD_SYSTEM,
            "calibrate": CR.CALIBRATE_SYSTEM,
            "write_character": CR.WRITE_RULES["character"],
            "write_ability": CR.WRITE_RULES["ability"],
            "write_skill": CR.WRITE_RULES["skill"],
            "write_domain": CR.WRITE_RULES["domain"],
            "formulas": CR.FORMULAS_TEXT,
            "growth": CR.GROWTH_RULES_TEXT,
            "rules": CR.RULES_TEXT,
        },
    }


# ─────────────────────────────────────────────────────────────
# 2. 自測向量（Python 引擎計算 → JS 引擎比對）
# ─────────────────────────────────────────────────────────────

def _norm(v):
    """把 Python tuple 轉成 JSON-safe（list），其他型別原樣。"""
    if isinstance(v, tuple):
        return [_norm(x) for x in v]
    if isinstance(v, list):
        return [_norm(x) for x in v]
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


def build_test_vectors() -> dict:
    roster = [
        {"name": "凌空", "stats": {"力量": "B", "敏捷": "A", "智力": "C", "體質": "B", "魅力": "D", "幸運": "E"},
         "exp": 25, "martial": 60, "max_mp": 45},
        {"name": "澪", "stats": {"力量": "C", "敏捷": "B", "智力": "S", "體質": "B", "魅力": "A", "幸運": "B"},
         "exp": 40, "martial": 20, "max_mp": 80},
    ]
    manabp = lambda mp: CE.compute_manamelee_bp(3.0, 4.0, 4.0, mp, 60, 50)  # noqa: E731

    cases = {
        "hiddenScore(B,1.0)": {"py": lambda: CE.hidden_score("B", 1.0), "js": "E.hiddenScore('B',1.0)"},
        "hiddenScore(S,1.5)": {"py": lambda: CE.hidden_score("S", 1.5), "js": "E.hiddenScore('S',1.5)"},
        "hiddenScore(上限9.0)": {"py": lambda: CE.hidden_score("S", 1.5), "js": "E.hiddenScore('S',1.5)"},
        "elementMod(水→火)": {"py": lambda: CE.element_mod("水", "火"), "js": "E.elementMod('水','火')"},
        "elementMod(火→水)": {"py": lambda: CE.element_mod("火", "水"), "js": "E.elementMod('火','水')"},
        "elementMod(未列組合1.0)": {"py": lambda: CE.element_mod("火", "風"), "js": "E.elementMod('火','風')"},
        "elementMod(水多弱點:冰/雷/葉)": {
            "py": lambda: [CE.element_mod(e, "水") for e in ("冰", "雷", "葉")],
            "js": "['冰','雷','葉'].map(e=>E.elementMod(e,'水'))"},
        "expMod(50)": {"py": lambda: CE.exp_mod(50), "js": "E.expMod(50)"},
        "expMod(0/100)": {"py": lambda: [CE.exp_mod(0), CE.exp_mod(100)], "js": "[E.expMod(0),E.expMod(100)]"},
        "AtkBp 完整": {
            "py": lambda: CE.compute_atk_bp(7.5, CE.hidden_score("A", 1.0), 50,
                                            element_m=1.2, domain_m=1.25, proficiency_m=1.5,
                                            status_m=0.95, form_m=1.3, variant_m=1.05,
                                            range_m=0.95, ambush_m=1.0),
            "js": "E.computeAtkBp(7.5, E.hiddenScore('A',1.0), 50, 1.2, 1.25, 1.5, 0.95, 1.3, 1.05, 0.95, 1.0)"},
        "DomainBp 中期覺醒": {
            "py": lambda: CE.compute_domain_bp(1.2, CE.hidden_score("S", 1.0), 55, domain_m=1.25),
            "js": "E.computeDomainBp(1.2, E.hiddenScore('S',1.0), 55, 1.25)"},
        "DomainBp 未覺醒": {
            "py": lambda: CE.compute_domain_bp(0.5, CE.hidden_score("C", 1.0), 20, domain_m=1.0),
            "js": "E.computeDomainBp(0.5, E.hiddenScore('C',1.0), 20, 1.0)"},
        "MeleeBp": {
            "py": lambda: CE.compute_melee_bp(3.0, CE.hidden_score("B", 1.0), 60, 40, status_m=0.9),
            "js": "E.computeMeleeBp(3.0, E.hiddenScore('B',1.0), 60, 40, 1.0, 0.9)"},
        "ManaMeleeBp": {
            "py": lambda: CE.compute_manamelee_bp(3.0, 4.0, 4.0, 1.5, 60, 50, element_m=0.8, proficiency_m=1.0),
            "js": "E.computeManaMeleeBp(3.0, 4.0, 4.0, 1.5, 60, 50, 0.8, 1.0)"},
        "weapon(徒手)": {
            "py": lambda: CE.resolve_weapon_override("bare", None, ""),
            "js": "E.resolveWeaponOverride('bare', null, '')"},
        "weapon(長劍)": {
            "py": lambda: CE.resolve_weapon_override("one_hand", None, "長劍"),
            "js": "E.resolveWeaponOverride('one_hand', null, '長劍')"},
        "weapon(覆寫5.0)": {
            "py": lambda: CE.resolve_weapon_override("heavy", 5.0, "聖劍"),
            "js": "E.resolveWeaponOverride('heavy', 5.0, '聖劍')"},
        "variantSkillBase([5.0,8.0])": {
            "py": lambda: CE.variant_skill_base([5.0, 8.0]), "js": "E.variantSkillBase([5.0,8.0])"},
        "variantSkillBase(空)": {
            "py": lambda: CE.variant_skill_base([]), "js": "E.variantSkillBase([])"},
        "decide(碾壓)": {"py": lambda: CE.decide(25.0, 10.0), "js": "E.decide(25.0,10.0)"},
        "decide(壓制)": {"py": lambda: CE.decide(15.0, 10.0), "js": "E.decide(15.0,10.0)"},
        "decide(僵持)": {"py": lambda: CE.decide(10.0, 10.0), "js": "E.decide(10.0,10.0)"},
        "EvadeBp": {
            "py": lambda: CE.compute_evade_bp(CE.hidden_score("S", 1.0)),
            "js": "E.computeEvadeBp(E.hiddenScore('S',1.0))"},
        "GuardBp(+0.3)": {
            "py": lambda: CE.compute_guard_bp(CE.hidden_score("B", 1.0), guard_bonus=0.3),
            "js": "E.computeGuardBp(E.hiddenScore('B',1.0), 1.0, 1.0, 1.0, 0.3)"},
        "decideNonaggressive(命中重創)": {
            "py": lambda: CE.decide_nonaggressive(25.0, 10.0), "js": "E.decideNonaggressive(25.0,10.0)"},
        "decideNonaggressive(化解)": {
            "py": lambda: CE.decide_nonaggressive(8.0, 10.0), "js": "E.decideNonaggressive(8.0,10.0)"},
        "decideWithDomain(未開1.0/1.0)": {
            "py": lambda: CE.decide_with_domain(20.0, 10.0, 1.0, 1.0),
            "js": "E.decideWithDomain(20.0,10.0,1.0,1.0)"},
        "decideWithDomain(一開1.0/1.4)": {
            "py": lambda: CE.decide_with_domain(12.0, 10.0, 1.0, 1.4),
            "js": "E.decideWithDomain(12.0,10.0,1.0,1.4)"},
        "decideWithDomain(融合 gap<0.3)": {
            "py": lambda: CE.decide_with_domain(15.0, 10.0, 1.4, 1.25),
            "js": "E.decideWithDomain(15.0,10.0,1.4,1.25)"},
        "decideWithDomain(覆蓋 gap>=0.3)": {
            "py": lambda: CE.decide_with_domain(15.0, 10.0, 1.5, 1.1),
            "js": "E.decideWithDomain(15.0,10.0,1.5,1.1)"},
        "decideWithDomain(彌補恢復融合)": {
            "py": lambda: CE.decide_with_domain(15.0, 10.0, 1.5, 1.1, mp_invested=14),
            "js": "E.decideWithDomain(15.0,10.0,1.5,1.1,14)"},
        "effects(護盾吸收+攻擊加成+防禦加成+削弱)": {
            "py": lambda: CE.apply_effects(
                20.0, 12.0,
                [{"type": "buff", "mult": 0.15, "note": "燃火增幅"},
                 {"type": "summon", "damage_bonus": 1.2, "note": "火焰分身"}],
                [{"type": "shield", "absorb": 2.5, "blocks": "magic", "note": "水球氣泡"},
                 {"type": "dodge", "bonus": 0.5, "note": "烈焰替身"},
                 {"type": "debuff", "penalty": 0.1, "note": "水氣干擾"}],
                atk_kind="magic", def_kind="magic"),
            "js": "E.applyEffects(20.0, 12.0, "
                  "[{type:'buff',mult:0.15,note:'燃火增幅'},{type:'summon',damage_bonus:1.2,note:'火焰分身'}], "
                  "[{type:'shield',absorb:2.5,blocks:'magic',note:'水球氣泡'},{type:'dodge',bonus:0.5,note:'烈焰替身'},"
                  "{type:'debuff',penalty:0.1,note:'水氣干擾'}], 'magic','magic')"},
        "effects(物理護盾不擋魔法)": {
            "py": lambda: CE.apply_effects(
                20.0, 12.0, [],
                [{"type": "shield", "absorb": 2.5, "blocks": "physical", "note": "岩壁"}],
                atk_kind="magic"),
            "js": "E.applyEffects(20.0, 12.0, [], "
                  "[{type:'shield',absorb:2.5,blocks:'physical',note:'岩壁'}], 'magic','magic')"},
        "effects(護盾未滿剩餘吸收)": {
            "py": lambda: CE.apply_effects(
                1.0, 12.0, [],
                [{"type": "shield", "absorb": 2.5, "blocks": "all", "note": "氣泡"}],
                atk_kind="magic"),
            "js": "E.applyEffects(1.0, 12.0, [], "
                  "[{type:'shield',absorb:2.5,blocks:'all',note:'氣泡'}], 'magic','magic')"},
        "effects(無效盾擋暗)": {
            "py": lambda: CE.apply_effects(
                20.0, 12.0, [],
                [{"type": "shield", "absorb": 999, "blocks": "all", "exempt": ["光"], "note": "暗影潛形"}],
                atk_kind="magic", atk_element="暗"),
            "js": "E.applyEffects(20.0, 12.0, [], "
                  "[{type:'shield',absorb:999,blocks:'all',exempt:['光'],note:'暗影潛形'}], 'magic','magic','暗','')"},
        "effects(無效盾被光穿透)": {
            "py": lambda: CE.apply_effects(
                20.0, 12.0, [],
                [{"type": "shield", "absorb": 999, "blocks": "all", "exempt": ["光"], "note": "暗影潛形"}],
                atk_kind="magic", atk_element="光"),
            "js": "E.applyEffects(20.0, 12.0, [], "
                  "[{type:'shield',absorb:999,blocks:'all',exempt:['光'],note:'暗影潛形'}], 'magic','magic','光','')"},
        "effects(無 exempt 護盾正常擋光)": {
            "py": lambda: CE.apply_effects(
                20.0, 12.0, [],
                [{"type": "shield", "absorb": 5.0, "blocks": "all", "note": "氣泡"}],
                atk_kind="magic", atk_element="光"),
            "js": "E.applyEffects(20.0, 12.0, [], "
                  "[{type:'shield',absorb:5.0,blocks:'all',note:'氣泡'}], 'magic','magic','光','')"},
        "MP追逐": {
            "py": lambda: CE.resolve_mp_invested(manabp, opponent_bp=25.0),
            "js": "E.resolveMpInvested(mp=>E.computeManaMeleeBp(3.0,4.0,4.0,mp,60,50),{opponentBp:25.0})"},
        "expTier(10)": {"py": lambda: CE.exp_tier(10), "js": "E.expTier(10)"},
        "expTier(90)": {"py": lambda: CE.exp_tier(90), "js": "E.expTier(90)"},
        "反派校準基準": {
            "py": lambda: CE.villain_calibration_baseline(roster, ratio=1.1),
            "js": "E.villainCalibrationBaseline(" + json.dumps(roster, ensure_ascii=False) + ",{ratio:1.1})"},
    }

    vectors = []
    for name, c in cases.items():
        vectors.append({"name": name, "expected": _norm(c["py"]()), "js": c["js"]})
    return {"tolerance": 1e-9, "cases": vectors}


# ─────────────────────────────────────────────────────────────
# 3. Node 驗證 JS 引擎與 Python 引擎一致
# ─────────────────────────────────────────────────────────────

def run_node_verification(sim_data: dict, vectors: dict) -> None:
    tmp = ROOT / "._sim_tmp"
    tmp.mkdir(exist_ok=True)
    data_js = tmp / "sim_data.json"
    test_js = tmp / "test.js"
    data_js.write_text(json.dumps(sim_data, ensure_ascii=False), encoding="utf-8")

    cases_literal = json.dumps(vectors, ensure_ascii=False)
    engine_path = str(ENGINE_JS).replace("\\", "/")
    test_js.write_text(
        f"""
"use strict";
const fs = require("fs");
global.SIM_DATA = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const E = require("{engine_path}").CollideEngine;
const V = {cases_literal};
function approx(a, b, eps) {{
  if (typeof a === "number" && typeof b === "number") return Math.abs(a - b) <= eps;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a)) return a.length === b.length && a.every((x, i) => approx(x, b[i], eps));
  if (a && typeof a === "object") {{
    const ka = Object.keys(a), kb = Object.keys(b);
    return ka.length === kb.length && ka.every(k => approx(a[k], b[k], eps));
  }}
  return a === b;
}}
let fail = 0;
for (const c of V.cases) {{
  let got;
  try {{ got = eval(c.js); }} catch (e) {{ got = "ERR:" + e.message; }}
  const ok = approx(c.expected, got, V.tolerance);
  if (!ok) {{
    fail++;
    console.log("FAIL  " + c.name);
    console.log("  expected: " + JSON.stringify(c.expected));
    console.log("  got     : " + JSON.stringify(got));
  }}
}}
if (fail === 0) {{ console.log("ALL " + V.cases.length + " JS ENGINE TESTS PASSED"); }}
process.exit(fail === 0 ? 0 : 1);
""",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [NODE_BIN, str(test_js), str(data_js)],
        capture_output=True, text=True, encoding="utf-8",
    )
    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if proc.returncode != 0:
        raise SystemExit(f"[make_simulator_html] JS 引擎與 Python 引擎不一致（自測失敗），中止建置")
    print("[make_simulator_html] JS 引擎一致性驗證通過")


# ─────────────────────────────────────────────────────────────
# 4. 產生 simulator.html
# ─────────────────────────────────────────────────────────────

def main() -> None:
    sim_data = build_sim_data()
    vectors = build_test_vectors()
    run_node_verification(sim_data, vectors)

    engine_js = ENGINE_JS.read_text(encoding="utf-8")
    template = TEMPLATE.read_text(encoding="utf-8")

    sim_data_js = "const SIM_DATA = " + json.dumps(sim_data, ensure_ascii=False, indent=2) + ";"
    vectors_js = "const TEST_VECTORS = " + json.dumps(vectors, ensure_ascii=False) + ";"
    build_info = (
        "const BUILD_INFO = " + json.dumps(sim_data["_build"], ensure_ascii=False, indent=2) + ";"
    )

    html = (
        template
        .replace("/*__SIM_DATA_JS__*/", sim_data_js)
        .replace("/*__ENGINE_JS__*/", engine_js)
        .replace("/*__TEST_VECTORS_JS__*/", vectors_js)
        .replace("/*__BUILD_INFO_JS__*/", build_info)
    )

    OUT.write_text(html, encoding="utf-8")
    print(f"已產生: {OUT}")
    print(f"提示詞來源: collide_rules.py（{sim_data['_build']['element_pairs']} 組元素剋制配對）")


if __name__ == "__main__":
    main()
