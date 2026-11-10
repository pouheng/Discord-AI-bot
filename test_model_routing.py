"""test_model_routing.py — 全域備援（主線路由場景決定、備援不分場景）。"""

import io
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import llm_models as L

SUCCESS = 0
FAIL = 0


def check(name, condition, detail=""):
    global SUCCESS, FAIL
    if condition:
        SUCCESS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


saved = io.open(L.MODELS_FILE, encoding="utf-8").read()
try:
    raw = json.loads(saved)
    raw["fallback_models"] = {
        # 舊格式：綁在模型 ID 上
        "m:flash": ["m:sg", "m:cn"],
    }
    raw["default"] = "m:pro"
    raw["plot"] = "m:flash"
    io.open(L.MODELS_FILE, "w", encoding="utf-8").write(
        json.dumps(raw, ensure_ascii=False, indent=2)
    )

    print("[1] 沒有全域設定時，沿用舊格式（單一模型專屬）")
    check("flash 有自己的備援", L.model_chain("plot") == ["m:flash", "m:sg", "m:cn"],
          str(L.model_chain("plot")))
    check("pro 沒有備援", L.model_chain("default") == ["m:pro"],
          str(L.model_chain("default")))

    print("[2] 設定全域備援後，所有場景共用")
    L.save_global_fallbacks(["m:sg", "m:cn"])
    check("pro 也有備援了", L.model_chain("default") == ["m:pro", "m:sg", "m:cn"],
          str(L.model_chain("default")))
    check("flash 仍正常", L.model_chain("plot") == ["m:flash", "m:sg", "m:cn"],
          str(L.model_chain("plot")))

    print("[3] 主線路不會重複出現在備援裡")
    raw = json.loads(io.open(L.MODELS_FILE, encoding="utf-8").read())
    raw["fallback_models"]["*"] = ["m:pro", "m:sg"]
    io.open(L.MODELS_FILE, "w", encoding="utf-8").write(
        json.dumps(raw, ensure_ascii=False, indent=2))
    check("重複項被去重", L.model_chain("default") == ["m:pro", "m:sg"],
          str(L.model_chain("default")))

    print("[4] 儲存備援不會動到各場景模型")
    raw = json.loads(io.open(L.MODELS_FILE, encoding="utf-8").read())
    before = {s: raw.get(s) for s, _ in L.SLOTS}
    L.save_global_fallbacks(["m:sg"])
    raw2 = json.loads(io.open(L.MODELS_FILE, encoding="utf-8").read())
    after = {s: raw2.get(s) for s, _ in L.SLOTS}
    check("場景模型完全沒變", before == after, f"{before} -> {after}")
    check("備援寫在 * 底下", raw2["fallback_models"].get("*") == ["m:sg"],
          str(raw2["fallback_models"]))

    print("[5] 清空備援")
    L.save_global_fallbacks([])
    raw3 = json.loads(io.open(L.MODELS_FILE, encoding="utf-8").read())
    check("* 被移除", "*" not in raw3["fallback_models"],
          str(raw3["fallback_models"]))
    check("清空後只剩主線路", L.model_chain("default") == ["m:pro"],
          str(L.model_chain("default")))
    print("[6] Phase 2 一律走「回覆」場景")
    import main as M
    check("OOC 也是 plot", M.phase2_slot("out_of_character", False) == "plot")
    check("IC 是 plot", M.phase2_slot("in_character", False) == "plot")
    check("OOC 但 IC 風格也是 plot", M.phase2_slot("out_of_character", True) == "plot")

finally:
    io.open(L.MODELS_FILE, "w", encoding="utf-8").write(saved)

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
