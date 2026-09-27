"""
collide_engine.py — 對撞計算純函式引擎（不呼叫 LLM）

提供：
- hidden score 計算
- 元素相剋
- 四條公式（Atk_BP / Melee_BP / ManaMelee_BP / Exp_Mod）
- 四階判定 + 結界覆蓋與魔力彌補
"""

from collide_rules import (
    DOMAIN_MOD_TIERS,
    ELEMENT_GRID,
    EXP_TIERS,
    HIDDEN_SCORE_MAX,
    RATING_CONST,
)


# ── 隱藏分數 ──────────────────────────────────────────────

def hidden_score(rating: str, race_mod: float = 1.0) -> float:
    """隱藏分數 = 評級常數 × 種族修正，上限 9.0。"""
    const = RATING_CONST.get(rating, 0)
    return min(round(const * race_mod, 2), HIDDEN_SCORE_MAX)


# ── 武器基數判定表（機械取值，AI 不判數值）───────────────

WEAPON_BASE_TABLE = {
    "bare": 1.0,       # 徒手：拳、掌、爪
    "light": 2.0,      # 輕武器：匕首、短刀、飛刀
    "one_hand": 3.0,   # 單手武器：長劍、單手戰斧、魔杖
    "heavy": 4.0,      # 重型武器：大劍、巨斧、長槍
}


def weapon_base(weapon_category: str, override: float | None = None) -> float:
    """依武器分類機械取值；管理員 weapon_base 覆寫優先（傳說/特製 5.0 級）。"""
    if override is not None:
        return round(float(override), 2)
    return float(WEAPON_BASE_TABLE.get(weapon_category, WEAPON_BASE_TABLE["bare"]))


def resolve_weapon_override(category: str | None, override: float | None, char_weapon: str = "") -> tuple[float, str]:
    """由角色資料解析實際 Weapon_Base，回傳 (數值, 判定說明)。"""
    if override is not None:
        return weapon_base(category, override), f"管理員覆寫 weapon_base={override}"
    cat = category or ("light" if char_weapon else "bare")
    base = weapon_base(cat)
    label = {"bare": "徒手", "light": "輕武器", "one_hand": "單手武器", "heavy": "重型武器"}.get(cat, "徒手")
    return base, f"{char_weapon or '無武器'} → {label} → Weapon_Base {base}"


# ── 元素相剋 ──────────────────────────────────────────────

def element_mod(attacker_element: str, defender_element: str) -> float:
    """元素相剋判定。剋制 1.2 / 無 1.0 / 被剋 0.8。"""
    if not attacker_element or not defender_element:
        return 1.0
    grid = ELEMENT_GRID.get(attacker_element, {})
    return float(grid.get(defender_element, 1.0))


# ── 反派動態難度校準基準（機械計算，AI 依此微調）──────────

STAT_KEYS = ["力量", "敏捷", "智力", "體質", "魅力", "幸運"]
RATING_REVERSE = {1: "E", 2: "D", 3: "C", 4: "B", 5: "A", 6: "S"}


def _hidden_of(char: dict, stat: str) -> float:
    """取角色某六維的隱藏分數（DB 存 *_hidden 欄位；未存則依評級 × 種族修正即時算）。

    種族修正六維各自獨立：優先取 `_race_mods[stat]`，退而求其次用單一 `_race_mod`。
    """
    hidden = char.get(f"stat_{stat}_hidden")
    if hidden is not None:
        return float(hidden)
    st = char.get("stats") or {}
    rating = st.get(stat)
    mods = char.get("_race_mods") or {}
    race_mod = float(mods.get(stat) or char.get("_race_mod") or 1.0)
    return hidden_score(rating, race_mod) if rating else 0.0


def _rating_for_hidden(target: float, race_mod: float = 1.0) -> str:
    """找「隱藏分數 ≥ target」的最低評級；種族修正使上限超過 9.0 時取 S。"""
    for const in range(1, 7):
        if const * race_mod >= target:
            return RATING_REVERSE[const]
    return "S"


