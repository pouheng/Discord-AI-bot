import tkinter as tk
from tkinter import ttk, scrolledtext
import subprocess, os, time, threading, json
import llm_models

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PID_FILE = os.path.join(SCRIPT_DIR, "bot.pid")
DB_FILE = os.path.join(SCRIPT_DIR, "rp_memory.db")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DB_VIEWER = os.path.join(SCRIPT_DIR, "db_viewer.py")
WATCHDOG_FILE = os.path.join(SCRIPT_DIR, "watchdog.json")
WATCHDOG_TASK_NAME = "MioBotWatchdog"

bot_proc = None
monitor_running = True
root = None
_wd_installed = False
_model_combos = {}
_model_vars = {}
_source_vars = {}
_route_vars = {}
_route_combos = {}


def get_pid() -> int | None:
    if not os.path.exists(PID_FILE):
        return None
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (ValueError, OSError):
        return None


def is_running() -> bool:
    """檢查 bot 是否仍在執行（唯讀探測，絕不終止目標）。

    Windows 上 os.kill(pid, 0) 不是存在檢查：0 == signal.CTRL_C_EVENT，
    會對目標送 Ctrl+C（GenerateConsoleCtrlEvent），把正在跑的 bot 殺掉
    （exit code 0、無 traceback）。監控迴圈每 2 秒呼叫一次 = bot 一啟動就被補掉。
    改用 OpenProcess + GetExitCodeProcess 讀取退出碼。
    """
    pid = get_pid()
    if pid is None:
        return False
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


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
    maint_dir = os.path.join(SCRIPT_DIR, "maintenance_logs")
    if os.path.exists(log_dir):
        os.startfile(log_dir)
        log("Opened prompt_logs/")
    if os.path.exists(maint_dir):
        os.startfile(maint_dir)
        log("Opened maintenance_logs/")


def open_maint_logs():
    maint_dir = os.path.join(SCRIPT_DIR, "maintenance_logs")
    error_dir = os.path.join(SCRIPT_DIR, "error_logs")
    count = 0
    if os.path.exists(maint_dir):
        os.startfile(maint_dir)
        count += 1
    if os.path.exists(error_dir):
        os.startfile(error_dir)
        count += 1
    if count:
        log(f"Opened maint logs ({count} folder(s))")
    else:
        log("No maint/error logs found")


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


