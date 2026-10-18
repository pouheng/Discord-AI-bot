"""
watchdog.py — 定期檢查主 bot（main.py）是否存活，掛掉就自動重啟。

由 Windows 工作排程器（Task: MioBotWatchdog）每分鐘呼叫一次。
實際檢查間隔由 watchdog.json 的 interval_minutes 決定（manager 滑桿可調）。

沿用 start.py 既有的 is_running() / start_bot()，不重寫啟動邏輯。
"""
import json
import os
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import start  # noqa: E402  (reuse is_running / start_bot)

CONFIG_FILE = os.path.join(SCRIPT_DIR, "watchdog.json")
STATE_FILE = os.path.join(SCRIPT_DIR, "watchdog_state.json")
LOG_DIR = os.path.join(SCRIPT_DIR, "watchdog_logs")
LOG_FILE = os.path.join(LOG_DIR, "watchdog.log")

DEFAULT_INTERVAL = 1


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (ValueError, OSError):
        return default


def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _rotate_log():
    try:
        if os.path.getsize(LOG_FILE) < 256 * 1024:
            return
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-500:]
        with open(LOG_FILE, "w", encoding="utf-8", errors="replace") as f:
            f.writelines(lines)
    except OSError:
        pass


def log(msg):
    os.makedirs(LOG_DIR, exist_ok=True)
    _rotate_log()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except OSError:
        pass


def _main_py_running() -> bool:
    """檢查是否有任何 main.py 行程（含尚未寫出 bot.pid 的啟動中行程）。

    與 stop.py / manager.py 相同的 WMI + CommandLine 比對方式。
    """
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                "Where-Object { $_.CommandLine -match 'main\\.py' } | "
                "Measure-Object | Select-Object -ExpandProperty Count",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return int((out.stdout or "0").strip() or "0") > 0
    except Exception:
        return False


def main():
    cfg = _load(CONFIG_FILE, {})
    if not cfg.get("enabled", True):
        return
    try:
        interval = max(1, int(cfg.get("interval_minutes", DEFAULT_INTERVAL)))
    except (TypeError, ValueError):
        interval = DEFAULT_INTERVAL

    state = _load(STATE_FILE, {})
    now = time.time()
    if now - float(state.get("last_check", 0)) < interval * 60:
        return

    state["last_check"] = now
    _save(STATE_FILE, state)

    if start.is_running() or _main_py_running():
        return

    # detached 子行程用 "python" 啟動，確保 PATH 找得到
    py_dir = os.path.dirname(sys.executable)
    os.environ["PATH"] = py_dir + os.pathsep + os.environ.get("PATH", "")

    log("bot 未在執行，自動重啟")
    proc = start.start_bot()
    log(f"已啟動新 bot (PID {proc.pid})")


if __name__ == "__main__":
    main()
