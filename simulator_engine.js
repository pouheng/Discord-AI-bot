/*
simulator_engine.js — 招式對撞模擬器的 JS 引擎
由 collide_engine.py 逐函式鏡像移植（相同運算順序，與正式上線一致）。
常數取自 globalThis.SIM_DATA（由 make_simulator_html.py 注入，來源為 collide_rules.py）。
可用於瀏覽器（window.SIM_DATA）或 Node（global.SIM_DATA = require(...)）。
*/

"use strict";

(function (root) {
  const D = root.SIM_DATA || {};

  const RATING_CONST = D.RATING_CONST || {};
  const HIDDEN_SCORE_MAX = D.HIDDEN_SCORE_MAX || 9.0;
  const STAT_KEYS = D.STAT_KEYS || [];
  const RATING_REVERSE = D.RATING_REVERSE || {};
  const WEAPON_BASE_TABLE = D.WEAPON_BASE_TABLE || {};
  const WEAPON_LABELS = D.WEAPON_LABELS || {};
  const ELEMENT_GRID = D.ELEMENT_GRID || {};
  const DOMAIN_MOD_TIERS = D.DOMAIN_MOD_TIERS || {};
  const EXP_TIERS = D.EXP_TIERS || [];
  const MP_INVESTED_MAX = D.MP_INVESTED_MAX || 3.0;
  const MP_INVESTED_STEP = D.MP_INVESTED_STEP || 0.5;

  // Python 的 round()：四捨五入到指定位數（正數情境與 JS 一致；tie 採 half-even）
  function pyRound(x, nd) {
    if (nd === undefined) nd = 2;
    if (!isFinite(x)) return x;
    const p = Math.pow(10, nd);
    const f = new Float64Array(1);
    const u = new BigUint64Array(f.buffer);
    f[0] = x;
    const bits = u[0];
    const sign = (bits >> 63n) ? -1 : 1;
    let exp = Number((bits >> 52n) & 0x7ffn);
    let m = bits & 0xfffffffffffffn;
    const shift = exp === 0 ? -1074 : exp - 1075;
    if (exp !== 0) m |= 0x10000000000000n;
    const M = m * BigInt(p);
    let scaled;
    if (shift >= 0) {
      scaled = M << BigInt(shift);
    } else {
      const q = -shift;
      const den = 1n << BigInt(q);
      const qd = M / den;
      const rem = M % den;
      if (rem === 0n) scaled = qd;
      else {
        const half = den >> 1n;
        if (rem > half) scaled = qd + 1n;
        else if (rem < half) scaled = qd;
        else scaled = (qd & 1n) === 0n ? qd : qd + 1n;
      }
    }
    return sign * Number(scaled) / p;
  }

  function num(v) { return Number(v); }

  function pyStr(x) { return Number.isInteger(x) ? x + ".0" : String(x); }

  // ── 隱藏分數 ──────────────────────────────────────────────
  function hiddenScore(rating, raceMod) {
    if (raceMod === undefined) raceMod = 1.0;
    const c = RATING_CONST[rating] || 0;
    return Math.min(pyRound(c * raceMod, 2), HIDDEN_SCORE_MAX);
  }

  // ── 武器基數（機械取值）──────────────────────────────────
  function weaponBase(category, override) {
    if (override !== undefined && override !== null) return pyRound(num(override), 2);
    const v = WEAPON_BASE_TABLE[category];
    return num(v === undefined ? WEAPON_BASE_TABLE["bare"] : v);
  }

  function resolveWeaponOverride(category, override, charWeapon) {
    charWeapon = charWeapon || "";
    if (override !== undefined && override !== null) {
      return [weaponBase(category, override), "管理員覆寫 weapon_base=" + pyStr(override)];
    }
    const cat = category || (charWeapon ? "light" : "bare");
    const base = weaponBase(cat);
    const label = WEAPON_LABELS[cat] || "徒手";
    return [base, (charWeapon || "無武器") + " → " + label + " → Weapon_Base " + pyStr(base)];
  }

  // ── 元素相剋 ──────────────────────────────────────────────
  function elementMod(attackerElement, defenderElement) {
    if (!attackerElement || !defenderElement) return 1.0;
    const grid = ELEMENT_GRID[attackerElement] || {};
    const v = grid[defenderElement];
    return v === undefined ? 1.0 : num(v);
  }

  // ── 經驗 / 熟練度 / 型態 / 變體 ──────────────────────────
  function expMod(exp) { return 1.0 + exp / 500.0; }
  function proficiencyMod(p) { return 1.0 + p / 100.0; }

  function variantSkillBase(existingPowerBases) {
    if (!existingPowerBases || existingPowerBases.length === 0) return 1.0;
    let m = Infinity;
    for (const b of existingPowerBases) m = Math.min(m, num(b));
    return pyRound(m, 2);
  }

  function domainMod(awakening, inDomain) {
    if (inDomain === undefined) inDomain = true;
    if (!inDomain) return 1.0;
    const v = DOMAIN_MOD_TIERS[awakening || "未覺醒"];
    return v === undefined ? 1.0 : num(v);
  }

  // ── 三條威力公式 ──────────────────────────────────────────
  function computeAtkBp(skillBase, statHidden, exp,
    elementM, domainM, proficiencyM, statusM, formM, variantM, rangeM, ambushM) {
    if (elementM === undefined) elementM = 1.0; if (domainM === undefined) domainM = 1.0;
    if (proficiencyM === undefined) proficiencyM = 1.0; if (statusM === undefined) statusM = 1.0;
    if (formM === undefined) formM = 1.0; if (variantM === undefined) variantM = 1.0;
    if (rangeM === undefined) rangeM = 1.0; if (ambushM === undefined) ambushM = 1.0;
    const bp = (skillBase + statHidden / 2)
      * expMod(exp) * elementM * domainM * proficiencyM
      * statusM * formM * variantM * rangeM * ambushM;
    return pyRound(bp, 2);
  }

  function computeDomainBp(precision, statHidden, exp,
    domainM, statusM, formM, variantM, rangeM, ambushM) {
    if (domainM === undefined) domainM = 1.0; if (statusM === undefined) statusM = 1.0;
    if (formM === undefined) formM = 1.0; if (variantM === undefined) variantM = 1.0;
    if (rangeM === undefined) rangeM = 1.0; if (ambushM === undefined) ambushM = 1.0;
    const base = precision * 3.0 + statHidden / 2;
    const bp = base * expMod(exp) * domainM
      * statusM * formM * variantM * rangeM * ambushM;
    return pyRound(bp, 2);
  }

  function computeMeleeBp(weaponBaseV, strHidden, martial, exp,
    domainM, statusM, formM, variantM, rangeM, ambushM) {
    if (domainM === undefined) domainM = 1.0; if (statusM === undefined) statusM = 1.0;
    if (formM === undefined) formM = 1.0; if (variantM === undefined) variantM = 1.0;
    if (rangeM === undefined) rangeM = 1.0; if (ambushM === undefined) ambushM = 1.0;
    const bp = (weaponBaseV + strHidden / 2)
      * (1 + martial / 200) * expMod(exp)
      * domainM * statusM * formM * variantM * rangeM * ambushM;
    return pyRound(bp, 2);
  }

  function computeManaMeleeBp(weaponBaseV, strHidden, intHidden, mpInvested, martial, exp,
    elementM, proficiencyM, statusM, formM, variantM, rangeM, ambushM) {
    if (elementM === undefined) elementM = 1.0; if (proficiencyM === undefined) proficiencyM = 1.0;
    if (statusM === undefined) statusM = 1.0; if (formM === undefined) formM = 1.0;
    if (variantM === undefined) variantM = 1.0; if (rangeM === undefined) rangeM = 1.0;
    if (ambushM === undefined) ambushM = 1.0;
    const bp = (weaponBaseV + strHidden / 2 + intHidden / 2 + mpInvested)
      * (1 + martial / 200) * expMod(exp)
      * elementM * proficiencyM * statusM * formM * variantM * rangeM * ambushM;
    return pyRound(bp, 2);
  }

  // ── MP_Invested 自動追回差距 ──────────────────────────────
  function resolveMpInvested(bpFactory, opts) {
    opts = opts || {};
    let mp = Math.min(Math.max(opts.initial === undefined ? 1.5 : opts.initial, 1.0), MP_INVESTED_MAX);
    const opponentBp = opts.opponentBp;
    const notes = [];
    let bp = bpFactory(mp);
    notes.push("初始投入 " + mp.toFixed(1) + " → BP " + bp);
    while (bp < opponentBp && mp < MP_INVESTED_MAX) {
      mp = Math.round(Math.min(mp + MP_INVESTED_STEP, MP_INVESTED_MAX) * 10) / 10;
      bp = bpFactory(mp);
      notes.push("仍落後 " + (opponentBp - bp).toFixed(2) + " → 加碼至 " + mp.toFixed(1) + " → BP " + bp);
    }
    return [mp, bp, notes];
  }

  // ── 四階判定 ──────────────────────────────────────────────
  const VERDICT_DECISIVE = "絕對碾壓";
  const VERDICT_SUPPRESS = "優勢壓制";
  const VERDICT_STALEMATE = "招式僵持";
  const VERDICT_FUSION = "結界交疊";

  function decide(atkBp, defBp) {
    if (atkBp > defBp * 2.0) return VERDICT_DECISIVE;
    if (atkBp > defBp) return VERDICT_SUPPRESS;
    return VERDICT_STALEMATE;
  }

  function computeEvadeBp(agilityHidden, statusM, formM, domainM, ambushM) {
    if (statusM === undefined) statusM = 1.0; if (formM === undefined) formM = 1.0;
    if (domainM === undefined) domainM = 1.0; if (ambushM === undefined) ambushM = 1.0;
    return pyRound(agilityHidden * statusM * formM * domainM * ambushM, 2);
  }

  function computeGuardBp(constitutionHidden, statusM, formM, domainM, guardBonus, ambushM) {
    if (statusM === undefined) statusM = 1.0; if (formM === undefined) formM = 1.0;
    if (domainM === undefined) domainM = 1.0; if (guardBonus === undefined) guardBonus = 0.0;
    if (ambushM === undefined) ambushM = 1.0;
    return pyRound(constitutionHidden * statusM * formM * domainM * (1.0 + guardBonus) * ambushM, 2);
  }

  function decideNonaggressive(atkBp, defBp) {
    if (atkBp > defBp * 2.0) return "命中重創";
    if (atkBp > defBp) return "部分命中";
    if (atkBp >= defBp) return "成功迴避/防禦";
    return "徹底化解";
  }

  function decideWithDomain(atkBp, defBp, domainA, domainD, mpInvested) {
    if (mpInvested === undefined) mpInvested = 0;
    const bothOpened = domainA > 1.0 && domainD > 1.0;
    if (!bothOpened) {
      return [decide(atkBp, defBp), false, pyRound(Math.abs(domainA - domainD), 2)];
    }
    let gap = pyRound(Math.abs(domainA - domainD), 2);
    if (gap >= 0.3) {
      const originalGap = gap;
      if (originalGap < 0.6) {
        let covered = Math.min(domainA, domainD);
        const needed = 0.3 - gap;
        let mpNeeded = Math.floor(needed / 0.03) + (needed % 0.03 > 0 ? 1 : 0);
        mpNeeded = Math.max(0, Math.min(mpNeeded, mpInvested));
        covered += mpNeeded * 0.03;
        gap = pyRound(Math.abs(covered - Math.max(domainA, domainD)), 2);
        if (gap < 0.3) return [VERDICT_FUSION, true, gap];
      }
      return [decide(atkBp, defBp), false, gap];
    }
    return [VERDICT_FUSION, true, gap];
  }

  // ── 實戰經驗等級 ──────────────────────────────────────────
  function expTier(exp) {
    for (const t of EXP_TIERS) {
      const lo = t[0], hi = t[1];
      if (exp >= lo && exp <= hi) {
        return { name: t[4], range: [lo, hi], exp_mod: [t[2], t[3]], desc: t[5] };
      }
    }
    return { name: "未知", range: [0, 0], exp_mod: [1.0, 1.2], desc: "" };
  }

  // ── 反派動態難度校準基準 ──────────────────────────────────
  function hiddenOf(char, stat) {
    const h = char["stat_" + stat + "_hidden"];
    if (h !== undefined && h !== null) return num(h);
    const st = char.stats || {};
    const rating = st[stat];
    let raceMod;
    if (char._race_mods && typeof char._race_mods === "object" && char._race_mods[stat] !== undefined) {
      raceMod = num(char._race_mods[stat]);
    } else {
      raceMod = num(char._race_mod === undefined || char._race_mod === null ? 1.0 : char._race_mod);
    }
    return rating ? hiddenScore(rating, raceMod) : 0.0;
  }

  function ratingForHidden(target, raceMod) {
    if (raceMod === undefined) raceMod = 1.0;
    for (let c = 1; c <= 6; c++) {
      if (c * raceMod >= target) return RATING_REVERSE[c];
    }
    return "S";
  }

  function villainCalibrationBaseline(roster, opts) {
    opts = opts || {};
    const ratio = opts.ratio === undefined ? 1.1 : opts.ratio;
    const raceMods = opts.raceMods || {};
    const raceMod = opts.raceMod === undefined ? 1.0 : opts.raceMod;
    const maxHidden = {};
    let strongestName = "";
    let strongestScore = -1.0;
    for (const c of roster) {
      let score = 0;
      for (const s of STAT_KEYS) score += hiddenOf(c, s);
      score += (num(c.max_mp) || 0) / 10;
      if (score > strongestScore) { strongestScore = score; strongestName = c.name || "未知"; }
      for (const s of STAT_KEYS) {
        const h = hiddenOf(c, s);
        maxHidden[s] = maxHidden[s] === undefined ? h : Math.max(maxHidden[s], h);
      }
    }
    const targets = {};
    const stats = {};
    for (const s of STAT_KEYS) {
      targets[s] = Math.min(maxHidden[s] === undefined ? 0 : maxHidden[s] * ratio, HIDDEN_SCORE_MAX);
      stats[s] = ratingForHidden(targets[s], raceMods[s] !== undefined ? raceMods[s] : raceMod);
    }
    let maxExp = 0, maxMartial = 0, maxMp = 10;
    for (const c of roster) {
      maxExp = Math.max(maxExp, num(c.exp) || 0);
      maxMartial = Math.max(maxMartial, num(c.martial) || 0);
      maxMp = Math.max(maxMp, num(c.max_mp) || 10);
    }
    const rounded = {};
    for (const s of STAT_KEYS) rounded[s] = pyRound(targets[s], 2);
    return {
      stats: stats,
      exp: Math.min(maxExp + 10, 100),
      martial: Math.min(maxMartial + 5, 100),
      max_mp: Math.min(Math.max(maxMp, maxMp + 10), 100),
      targets: rounded,
      strongest: strongestName,
      ratio: ratio,
    };
  }

  root.CollideEngine = {
    pyRound, hiddenScore, weaponBase, resolveWeaponOverride, elementMod,
    expMod, proficiencyMod, variantSkillBase, domainMod,
    computeAtkBp, computeDomainBp, computeMeleeBp, computeManaMeleeBp, resolveMpInvested,
    decide, computeEvadeBp, computeGuardBp, decideNonaggressive, decideWithDomain,
    expTier, villainCalibrationBaseline, hiddenOf, ratingForHidden,
    VERDICT_DECISIVE, VERDICT_SUPPRESS, VERDICT_STALEMATE, VERDICT_FUSION,
    STAT_KEYS, RATING_REVERSE,
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { CollideEngine: root.CollideEngine };
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
