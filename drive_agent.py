"""drive_agent.py — Google Drive 資料夾 Agent 工具層。

用途：讓角色能「自己判斷需不需要」讀取某個 Google Drive 資料夾裡的檔案，
並且在需要時寫入／修改文字檔。所有操作都被限制在 config.json 的
`drive_agent.folder_id` 這棵子樹底下（用 `..` 或檔案 ID 都繞不出去）。

安全設計：
- 只讀寫白名單副檔名（預設 .md/.txt/.json/.csv），其餘一律拒絕
- 不支援刪除、搬移、改權限、分享（避免不可逆操作）
- 覆蓋前先把舊內容快照到 drive_snapshots/（Drive 版本回溯是第二道保險）
- 每次寫入都先用 review 模型審核，通過才寫（可關閉）
- 所有動作寫入 drive_logs/audit.jsonl

對外介面：
    list_files(path="")              -> dict
    read_file(path)                  -> dict
    write_file(path, content, mode)  -> dict
    search_files(query)              -> dict
    run_agent_loop(messages, ...)    -> str（讓模型用 tool calling 自己決定）

CLI（手動測試）：
    python drive_agent.py list
    python drive_agent.py list 子資料夾
    python drive_agent.py read notes.md
    python drive_agent.py write notes.md "內容" --mode create
    python drive_agent.py search 關鍵字
"""
import argparse
import asyncio
import datetime
import io
import json
import os
import re
import sys
import threading

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
TOKEN_FILE = os.environ.get(
    "DRIVE_AGENT_TOKEN", os.path.join(SCRIPT_DIR, "drive_agent_token.json")
)
SCOPE = "https://www.googleapis.com/auth/drive"

FOLDER_MIME = "application/vnd.google-apps.folder"
GDOC_MIME = "application/vnd.google-apps.document"
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"
GSLIDE_MIME = "application/vnd.google-apps.presentation"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

DEFAULTS = {
    "enabled": True,
    "folder_id": "",
    "folder_name": "",
    "max_steps": 6,
    "read_steps": 3,
    "max_read_bytes": 200_000,
    "max_write_bytes": 200_000,
    "max_chunk_chars": 4000,
    "max_image_bytes": 8_388_608,
    "writable_extensions": [".md", ".txt", ".json", ".csv"],
    "readonly_extensions": [".pdf", ".docx", ".xlsx"],
    "image_extensions": [".png", ".jpg", ".jpeg", ".webp", ".gif"],
    "snapshot_dir": "drive_snapshots",
    "review_writes": True,
    "review_fail_open": False,
}

AUDIT_DIR = os.path.join(SCRIPT_DIR, "drive_logs")
AUDIT_FILE = os.path.join(AUDIT_DIR, "audit.jsonl")
IMAGE_CACHE_DIR = os.path.join(SCRIPT_DIR, "drive_cache")


class DriveAgentError(Exception):
    """工具層的可預期錯誤（會原樣回報給模型，不當成程式崩潰）。"""


# ── 設定 ──────────────────────────────────────────────────

_cfg_cache = {"mtime": None, "data": None}


def get_config() -> dict:
    """讀 config.json 的 drive_agent 區塊（依檔案 mtime 快取）。"""
    try:
        mtime = os.path.getmtime(CONFIG_FILE)
    except OSError:
        return dict(DEFAULTS)
    if _cfg_cache["mtime"] != mtime:
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = {}
        cfg = dict(DEFAULTS)
        cfg.update(raw.get("drive_agent") or {})
        _cfg_cache["mtime"] = mtime
        _cfg_cache["data"] = cfg
    return dict(_cfg_cache["data"] or DEFAULTS)


def is_enabled() -> bool:
    cfg = get_config()
    return bool(cfg.get("enabled")) and bool(cfg.get("folder_id"))


# ── Google 授權 ───────────────────────────────────────────

_service_cache = {"service": None}
_service_lock = threading.Lock()


def get_service():
    """Drive service（獨立 drive_agent_token.json），來源變更時重建。"""
    with _service_lock:
        if _service_cache["service"] is not None:
            return _service_cache["service"]
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as e:
            raise DriveAgentError(
                "缺少 Google API 套件（google-auth-oauthlib / google-auth-httplib2 / "
                "google-api-python-client）"
            ) from e

        if not os.path.exists(TOKEN_FILE):
            raise DriveAgentError(
                f"找不到 {TOKEN_FILE}，請先執行 python drive_agent_auth.py 授權"
            )
        _install_retry_shim()
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, [SCOPE])
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                with open(TOKEN_FILE, "w", encoding="utf-8") as f:
                    f.write(creds.to_json())
            else:
                raise DriveAgentError("授權已失效，請重新執行 python drive_agent_auth.py")
        _service_cache["service"] = build("drive", "v3", credentials=creds)
        return _service_cache["service"]


def _reset_service():
    """測試／換帳號用。"""
    with _service_lock:
        _service_cache["service"] = None


# ── 路徑與範圍 ────────────────────────────────────────────

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,}$")


def _install_retry_shim():
    """Google 端點偶爾直接 SSL EOF／連線中斷；對 execute() 加一次輕量重試。

    只攔截傳輸層錯誤（SSL/連線），HTTP 4xx/5xx 仍照原本行為往上拋，
    由 googleapiclient 的 num_retries 處理。
    """
    import ssl
    import time

    try:
        from googleapiclient import http as ghttp
    except ImportError:
        return
    if getattr(ghttp.HttpRequest, "_drive_agent_patched", False):
        return

    original = ghttp.HttpRequest.execute

    def execute_with_retry(self, http=None, num_retries=0):
        last = None
        for attempt in range(3):
            try:
                return original(self, http=http, num_retries=num_retries)
            except (ssl.SSLError, OSError, ConnectionError) as e:
                last = e
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise last

    ghttp.HttpRequest.execute = execute_with_retry
    ghttp.HttpRequest._drive_agent_patched = True


def _norm(meta: dict, parent: str = "") -> dict:
    mime = meta.get("mimeType", "")
    return {
        "id": meta.get("id", ""),
        "name": meta.get("name", ""),
        "mime": mime,
        "size": int(meta.get("size") or 0),
        "modified": meta.get("modifiedTime", ""),
        "parent": parent,
        "is_folder": mime == FOLDER_MIME,
    }


