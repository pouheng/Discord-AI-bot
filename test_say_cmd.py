"""離線測試：/say 的「只補一句」行為、planning 清理與診斷紀錄。"""

import asyncio
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import say_cmd

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


print("[1] 相依匯入")
check("say_cmd 有 asyncio", hasattr(say_cmd, "asyncio"))
check("asyncio 是模組", say_cmd.asyncio is asyncio)
check("register_say 存在", callable(getattr(say_cmd, "register_say", None)))

print("[2] planning 清理")
clean = say_cmd._clean_planning
check("完整 planning 被移除", clean("<planning>a\nb</planning>台詞") == "台詞")
check("未閉合 planning 全切掉", clean("<planning>想很久沒收尾") == "")
check("未閉合但前有台詞保留",
      clean("先講話<planning>後面被截斷") == "先講話")
check("沒有 planning 原樣保留", clean(" 只是台詞 ") == "只是台詞")

print("[3] 不再因不適合而完全沉默")
src = open(os.path.join(SCRIPT_DIR, "say_cmd.py"), encoding="utf-8").read()
check("有 short_mode", "short_mode" in src)
check("不適合時改成補一句", "只補一句" in src)
check("短模式 token 400", "400 if short_mode else 600" in src)
check("短模式會截成第一句", "maxsplit=1" in src)
check("空回覆會重試一次", "retry_prompt" in src and "_generate" in src)
check("重試會禁用 planning", "不要 <planning>" in src)
check("放棄時有明確訊息", "想了想，還是沒開口" in src)

print("[4] 診斷紀錄")
check("有 debug 目錄", say_cmd.SAY_DEBUG_DIR.endswith("say_logs"))
say_cmd._log_say_debug("test_stage", value=1)
today = os.path.join(
    say_cmd.SAY_DEBUG_DIR,
    __import__("datetime").datetime.now().strftime("%Y-%m-%d") + ".jsonl",
)
check("debug 檔已寫入", os.path.exists(today))
check("內容含 stage", "test_stage" in open(today, encoding="utf-8").read())

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
