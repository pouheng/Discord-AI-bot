"""run_tests.py — 一次跑完所有回歸測試並彙總。

用法：
    python run_tests.py              # 跑主要測試（離線、不花 API）
    python run_tests.py --all        # 加上 prompt／parity（需資料庫）
    python run_tests.py drive        # 只跑名稱含 drive 的測試
    python run_tests.py --fail-fast  # 第一個失敗就停

離開碼：0 = 全部通過，1 = 有失敗。
"""
import argparse
import os
import re
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 主要回歸測試（全部離線，不呼叫外部 API）
SUITE = [
    "test_e2e.py",            # 主流程（含 Drive 使用場景）
    "test_virtual.py",        # 虛擬對話流程
    "test_reply_split.py",    # 多人回覆／[TO:]／[REPLY]
    "test_time_awareness.py", # 時間感知與跨日
    "test_web_context.py",    # 連結讀取
    "test_empty_message.py",  # 非文字訊息描述
    "test_multimodal.py",     # 圖片／多模態
    "test_say_cmd.py",        # /say 補一句
    "test_memory.py",         # 記憶
    "test_person_recall.py",  # 人物召回
    "test_deepseek_search.py",# 搜尋與降級
    "test_webreader.py",      # 網頁 reader
    "test_drive_agent.py",    # Drive 工具層
    "test_drive_scenarios.py",# Drive 使用場景
    "test_prompts.py",        # 提示詞結構
    "test_parity.py",         # block 版 vs legacy 版一致性
    "test_model_routing.py",  # 全域備援（主線路由場景決定）
]

# 保留給舊指令（--all）相容，目前無額外測試
EXTRA: list[str] = []

_PASSED_FAILED = re.compile(r"(\d+)\s+passed,\s*(\d+)\s+failed")
_RESULTS = re.compile(r"Results:\s*(\d+)/(\d+)\s+passed")
_PASS_TOTAL = re.compile(r"PASS\s+(\d+)/(\d+)")


def parse_result(output: str):
    m = _PASSED_FAILED.findall(output)
    if m:
        passed, failed = m[-1]
        return int(passed), int(passed) + int(failed)
    m = _RESULTS.findall(output)
    if m:
        passed, total = m[-1]
        return int(passed), int(total)
    m = _PASS_TOTAL.findall(output)
    if m:
        passed, total = m[-1]
        return int(passed), int(total)
    return None, None


def run_one(name: str, fail_fast: bool):
    path = os.path.join(SCRIPT_DIR, name)
    if not os.path.exists(path):
        return {"name": name, "status": "MISSING", "detail": "找不到檔案", "sec": 0}
    start = time.time()
    proc = subprocess.run(
        [sys.executable, path],
        cwd=SCRIPT_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    sec = time.time() - start
    out = (proc.stdout or "") + (proc.stderr or "")
    passed, total = parse_result(out)

    if proc.returncode == 0:
        detail = f"{passed}/{total}" if total else "OK"
        status = "PASS"
    else:
        status = "FAIL"
        fails = [ln.strip() for ln in out.splitlines() if "FAIL" in ln or "Error" in ln]
        detail = (f"{passed}/{total}｜" if total else "") + (fails[0][:80] if fails else "見輸出")
    return {"name": name, "status": status, "detail": detail, "sec": sec, "output": out}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("filter", nargs="?", default="", help="只跑名稱含此字串的測試")
    parser.add_argument("--all", action="store_true", help="（相容用，已無額外測試）")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--verbose", action="store_true", help="失敗時印出完整輸出")
    args = parser.parse_args()

    tests = SUITE + (EXTRA if args.all else [])
    if args.filter:
        tests = [t for t in tests if args.filter.lower() in t.lower()]
    if not tests:
        print(f"沒有符合「{args.filter}」的測試")
        return 1

    print("=" * 64)
    print(f"跑 {len(tests)} 個測試檔")
    print("=" * 64)
    results = []
    for name in tests:
        r = run_one(name, args.fail_fast)
        results.append(r)
        mark = {"PASS": "PASS", "FAIL": "FAIL", "MISSING": "MISS"}[r["status"]]
        print(f"  [{mark}] {name:<26} {r['detail']:<28} {r['sec']:.1f}s")
        if r["status"] == "FAIL" and args.verbose:
            print("-" * 60)
            print(r.get("output", "")[-2000:])
            print("-" * 60)
        if r["status"] == "FAIL" and args.fail_fast:
            break

    failed = [r for r in results if r["status"] != "PASS"]
    print("-" * 64)
    print(f"結果：{len(results) - len(failed)}/{len(results)} 通過"
          f"（{sum(r['sec'] for r in results):.1f}s）")
    if failed:
        print("失敗：" + "、".join(r["name"] for r in failed))
        return 1
    print("全部通過")
    return 0


if __name__ == "__main__":
    sys.exit(main())