def _list_children(service, parent_id: str) -> list[dict]:
    out: list[dict] = []
    token = None
    while True:
        res = (
            service.files()
            .list(
                q=f"'{parent_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,size,modifiedTime)",
                pageSize=200,
                pageToken=token,
                orderBy="folder,name",
            )
            .execute()
        )
        out.extend(_norm(f, parent_id) for f in res.get("files", []))
        token = res.get("nextPageToken")
        if not token:
            break
    return out


def _in_tree(service, file_id: str, root: str, max_depth: int = 12) -> bool:
    """檔案是否在 root 子樹內（往上追 parents，最多 max_depth 層）。"""
    cur = file_id
    for _ in range(max_depth):
        if cur == root:
            return True
        meta = service.files().get(fileId=cur, fields="id,parents").execute()
        parents = meta.get("parents") or []
        if not parents:
            return False
        cur = parents[0]
    return False


def _resolve(service, path: str):
    """把相對路徑（或檔案 ID）解析成 (entry|None, parent_id, name)。"""
    cfg = get_config()
    root = cfg["folder_id"]
    if not root:
        raise DriveAgentError("drive_agent.folder_id 未設定")

    raw = (path or "").strip().replace("\\", "/")
    if not raw or raw in (".", "/"):
        entry = service.files().get(
            fileId=root, fields="id,name,mimeType"
        ).execute()
        return _norm(entry), root, entry.get("name", "")

    if _ID_RE.match(raw) and "/" not in raw:
        meta = service.files().get(
            fileId=raw, fields="id,name,mimeType,size,modifiedTime,parents"
        ).execute()
        if not _in_tree(service, raw, root):
            raise DriveAgentError("這個檔案不在允許的資料夾內")
        parent = (meta.get("parents") or [root])[0]
        return _norm(meta, parent), parent, meta.get("name", "")

    parts = [p for p in raw.split("/") if p and p != "."]
    if any(p == ".." for p in parts):
        raise DriveAgentError("路徑不合法（不允許 ..）")
    # 模型常把資料夾本身也寫進路徑（澪創作/世界觀.md）→ 去掉
    folder_name = (cfg.get("folder_name") or "").strip()
    while parts and folder_name and parts[0] == folder_name:
        parts.pop(0)
    if not parts:
        raise DriveAgentError("路徑不合法")

    parent = root
    entry = None
    for idx, part in enumerate(parts):
        matches = [c for c in _list_children(service, parent) if c["name"] == part]
        if not matches:
            if idx == len(parts) - 1:
                return None, parent, part
            raise DriveAgentError(f"找不到資料夾「{part}」")
        entry = matches[0]
        parent = entry["id"]
    return entry, entry["parent"], entry["name"]


# ── 審計與快照 ────────────────────────────────────────────

def _audit(action: str, **fields):
    try:
        os.makedirs(AUDIT_DIR, exist_ok=True)
        record = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "action": action,
        }
        record.update(fields)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _snapshot(name: str, data: bytes) -> str:
    cfg = get_config()
    folder = os.path.join(SCRIPT_DIR, cfg.get("snapshot_dir") or "drive_snapshots")
    os.makedirs(folder, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe = re.sub(r'[\\/:*?"<>|]', "_", name) or "file"
    path = os.path.join(folder, f"{ts}_{safe}")
    with open(path, "wb") as f:
        f.write(data)
    return path


# ── 讀取 ──────────────────────────────────────────────────

def _decode(data: bytes) -> str:
    return data.decode("utf-8", errors="replace")


def _ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


def _read_docx(data: bytes) -> str:
    try:
        import docx
    except ImportError as e:
        raise DriveAgentError("讀取 .docx 需要 python-docx") from e
    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(c.text for c in row.cells))
    return "\n".join(p for p in parts if p is not None)


def _read_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise DriveAgentError("讀取 PDF 需要安裝 pypdf（pip install pypdf）") from e
    reader = PdfReader(io.BytesIO(data))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def _sections(text: str) -> list[dict]:
    """抓 Markdown 標題（# ~ ######），給模型當目錄用。"""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.strip())
        if m:
            out.append({"level": len(m.group(1)), "title": m.group(2).strip()})
    return out


