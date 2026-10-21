"""
db_backup_task.py — 定時備份 wrapper（Windows 工作排程器用）

用途：
- pythonw.exe 沒有主控台，sys.stdout 會是 None，這裡把輸出導到 log。
- Python 預設不讀 Windows 系統 proxy（Clash 等），這裡從登錄檔補上，
  讓 token refresh / Drive 上傳能走使用者的 proxy。
- 實際備份邏輯仍由 db_backup.py 負責。

手動測試：
    python db_backup_task.py            # 備份一次
    python db_backup_task.py --list     # 列出 Drive 備份
日誌：
    db_backup_logs/db_backup.log
"""
import datetime
import os
import sys
import traceback
import winreg

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

LOG_DIR = os.path.join(SCRIPT_DIR, "db_backup_logs")
LOG_FILE = os.path.join(LOG_DIR, "db_backup.log")


def _apply_system_proxy():
    """把 Windows 系統 proxy 補到環境變數（Python 預設不會讀）。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
        if not (enabled and server):
            return
        if "=" in server:
            parts = dict(p.split("=", 1) for p in server.split(";") if "=" in p)
            server = parts.get("https") or parts.get("http") or server
        if "://" not in server:
            server = "http://" + server
        os.environ.setdefault("HTTPS_PROXY", server)
        os.environ.setdefault("HTTP_PROXY", server)
        os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,::1")
    except OSError:
        pass


def main():
    _apply_system_proxy()
    os.makedirs(LOG_DIR, exist_ok=True)

    with open(LOG_FILE, "a", encoding="utf-8") as log:
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = log
        sys.stderr = log
        log.write(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] === 開始 ===\n")
        log.flush()
        try:
            import db_backup

            db_backup.main()
        except SystemExit as exc:
            if exc.code not in (0, None):
                log.write(f"結束（exit {exc.code}）\n")
            raise
        except Exception:
            traceback.print_exc()
            raise
        finally:
            log.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] === 結束 ===\n")
            log.flush()
            sys.stdout, sys.stderr = old_out, old_err


if __name__ == "__main__":
    main()