def open_prompt_editor():
    """啟動 prompt_ui.py（Phase 2 提示詞查看/編輯器）並開瀏覽器。"""
    script = os.path.join(SCRIPT_DIR, "prompt_ui.py")
    if not os.path.exists(script):
        log("prompt_ui.py 不存在")
        return
    subprocess.Popen(
        ["python", "prompt_ui.py"],
        cwd=SCRIPT_DIR,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    time.sleep(1.2)
    import webbrowser

    webbrowser.open("http://127.0.0.1:8788")
    log("Opened Prompt Editor (http://127.0.0.1:8788)")


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


def monitor_loop():
    """背景監控執行緒：只做狀態探測，把 UI 更新丟回 tkinter 主執行緒執行。

    tkinter 不是 thread-safe，background thread 直接碰 widget（config/insert）
    會拋 SystemError/WinError 87 並讓監控執行緒崩潰，導致狀態欄停在舊值。
    """
    global _wd_installed
    tick = 0
    while monitor_running:
        try:
            pid = get_pid()
            alive = is_running()
            ap_on = get_autopilot_status()
            wd = get_watchdog_config()
            wd_on = bool(wd.get("enabled", True))
            try:
                wd_min = max(1, int(wd.get("interval_minutes", 1)))
            except (TypeError, ValueError):
                wd_min = 1
            # 工作是否已註冊要跑 schtasks，較慢；每 5 圈（約 10 秒）查一次。
            if tick % 5 == 0:
                _wd_installed = watchdog_task_installed()
        except Exception:
            time.sleep(2)
            continue
        # 透過 after 排程到主執行緒更新 UI（確保 widget 只被主執行緒觸碰）
        try:
            root.after(0, _apply_status, pid, alive, ap_on, wd_on, wd_min, _wd_installed)
        except Exception:
            pass
        tick += 1
        time.sleep(2)


def _apply_status(pid, alive, ap_on, wd_on, wd_min, wd_installed):
    try:
        if alive:
            status_label.config(text=f"RUNNING (PID {pid})", foreground="#00ff00")
        else:
            status_label.config(text="STOPPED", foreground="#ff4444")
        if ap_on:
            ap_status_label.config(text="ON", foreground="#00ff00")
        else:
            ap_status_label.config(text="OFF", foreground="#ff4444")
        task_txt = "task OK" if wd_installed else "no task"
        wd_status_label.config(
            text=f"{'ON' if wd_on else 'OFF'} · {wd_min}min · {task_txt}",
            foreground="#00ff00" if wd_on else "#ff4444",
        )
    except Exception as e:
        print(f"[manager] 狀態更新失敗: {e}")


def log(msg: str):
    """thread-safe 紀錄：背景執行緒也能安全呼叫（透過 after 排程到主執行緒）。"""
    now = time.strftime("%H:%M:%S")

    def _do(m=msg, n=now):
        try:
            console.insert(tk.END, f"[{n}] {m}\n")
            console.see(tk.END)
        except tk.TclError:
            pass

    try:
        if root is not None:
            root.after(0, _do)
        else:
            _do()
    except Exception:
        pass


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


def get_watchdog_config() -> dict:
    """讀取 watchdog.json（不存在或壞掉時回預設值）。"""
    try:
        with open(WATCHDOG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            raise ValueError("watchdog.json 不是物件")
        return cfg
    except (ValueError, OSError):
        return {"enabled": True, "interval_minutes": 1}


def set_watchdog_enabled(enabled: bool):
    """只寫設定檔；畫面由監控迴圈自動更新（不需提權）。"""
    cfg = get_watchdog_config()
    cfg["enabled"] = bool(enabled)
    _save_watchdog(cfg)
    log(f"Watchdog {'ON' if enabled else 'OFF'}")


def set_watchdog_interval(minutes: int):
    cfg = get_watchdog_config()
    cfg["interval_minutes"] = max(1, int(minutes))
    _save_watchdog(cfg)
    log(f"Watchdog interval = {cfg['interval_minutes']} min")


def _save_watchdog(cfg: dict):
    try:
        with open(WATCHDOG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except OSError as e:
        log(f"Watchdog 設定寫入失敗: {e}")


def watchdog_task_installed() -> bool:
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", WATCHDOG_TASK_NAME],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return r.returncode == 0
    except Exception:
        return False


def _run_watchdog_ps1(name: str):
    script = os.path.join(SCRIPT_DIR, name)
    if not os.path.exists(script):
        log(f"{name} 不存在")
        return
    log(f"執行 {name}（會跳出 UAC / 確認視窗）...")
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            timeout=180,
        )
    except Exception as e:
        log(f"{name} 執行失敗: {e}")


def install_watchdog_task():
    _run_watchdog_ps1("install_watchdog.ps1")
    log("若 UAC 已確認，狀態將於數秒內更新為 task OK")


def uninstall_watchdog_task():
    _run_watchdog_ps1("uninstall_watchdog.ps1")
    log("若 UAC 已確認，工作將於數秒內移除")


def _on_source_changed(_event=None):
    """Endpoint / 金鑰改完即時寫入 models.json。"""
    try:
        cur = llm_models.get_saved_source()
        new = {
            "base_url": _source_vars["base_url"].get().strip(),
            "api_key": _source_vars["api_key"].get().strip(),
        }
        if new == cur:
            return
        llm_models.save_source(new["base_url"], new["api_key"])
        log("來源設定已更新（下一則訊息生效）")
    except Exception as e:
        log(f"儲存來源設定失敗: {e}")


def _test_connection():
    """打一次 GET /models 驗證目前來源可用。"""
    try:
        ids = llm_models.fetch_models()
        log(f"連線成功：{len(ids)} 個模型（{', '.join(ids)}）")
    except Exception as e:
        log(f"連線失敗：{type(e).__name__}: {e}")


def _combo_values(slot, ids):
    """下拉選項＝拉取清單；現值若不在清單內也帶出（放最前），避免被清空。"""
    cur = llm_models.get_model(slot)
    vals = list(ids or [])
    if cur and cur not in vals:
        vals = [cur] + vals
    return vals


def _on_model_selected(slot):
    """使用者改選／輸入後即時寫入 models.json（下一則訊息生效）。"""
    try:
        val = _model_vars[slot].get().strip()
        if not val or val == llm_models.get_model(slot):
            return
        llm_models.save_model(slot, val)
        log(f"模型 {llm_models.SLOT_LABELS.get(slot, slot)} → {val}")
    except Exception as e:
        log(f"儲存模型設定失敗: {e}")


def _combo_values_cur(cur, ids):
    """下拉選項＝清單；目前值不在清單內也帶出，避免被清空。"""
    vals = list(ids or [])
    if cur and cur not in vals:
        vals = [cur] + vals
    return vals


def _fetch_and_apply_models():
    """背景執行：向來源拉取模型清單，更新所有下拉選項（含三線路）。"""
    try:
        ids = llm_models.fetch_models()
    except Exception as e:
        log(f"拉取模型失敗: {type(e).__name__}: {e}")
        return

    def _apply():
        for slot, combo in _model_combos.items():
            combo["values"] = _combo_values(slot, ids)
        for i, combo in _route_combos.items():
            cur = _route_vars[i].get().strip()
            combo["values"] = _combo_values_cur(cur, ids)
        log(f"已拉取 {len(ids)} 個模型: {', '.join(ids)}")

    try:
        root.after(0, _apply)
    except Exception:
        _apply()


class BotManagerGUI:
    def __init__(self):
        global root
        self.root = tk.Tk()
        root = self.root
        self.root.title("Bot Manager")
        self.root.geometry("660x840")
        self.root.resizable(False, False)

        nb = ttk.Notebook(self.root)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        console_tab = ttk.Frame(nb, padding=12)
        settings_tab = ttk.Frame(nb, padding=12)
        nb.add(console_tab, text="控制台")
        nb.add(settings_tab, text="設定")

        # 既有控制項全部掛到「控制台」分頁（沿用 main 變數名）
        main = console_tab

        # 設定分頁：來源 + 模型選擇 + Watchdog
        self._build_source_section(settings_tab)
        self._build_model_section(settings_tab)
        self._build_fallback_section(settings_tab)
        self._build_reply_section(settings_tab)
        ttk.Label(settings_tab, text="Watchdog（定時自動重啟）",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W, pady=(12, 2))

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

        # Watchdog row（定時自動重啟 — Windows 工作排程器）→ 掛在「設定」分頁
        wd_frame = ttk.Frame(settings_tab)
        wd_frame.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(wd_frame, text="Watchdog:", font=("Segoe UI", 9, "bold")).pack(
            side=tk.LEFT
        )
        global wd_status_label, wd_interval_label, wd_scale
        wd_status_label = ttk.Label(wd_frame, text="...", font=("Segoe UI", 10))
        wd_status_label.pack(side=tk.LEFT, padx=6)
        ttk.Button(
            wd_frame, text="ON", command=lambda: set_watchdog_enabled(True), width=5
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            wd_frame, text="OFF", command=lambda: set_watchdog_enabled(False), width=5
        ).pack(side=tk.LEFT, padx=2)
        self.install_wd_btn = ttk.Button(
            wd_frame,
            text="Install task",
            command=lambda: self._thread_action("install_watchdog"),
            width=11,
        )
        self.install_wd_btn.pack(side=tk.LEFT, padx=2)
        self.uninstall_wd_btn = ttk.Button(
            wd_frame,
            text="Remove",
            command=lambda: self._thread_action("uninstall_watchdog"),
            width=8,
        )
        self.uninstall_wd_btn.pack(side=tk.LEFT, padx=2)

        wd_row2 = ttk.Frame(settings_tab)
        wd_row2.pack(fill=tk.X, pady=(0, 4))
        ttk.Label(wd_row2, text="Interval:", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        wd_interval_label = ttk.Label(wd_row2, text="1 min", font=("Segoe UI", 9))
        wd_scale = tk.Scale(
            wd_row2,
            from_=1,
            to=60,
            orient=tk.HORIZONTAL,
            length=200,
            showvalue=False,
            command=self._on_wd_slide,
        )
        wd_scale.pack(side=tk.LEFT, padx=6)
        wd_scale.bind("<ButtonRelease-1>", self._on_wd_slide_release)
        wd_interval_label.pack(side=tk.LEFT, padx=4)

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

        ops_row1 = [
            ("Memory Count", show_memory_count),
            ("DB Viewer", open_db_viewer),
            ("Run Review", run_review),
            ("Open Config", open_config),
        ]
        ops_row2 = [
            ("Open Logs", open_logs),
            ("Maint Log", open_maint_logs),
            ("Open DB", open_db),
            ("Prompt Editor", open_prompt_editor),
        ]
        op_btn_row1 = ttk.Frame(op_frame)
        op_btn_row1.pack(fill=tk.X, pady=2)
        op_btn_row2 = ttk.Frame(op_frame)
        op_btn_row2.pack(fill=tk.X, pady=2)
        for text, cmd in ops_row1:
            ttk.Button(op_btn_row1, text=text, command=cmd, width=14).pack(
                side=tk.LEFT, padx=2
            )
        for text, cmd in ops_row2:
            ttk.Button(op_btn_row2, text=text, command=cmd, width=14).pack(
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
        try:
            wd = get_watchdog_config()
            wd_min = max(1, int(wd.get("interval_minutes", 1) or 1))
            wd_scale.set(wd_min)
            wd_interval_label.config(text=f"{wd_min} min")
            pid = get_pid()
            alive = is_running()
            ap_on = get_autopilot_status()
            _apply_status(pid, alive, ap_on, bool(wd.get("enabled", True)),
                          wd_min, watchdog_task_installed())
        except Exception:
            pass
        log("Bot Manager ready.")
        # 開啟時自動拉取一次模型清單
        threading.Thread(target=_fetch_and_apply_models, daemon=True).start()

    def _build_source_section(self, parent):
        ttk.Label(parent, text="來源（相容 OpenAI 的聊天補全）",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        saved = llm_models.get_saved_source()

        row1 = ttk.Frame(parent)
        row1.pack(fill=tk.X, pady=(4, 2))
        ttk.Label(row1, text="Endpoint URL:", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        _source_vars["base_url"] = tk.StringVar(value=saved["base_url"])
        e1 = ttk.Entry(row1, textvariable=_source_vars["base_url"], width=44)
        e1.pack(side=tk.LEFT, padx=6)
        e1.bind("<Return>", _on_source_changed)
        e1.bind("<FocusOut>", _on_source_changed)

        row2 = ttk.Frame(parent)
        row2.pack(fill=tk.X, pady=(2, 2))
        ttk.Label(row2, text="API 金鑰:", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        _source_vars["api_key"] = tk.StringVar(value=saved["api_key"])
        e2 = ttk.Entry(row2, textvariable=_source_vars["api_key"], width=44, show="*")
        e2.pack(side=tk.LEFT, padx=6)
        e2.bind("<Return>", _on_source_changed)
        e2.bind("<FocusOut>", _on_source_changed)

        def _toggle_key():
            e2.config(show="" if e2.cget("show") else "*")

        ttk.Button(row2, text="顯示", width=6, command=_toggle_key).pack(
            side=tk.LEFT, padx=2)

        hint = ttk.Frame(parent)
        hint.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(hint, text="留空＝Endpoint 用預設 DeepSeek、金鑰用 .env。",
                  font=("Segoe UI", 8)).pack(side=tk.LEFT)
        ttk.Button(
            hint, text="測試連線", width=10,
            command=lambda: threading.Thread(target=_test_connection, daemon=True).start(),
        ).pack(side=tk.RIGHT)

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)

    def _build_model_section(self, parent):
        ttk.Label(parent, text="模型選擇", font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        header = ttk.Frame(parent)
        header.pack(fill=tk.X, pady=(2, 8))
        ttk.Label(header, text="改選即時生效（下一則訊息）。",
                  font=("Segoe UI", 8)).pack(side=tk.LEFT)
        ttk.Button(
            header, text="重新拉取模型", width=13,
            command=lambda: threading.Thread(
                target=_fetch_and_apply_models, daemon=True).start(),
        ).pack(side=tk.RIGHT)

        grid = ttk.Frame(parent)
        grid.pack(fill=tk.X)
        for i, (slot, label) in enumerate(llm_models.SLOTS[:3]):
            self._make_model_row(grid, i, slot, label)

        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        other = ttk.Frame(parent)
        other.pack(fill=tk.X)
        for i, (slot, label) in enumerate(llm_models.SLOTS[3:]):
            self._make_model_row(other, i, slot, label)

    def _make_model_row(self, parent, row, slot, label):
        ttk.Label(parent, text=label, font=("Segoe UI", 9)).grid(
            row=row, column=0, sticky=tk.W, pady=2, padx=(0, 8))
        var = tk.StringVar(value=llm_models.get_model(slot))
        combo = ttk.Combobox(parent, textvariable=var, width=30,
                             values=_combo_values(slot, []))
        combo.grid(row=row, column=1, sticky=tk.W, pady=2)
        combo.bind("<<ComboboxSelected>>", lambda e, s=slot: _on_model_selected(s))
        combo.bind("<Return>", lambda e, s=slot: _on_model_selected(s))
        combo.bind("<FocusOut>", lambda e, s=slot: _on_model_selected(s))
        _model_combos[slot] = combo
        _model_vars[slot] = var

    def _build_fallback_section(self, parent):
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        header = ttk.Frame(parent)
        header.pack(fill=tk.X)
        ttk.Label(header, text="全域備援（不分場景）",
                  font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)
        ttk.Button(header, text="儲存備援", width=11,
                   command=self._save_routes).pack(side=tk.RIGHT)
        ttk.Label(
            parent,
            text="主線路＝上方各場景選的模型；失敗（5xx／限流／逾時／401）時，"
                 "依序切到備援 1、2、3。留空＝不使用。存檔下一則生效。",
            font=("Segoe UI", 8),
        ).pack(anchor=tk.W, pady=(2, 4))

        fbs = llm_models.global_fallbacks()
        if not fbs:
            # 還沒有全域設定 → 先用「日常/通用」目前模型的舊設定墊著
            fbs = llm_models.get_fallbacks(llm_models.get_model("default"))
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X)
        labels = ["備援 1", "備援 2", "備援 3"]
        for idx in range(3):
            ttk.Label(frame, text=labels[idx], font=("Segoe UI", 9)).grid(
                row=idx, column=0, sticky=tk.W, pady=2, padx=(0, 8))
            cur = fbs[idx] if len(fbs) > idx else ""
            var = tk.StringVar(value=cur)
            combo = ttk.Combobox(frame, textvariable=var, width=38,
                                 values=_combo_values_cur(cur, []))
            combo.grid(row=idx, column=1, sticky=tk.W, pady=2)
            _route_vars[idx + 1] = var
            _route_combos[idx + 1] = combo

        # 目前各場景的主線路（唯讀，方便對照「誰是誰的備援」）
        cur_models = [
            f"{label}={llm_models.get_model(slot)}"
            for slot, label in llm_models.SLOTS
            if llm_models.get_model(slot)
        ]
        ttk.Label(
            frame,
            text="目前主線路：" + "、".join(cur_models),
            font=("Segoe UI", 8), foreground="#666666", wraplength=560,
        ).grid(row=3, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

    def _save_routes(self):
        try:
            backups = [(_route_vars[i].get() or "").strip() for i in (1, 2, 3)]
            llm_models.save_global_fallbacks(backups)
            shown = " → ".join(b or "—" for b in backups)
            log(f"全域備援已存：{shown}（不影響各場景主線路）")
        except Exception as e:
            log(f"儲存備援失敗: {e}")

    def _build_reply_section(self, parent):
        ttk.Separator(parent, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=8)
        ttk.Label(parent, text="回覆功能（引用訊息）",
                  font=("Segoe UI", 11, "bold")).pack(anchor=tk.W)
        ttk.Label(
            parent,
            text="自動＝角色自己決定要不要引用（預設不引用，需要時寫 [REPLY]）；改完需 /reload_config 或重啟。",
            font=("Segoe UI", 8),
        ).pack(anchor=tk.W, pady=(2, 4))

        row = ttk.Frame(parent)
        row.pack(fill=tk.X)
        self.reply_mode_var = tk.StringVar(value=self._load_reply_mode())
        for text, value in (
            ("自動（角色決定）", "auto"),
            ("一律引用", "always"),
            ("不引用", "never"),
        ):
            ttk.Radiobutton(
                row, text=text, value=value,
                variable=self.reply_mode_var,
                command=self._save_reply_mode,
            ).pack(side=tk.LEFT, padx=(0, 12))

    @staticmethod
    def _load_reply_mode() -> str:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("reply_mode", "auto") or "auto"
        except Exception:
            return "auto"

    def _save_reply_mode(self):
        mode = self.reply_mode_var.get()
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["reply_mode"] = mode
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            log(f"回覆模式設為「{mode}」")
        except Exception as e:
            log(f"儲存回覆模式失敗: {e}")

    def _thread_action(self, action: str):
        """Run action in thread with instant button feedback."""
        btn_map = {
            "start": (self.start_btn, "Starting...", "Start"),
            "stop": (self.stop_btn, "Stopping...", "Stop"),
            "restart": (self.restart_btn, "Restarting...", "Restart"),
            "install_watchdog": (self.install_wd_btn, "Installing...", "Install task"),
            "uninstall_watchdog": (self.uninstall_wd_btn, "Removing...", "Remove"),
        }
        btn, busy_text, idle_text = btn_map[action]
        btn.config(text=busy_text, state=tk.DISABLED)

        def _do():
            try:
                if action == "start":
                    start_bot()
                    # 啟動 = 允許 watchdog 接手自動重啟
                    set_watchdog_enabled(True)
                elif action == "stop":
                    stop_bot()
                    # 停止 = 關掉 watchdog，否則會被自動重啟
                    set_watchdog_enabled(False)
                elif action == "restart":
                    stop_bot()
                    time.sleep(2)
                    start_bot()
                elif action == "install_watchdog":
                    install_watchdog_task()
                elif action == "uninstall_watchdog":
                    uninstall_watchdog_task()
            finally:
                console.after(0, lambda: btn.config(text=idle_text, state=tk.NORMAL))

        threading.Thread(target=_do, daemon=True).start()

    def _on_wd_slide(self, val):
        """拖動滑桿時即時更新文字（不寫檔）。"""
        try:
            wd_interval_label.config(text=f"{int(float(val))} min")
        except Exception:
            pass

    def _on_wd_slide_release(self, _event):
        """放開滑桿才寫入 watchdog.json。"""
        try:
            set_watchdog_interval(int(float(wd_scale.get())))
        except Exception:
            pass

    def on_close(self):
        global monitor_running
        monitor_running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    BotManagerGUI().run()