def _slice_by_section(text: str, section: str, limit: int) -> tuple[str, bool]:
    """取出某個 Markdown 標題底下的內容（到下一個同級或更高級標題為止）。"""
    lines = text.splitlines()
    want = (section or "").strip().lower()
    start = -1
    level = 6
    for i, line in enumerate(lines):
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line.strip())
        if not m:
            continue
        title = m.group(2).strip()
        if want and (want in title.lower() or title.lower() in want):
            start = i
            level = len(m.group(1))
            break
    if start < 0:
        raise DriveAgentError(
            "找不到這個段落，可用段落："
            + "、".join(s["title"] for s in _sections(text)[:15])
        )
    end = len(lines)
    for j in range(start + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s+", lines[j].strip())
        if m and len(m.group(1)) <= level:
            end = j
            break
    chunk = "\n".join(lines[start:end]).strip()
    truncated = len(chunk) > limit
    return chunk[:limit], truncated


def _grep_text(text: str, query: str, limit: int, context: int = 1) -> tuple[str, bool]:
    """在檔案內找關鍵字，回傳含上下各 context 行的片段。"""
    q = (query or "").strip().lower()
    if not q:
        raise DriveAgentError("grep 需要 query")
    lines = text.splitlines()
    hits = []
    for i, line in enumerate(lines):
        if q in line.lower():
            lo = max(0, i - context)
            hi = min(len(lines), i + context + 1)
            hits.append((i + 1, "\n".join(lines[lo:hi])))
            if len(hits) >= 20:
                break
    if not hits:
        return "（沒有符合的內容）", False
    out = "\n---\n".join(f"[第 {no} 行]\n{body}" for no, body in hits)
    truncated = len(out) > limit
    return out[:limit], truncated


def _read_sync(path: str, max_bytes: int | None = None, *,
               mode: str = "auto", offset: int = 0, limit: int | None = None,
               section: str = "", query: str = "") -> dict:
    cfg = get_config()
    hard_limit = int(max_bytes or cfg["max_read_bytes"])
    chunk_cap = int(cfg.get("max_chunk_chars") or 4000)
    chunk = max(200, min(int(limit or chunk_cap), 20000))
    service = get_service()
    entry, _parent, _name = _resolve(service, path)
    if entry is None:
        raise DriveAgentError(f"找不到檔案：{path}")
    if entry["is_folder"]:
        children = _list_children(service, entry["id"])
        return {
            "ok": True,
            "kind": "folder",
            "name": entry["name"],
            "path": path,
            "entries": [
                {
                    "name": c["name"],
                    "kind": "folder" if c["is_folder"] else "file",
                    "size": c["size"],
                    "modified": c["modified"],
                }
                for c in children
            ],
        }

    name = entry["name"]
    mime = entry["mime"]
    ext = _ext(name)

    if mime in (GDOC_MIME, GSLIDE_MIME):
        data = service.files().export(fileId=entry["id"], mimeType="text/plain").execute()
        kind = "text"
    elif mime == GSHEET_MIME:
        data = service.files().export(fileId=entry["id"], mimeType="text/csv").execute()
        kind = "table"
    elif mime == DOCX_MIME or ext == ".docx":
        text = _read_docx(service.files().get_media(fileId=entry["id"]).execute())
        data = text.encode("utf-8")
        kind = "text"
    elif mime == "application/pdf" or ext == ".pdf":
        text = _read_pdf(service.files().get_media(fileId=entry["id"]).execute())
        data = text.encode("utf-8")
        kind = "text"
    elif mime.startswith("image/") or ext in cfg["image_extensions"]:
        raw = service.files().get_media(fileId=entry["id"]).execute()
        if len(raw) > int(cfg["max_image_bytes"]):
            raise DriveAgentError("圖片太大，超過上限")
        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r'[\\/:*?"<>|]', "_", name) or "image"
        local = os.path.join(IMAGE_CACHE_DIR, f"{ts}_{safe}")
        with open(local, "wb") as f:
            f.write(raw)
        return {
            "ok": True,
            "kind": "image",
            "name": name,
            "path": path,
            "local_path": local,
            "bytes": len(raw),
        }
    else:
        raw = service.files().get_media(fileId=entry["id"]).execute()
        if b"\x00" in raw[:1024]:
            raise DriveAgentError(f"不支援的二進位檔：{name}")
        data = raw
        kind = "text"

    # 全文 → 再依模型指定的模式切片（total 用實際字數，不受 max_read_bytes 截斷）
    full_all = _decode(data)
    total = len(full_all)
    full = full_all[:hard_limit]
    secs = _sections(full) if kind == "text" else []
    mode = (mode or "auto").strip().lower()

    if mode == "full":
        if total > hard_limit:
            raise DriveAgentError(
                f"檔案太大（{total} 字），請改用 mode=section／grep／offset 分段讀"
            )
        body, truncated, start = full_all, False, 0
    elif mode == "section":
        body, truncated = _slice_by_section(full, section, chunk)
        start = 0
    elif mode == "grep":
        body, truncated = _grep_text(full, query, chunk)
        start = 0
    else:
        start = max(0, int(offset or 0))
        body = full[start:start + chunk]
        truncated = start + chunk < total

    result = {
        "ok": True,
        "kind": kind,
        "name": name,
        "path": path,
        "bytes": len(data),
        "total_chars": total,
        "returned_chars": len(body),
        "truncated": truncated or total > hard_limit,
        "mode": mode,
        "text": body,
    }
    if secs:
        result["sections"] = [s["title"] for s in secs[:30]]
    if mode in ("auto", "offset") and start + len(body) < total:
        result["next_offset"] = start + len(body)
    return result


async def read_file(path: str, max_bytes: int | None = None, *,
                    mode: str = "auto", offset: int = 0, limit: int | None = None,
                    section: str = "", query: str = "") -> dict:
    return await asyncio.to_thread(
        _read_sync, path, max_bytes,
        mode=mode, offset=offset, limit=limit, section=section, query=query,
    )


async def list_files(path: str = "") -> dict:
    return await asyncio.to_thread(_read_sync, path, None)


# ── 搜尋 ──────────────────────────────────────────────────

def _walk(service, folder_id: str, depth: int = 0, acc: list | None = None,
          prefix: str = "") -> list[dict]:
    if acc is None:
        acc = []
    if depth > 4 or len(acc) > 300:
        return acc
    for child in _list_children(service, folder_id):
        child = dict(child)
        child["path"] = prefix + child["name"]
        acc.append(child)
        if child["is_folder"]:
            _walk(service, child["id"], depth + 1, acc, child["path"] + "/")
    return acc


_tree_cache = {"ts": 0.0, "text": "", "folder": ""}


def folder_tree_text(ttl: int = 300, max_entries: int = 60) -> str:
    """資料夾的檔案清單（只有檔名），給 Phase 1 判斷要不要讀；快取 ttl 秒。"""
    import time

    cfg = get_config()
    if not is_enabled():
        return ""
    now = time.time()
    if _tree_cache["text"] and _tree_cache["folder"] == cfg["folder_id"] \
            and now - _tree_cache["ts"] < ttl:
        return _tree_cache["text"]
    try:
        entries = _walk(get_service(), cfg["folder_id"])
        lines = []
        for e in entries[:max_entries]:
            lines.append(f"- {e['path']}{'/' if e['is_folder'] else ''}")
        if len(entries) > max_entries:
            lines.append(f"（還有 {len(entries) - max_entries} 個項目）")
        text = "\n".join(lines)
    except Exception as e:
        _audit("tree_error", error=f"{type(e).__name__}: {e}")
        text = _tree_cache["text"]
    _tree_cache.update(ts=now, text=text, folder=cfg["folder_id"])
    return text


def _search_sync(query: str, limit: int = 20) -> dict:
    cfg = get_config()
    service = get_service()
    q = (query or "").strip().lower()
    if not q:
        raise DriveAgentError("缺少搜尋關鍵字")
    hits = [c for c in _walk(service, cfg["folder_id"]) if q in c["name"].lower()]
    return {
        "ok": True,
        "query": query,
        "count": len(hits),
        "entries": [
            {
                "name": h["name"],
                "kind": "folder" if h["is_folder"] else "file",
                "size": h["size"],
                "modified": h["modified"],
            }
            for h in hits[:limit]
        ],
    }