def villain_calibration_baseline(
    roster: list[dict],
    *,
    ratio: float = 1.1,
    race_mod: float = 1.0,
    race_mods: dict | None = None,
) -> dict:
    """反派動態難度的機械校準基準（可複現，AI 依 CALIBRATE_SYSTEM 微調）。

    基準：各六維取「預定上場角色中該項最高隱藏分數」× ratio（篇章定位加成），
    再反推建議評級；exp/martial 略高於上場最高者，max_mp ≥ 上場最高。

    Args:
        roster: 預定上場角色（每筆含 stats 或 *_hidden 欄位、exp、martial、max_mp）。
        ratio: 加成倍率（普通反派 0.9~1.0、篇章 Boss 1.05~1.15、最終 Boss ≤1.2）。
        race_mod: 反派種族修正單一值（六維同值時用；用於反推評級）。
        race_mods: 反派種族六維修正 dict（依六維各自套用；優先於 race_mod）。

    Returns:
        {"stats": {六維建議評級}, "exp": n, "martial": n, "max_mp": n,
         "targets": {六維目標隱藏分數}, "strongest": 基準角色名, "ratio": ratio}
    """
    mods = race_mods or {}
    max_hidden: dict[str, float] = {}
    strongest_name = ""
    strongest_score = -1.0
    for char in roster:
        score = sum(_hidden_of(char, s) for s in STAT_KEYS) + float(char.get("max_mp") or 0) / 10
        if score > strongest_score:
            strongest_score = score
            strongest_name = char.get("name") or "未知"
        for s in STAT_KEYS:
            h = _hidden_of(char, s)
            max_hidden[s] = max(max_hidden.get(s, 0.0), h)

    targets = {s: min(max_hidden[s] * ratio, HIDDEN_SCORE_MAX) for s in STAT_KEYS}
    stats = {
        s: _rating_for_hidden(targets[s], float(mods.get(s) or race_mod))
        for s in STAT_KEYS
    }

    max_exp = max((int(c.get("exp") or 0) for c in roster), default=0)
    max_martial = max((int(c.get("martial") or 0) for c in roster), default=0)
    max_mp = max((int(c.get("max_mp") or 10) for c in roster), default=10)

    return {
        "stats": stats,
        "exp": min(max_exp + 10, 100),
        "martial": min(max_martial + 5, 100),
        "max_mp": min(max(max_mp, max_mp + 10), 100),
        "targets": {s: round(v, 2) for s, v in targets.items()},
        "strongest": strongest_name,
        "ratio": ratio,
    }


# ── 經驗修正 ──────────────────────────────────────────────

def exp_mod(exp: int) -> float:
    """實戰經驗修正 = 1.0 + E/500（1.0~1.2）。"""
    return 1.0 + exp / 500.0


# ── 熟練度 / 型態 / 變體 / 狀態 ───────────────────────────

def proficiency_mod(proficiency: int) -> float:
    """能力熟練度加成 1.0 + proficiency/100（同能力招式共用）。"""
    return 1.0 + proficiency / 100.0


def variant_skill_base(existing_power_bases: list[float]) -> float:
    """未登記「合理變體」的 Skill_Base = 該能力下既有招式「最低 power_base」。

    規則（防鑽漏洞）：變體一律以該能力既有招式的最低 power_base 為 Skill_Base，
    1.05 的加成由公式中的 Variant_Mod 套用（**不再重複乘算**）。
    例：火焰彈 5.0 / 烈焰風暴 8.0 → 未登記「火焰連彈」Skill_Base = min(5.0, 8.0) = 5.0，
    Atk_BP 計算時 Variant_Mod = 1.05。
    """
    if not existing_power_bases:
        return 1.0
    return round(min(float(b) for b in existing_power_bases), 2)


def domain_mod(awakening: str | None, *, in_domain: bool = True) -> float:
    """結界修正：未開結界 1.0；開結界依覺醒進度。"""
    if not in_domain:
        return 1.0
    return float(DOMAIN_MOD_TIERS.get(awakening or "未覺醒", 1.0))


# ── 三條威力公式 ──────────────────────────────────────────

def compute_atk_bp(
    skill_base: float,
    stat_hidden: float,
    exp: int,
    element_m: float = 1.0,
    domain_m: float = 1.0,
    proficiency_m: float = 1.0,
    status_m: float = 1.0,
    form_m: float = 1.0,
    variant_m: float = 1.0,
    range_m: float = 1.0,
    ambush_m: float = 1.0,
) -> float:
    """招式/魔法攻擊威力（含距離衰減與偷襲劣勢）。"""
    bp = (
        (skill_base + stat_hidden / 2)
        * exp_mod(exp)
        * element_m
        * domain_m
        * proficiency_m
        * status_m
        * form_m
        * variant_m
        * range_m
        * ambush_m
    )
    return round(bp, 2)


