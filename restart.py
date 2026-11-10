"""
restart.py — CLI bot restart for AI Agent / headless use.

Usage:
    python restart.py

Stops any running main.py process, waits 2s, starts a new one in a
fully detached background process, then exits immediately.

Exit code:
    0 — success (new bot process launched)
    1 — start failed
"""
import os
import sys
import subprocess
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(SCRIPT_DIR, "bot.pid")


def stop_bot():
    # 先用 bot.pid 精準殺，再掃命名為 main.py 的 python 行程。
    # 舊版用 Get-WmiObject，在 PowerShell 7 已被移除 → 常常「殺不到」，
    # 舊 bot 就會一直佔著 bot.lock，新 bot 還以為有人在使用而自我結束。
    pids = []
    for pid_file in (PID_FILE, os.path.join(SCRIPT_DIR, "bot.lock")):
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as f:
                    pids.append(int((f.read() or "0").strip() or 0))
            except (OSError, ValueError):
                pass
    try:
        out = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
                "Where-Object { $_.CommandLine -match 'main\\.py' } | "
                "ForEach-Object { $_.ProcessId }",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
        pids.extend(int(x) for x in out.split() if x.strip().isdigit())
    except (OSError, subprocess.SubprocessError, ValueError):
        pass

    for pid in {p for p in pids if p and p != os.getpid()}:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    time.sleep(1)
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass


def start_bot():
    proc = subprocess.Popen(
        ["python", "main.py"],
        cwd=SCRIPT_DIR,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
        stdout=None,
        stderr=None,
        stdin=None,
    )
    return proc


def main():
    stop_bot()
    time.sleep(2)
    proc = start_bot()
    if proc.poll() is not None:
        print(f"[restart.py] Bot start failed (exit code {proc.returncode})")
        sys.exit(1)
    print(f"[restart.py] Bot restarted (PID {proc.pid})")


if __name__ == "__main__":
    main()