async def search_files(query: str, limit: int = 20) -> dict:
    return await asyncio.to_thread(_search_sync, query, limit)


# ── 寫入審核 ──────────────────────────────────────────────

_REVIEW_RULES = """你是檔案變更審核員。以下是一個 Discord 角色想寫入 Google Drive 資料夾的變更。
只回 JSON：{{"approve": true/false, "reason": "簡短原因"}}

重要：檔案位置已由程式驗證，必定在資料夾「{folder}」內，不需要你判斷路徑。

規則：
1. 內容不得包含 API key、token、密碼、私鑰、身分證字號等機密。
2. 內容不得是會自動執行的程式碼（shell／Python／JS）或引流到外部網站的惡意內容。
3. 覆蓋既有檔案時，若新內容把原內容刪掉超過 80% 且長度少於 200 字，視為可疑
   （除非是明確的重新整理）。
4. 內容不得是無意義的大量重複字元或明顯的垃圾內容。
5. 短句、測試文字、草稿、設定筆記都算正常內容，不要因為「跟創作無關」而否決。
6. 內容長度不得超過 {max_bytes} bytes。

模式：{mode}
檔案：{path}
── 原內容（最多 1200 字）──
{old}
── 新內容（最多 1200 字）──
{new}
"""


async def review_write(path: str, old_text: str, new_text: str, mode: str) -> tuple[bool, str]:
    cfg = get_config()
    if not cfg.get("review_writes", True):
        return True, "審核已關閉"
    try:
        from llm_models import chat_create

        prompt = _REVIEW_RULES.format(
            folder=cfg.get("folder_name") or cfg.get("folder_id"),
            max_bytes=cfg["max_write_bytes"],
            mode=mode,
            path=path,
            old=(old_text or "")[:1200] or "（新檔案）",
            new=(new_text or "")[:1200],
        )
        resp = await chat_create(
            "review",
            messages=[
                {"role": "system", "content": "只輸出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=900,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        if not raw.strip():
            # 思考型模型偶爾會把 token 全花在 reasoning 上，重試一次
            resp = await chat_create(
                "review",
                messages=[
                    {"role": "system", "content": "只輸出 JSON，不要思考。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=1200,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or ""
        data = json.loads(re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip())
        approved = bool(data.get("approve", False))
        return approved, str(data.get("reason", ""))[:200]
    except Exception as e:
        if cfg.get("review_fail_open"):
            return True, f"審核失敗但設定為放行：{type(e).__name__}"
        return False, f"審核失敗（預設擋下）：{type(e).__name__}: {e}"


# ── 寫入 ──────────────────────────────────────────────────

def _validate_write(cfg: dict, name: str, content: str, mode: str):
    ext = _ext(name)
    if ext not in [e.lower() for e in cfg["writable_extensions"]]:
        raise DriveAgentError(
            f"「{ext or '無副檔名'}」不可寫入，只允許 {', '.join(cfg['writable_extensions'])}"
        )
    size = len(content.encode("utf-8"))
    if size > int(cfg["max_write_bytes"]):
        raise DriveAgentError(f"內容太大（{size} bytes），上限 {cfg['max_write_bytes']}")
    if mode not in ("create", "overwrite", "append"):
        raise DriveAgentError("mode 只能是 create / overwrite / append")


def _current_text(service, entry: dict) -> str:
    if not entry:
        return ""
    if entry["mime"] in (GDOC_MIME, GSHEET_MIME, GSLIDE_MIME):
        return ""
    try:
        return _decode(service.files().get_media(fileId=entry["id"]).execute())
    except Exception:
        return ""


def _last_name(path: str) -> str:
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p and p != "."]
    return parts[-1] if parts else ""


def _ensure_parent_folders(service, path: str) -> tuple[str, str]:
    """建立（必要時）目標路徑的父資料夾，回傳 (parent_id, 檔名)。

    只作用在白名單根目錄底下；已存在的資料夾直接沿用。
    """
    cfg = get_config()
    root = cfg["folder_id"]
    folder_name = (cfg.get("folder_name") or "").strip()
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p and p != "."]
    if any(p == ".." for p in parts) or len(parts) < 2:
        raise DriveAgentError("路徑不合法")
    while parts and folder_name and parts[0] == folder_name:
        parts.pop(0)
    if len(parts) < 2:
        raise DriveAgentError("路徑不合法")

    parent = root
    for part in parts[:-1]:
        matches = [
            c for c in _list_children(service, parent)
            if c["name"] == part and c["is_folder"]
        ]
        if matches:
            parent = matches[0]["id"]
            continue
        created = (
            service.files()
            .create(
                body={"name": part, "mimeType": FOLDER_MIME, "parents": [parent]},
                fields="id,name",
            )
            .execute()
        )
        parent = created["id"]
        _audit("mkdir", path=part, parent=parent)
    return parent, parts[-1]


def _write_sync(path: str, content: str, mode: str) -> dict:
    from googleapiclient.http import MediaIoBaseUpload

    cfg = get_config()
    service = get_service()
    try:
        entry, parent_id, name = _resolve(service, path)
    except DriveAgentError as e:
        if "找不到資料夾" in str(e) and "/" in (path or "").replace("\\", "/").strip("/"):
            entry, name = None, _last_name(path)
            parent_id = ""
        else:
            raise
    _validate_write(cfg, name, content, mode)

    if entry and entry["is_folder"]:
        raise DriveAgentError("目標是資料夾，不能寫入")
    if entry and entry["mime"] in (GDOC_MIME, GSHEET_MIME, GSLIDE_MIME):
        raise DriveAgentError("Google 原生文件不支援直接寫入，請改用 .md 檔")
    if mode == "create" and entry:
        raise DriveAgentError(f"檔案已存在：{name}")
    if mode in ("overwrite", "append") and not entry:
        raise DriveAgentError(f"找不到檔案：{name}（要新建請用 mode=create）")
    if entry is None and "/" in (path or "").replace("\\", "/").strip("/"):
        # 目標在子資料夾裡而資料夾不存在 → 自動建立（只會建在白名單根目錄下）
        parent_id, name = _ensure_parent_folders(service, path)
        _validate_write(cfg, name, content, mode)

    old_text = _current_text(service, entry) if entry else ""
    new_text = (old_text + content) if mode == "append" else content
    raw = new_text.encode("utf-8")
    if entry:
        old_raw = old_text.encode("utf-8")
        snap = _snapshot(name, old_raw) if old_raw else ""
        media = MediaIoBaseUpload(
            io.BytesIO(raw), mimetype="text/plain; charset=utf-8", resumable=False
        )
        service.files().update(fileId=entry["id"], media_body=media).execute()
        result = {"ok": True, "action": "updated", "name": name, "bytes": len(raw)}
        if snap:
            result["snapshot"] = os.path.relpath(snap, SCRIPT_DIR)
    else:
        media = MediaIoBaseUpload(
            io.BytesIO(raw), mimetype="text/plain; charset=utf-8", resumable=False
        )
        created = (
            service.files()
            .create(
                body={"name": name, "parents": [parent_id]},
                media_body=media,
                fields="id,name",
            )
            .execute()
        )
        result = {
            "ok": True,
            "action": "created",
            "name": name,
            "bytes": len(raw),
            "id": created.get("id"),
        }
    _audit("write", path=path, mode=mode, **{k: result[k] for k in ("name", "bytes") if k in result})
    return result


async def write_file(path: str, content: str, mode: str = "overwrite") -> dict:
    """寫入文字檔。寫之前會先讀舊內容給審核模型看，通過才寫。"""
    if not content:
        raise DriveAgentError("內容是空的")
    cfg = get_config()

    def _prepare():
        service = get_service()
        try:
            entry, _parent, name = _resolve(service, path)
        except DriveAgentError as e:
            if "找不到資料夾" in str(e):
                entry, name = None, _last_name(path)
            else:
                raise
        _validate_write(cfg, name, content, mode)
        old = _current_text(service, entry) if entry else ""
        return name, old, (old + content) if mode == "append" else content

    name, old_text, new_text = await asyncio.to_thread(_prepare)
    approved, reason = await review_write(path, old_text, new_text, mode)
    _audit(
        "review", path=path, mode=mode, approved=approved, reason=reason,
        bytes=len(new_text.encode("utf-8")),
    )
    if not approved:
        return {"ok": False, "error": f"審核未通過：{reason}", "review": reason}
    result = await asyncio.to_thread(_write_sync, path, content, mode)
    result["review"] = reason
    return result


# ── 給模型的工具定義 ──────────────────────────────────────

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "drive_list",
            "description": "列出指定資料夾裡的檔案（不給路徑＝資料夾根目錄）",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "資料夾相對路徑，例如 設定"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_read",
            "description": (
                "讀取檔案。大文件不要一次讀完：先用 mode=section 讀某個標題，"
                "或 mode=grep 找關鍵字，需要全文才用 mode=full"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "檔案相對路徑，例如 筆記/世界觀.md"},
                    "mode": {
                        "type": "string",
                        "enum": ["auto", "section", "grep", "full"],
                        "description": "auto=從頭讀一段、section=讀某標題、grep=找關鍵字、full=全文",
                    },
                    "section": {"type": "string", "description": "mode=section 時的標題名稱"},
                    "query": {"type": "string", "description": "mode=grep 時的關鍵字"},
                    "offset": {"type": "integer", "description": "mode=auto 時的起始字元位置"},
                    "limit": {"type": "integer", "description": "這次最多回傳幾個字（預設 4000）"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_write",
            "description": "寫入白名單文字檔（.md/.txt/.json/.csv）。新檔用 create",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "檔案相對路徑"},
                    "content": {"type": "string", "description": "要寫入的完整內容"},
                    "mode": {
                        "type": "string",
                        "enum": ["create", "overwrite", "append"],
                        "description": "create=新建、overwrite=覆蓋、append=附加",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "drive_search",
            "description": "在資料夾子樹裡用檔名關鍵字搜尋",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]


# 模型偶爾會自己發明工具名（drive_create、drive_save…）→ 對應到真正的工具
_TOOL_ALIASES = {
    "drive_create": ("drive_write", "create"),
    "drive_new": ("drive_write", "create"),
    "drive_save": ("drive_write", "overwrite"),
    "drive_update": ("drive_write", "overwrite"),
    "drive_append": ("drive_write", "append"),
    "drive_ls": ("drive_list", None),
    "drive_dir": ("drive_list", None),
    "drive_find": ("drive_search", None),
    "drive_open": ("drive_read", None),
    "drive_cat": ("drive_read", None),
}


async def call_tool(name: str, args: dict) -> dict:
    """執行模型指定的工具，錯誤一律包成 {"ok": false, "error": ...}。"""
    try:
        args = dict(args or {})
        key = (name or "").strip()
        if key in _TOOL_ALIASES:
            key, forced_mode = _TOOL_ALIASES[key]
            if forced_mode and not args.get("mode"):
                args["mode"] = forced_mode
            print(f"[Drive] 工具別名 {name} → {key}")
            name = key
        # 模型常把 path 寫成 file／filename／name，這裡統一
        if "path" not in args:
            for alias in ("file", "filename", "file_path", "filepath", "name"):
                if args.get(alias):
                    args["path"] = args[alias]
                    break
        if "query" not in args and args.get("keyword"):
            args["query"] = args["keyword"]
        if "section" not in args and args.get("heading"):
            args["section"] = args["heading"]
        if name == "drive_list":
            return await list_files(str(args.get("path") or ""))
        if name == "drive_read":
            return await read_file(
                str(args.get("path") or ""),
                mode=str(args.get("mode") or "auto"),
                section=str(args.get("section") or ""),
                query=str(args.get("query") or ""),
                offset=int(args.get("offset") or 0),
                limit=int(args["limit"]) if args.get("limit") else None,
            )
        if name == "drive_write":
            return await write_file(
                str(args.get("path") or ""),
                str(args.get("content") or ""),
                str(args.get("mode") or "overwrite"),
            )
        if name == "drive_search":
            return await search_files(str(args.get("query") or ""))
        return {"ok": False, "error": f"未知工具：{name}"}
    except DriveAgentError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        _audit("tool_error", tool=name, error=f"{type(e).__name__}: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ── Agent 迴圈 ────────────────────────────────────────────

def _parse_args(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


# DeepSeek 系列偶爾會把工具呼叫寫成自家 DSML 文字而不是 tool_calls。
# 例：<｜｜DSML｜｜ invoke name="drive_read"> ... </｜｜DSML｜｜ invoke>
_DSML_INVOKE_RE = re.compile(
    r"<[^<>]*\binvoke\s+name=\"([^\"]+)\"[^<>]*>(.*?)</[^<>]*\binvoke\s*>",
    re.DOTALL,
)
_DSML_PARAM_RE = re.compile(
    r"<[^<>]*\bparameter\s+name=\"([^\"]+)\"[^<>]*>(.*?)</[^<>]*\bparameter\s*>",
    re.DOTALL,
)
_DSML_ANY_RE = re.compile(r"<[^<>]*DSML[^<>]*>")


def extract_dsml_calls(text: str) -> list[tuple[str, dict]]:
    calls = []
    for m in _DSML_INVOKE_RE.finditer(text or ""):
        name = m.group(1).strip()
        args = {}
        for p in _DSML_PARAM_RE.finditer(m.group(2)):
            key = p.group(1).strip()
            val = p.group(2).strip()
            if key in ("offset", "limit"):
                try:
                    val = int(val)
                except ValueError:
                    pass
            args[key] = val
        calls.append((name, args))
    return calls


def strip_dsml(text: str) -> str:
    """移除 DSML 工具呼叫區塊（連同參數內容），避免漏進台詞。"""
    text = _DSML_INVOKE_RE.sub("", text or "")
    return _DSML_ANY_RE.sub("", text).strip()


def _tool_result_text(result: dict) -> str:
    """把工具結果壓成模型看得懂、但不吃爆 context 的文字。"""
    if not result.get("ok", False) and "error" in result:
        return f"錯誤：{result['error']}"
    kind = result.get("kind")
    if kind == "folder":
        lines = [f"資料夾「{result.get('name')}」："]
        for e in result.get("entries", []):
            lines.append(f"- {e['name']}（{'資料夾' if e['kind'] == 'folder' else '檔案'}，{e['size']} bytes）")
        return "\n".join(lines) or "（空資料夾）"
    if kind == "image":
        return f"圖片「{result.get('name')}」已下載到 {result.get('local_path')}（{result.get('bytes')} bytes）"
    if "text" in result:
        meta = []
        if result.get("total_chars"):
            meta.append(f"全長 {result['total_chars']} 字")
        if result.get("sections"):
            meta.append("段落：" + "、".join(result["sections"][:15]))
        if result.get("next_offset"):
            meta.append(f"還有後續，offset={result['next_offset']}")
        tail = "\n（內容過長，已截斷）" if result.get("truncated") else ""
        head = ("｜".join(meta) + "\n") if meta else ""
        return f"檔案「{result.get('name')}」{head}{result.get('text')}{tail}"
    if "entries" in result:
        return "\n".join(f"- {e['name']}" for e in result["entries"]) or "（沒有符合的檔案）"
    return json.dumps(result, ensure_ascii=False)[:1500]


_DEBUG = os.environ.get("MIO_DRIVE_DEBUG") == "1"


def _is_write_call(name: str) -> bool:
    return _TOOL_ALIASES.get((name or "").strip(), (name, None))[0] == "drive_write"


def _has_write(writes: list[dict]) -> bool:
    return any(
        w.get("ok") and w.get("action") in ("created", "updated") for w in writes
    )


def _clean_generated(text: str, path: str) -> str:
    """清掉模型把「路徑／mode」也一起輸出、或包了程式碼圍籬的情況。"""
    s = (text or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    junk = {
        path.strip(), path.strip().lstrip("/"), "create", "overwrite", "append",
        "mode=create", "mode: create", "mode：create", "已建立", "已寫入",
    }
    for _ in range(3):
        lines = s.splitlines()
        if lines and lines[0].strip() in junk:
            s = "\n".join(lines[1:]).lstrip()
        else:
            break
    return s


async def run_agent_loop(
    messages: list[dict],
    *,
    max_steps: int | None = None,
    temperature: float = 0.5,
    max_tokens: int = 2000,
    slot: str = "default",
    require_write: bool = False,
    fallback_path: str = "",
) -> dict:
    """讓模型自己決定要不要用 Drive 工具（tool calling）。

    require_write=True 時，若跑完還沒寫入，會再用「強制 drive_write」逼一次，
    避免模型只讀資料、最後什麼都沒存。
    回傳 {"text": 最終回覆, "tool_calls": [...], "writes": [...], "steps": n}
    """
    from llm_models import chat_create

    cfg = get_config()
    steps = int(max_steps or cfg.get("max_steps", 6))
    used: list[dict] = []
    writes: list[dict] = []
    msgs = list(messages)

    for step in range(steps):
        kwargs = {
            "messages": msgs,
            "tools": TOOL_DEFS,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if step == steps - 1:
            kwargs["tool_choice"] = "none"  # 最後一輪強制收尾，避免無限呼叫
        resp = await chat_create(slot, **kwargs)
        msg = resp.choices[0].message
        tcs = getattr(msg, "tool_calls", None) or []
        if _DEBUG:
            print(f"[Drive] step={step} tools={len(tcs)} "
                  f"finish={getattr(resp.choices[0], 'finish_reason', '?')} "
                  f"content={(msg.content or '')[:40]!r}")
        if (not tcs and not (msg.content or "").strip()
                and getattr(resp.choices[0], "finish_reason", "") == "length"):
            # 思考／長內容把 token 額度吃光 → 放大額度重試一次
            retry_kwargs = dict(kwargs)
            retry_kwargs["max_tokens"] = max(max_tokens, 3000)
            print("[Drive] 回應被長度截斷 → 提高額度重試")
            resp = await chat_create(slot, **retry_kwargs)
            msg = resp.choices[0].message
            tcs = getattr(msg, "tool_calls", None) or []
        if not tcs:
            text = (msg.content or "").strip()
            dsml = extract_dsml_calls(text)
            if dsml:
                for name, args in dsml:
                    result = await call_tool(name, args)
                    used.append({"tool": name, "args": args, "ok": bool(result.get("ok"))})
                    if _is_write_call(name):
                        writes.append(result)
                    msgs.append({
                        "role": "user",
                        "content": f"工具結果（{name}）：\n{_tool_result_text(result)[:4000]}",
                    })
                if require_write and not _has_write(writes):
                    break  # 交給下面的強制寫入
                msgs.append({
                    "role": "user",
                    "content": "以上是工具結果。請用一句話總結你做了什麼，不要再呼叫工具。",
                })
                final = await chat_create(
                    slot, messages=msgs, temperature=temperature, max_tokens=max_tokens
                )
                return {
                    "text": strip_dsml(final.choices[0].message.content or "").strip(),
                    "tool_calls": used,
                    "writes": writes,
                    "steps": step + 1,
                }
            if require_write and not _has_write(writes):
                if text:
                    # 空字串的 assistant 訊息會被 API 拒絕（400）→ 只有有內容才加
                    msgs.append({"role": "assistant", "content": text})
                break  # 只回文字、沒有寫入 → 交給強制寫入
            return {"text": strip_dsml(text), "tool_calls": used,
                    "writes": writes, "steps": step + 1}

        msgs.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tcs
            ],
        })
        for tc in tcs:
            name = tc.function.name
            args = _parse_args(tc.function.arguments)
            result = await call_tool(name, args)
            used.append({"tool": name, "args": args, "ok": bool(result.get("ok"))})
            if _is_write_call(name):
                writes.append(result)
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _tool_result_text(result)[:4000],
            })

        # 已經讀很多次還沒寫 → 明確催它開始寫（require_write 時才啟用）
        if require_write and not _has_write(writes):
            read_calls = sum(
                1 for u in used
                if _TOOL_ALIASES.get(u["tool"], (u["tool"], None))[0]
                in ("drive_read", "drive_list", "drive_search")
            )
            nudged = any(
                "資料已經足夠" in (m.get("content") or "")
                for m in msgs if m.get("role") == "user"
            )
            if read_calls >= 3 and not nudged:
                msgs.append({
                    "role": "user",
                    "content": "資料已經足夠，請不要再讀取，直接呼叫 drive_write 把內容寫入。",
                })
                print("[Drive] 已讀 3 次，提醒模型開始寫入")

    # 迴圈用完還沒寫入 → 用「強制 drive_write」再逼一次（只讀不寫的常見失敗）
    if require_write and not _has_write(writes):
        msgs.append({
            "role": "user",
            "content": (
                "你還沒有寫入任何檔案。請立刻呼叫 drive_write 完成使用者的需求"
                "（新檔用 mode=create、改舊檔用 mode=overwrite），"
                "不要再讀檔案，也不要只回文字說明。"
                + (f"\n目標檔案：{fallback_path}" if fallback_path else "")
            ),
        })
        forced = None
        write_tools = [t for t in TOOL_DEFS if t["function"]["name"] == "drive_write"]
        try:
            # DeepSeek 會忽略「指定函式名」的 tool_choice，只認 required／auto／none。
            # 因此這一輪只提供 drive_write，等於強迫它非得寫檔不可。
            forced = await chat_create(
                slot, messages=msgs, tools=write_tools,
                temperature=temperature, max_tokens=max(max_tokens, 3000),
                tool_choice="required",
            )
        except Exception as e:
            print(f"[Drive] 指定工具強制寫入失敗：{e}")
            forced = None
        if forced is None:
            try:
                forced = await chat_create(
                    slot, messages=msgs, tools=write_tools,
                    temperature=temperature, max_tokens=max(max_tokens, 3000),
                )
            except Exception as e:
                print(f"[Drive] 強制寫入重試失敗：{e}")
                forced = None
        forced_text = ""
        if forced is not None:
            fmsg = forced.choices[0].message
            for tc in (getattr(fmsg, "tool_calls", None) or []):
                name = tc.function.name
                if _TOOL_ALIASES.get(name, (name, None))[0] != "drive_write":
                    continue
                args = _parse_args(tc.function.arguments)
                result = await call_tool(name, args)
                used.append({"tool": name, "args": args, "ok": bool(result.get("ok"))})
                if _is_write_call(name):
                    writes.append(result)
            forced_text = strip_dsml(fmsg.content or "").strip()
            if _has_write(writes):
                return {"text": forced_text, "tool_calls": used, "writes": writes,
                        "steps": steps + 1}

    # 最後保險：模型堅持不呼叫工具 → 由程式指定路徑，模型只負責生內容
    if require_write and not _has_write(writes) and fallback_path:
        try:
            gen = await chat_create(
                slot,
                messages=msgs + [{
                    "role": "user",
                    "content": (
                        f"請直接輸出要寫入「{fallback_path}」的完整檔案內容（純 Markdown／純文字），"
                        "不要任何解釋、不要程式碼圍籬、不要標題以外的廢話。"
                    ),
                }],
                temperature=temperature,
                max_tokens=max(max_tokens, 3600),
            )
            if getattr(gen.choices[0], "finish_reason", "") == "length":
                print("[Drive] 代寫內容被截斷 → 提高額度重寫")
                gen = await chat_create(
                    slot,
                    messages=msgs + [{
                        "role": "user",
                        "content": (
                            f"請直接輸出要寫入「{fallback_path}」的完整檔案內容（純 Markdown／純文字），"
                            "不要任何解釋、不要程式碼圍籬、不要標題以外的廢話。"
                        ),
                    }],
                    temperature=temperature,
                    max_tokens=6000,
                )
            content = _clean_generated(
                strip_dsml(gen.choices[0].message.content or ""), fallback_path
            )
            if content:
                res = await write_file(fallback_path, content, "create")
                if not res.get("ok") and "已存在" in (res.get("error") or ""):
                    res = await write_file(fallback_path, content, "overwrite")
                used.append({
                    "tool": "drive_write",
                    "args": {"path": fallback_path, "mode": res.get("action") or "create"},
                    "ok": bool(res.get("ok")),
                })
                writes.append(res)
                print(f"[Drive] 改用程式代寫：{fallback_path} → {res.get('action') or res.get('error')}")
        except Exception as e:
            print(f"[Drive] 程式代寫失敗：{type(e).__name__}: {e}")

    return {"text": "", "tool_calls": used, "writes": writes, "steps": steps}


# ── 寫入提示（由程式產生，不靠模型記得說）────────────────

def format_write_notice(writes: list[dict]) -> str:
    """把寫入結果變成一行低調的系統提示；沒有動作就回空字串。"""
    if not writes:
        return ""
    done = [w for w in writes if w.get("ok") and w.get("action") in ("created", "updated")]
    failed = [w for w in writes if not w.get("ok")]

    lines: list[str] = []
    if len(done) == 1:
        w = done[0]
        verb = "新增" if w["action"] == "created" else "更新"
        lines.append(f"📁 {verb}「{w.get('name', '')}」")
    elif done:
        lines.append(f"📁 已寫入 {len(done)} 個檔案")
        for w in done[:5]:
            verb = "新增" if w["action"] == "created" else "更新"
            lines.append(f"・{verb}「{w.get('name', '')}」")
    for w in failed[:3]:
        reason = (w.get("error") or w.get("review") or "未寫入").strip()
        lines.append(f"⚠️ 沒有寫入：{reason[:60]}")
    if not lines:
        return ""
    return "-# " + "\n-# ".join(lines)


# ── 唯讀研究員迴圈 ────────────────────────────────────────

READ_TOOL_DEFS = [
    t for t in TOOL_DEFS if t["function"]["name"] != "drive_write"
]

_RESEARCHER_SYSTEM = """你是資料研究員，只能「讀」不能寫。
目標：用最少的讀取次數，找到與使用者需求相關的內容，然後回報。

路徑規則：一律相對於資料夾根目錄，例如 `世界觀.md`、`設定/角色.md`；
不要加資料夾名稱（不要寫成「澪創作/世界觀.md」）。

讀取策略：
1. 不確定檔案在哪 → 先 drive_list 或 drive_search 看檔名。
2. 大文件不要一次讀完：先用 mode=section 讀指定標題，或 mode=grep 找關鍵字。
3. 只有整份都相關、且不算大時，才用 mode=full。
4. 拿到足夠資訊就停止，不要為了完整而把整份文件讀完。
5. 同一輪讀失敗就換方法，不要重複同一個錯誤路徑。

最後輸出：
- 只寫「與需求相關的重點」，用自己的話整理，不要照抄整段。
- 最多 1200 字；有引用到檔案就註明檔名與段落。
- 完全找不到相關內容時，直接說「資料夾裡沒有相關內容」。
"""


async def run_read_agent(
    question: str,
    extra_context: str = "",
    *,
    max_steps: int | None = None,
    slot: str = "default",
) -> dict:
    """唯讀研究員：讓模型自己決定要讀哪個檔案、讀到什麼範圍。"""
    from llm_models import chat_create

    cfg = get_config()
    steps = max(1, int(max_steps or cfg.get("read_steps", 2)))
    msgs = [
        {"role": "system", "content": _RESEARCHER_SYSTEM},
        {
            "role": "user",
            "content": (extra_context + "\n\n" if extra_context else "")
            + f"【需求】\n{question}",
        },
    ]
    used: list[dict] = []
    for step in range(steps):
        kwargs = {
            "messages": msgs,
            "tools": READ_TOOL_DEFS,
            "temperature": 0.2,
            "max_tokens": 900,
        }
        if step == steps - 1:
            kwargs["tool_choice"] = "none"  # 最後一輪強制給結論
        resp = await chat_create(slot, **kwargs)
        msg = resp.choices[0].message
        tcs = getattr(msg, "tool_calls", None) or []
        if not tcs:
            text = (msg.content or "").strip()
            dsml = extract_dsml_calls(text)
            if dsml:
                # 模型把工具呼叫寫成 DSML 文字：執行後再要一次結論
                for name, args in dsml:
                    result = await call_tool(name, args)
                    used.append({"tool": name, "args": args, "ok": bool(result.get("ok"))})
                    msgs.append({
                        "role": "user",
                        "content": f"工具結果（{name}）：\n{_tool_result_text(result)[:4000]}",
                    })
                msgs.append({
                    "role": "user",
                    "content": "以上是工具結果。請直接給出結論，不要再呼叫工具。",
                })
                final = await chat_create(
                    slot, messages=msgs, temperature=0.2, max_tokens=900
                )
                return {
                    "ok": True,
                    "summary": strip_dsml(
                        final.choices[0].message.content or ""
                    ).strip(),
                    "tool_calls": used,
                    "steps": step + 1,
                }
            return {
                "ok": True,
                "summary": strip_dsml(text),
                "tool_calls": used,
                "steps": step + 1,
            }
        msgs.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tcs
            ],
        })
        for tc in tcs:
            name = tc.function.name
            args = _parse_args(tc.function.arguments)
            result = await call_tool(name, args)
            used.append({"tool": name, "args": args, "ok": bool(result.get("ok"))})
            msgs.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": _tool_result_text(result)[:4000],
            })
    return {"ok": True, "summary": "", "tool_calls": used, "steps": steps}


