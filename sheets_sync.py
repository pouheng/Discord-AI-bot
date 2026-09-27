"""
sheets_sync.py — Google Sheets 唯讀公開鏡像同步（T11/T12）

單一試算表、多工作表（角色檔案 / 招式 / 結界 / 對撞紀錄）重寫式同步：
- OAuth 2.0：credentials.json（OAuth 用戶端憑據）→ 首次授權產生 token.json
- 每次寫入後標記該工作表 dirty，啟動 5 秒累積視窗（coalescing），
  視窗結束一次重寫所有 dirty 工作表（資料量小，重寫保證一致性）
- Sheets 為唯讀公開鏡像：本地 SQLite 是唯一資料源，不支援回寫
- 同步失敗（授權過期/斷線）：dirty 保留，下次觸發重試

依賴套件（spec 8.2）：google-auth-oauthlib、google-auth-httplib2、
google-api-python-client。未安裝時 sync 回報環境缺依賴，不崩潰。
"""

import asyncio
import json
import os
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

import collide_db


# ── 設定 ──────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.environ.get("GOOGLE_CREDENTIALS", os.path.join(SCRIPT_DIR, "credentials.json"))
TOKEN_FILE = os.environ.get("GOOGLE_TOKEN", os.path.join(SCRIPT_DIR, "token.json"))
SHEET_ID = os.environ.get("GOOGLE_SHEETS_ID", "")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")


def _config() -> dict:
    """讀取 config.json（含 google_sheets 區塊）。不存在時回空。"""
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_spreadsheet_id() -> str:
    """spreadsheet_id 來源：config google_sheets.spreadsheet_id → 環境變數。"""
    cfg = _config().get("google_sheets") or {}
    return str(cfg.get("spreadsheet_id") or os.environ.get("GOOGLE_SHEETS_ID", ""))


# ── OAuth ─────────────────────────────────────────────────