def compute_domain_bp(
    precision: float,
    stat_hidden: float,
    exp: int,
    domain_m: float = 1.0,
    status_m: float = 1.0,
    form_m: float = 1.0,
    variant_m: float = 1.0,
    range_m: float = 1.0,
    ambush_m: float = 1.0,
) -> float:
    """結界張開的對抗威力（Domain_BP）。

    結界張開不是一般招式/近戰，而是展開領域壓迫對方。威力以：
    - 結界術精度（precision，0~1.5）為基數
    - 覺醒進度（awakening → Domain_Mod）為主要強度
    - 角色隱藏分數（施法對應）與經驗為輔

    公式：Domain_BP = (precision × 3 + Stat_hidden/2) × Exp_Mod × Domain_Mod
                     × Status_Mod × Form_Mod × Variant_Mod × Range_Mod × Ambush_Mod
    """
    base = precision * 3.0 + stat_hidden / 2
    bp = (
        base
        * exp_mod(exp)
        * domain_m
        * status_m
        * form_m
        * variant_m
        * range_m
        * ambush_m
    )
    return round(bp, 2)


def compute_melee_bp(
    weapon_base: float,
    str_hidden: float,
    martial: int,
    exp: int,
    domain_m: float = 1.0,
    status_m: float = 1.0,
    form_m: float = 1.0,
    variant_m: float = 1.0,
    range_m: float = 1.0,
    ambush_m: float = 1.0,
) -> float:
    """非魔法近戰威力（含距離衰減 Range_Mod）。

    近身交戰時 Range_Mod = 1.0；招式/普通攻擊在交戰距離超出近戰範圍時，
    由距離判定器給出 <1.0 的 Range_Mod（距離不符，無法有效命中）。
    """
    bp = (
        (weapon_base + str_hidden / 2)
        * (1 + martial / 200)
        * exp_mod(exp)
        * domain_m
        * status_m
        * form_m
        * variant_m
        * range_m
        * ambush_m
    )
    return round(bp, 2)


def compute_manamelee_bp(
    weapon_base: float,
    str_hidden: float,
    int_hidden: float,
    mp_invested: float,
    martial: int,
    exp: int,
    element_m: float = 1.0,
    proficiency_m: float = 1.0,
    status_m: float = 1.0,
    form_m: float = 1.0,
    variant_m: float = 1.0,
    range_m: float = 1.0,
    ambush_m: float = 1.0,
) -> float:
    """魔力包裹近戰威力（含距離衰減與偷襲劣勢）。"""
    bp = (
        (weapon_base + str_hidden / 2 + int_hidden / 2 + mp_invested)
        * (1 + martial / 200)
        * exp_mod(exp)
        * element_m
        * proficiency_m
        * status_m
        * form_m
        * variant_m
        * range_m
        * ambush_m
    )
    return round(bp, 2)


# ── MP_Invested 自動追回差距 ──────────────────────────────

MP_INVESTED_MAX = 3.0
MP_INVESTED_STEP = 0.5


def resolve_mp_invested(
    bp_factory,
    *,
    initial: float = 1.5,
    opponent_bp: float,
) -> tuple[float, float, list[str]]:
    """以初始 MP_Invested 計算該方 BP，落後時自動加碼追回差距。

    每次 +0.5 直到追回或達上限 3.0；加碼過程逐筆回傳說明（寫入思維鏈）。

    Args:
        bp_factory: callable(mp_invested) -> 該方 BP。
        initial: 初始投入 1.0~2.0。
        opponent_bp: 對手最終 BP。

    Returns:
        (mp_invested, final_bp, notes)
    """
    mp = min(max(initial, 1.0), MP_INVESTED_MAX)
    notes: list[str] = []
    bp = bp_factory(mp)
    notes.append(f"初始投入 {mp:.1f} → BP {bp}")
    while bp < opponent_bp and mp < MP_INVESTED_MAX:
        mp = round(min(mp + MP_INVESTED_STEP, MP_INVESTED_MAX), 1)
        bp = bp_factory(mp)
        notes.append(f"仍落後 {opponent_bp - bp:.2f} → 加碼至 {mp:.1f} → BP {bp}")
    return mp, bp, notes


