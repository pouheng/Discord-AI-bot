"""
db_backup.py — 把 rp_memory.db（記憶資料庫）備份到 Google Drive

- SQLite backup API 做一致性快照（WAL 內容一併納入），不中斷運作中的 bot
- 上傳到 Drive 專屬資料夾「澪DiscordRP備份」，保留最近 KEEP_COUNT 份，舊的刪除
- 授權與 sheets_sync 分開：用獨立的 drive_token.json（僅 drive.file scope，
  只能碰這個 app 自己建立的檔案），不影響現有 Sheets 同步

用法：
    python db_backup.py                      # 備份一次 + 清理舊備份
    python db_backup.py --list               # 列出 Drive 上現有備份
    python db_backup.py --download-latest    # 下載最新備份到本機
    python db_backup.py --restore-latest     # 下載最新備份並覆蓋回 rp_memory.db（需 bot 停止）

首次執行會跳出瀏覽器授權（Google 帳號），之後用 drive_token.json 靜默執行。
若 Google Cloud Console 未啟用 Google Drive API，授權頁會回報
「access not configured」，需先到 Console 啟用後重跑。
"""

import argparse
import datetime
import json
import os
import sqlite3
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "rp_memory.db")
TOKEN_FILE = os.environ.get("DRIVE_BACKUP_TOKEN", os.path.join(SCRIPT_DIR, "drive_token.json"))
FOLDER_NAME = os.environ.get("DRIVE_BACKUP_FOLDER", "澪DiscordRP備份")
KEEP_COUNT = int(os.environ.get("DRIVE_BACKUP_KEEP", "10"))
SCOPE = "https://www.googleapis.com/auth/drive.file"


def _build_service():
    """Google Drive service（獨立 drive_token.json）。"""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "缺少 Google API 套件（google-auth-oauthlib / google-auth-httplib2 / "
            "google-api-python-client）。請先 pip install。"
        ) from e

    if not os.path.exists(os.path.join(SCRIPT_DIR, "credentials.json")):
        raise RuntimeError("找不到 credentials.json")

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, [SCOPE])
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                os.path.join(SCRIPT_DIR, "credentials.json"), [SCOPE]
            )
            creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def make_snapshot(tmpdir: str) -> str:
    """用 SQLite backup API 做一致性快照（包含 WAL 未 checkpoint 的交易）。"""
    if not os.path.exists(DB_FILE):
        raise RuntimeError(f"找不到資料庫：{DB_FILE}")
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    snap = os.path.join(tmpdir, f"rp_memory_{ts}.db")
    src = sqlite3.connect(DB_FILE)
    dst = sqlite3.connect(snap)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return snap


def _get_or_create_folder(service) -> str:
    q = f"name='{FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = service.files().list(q=q, fields="files(id,name)", pageSize=1).execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    body = {
        "name": FOLDER_NAME,
        "mimeType": "application/vnd.google-apps.folder",
    }
    f = service.files().create(body=body, fields="id").execute()
    return f["id"]


def upload(service, snap: str) -> str:
    from googleapiclient.http import MediaFileUpload

    folder_id = _get_or_create_folder(service)
    name = os.path.basename(snap)
    media = MediaFileUpload(snap, mimetype="application/octet-stream", resumable=True)
    body = {"name": name, "parents": [folder_id]}
    f = service.files().create(body=body, media_body=media, fields="id,name,size").execute()
    return f["name"]


def prune(service, keep: int) -> int:
    """刪除資料夾內較舊的 rp_memory_*.db，保留最近 keep 份。回傳刪除數。"""
    folder_id = _get_or_create_folder(service)
    q = (f"'{folder_id}' in parents and "
         f"name contains 'rp_memory_' and trashed=false")
    res = service.files().list(q=q, fields="files(id,name,createdTime)",
                               orderBy="createdTime desc", pageSize=100).execute()
    files = res.get("files", [])
    removed = 0
    for f in files[keep:]:
        service.files().delete(fileId=f["id"]).execute()
        removed += 1
    return removed


def list_backups(service) -> list:
    folder_id = _get_or_create_folder(service)
    q = (f"'{folder_id}' in parents and "
         f"name contains 'rp_memory_' and trashed=false")
    res = service.files().list(q=q, fields="files(id,name,size,createdTime)",
                               orderBy="createdTime desc", pageSize=100).execute()
    return res.get("files", [])


def download_latest(service, dst_dir: str) -> str:
    files = list_backups(service)
    if not files:
        raise RuntimeError("Drive 上沒有備份")
    latest = files[0]
    target = os.path.join(dst_dir, latest["name"])
    service.files().get_media(fileId=latest["id"]).execute().write_to_file(target)
    return target


def main():
    parser = argparse.ArgumentParser(description="rp_memory.db → Google Drive 備份")
    parser.add_argument("--list", action="store_true", help="列出 Drive 上現有備份")
    parser.add_argument("--download-latest", action="store_true",
                        help="下載最新備份到目前目錄（不覆蓋資料庫）")
    parser.add_argument("--restore-latest", action="store_true",
                        help="下載最新備份並覆蓋回 rp_memory.db（務先停止 bot）")
    args = parser.parse_args()

    service = _build_service()

    if args.list:
        for f in list_backups(service):
            print(f"{f['createdTime']}  {f['name']}  {f.get('size', '?')} bytes")
        return

    if args.download_latest:
        target = download_latest(service, SCRIPT_DIR)
        print(f"已下載：{target}")
        return

    if args.restore_latest:
        snap = download_latest(service, tempfile.gettempdir())
        if os.path.exists(DB_FILE):
            os.replace(DB_FILE, DB_FILE + ".pre_restore")
        os.replace(snap, DB_FILE)
        print(f"已還原 rp_memory.db（舊檔備份為 rp_memory.db.pre_restore）")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        snap = make_snapshot(tmpdir)
        name = upload(service, snap)
        removed = prune(service, KEEP_COUNT)
        print(f"✅ 已上傳：{name}")
        print(f"   保留最近 {KEEP_COUNT} 份，刪除舊備份 {removed} 份")


if __name__ == "__main__":
    sys.exit(main())