def _build_service():
    """建立 Google Sheets service（OAuth）。缺 credentials.json 或依賴時 raise。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "缺少 Google API 套件（google-auth-oauthlib / google-auth-httplib2 / "
            "google-api-python-client）。請先 pip install 並依 spec 8.2 提供 credentials.json。"
        ) from e

    if not os.path.exists(CREDENTIALS_FILE):
        raise RuntimeError(
            f"找不到 credentials.json（預期路徑：{CREDENTIALS_FILE}）。\n"
            "請至 Google Cloud Console：啟用 Google Sheets API → 建立 OAuth 用戶端（Web）"
            "→ 下載 credentials.json 放至專案根目錄。"
        )

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, scopes)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, scopes)
            # 憑證登記的 redirect_uri 是固定 http://localhost:8080/，必須用同一個埠
            #（port=0 隨機選埠會導致 redirect_uri_mismatch）
            creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("sheets", "v4", credentials=creds)


# ── 工作表定義 ────────────────────────────────────────────

@dataclass
class SheetSpec:
    """一個工作表的定義：名稱、欄位清單、資料來源查詢。"""
    name: str
    columns: list[str]
    rows: Callable[[object], list[dict]]  # (conn) -> list of row dicts


def _char_rows(conn):
    return collide_db.list_characters(conn)


def _skill_rows(conn):
    return collide_db.list_skills(conn)


def _ability_rows(conn):
    return collide_db.list_abilities(conn)


def _domain_rows(conn):
    return collide_db.list_domains(conn)


def _battle_rows(conn):
    return collide_db.list_battles(conn)


# 角色檔案：展開 stats / special_forms 為欄
_CHAR_COLUMNS = ["name", "affiliation", "race", "school_rank", "stats", "special_tags",
                 "special_forms", "weapon", "weapon_base", "max_mp", "martial", "exp",
                 "is_teacher", "is_villain", "dynamic_difficulty"]

_SKILL_COLUMNS = ["name", "ability", "owner", "application", "skill_type",
                  "magic_system", "element", "mp_cost", "power_base", "effect"]

_ABILITY_COLUMNS = ["name", "owner", "category", "principle",
                    "range_limit", "range_decay", "proficiency"]

_DOMAIN_COLUMNS = ["name", "owner", "domain_type", "awakening", "precision",
                   "rule_self", "rule_enemy", "rule_environment", "cooldown", "sustain_mp"]

_BATTLE_COLUMNS = ["id", "thread_id", "battle_time", "attacker", "attacker_skill",
                   "defender", "defender_skill", "atk_output", "def_output", "element_mod",
                   "verdict", "result", "remaining_damage", "domain_fusion", "status"]


def _serialize_cells(row: dict, columns: list[str]) -> list:
    """把 row dict 依欄位順序序列化；dict/list 轉 JSON 字串。"""
    out = []
    for col in columns:
        v = row.get(col)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        if v is None:
            v = ""
        out.append(v)
    return out


def build_sheet_specs() -> list[SheetSpec]:
    return [
        SheetSpec("角色檔案", _CHAR_COLUMNS, _char_rows),
        SheetSpec("能力", _ABILITY_COLUMNS, _ability_rows),
        SheetSpec("招式", _SKILL_COLUMNS, _skill_rows),
        SheetSpec("結界", _DOMAIN_COLUMNS, _domain_rows),
        SheetSpec("對撞紀錄", _BATTLE_COLUMNS, _battle_rows),
    ]


# ── 同步核心 ──────────────────────────────────────────────

def sync_sheet(service, sheet_id: str, spec: SheetSpec, conn) -> dict:
    """重寫式同步一個工作表。回傳 {"title", "rows", "ok", "error"}。"""
    rows = spec.rows(conn)
    header = spec.columns
    values = [header] + [_serialize_cells(r, spec.columns) for r in rows]
    # 依名稱找工作表 id；不存在則建立
    meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    title = spec.name
    sheet = next((s for s in meta["sheets"] if s["properties"]["title"] == title), None)
    if sheet is None:
        body = {"requests": [{"addSheet": {"properties": {"title": title}}}]}
        service.spreadsheets().batchUpdate(spreadsheetId=sheet_id, body=body).execute()
        sid = None
    else:
        sid = sheet["properties"]["sheetId"]
    # 先清空再寫入（重寫式）
    if sid is not None:
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id, range=f"'{title}'!A1:ZZ",
        ).execute()
    body = {"majorDimension": "ROWS", "values": values}
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id, range=f"'{title}'!A1",
        valueInputOption="USER_ENTERED", body=body,
    ).execute()
    return {"title": title, "rows": len(rows), "ok": True}


# ── 批量寫入緩衝（T12：5 秒 coalescing）───────────────────

@dataclass
class SyncBuffer:
    """5 秒 trailing debounce 同步緩衝。thread-safe。"""

    sheet_id: str = ""
    window_seconds: float = 5.0
    _lock: object = field(default_factory=threading.Lock)
    _dirty: set = field(default_factory=set)  # 需要重寫的工作表名
    _timer: Optional[threading.Timer] = None
    _running: bool = False
    _call_count: int = 0
    _last_error: Optional[str] = None
    db_path: str = ""

    def _build_service(self):
        """建立 Google Sheets service（可被測試覆寫）。"""
        return _build_service()

    def _fire(self) -> None:
        if not self.sheet_id:
            return
        try:
            self._flush_locked()
        finally:
            with self._lock:
                self._running = False
                self._timer = None

    def _flush_locked(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            dirty = set(self._dirty)
            self._dirty = set()
        try:
            service = self._build_service()
            conn = collide_db.open_conn(self.db_path or collide_db.DB_FILE)
            try:
                for spec in build_sheet_specs():
                    if spec.name in dirty:
                        self._call_count += 1
                        sync_sheet(service, self.sheet_id, spec, conn)
            finally:
                conn.close()
        except Exception as e:
            with self._lock:
                self._last_error = f"{e}"
                self._dirty |= dirty  # 失敗保留 dirty，下次重試
            if os.environ.get("COLLIDE_SYNC_DEBUG"):
                print("sheets sync failed:", traceback.format_exc(limit=3))

    def mark_dirty(self, sheet_name: str) -> None:
        """標記工作表 dirty 並啟動（或重置）5 秒視窗。"""
        with self._lock:
            self._dirty.add(sheet_name)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self.window_seconds, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def flush_now(self) -> None:
        """立即同步（測試/關閉時用）。"""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._flush_locked()

    @property
    def call_count(self) -> int:
        return self._call_count


_buffer: Optional[SyncBuffer] = None


def get_buffer(sheet_id: str = "", db_path: str = "") -> SyncBuffer:
    """取得全域同步緩衝（惰性建立）。"""
    global _buffer
    if _buffer is None:
        _buffer = SyncBuffer(sheet_id=sheet_id or get_spreadsheet_id(), db_path=db_path)
    elif sheet_id:
        _buffer.sheet_id = sheet_id or _buffer.sheet_id
    return _buffer


def mark_all_dirty(buffer: Optional[SyncBuffer] = None) -> None:
    """全部工作表標記 dirty（初始同步 / 手動觸發）。"""
    buf = buffer or get_buffer()
    for spec in build_sheet_specs():
        buf.mark_dirty(spec.name)


def sync_now() -> dict:
    """同步全部工作表（同步執行，回傳結果清單）。"""
    buf = get_buffer()
    if not buf.sheet_id:
        raise RuntimeError("未設定 spreadsheet_id（config google_sheets.spreadsheet_id 或 GOOGLE_SHEETS_ID）")
    results = []
    service = _build_service()
    conn = collide_db.open_conn(buf.db_path or collide_db.DB_FILE)
    try:
        for spec in build_sheet_specs():
            buf._call_count += 1
            results.append(sync_sheet(service, buf.sheet_id, spec, conn))
    finally:
        conn.close()
    return results


# ── 對外通知（寫入後標記 dirty）──────────────────────────

# sheet_name 對照：character→角色檔案 / ability→能力 / skill→招式 / domain→結界 / battle→對撞紀錄
_NAME_MAP = {
    "character": "角色檔案",
    "ability": "能力",
    "skill": "招式",
    "domain": "結界",
    "battle": "對撞紀錄",
    "clash": "對撞紀錄",
}


def notify_write(kind: str) -> None:
    """資料寫入後呼叫：標記對應工作表 dirty 並啟動 5 秒累積視窗。

    僅在設定 spreadsheet_id 後才實際排程；未設定時為 no-op（純本地運作）。
    """
    buf = get_buffer()
    if not buf.sheet_id:
        return
    name = _NAME_MAP.get(kind)
    if name:
        buf.mark_dirty(name)


# ── 背景自動同步（自我修復）──────────────────────────────

def start_auto_flush(interval_seconds: float = 30.0) -> Optional[threading.Thread]:
    """啟動背景執行緒：每 interval 秒檢查 dirty，有則立即同步。

    用途：debounce 視窗可能因程序重啟而遺失（寫入後 5 秒內重啟會丟掉待同步標記），
    或同步失敗後沒有重試觸發。長期運作的程序（bot / 管理頁）啟動時呼叫一次，
    之後所有寫入都會在數秒內自動同步，且失敗會在下一輪自癒重試。
    已啟動過則直接回傳既有執行緒（不重複建立）。
    """
    buf = get_buffer()
    if not buf.sheet_id:
        return None
    existing = getattr(buf, "_auto_thread", None)
    if existing and existing.is_alive():
        return existing

    def _loop():
        while True:
            time.sleep(interval_seconds)
            with buf._lock:
                dirty = bool(buf._dirty)
            if dirty:
                try:
                    buf.flush_now()
                except Exception:
                    pass

    t = threading.Thread(target=_loop, name="sheets-auto-flush", daemon=True)
    t.start()
    buf._auto_thread = t
    return t
