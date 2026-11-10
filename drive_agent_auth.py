"""
drive_agent_auth.py — 一次性建立 Agent 用的 Google Drive 授權。

和現有的 token.json（Sheets）／drive_token.json（備份用 drive.file）分開，
這個 token 的 scope 是完整的 Drive 讀寫，只給 drive_agent.py 使用。

用法：
    python drive_agent_auth.py            # 授權 + 重新整理 token + 列出資料夾
    python drive_agent_auth.py --folders  # 只列出資料夾（已授權過）

授權後會產生 drive_agent_token.json（已列入 .gitignore）。
"""
import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(SCRIPT_DIR, "credentials.json")
TOKEN_FILE = os.environ.get(
    "DRIVE_AGENT_TOKEN", os.path.join(SCRIPT_DIR, "drive_agent_token.json")
)
SCOPES = ["https://www.googleapis.com/auth/drive"]


def build_service():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as e:
        raise RuntimeError(
            "缺少 Google API 套件（google-auth-oauthlib / google-auth-httplib2 / "
            "google-api-python-client）"
        ) from e

    if not os.path.exists(CREDENTIALS_FILE):
        raise RuntimeError(f"找不到 credentials.json：{CREDENTIALS_FILE}")

    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.valid:
        print(f"[授權] 已有有效 token：{TOKEN_FILE}")
    elif creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        print("[授權] token 已過期，已自動 refresh")
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    else:
        print("[授權] 需要瀏覽器授權（scope: drive 完整讀寫）")
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        # credentials.json 登記的 redirect_uri 固定是 http://localhost:8080/
        creds = flow.run_local_server(port=8080)
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
        print(f"[授權] 已寫入 {TOKEN_FILE}")

    return build("drive", "v3", credentials=creds)


def list_folders(service):
    about = service.about().get(fields="user(displayName,emailAddress),storageQuota").execute()
    user = about.get("user", {})
    quota = about.get("storageQuota", {})
    print(f"\n[帳號] {user.get('displayName')} <{user.get('emailAddress')}>")
    limit = int(quota.get("limit") or 0)
    usage = int(quota.get("usage") or 0)
    if limit:
        print(f"[容量] 已用 {usage / 2**30:.2f} GB / {limit / 2**30:.2f} GB")

    print("\n[我的雲端硬碟資料夾]")
    page_token = None
    folders = []
    while True:
        res = service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and 'root' in parents and trashed=false",
            fields="nextPageToken, files(id,name)",
            pageSize=100,
            pageToken=page_token,
        ).execute()
        folders.extend(res.get("files", []))
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    for f in sorted(folders, key=lambda x: x["name"]):
        print(f"  {f['id']}  {f['name']}")
    if not folders:
        print("  （沒有資料夾）")
    print("\n把要給 Agent 用的資料夾 ID 填到 config.json 的 drive_agent.folder_id")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--folders", action="store_true", help="只列出資料夾")
    args = parser.parse_args()

    try:
        service = build_service()
        list_folders(service)
    except Exception as e:
        print(f"[授權] 失敗：{type(e).__name__}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
