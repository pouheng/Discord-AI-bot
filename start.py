"""
start.py — CLI bot start for AI Agent / headless use.

Usage:
    python start.py

Starts main.py as a fully detached background process, then exits immediately.
If the bot is already running, prints a warning and exits.

Exit code:
    0 — success (bot launched / already running)
    1 — start failed
"""
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(SCRIPT_DIR, "bot.pid")


def get_pid() -> int | None:
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def is_running() -> bool:
    pid = get_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False


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
    if is_running():
        print("[start.py] Bot already running")
        sys.exit(0)
    proc = start_bot()
    if proc.poll() is not None:
        print(f"[start.py] Bot start failed (exit code {proc.returncode})")
        sys.exit(1)
    print(f"[start.py] Bot started (PID {proc.pid})")


if __name__ == "__main__":
    main()