# ── 招式效果（effect）機械套用 ─────────────────────────────
#
# 招式庫的 effect 欄位是自由文字；Phase A2（效果解析）每次對撞把它解析成結構化
# 效果清單，這裡以純函式**機械套用**（數值決定勝負，LLM 不重判）。
#
# 效果型別與參數：
#   shield  護盾/氣泡/防護罩  {absorb, blocks: "all"|"magic"|"physical",
#                             exempt: ["光", ...], duration}
#           blocks 控制擋魔法/物理/全部；exempt 為「元素例外」——攻擊為該元素時護盾**不生效**
#           （例：無效盾 blocks:"all"+exempt:["光"] → 光傷害以外的物理/魔法全數無效）
#   dodge   替身/分身/閃避強化 {bonus}          → 防禦方有效防禦 + bonus
#   buff    增益強化（自身）   {mult}            → 自身 BP × (1+mult)
#   debuff  削弱敵方           {penalty}         → 對方 BP × (1-penalty)
#   bind    束縛/控制/位移限制 {penalty}         → 對方 BP × (1-penalty)
#   summon  召喚協同           {damage_bonus}    → 自身 BP + damage_bonus
#   trap    陷阱/環境設置      {damage_bonus}    → 自身 BP + damage_bonus
#   heal    治療/回復          {amount}          → 不影響對撞數值（記帳性質）
#
# shield/dodge 為「防禦性效果」：只套用在該場對撞的**防禦方**（被攻擊方）身上。
# buff/debuff/bind/summon/trap 對自身或對方有效威力作用，與攻守位置無關。
# 所有效果附帶 note 供思維鏈/正文引用（如「水球氣泡」）。


def _fx_float(effect: dict, key: str, default: float = 0.0) -> float:
    try:
        return round(float(effect.get(key) or default), 2)
    except (TypeError, ValueError):
        return default


def _fx_blocks(
    blocks: str,
    atk_kind: str,
    atk_element: str = "",
    exempt: object = None,
) -> bool:
    """護盾是否能擋住該次攻擊。

    blocks 為 all 或與攻擊型別相符，且攻擊元素不在 exempt（元素例外）內才擋。
    """
    exempt_list = [str(e).strip() for e in (exempt or []) if str(e).strip()]
    if exempt_list and atk_element and atk_element in exempt_list:
        return False
    b = (blocks or "all").strip()
    if not b or b == "all":
        return True
    if b in ("magic", "魔法"):
        return atk_kind in ("magic", "manamelee")
    if b in ("physical", "物理"):
        return atk_kind == "melee"
    return False


