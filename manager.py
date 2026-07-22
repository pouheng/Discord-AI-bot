import tkinter as tk
from tkinter import ttk, scrolledtext
import subprocess, os, time, threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(SCRIPT_DIR, "bot.pid")
DB_FILE = os.path.join(SCRIPT_DIR, "rp_memory.db")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DB_VIEWER = os.path.join(SCRIPT_DIR, "db_viewer.py")

bot_proc = None
monitor_running = True


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
    global bot_proc
    if is_running():
        log("Bot already running.")
        return
    try:
        bot_proc = subprocess.Popen(
            ["python", "main.py"],
            cwd=SCRIPT_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log("Bot started.")
        update_status()
    except Exception as e:
        log(f"Start failed: {e}")


def stop_bot():
    import subprocess

    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-WmiObject Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'main\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }",
        ],
        capture_output=True,
        timeout=10,
    )
    if bot_proc:
        bot_proc.terminate()
    time.sleep(1)
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
        except OSError:
            pass
    update_status()
    log("Bot stopped.")


def restart_bot():
    stop_bot()
    time.sleep(2)
    start_bot()


def open_config():
    if os.path.exists(CONFIG_FILE):
        os.startfile(CONFIG_FILE)
        log("Opened config.json")


def open_logs():
    log_dir = os.path.join(SCRIPT_DIR, "prompt_logs")
    maint_dir = os.path.join(SCRIPT_DIR, "maint_logs")
    if os.path.exists(log_dir):
        os.startfile(log_dir)
        log("Opened prompt_logs/")
    if os.path.exists(maint_dir):
        os.startfile(maint_dir)


def open_db():
    if os.path.exists(DB_FILE):
        os.startfile(DB_FILE)
        log("Opened rp_memory.db")


