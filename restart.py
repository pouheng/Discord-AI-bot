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
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.CommandLine -match 'main\\.py' } | "
            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }",
        ],
        capture_output=True,
        timeout=10,
    )
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
