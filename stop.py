"""
stop.py — CLI bot stop for AI Agent / headless use.

Usage:
    python stop.py

Kills all running main.py processes and removes bot.pid.
Exits immediately after cleanup.

Exit code:
    0 — success (all bot processes stopped / none running)
"""
import os
import subprocess
import sys

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


def main():
    stop_bot()
    print("[stop.py] Bot stopped")


if __name__ == "__main__":
    main()
