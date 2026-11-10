"""test_priming.py — 開場提示句（priming）單一來源：編輯器改一處，全系統跟著換。"""

import io
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import main  # noqa: E402
import prompt_blocks as PB  # noqa: E402

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


saved = io.open(PB.BLOCKS_FILE, encoding="utf-8").read()
_orig_history = PB.HISTORY_DIR
PB.HISTORY_DIR = os.path.join(
    __import__("tempfile").mkdtemp(prefix="priming_hist_"), "h")
try:
    print("[1] 沒有 priming 欄位時用預設值")
    raw = json.loads(saved)
    raw.pop("priming", None)
    io.open(PB.BLOCKS_FILE, "w", encoding="utf-8").write(
        json.dumps(raw, ensure_ascii=False, indent=2))
    check("OOC 預設", PB.get_priming("ooc") == PB.DEFAULT_PRIMING["ooc"],
          repr(PB.get_priming("ooc")))
    check("IC 預設含 placeholder",
          PB.get_priming("ic") == PB.DEFAULT_PRIMING["ic"],
          repr(PB.get_priming("ic")))

    print("[2] 用編輯器的方式改 priming（set_priming + save_blocks）")
    data = PB.load_blocks()
    PB.set_priming(data, "ooc", "好喔收到，我現在開始回：")
    PB.set_priming(data, "ic", "開始回應：{{char_name}}上場")
    PB.save_blocks(data, note="test_priming")
    check("寫回後 OOC", PB.get_priming("ooc") == "好喔收到，我現在開始回：",
          repr(PB.get_priming("ooc")))
    check("寫回後 IC", PB.get_priming("ic") == "開始回應：{{char_name}}上場")

    print("[3] 系統提示詞尾巴自動引用同一段（{{priming}}）")
    ooc_sys = PB.render_system("ooc", {})
    ic_sys = PB.render_system("ic", {"char_name": "澪"})
    check("OOC 尾巴是新句子", "好喔收到，我現在開始回：" in ooc_sys,
          ooc_sys[-200:])
    check("OOC 沒有殘留佔位", "{{priming}}" not in ooc_sys)
    check("IC 尾巴代入角色名", "開始回應：澪上場" in ic_sys, ic_sys[-200:])
    check("IC 沒有殘留佔位", "{{priming}}" not in ic_sys)

    print("[4] 使用者訊息結尾的 priming 跟著改")
    text, _planning, _fmt, priming = main._reply_format_instruction(
        "out_of_character", "澪", "小明", "（無）")
    check("OOC 用的是新句子", priming == "好喔收到，我現在開始回：", repr(priming))
    _t2, _p2, _f2, priming_ic = main._reply_format_instruction(
        "in_character", "澪", "小明", "（無）")
    check("IC 代入角色名", priming_ic == "開始回應：澪上場", repr(priming_ic))

    print("[5] 模型複述新句子時自動清掉（綁定同一個來源）")
    out = main._strip_priming(priming + "\n\n嗯嗯原來如此", priming)
    check("OOC 新句子被清掉", out == "嗯嗯原來如此", repr(out))
    out_ic = main._strip_priming(priming_ic + "\n\n澪點了點頭", priming_ic)
    check("IC 新句子被清掉", out_ic == "澪點了點頭", repr(out_ic))
    check("正常正文不受影響",
          main._strip_priming("澪點了點頭。", priming_ic) == "澪點了點頭。")

    print("[6] 留空＝不注入")
    data = PB.load_blocks()
    PB.set_priming(data, "ooc", "")
    PB.save_blocks(data, note="test_priming-empty")
    _t3, _p3, _f3, empty = main._reply_format_instruction(
        "out_of_character", "澪", "小明", "（無）")
    check("清空後回空字串", empty == "", repr(empty))
    check("清空後系統尾巴沒有殘留句子",
          "喔好，收到了" not in PB.render_system("ooc", {}))

    print("[7] 編輯器存檔 API 會寫入 priming")
    from fastapi.testclient import TestClient
    import prompt_ui
    client = TestClient(prompt_ui.app)
    d = client.get("/api/data").json()
    d["blocks"]["priming"]["ooc"] = "API 設定的句子："
    r = client.post("/api/save", json={
        "tabs": d["blocks"]["tabs"], "priming": d["blocks"]["priming"],
        "note": "api test"})
    check("存檔成功", r.status_code == 200 and r.json().get("ok"), r.text[:120])
    check("API 寫入生效", PB.get_priming("ooc") == "API 設定的句子：",
          repr(PB.get_priming("ooc")))
finally:
    PB.HISTORY_DIR = _orig_history
    io.open(PB.BLOCKS_FILE, "w", encoding="utf-8").write(saved)

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