def apply_effects(
    atk_bp: float,
    def_bp: float,
    atk_effects: list[dict] | None = None,
    def_effects: list[dict] | None = None,
    *,
    atk_kind: str = "magic",
    def_kind: str = "magic",
    atk_element: str = "",
    def_element: str = "",
) -> tuple[float, float, list[str]]:
    """機械套用雙方效果到有效 BP。

    順序：
    1. 防禦方 shield 逐層吸收來襲威力（blocks 相符且元素不在 exempt 才擋；吸收滿 → 氣泡破裂）
    2. 平面加成：防禦方 dodge → Def_BP + bonus；攻擊方 summon/trap → Atk_BP + damage_bonus
    3. 倍率：buff（自身）、debuff/bind（對方）
    4. heal 只記錄，不改數值

    Args:
        atk_bp / def_bp: 套用前雙方 BP。
        atk_effects / def_effects: 各自已解析的效果清單（[{type, ...params}]）。
        atk_kind / def_kind: 雙方招式型別（magic/melee/manamelee/domain），
            供護盾 blocks 過濾（擋住的是「來襲方」的型別）。
        atk_element / def_element: 雙方招式元素（自然系才有值，如光/暗/火），
            供護盾 exempt 過濾（例：無效盾 exempt:["光"] → 光傷害穿透）。

    Returns:
        (final_atk_bp, final_def_bp, log)
    """
    atk_effects = atk_effects or []
    def_effects = def_effects or []
    log: list[str] = []

    # 1. 防禦方護盾（absorb 來襲威力）
    for fx in def_effects:
        if str(fx.get("type") or "").lower() != "shield":
            continue
        absorb = _fx_float(fx, "absorb")
        if absorb <= 0:
            continue
        exempt_list = [str(e).strip() for e in (fx.get("exempt") or []) if str(e).strip()]
        if exempt_list and atk_element and atk_element in exempt_list:
            log.append(
                f"護盾「{fx.get('note') or '?'}」不生效：{atk_element}屬性的攻擊穿透，護盾被突破"
            )
            continue
        if not _fx_blocks(fx.get("blocks") or "", atk_kind):
            log.append(f"護盾「{fx.get('note') or '?'}」不生效（本次為{atk_kind}攻擊，型別不符）")
            continue
        blocked = min(absorb, atk_bp)
        atk_bp = round(atk_bp - blocked, 2)
        name = fx.get("note") or fx.get("type") or "護盾"
        if blocked >= absorb:
            log.append(f"護盾「{name}」抵擋 {blocked:.2f} BP，完全吸收（氣泡破裂）")
        else:
            log.append(
                f"護盾「{name}」抵擋 {blocked:.2f} BP，剩餘吸收 {round(absorb - blocked, 2):.2f}"
            )

    # 2. 平面加成
    for fx in def_effects:
        if str(fx.get("type") or "").lower() == "dodge":
            bonus = _fx_float(fx, "bonus")
            if bonus > 0:
                def_bp = round(def_bp + bonus, 2)
                log.append(f"替身/閃避「{fx.get('note') or '?'}」：有效防禦 +{bonus:.2f}")
    for fx in atk_effects:
        t = str(fx.get("type") or "").lower()
        if t in ("summon", "trap"):
            dmg = _fx_float(fx, "damage_bonus")
            if dmg > 0:
                atk_bp = round(atk_bp + dmg, 2)
                log.append(f"「{fx.get('note') or t}」：威力 +{dmg:.2f}")

    # 3. 倍率（buff 自身 / debuff·bind 對方）
    for fx in atk_effects:
        t = str(fx.get("type") or "").lower()
        if t == "buff":
            mult = _fx_float(fx, "mult")
            if mult > 0:
                atk_bp = round(atk_bp * (1 + mult), 2)
                log.append(f"增益「{fx.get('note') or '?'}」：自身威力 ×{round(1 + mult, 2):.2f}")
    for fx in def_effects:
        t = str(fx.get("type") or "").lower()
        if t == "buff":
            mult = _fx_float(fx, "mult")
            if mult > 0:
                def_bp = round(def_bp * (1 + mult), 2)
                log.append(f"增益「{fx.get('note') or '?'}」：自身防禦 ×{round(1 + mult, 2):.2f}")
    for fx in atk_effects:
        t = str(fx.get("type") or "").lower()
        if t in ("debuff", "bind"):
            penalty = _fx_float(fx, "penalty")
            if 0 < penalty < 1:
                def_bp = round(def_bp * (1 - penalty), 2)
                log.append(f"削弱「{fx.get('note') or t}」：對方威力 ×{round(1 - penalty, 2):.2f}")
    for fx in def_effects:
        t = str(fx.get("type") or "").lower()
        if t in ("debuff", "bind"):
            penalty = _fx_float(fx, "penalty")
            if 0 < penalty < 1:
                atk_bp = round(atk_bp * (1 - penalty), 2)
                log.append(f"削弱「{fx.get('note') or t}」：對方威力 ×{round(1 - penalty, 2):.2f}")

    # 4. heal 記錄
    for fx in atk_effects:
        if str(fx.get("type") or "").lower() == "heal":
            log.append(f"治療「{fx.get('note') or '?'}」回復 {_fx_float(fx, 'amount'):.0f}（記帳，不影響本次勝負）")
    for fx in def_effects:
        if str(fx.get("type") or "").lower() == "heal":
            log.append(f"治療「{fx.get('note') or '?'}」回復 {_fx_float(fx, 'amount'):.0f}（記帳，不影響本次勝負）")

    return atk_bp, def_bp, log


# ── 四階判定 ──────────────────────────────────────────────

VERDICT_DECISIVE = "絕對碾壓"
VERDICT_SUPPRESS = "優勢壓制"
VERDICT_STALEMATE = "招式僵持"
VERDICT_FUSION = "結界交疊"


def decide(atk_bp: float, def_bp: float) -> str:
    """依 Atk vs Def 判定階級（不含結界融合，該邏輯另由 collide 流程處理）。"""
    if atk_bp > def_bp * 2.0:
        return VERDICT_DECISIVE
    if atk_bp > def_bp:
        return VERDICT_SUPPRESS
    return VERDICT_STALEMATE


# ── 純迴避 / 純防禦（非對抗性動作）──────────────────────

def compute_evade_bp(
    agility_hidden: float,
    status_m: float = 1.0,
    form_m: float = 1.0,
    domain_m: float = 1.0,
    ambush_m: float = 1.0,
) -> float:
    """純迴避基準：敏捷隱藏分數 × 修正（無攻擊威力）。偷襲時被偷襲方 ambush_m < 1.0。"""
    return round(agility_hidden * status_m * form_m * domain_m * ambush_m, 2)


def compute_guard_bp(
    constitution_hidden: float,
    status_m: float = 1.0,
    form_m: float = 1.0,
    domain_m: float = 1.0,
    guard_bonus: float = 0.0,
    ambush_m: float = 1.0,
) -> float:
    """純防禦基準：體質隱藏分數 × 修正（無攻擊威力）。防禦招式可加 0.3 格擋加成。"""
    return round(constitution_hidden * status_m * form_m * domain_m * (1.0 + guard_bonus) * ambush_m, 2)