def open_db_viewer():
    if os.path.exists(DB_VIEWER):
        subprocess.Popen(
            ["python", "db_viewer.py"],
            cwd=SCRIPT_DIR,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        log("Opened DB Viewer")


def run_review():
    log("Running full prompt review (all 3 phases)...")

    def _run():
        try:
            result = subprocess.run(
                ["python", "review_prompt.py", "all"],
                cwd=SCRIPT_DIR,
                capture_output=True,
                text=True,
                timeout=300,
            )
            out = ((result.stdout or "") + (result.stderr or "")).strip()
            phases = out.count("[Phase")
            log(f"Review done ({phases} phases). See last_review_log.txt")
        except subprocess.TimeoutExpired:
            log("Review timed out (300s).")
        except Exception as e:
            log(f"Review failed: {e}")

    threading.Thread(target=_run, daemon=True).start()


def show_memory_count():
    try:
        import sqlite3

        c = sqlite3.connect(DB_FILE)
        r = c.execute("SELECT count(*) FROM memories WHERE user_id = '__BOT__'")
        n = r.fetchone()[0]
        c.close()
        log(f"Self memories: {n} 條")
    except Exception as e:
        log(f"DB error: {e}")


def update_status():
    pid = get_pid()
    alive = is_running()
    if alive:
        status_label.config(text=f"RUNNING (PID {pid})", foreground="#00ff00")
    else:
        status_label.config(text="STOPPED", foreground="#ff4444")


def monitor_loop():
    while monitor_running:
        update_status()
        update_autopilot_status()
        time.sleep(2)


def log(msg: str):
    now = time.strftime("%H:%M:%S")
    console.insert(tk.END, f"[{now}] {msg}\n")
    console.see(tk.END)


def get_autopilot_status() -> bool:
    try:
        import sqlite3

        c = sqlite3.connect(DB_FILE, timeout=3)
        r = c.execute("SELECT value FROM autopilot_config WHERE key = 'enabled'")
        row = r.fetchone()
        c.close()
        return bool(row) and row[0] == "1"
    except Exception:
        return False


def set_autopilot(enabled: bool):
    try:
        import sqlite3

        c = sqlite3.connect(DB_FILE, timeout=3)
        c.execute(
            "INSERT OR REPLACE INTO autopilot_config (key, value) VALUES ('enabled', ?)",
            ("1" if enabled else "0"),
        )
        c.commit()
        c.close()
        name = "ON" if enabled else "OFF"
        log(f"Autopilot {name}")
        update_autopilot_status()
    except Exception as e:
        log(f"Autopilot toggle failed: {e}")


def update_autopilot_status():
    on = get_autopilot_status()
    if on:
        ap_status_label.config(text="ON", foreground="#00ff00")
    else:
        ap_status_label.config(text="OFF", foreground="#ff4444")


class BotManagerGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Bot Manager")
        self.root.geometry("520x480")
        self.root.resizable(False, False)

        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        # Status bar
        global status_label
        status_frame = ttk.Frame(main)
        status_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(status_frame, text="Status:", font=("Segoe UI", 10, "bold")).pack(
            side=tk.LEFT
        )
        status_label = ttk.Label(status_frame, text="...", font=("Segoe UI", 10))
        status_label.pack(side=tk.LEFT, padx=6)

        # Button rows
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill=tk.X, pady=(0, 8))

        btn_style = dict(width=12)
        self.start_btn = ttk.Button(
            btn_frame,
            text="Start",
            command=lambda: self._thread_action("start"),
            **btn_style,
        )
        self.start_btn.pack(side=tk.LEFT, padx=2)
        self.stop_btn = ttk.Button(
            btn_frame,
            text="Stop",
            command=lambda: self._thread_action("stop"),
            **btn_style,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=2)
        self.restart_btn = ttk.Button(
            btn_frame,
            text="Restart",
            command=lambda: self._thread_action("restart"),
            **btn_style,
        )
        self.restart_btn.pack(side=tk.LEFT, padx=2)

        # Autopilot row
        ap_frame = ttk.Frame(main)
        ap_frame.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(ap_frame, text="Autopilot:", font=("Segoe UI", 9, "bold")).pack(
            side=tk.LEFT
        )
        global ap_status_label
        ap_status_label = ttk.Label(ap_frame, text="...", font=("Segoe UI", 10))
        ap_status_label.pack(side=tk.LEFT, padx=6)
        ttk.Button(
            ap_frame, text="ON", command=lambda: set_autopilot(True), width=6
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            ap_frame, text="OFF", command=lambda: set_autopilot(False), width=6
        ).pack(side=tk.LEFT, padx=2)

        sep = ttk.Separator(main, orient=tk.HORIZONTAL)
        sep.pack(fill=tk.X, pady=6)

        # Operations
        op_frame = ttk.Frame(main)
        op_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(op_frame, text="Operations:", font=("Segoe UI", 9, "bold")).pack(
            anchor=tk.W
        )
        op_btn_frame = ttk.Frame(op_frame)
        op_btn_frame.pack(fill=tk.X, pady=2)

        ops = [
            ("Memory Count", show_memory_count),
            ("DB Viewer", open_db_viewer),
            ("Run Review", run_review),
            ("Open Config", open_config),
            ("Open Logs", open_logs),
            ("Open DB", open_db),
        ]
        for text, cmd in ops:
            ttk.Button(op_btn_frame, text=text, command=cmd, width=14).pack(
                side=tk.LEFT, padx=2
            )

        sep2 = ttk.Separator(main, orient=tk.HORIZONTAL)
        sep2.pack(fill=tk.X, pady=6)

        # Console
        ttk.Label(main, text="Console:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        global console
        console = scrolledtext.ScrolledText(
            main,
            height=14,
            font=("Consolas", 9),
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            state=tk.NORMAL,
        )
        console.pack(fill=tk.BOTH, expand=True)

        # Monitor thread
        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        update_status()
        update_autopilot_status()
        log("Bot Manager ready.")

    def _thread_action(self, action: str):
        """Run action in thread with instant button feedback."""
        btn_map = {
            "start": (self.start_btn, "Starting...", "Start"),
            "stop": (self.stop_btn, "Stopping...", "Stop"),
            "restart": (self.restart_btn, "Restarting...", "Restart"),
        }
        btn, busy_text, idle_text = btn_map[action]
        btn.config(text=busy_text, state=tk.DISABLED)

        def _do():
            try:
                if action == "start":
                    start_bot()
                elif action == "stop":
                    stop_bot()
                elif action == "restart":
                    stop_bot()
                    time.sleep(2)
                    start_bot()
            finally:
                console.after(0, lambda: btn.config(text=idle_text, state=tk.NORMAL))

        threading.Thread(target=_do, daemon=True).start()

    def on_close(self):
        global monitor_running
        monitor_running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    BotManagerGUI().run()