# ── CLI ───────────────────────────────────────────────────

def _print(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Drive Agent 工具層 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("path", nargs="?", default="")
    p_read = sub.add_parser("read")
    p_read.add_argument("path")
    p_read.add_argument("--mode", default="auto",
                        choices=["auto", "section", "grep", "full"])
    p_read.add_argument("--section", default="")
    p_read.add_argument("--query", default="")
    p_read.add_argument("--offset", type=int, default=0)
    p_read.add_argument("--limit", type=int, default=None)
    p_write = sub.add_parser("write")
    p_write.add_argument("path")
    p_write.add_argument("content")
    p_write.add_argument("--mode", default="overwrite",
                         choices=["create", "overwrite", "append"])
    p_search = sub.add_parser("search")
    p_search.add_argument("query")
    args = parser.parse_args()

    try:
        if args.cmd == "list":
            _print(asyncio.run(list_files(args.path)))
        elif args.cmd == "read":
            _print(asyncio.run(read_file(
                args.path, mode=args.mode, section=args.section,
                query=args.query, offset=args.offset, limit=args.limit,
            )))
        elif args.cmd == "write":
            _print(asyncio.run(write_file(args.path, args.content, args.mode)))
        elif args.cmd == "search":
            _print(asyncio.run(search_files(args.query)))
    except DriveAgentError as e:
        print(f"錯誤：{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