def decide_nonaggressive(atk_bp: float, def_bp: float) -> str:
    """非對抗性動作判定（Atk vs Def_BP，無攻守對調——防禦方即非對抗方）。"""
    if atk_bp > def_bp * 2.0:
        return "命中重創"
    if atk_bp > def_bp:
        return "部分命中"
    if atk_bp >= def_bp:
        return "成功迴避/防禦"
    return "徹底化解"


def decide_with_domain(
    atk_bp: float,
    def_bp: float,
    domain_a: float,
    domain_d: float,
    mp_invested: int = 0,
) -> tuple[str, bool, float]:
    """含結界融合的完整判定。

    規則：
    - 任一方未開結界（domain_mod ≤ 1.0，即未覺醒/未張開）：**不觸發結界融合/覆蓋**，
      直接依威力比較判定——未開方以其未開結界 BP（Domain_Mod=1.0）對抗對方開結界 BP，
      正常計算。簡易結界無法與張開的結界對抗。
    - 雙方皆開結界且 Domain_Mod 差距 < 0.3 → 結界交疊（融合）
    - 差距 ≥ 0.3 → 結界被覆蓋；被覆蓋方可耗魔力（每 1 點 +0.03）補回
      （原始差距 ≥ 0.6 無法彌補）
    - 未觸發融合 → 依威力比較判定

    Returns:
        (verdict, domain_fusion, effective_gap)
    """
    both_opened = domain_a > 1.0 and domain_d > 1.0
    if not both_opened:
        return decide(atk_bp, def_bp), False, round(abs(domain_a - domain_d), 2)
    gap = round(abs(domain_a - domain_d), 2)
    if gap >= 0.3:
        original_gap = gap
        if original_gap < 0.6:
            covered = min(domain_a, domain_d)
            needed = 0.3 - gap
            mp_needed = int(needed // 0.03) + (1 if needed % 0.03 > 0 else 0)
            mp_needed = max(0, min(mp_needed, mp_invested))
            covered += mp_needed * 0.03
            gap = round(abs(covered - max(domain_a, domain_d)), 2)
            if gap < 0.3:
                return VERDICT_FUSION, True, gap
        # 仍被覆蓋，依威力比較
        return decide(atk_bp, def_bp), False, gap
    return VERDICT_FUSION, True, gap


_DOMAIN_RULE_KEYS = ("rule_self", "rule_enemy", "rule_environment")
_BURN_KEYWORDS = ("燒除", "燒掉", "規則效果與特殊效果")


def domain_rule_text(dom: dict) -> str:
    return " ".join(str(dom.get(k) or "") for k in _DOMAIN_RULE_KEYS)


def domain_fusion_effects(dom_a: dict | None, dom_b: dict | None) -> dict:
    """結界交疊/融合時的特殊規則處理（機械判定，不回傳計算細節以外的判斷）。

    規則：
    1. 必中中和：任一方結界規則含「必中」→ 融合時雙方結界的必中效果互相中和，
       皆無法獲得必中優勢（雙方平手，必中不疊加、不偏袒任一方）。
    2. 特殊規則融合：雙方結界的特殊規則（rule_self / rule_enemy / rule_environment）
       合併為融合領域的規則。
    3. 燒除規則：任一結界規則含「燒除/無效化規則與特殊效果」描述 → 融合領域內所有
       規則效果與特殊效果被燒除，雙方只能以純粹的蠻力對抗。
    """
    dom_a, dom_b = dom_a or {}, dom_b or {}
    text_a, text_b = domain_rule_text(dom_a), domain_rule_text(dom_b)

    hit_cancel = "必中" in text_a or "必中" in text_b
    burn = any(kw in text_a or kw in text_b for kw in _BURN_KEYWORDS)

    merged = {}
    for key in _DOMAIN_RULE_KEYS:
        parts = []
        for dom in (dom_a, dom_b):
            if dom.get(key):
                parts.append(f"{dom.get('name', '結界')}：{dom[key]}")
        merged[key] = "；".join(parts) if parts else "無"

    return {
        "必中中和": {
            "active": hit_cancel,
            "desc": "雙方結界的必中效果互相中和，皆無法獲得必中優勢" if hit_cancel else "雙方無必中效果",
        },
        "特殊規則融合": merged,
        "燒除規則": {
            "active": burn,
            "desc": "融合領域內規則效果與特殊效果被燒除，雙方只能以純粹的蠻力對抗" if burn else "無燒除規則，融合領域特殊規則生效",
        },
        "對抗方式": "純粹的蠻力對抗" if burn else "融合領域特殊規則生效",
    }


# ── 實戰經驗等級 ──────────────────────────────────────────

def exp_tier(exp: int) -> dict:
    """回傳該經驗值對應的等級資訊。"""
    for lo, hi, mod_lo, mod_hi, name, desc in EXP_TIERS:
        if lo <= exp <= hi:
            return {"name": name, "range": (lo, hi), "exp_mod": (mod_lo, mod_hi), "desc": desc}
    return {"name": "未知", "range": (0, 0), "exp_mod": (1.0, 1.2), "desc": ""}


if __name__ == "__main__":
    # 簡易自測
    hs = hidden_score("B", 1.0)
    print("B hidden (mod 1.0) =", hs)                      # 4.0
    print("S hidden (mod 1.5) =", hidden_score("S", 1.5))  # 9.0
    print("火攻水 =", element_mod("火", "水"))             # 0.8
    print("火攻風 =", element_mod("火", "風"))             # 1.0
    print("exp 50 =", exp_mod(50))                          # 1.1

    atk = compute_atk_bp(7.5, hidden_score("A", 1.0), 50, element_m=1.2, proficiency_m=1.5)
    print("Atk_BP =", atk)
    for e in (10, 50, 90):
        print("exp", e, "->", exp_tier(e)["name"], exp_tier(e)["exp_mod"])

    ev = compute_evade_bp(hidden_score("S", 1.0))
    print("Evade_BP (S agility) =", ev)
    print("Atk vs Evade:", decide_nonaggressive(atk, ev))
    gd = compute_guard_bp(hidden_score("B", 1.0), guard_bonus=0.3)
    print("Guard_BP (B con, +0.3) =", gd)
    print("Atk vs Guard:", decide_nonaggressive(atk, gd))

    print("weapon bare:", resolve_weapon_override("bare", None, ""))
    print("weapon longsword:", resolve_weapon_override("one_hand", None, "長劍"))
    print("weapon legendary override:", resolve_weapon_override("heavy", 5.0, "聖劍"))

    def manabp(mp):
        return compute_manamelee_bp(3.0, 4.0, 4.0, mp, 60, 50)

    mp, bp, notes = resolve_mp_invested(manabp, opponent_bp=25.0)
    print("MP chase:", mp, bp)
    for n in notes:
        print("  ", n)

    # 結界判定：任一方未開結界（≤1.0）不觸發融合/覆蓋
    print("both closed (1.0/1.0):", decide_with_domain(20.0, 10.0, 1.0, 1.0))
    print("one closed (1.0 vs 1.4):", decide_with_domain(12.0, 10.0, 1.0, 1.4))
    print("both opened gap<0.3 (1.4/1.25):", decide_with_domain(15.0, 10.0, 1.4, 1.25))
    print("both opened gap>=0.3 (1.5/1.1):", decide_with_domain(15.0, 10.0, 1.5, 1.1))

    # 20 元素剋制草稿（引用世界元素表；一元素可多弱點）
    print("光攻暗 =", element_mod("光", "暗"), "| 暗攻光 =", element_mod("暗", "光"))
    print("水攻火 =", element_mod("水", "火"), "| 火攻水 =", element_mod("火", "水"), "(水剋火，火不反剋)")
    print("水被剋: 冰/雷/葉 =", element_mod("冰", "水"), element_mod("雷", "水"), element_mod("葉", "水"))
    print("雷攻鐵 =", element_mod("雷", "鐵"), "| 時間攻無限 =", element_mod("時間", "無限"))

    # 未登記變體基準（Skill_Base = 該能力最低 power_base；Variant_Mod 1.05 由公式套用）
    print("變體 Skill_Base [5.0, 8.0]:", variant_skill_base([5.0, 8.0]), "× variant 1.05 =", round(variant_skill_base([5.0, 8.0]) * 1.05, 2))
    print("變體 Skill_Base [7.5]:", variant_skill_base([7.5]))

    # 反派動態難度校準基準
    roster = [
        {"name": "凌空", "stats": {"力量": "B", "敏捷": "A", "智力": "C", "體質": "B", "魅力": "D", "幸運": "E"},
         "exp": 25, "martial": 60, "max_mp": 45},
        {"name": "澪", "stats": {"力量": "C", "敏捷": "B", "智力": "S", "體質": "B", "魅力": "A", "幸運": "B"},
         "exp": 40, "martial": 20, "max_mp": 80},
    ]
    print("calibrate:", villain_calibration_baseline(roster, ratio=1.1))

    # 結界融合特殊規則（必中中和 / 特殊規則融合 / 燒除規則）
    a_dom = {"name": "地獄水牢", "rule_self": "水屬性招式威力提升；結界必中效果（攻勢不可閃避）",
             "rule_enemy": "行動受限且逐漸窒息", "rule_environment": "場地化為水牢環境"}
    b_dom = {"name": "龍王禁地", "rule_self": "龍王之力加護，火焰威力增強；龍王火焰能燒除神月潛意識覺得麻煩的規則效果與特殊效果，雙方只能以純粹的蠻力對抗",
             "rule_enemy": "環境灼熱，持續火焰傷害，行動遲緩", "rule_environment": "火山地形，岩漿與烈火遍布，高溫環境"}
    fx = domain_fusion_effects(a_dom, b_dom)
    assert fx["必中中和"]["active"] is True, "含必中應觸發必中中和"
    assert fx["燒除規則"]["active"] is True, "含燒除規則應觸發"
    assert fx["對抗方式"] == "純粹的蠻力對抗"
    assert "龍王禁地" in fx["特殊規則融合"]["rule_self"] and "地獄水牢" in fx["特殊規則融合"]["rule_self"]
    print("fusion_effects:", fx)
    assert domain_fusion_effects({"name": "甲", "rule_self": "普通水牢"}, {"name": "乙", "rule_self": "普通火界"})["必中中和"]["active"] is False

    # 招式效果機械套用
    fx_atk = [
        {"type": "buff", "mult": 0.15, "note": "燃火增幅"},
        {"type": "summon", "damage_bonus": 1.2, "note": "火焰分身協同"},
    ]
    fx_def = [
        {"type": "shield", "absorb": 2.5, "blocks": "magic", "note": "水球氣泡"},
        {"type": "dodge", "bonus": 0.5, "note": "烈焰替身"},
        {"type": "debuff", "penalty": 0.1, "note": "水氣干擾"},
    ]
    a0, d0 = 20.0, 12.0
    a1, d1, log = apply_effects(a0, d0, fx_atk, fx_def, atk_kind="magic", def_kind="magic")
    # 護盾吸收 2.5 → 17.5；summon +1.2 → 18.7；buff ×1.15 → 21.505
    # 防禦方 debuff 削攻擊方 ×0.9 → 19.3545
    # dodge +0.5 → 12.5（防禦方自己的 debuff 不影響自己）
    assert abs(a1 - 19.35) < 0.01, f"atk={a1}"
    assert abs(d1 - 12.5) < 0.01, f"def={d1}"
    assert any("水球氣泡" in l and "破裂" in l for l in log), "吸收滿應標記氣泡破裂"
    print("effects:", a0, d0, "->", a1, d1)
    for l in log:
        print("  ", l)
    # 物理護盾不擋魔法
    a2, d2, log2 = apply_effects(20.0, 12.0, [], [{"type": "shield", "absorb": 2.5, "blocks": "physical", "note": "岩壁"}], atk_kind="magic")
    assert a2 == 20.0, "physical 護盾不應擋魔法"
    # 護盾未滿：剩餘吸收
    a3, d3, log3 = apply_effects(1.0, 12.0, [], [{"type": "shield", "absorb": 2.5, "blocks": "all", "note": "氣泡"}], atk_kind="magic")
    assert a3 == 0.0 and any("剩餘吸收" in l for l in log3)
    # 無效盾（blocks all + exempt 光）：非光傷害全數無效
    null_shield = [{"type": "shield", "absorb": 999, "blocks": "all", "exempt": ["光"], "note": "暗影潛形"}]
    a4, d4, log4 = apply_effects(20.0, 12.0, [], null_shield, atk_kind="magic", atk_element="暗")
    assert a4 == 0.0, f"無效盾應完全吸收，got atk={a4}"
    # 光屬性的攻擊穿透無效盾
    a5, d5, log5 = apply_effects(20.0, 12.0, [], null_shield, atk_kind="magic", atk_element="光")
    assert a5 == 20.0, f"光屬性應穿透無效盾，got atk={a5}"
    assert any("穿透" in l for l in log5), "應記錄光屬性穿透"
    # 光屬性但護盾無 exempt → 正常擋
    a6, d6, log6 = apply_effects(20.0, 12.0, [], [{"type": "shield", "absorb": 5.0, "blocks": "all", "note": "氣泡"}], atk_kind="magic", atk_element="光")
    assert a6 == 15.0, f"無 exempt 護盾應正常擋光，got atk={a6}"
