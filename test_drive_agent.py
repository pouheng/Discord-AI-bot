"""離線測試：Drive Agent 工具層（不連 Google，不呼叫 LLM）。

用假的 Drive service 驗證：
- 路徑解析與越界防護（..、資料夾外檔案 ID）
- 白名單副檔名、大小上限
- create / append / overwrite 與快照
- 工具結果轉文字（餵給模型用）
"""

import asyncio
import io
import os
import sys
from types import SimpleNamespace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import drive_agent as D

SUCCESS = 0
FAIL = 0


def check(name, condition, detail=""):
    global SUCCESS, FAIL
    if condition:
        SUCCESS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}" + (f"  ({detail})" if detail else ""))


ROOT = "ROOTFOLDER0000000000000"
SUB = "SUBFOLDER00000000000000"
F1 = "FILE1AAAAAAAAAAAAAAAAAAA"
F2 = "FILE2BBBBBBBBBBBBBBBBBBB"
F3 = "FILE3CCCCCCCCCCCCCCCCCCC"
F4 = "FILE4DDDDDDDDDDDDDDDDDDD"
OUTSIDE = "OUTSIDE00000000000000000"


class FakeMeta:
    def __init__(self, fid, name, mimeType, parents, size=0):
        self.data = {
            "id": fid, "name": name, "mimeType": mimeType,
            "parents": parents, "size": str(size),
            "modifiedTime": "2026-11-10T00:00:00.000Z",
        }

    def execute(self, **kwargs):
        return dict(self.data)


class FakeMedia:
    def __init__(self, data=None, mime=None, **kwargs):
        self.data = data

    def getbytes(self):
        return self.data.read() if hasattr(self.data, "read") else self.data


class FakeFiles:
    def __init__(self, store):
        self.store = store  # id -> {"meta": {...}, "data": bytes}

    def get(self, fileId, fields=None, **kwargs):
        if fileId not in self.store:
            raise RuntimeError(f"404 {fileId}")
        return FakeMeta(fileId, **{
            k: self.store[fileId]["meta"][k]
            for k in ("name", "mimeType", "parents", "size")
        })

    def get_media(self, fileId, **kwargs):
        return FakeRequest(lambda: self.store[fileId]["data"])

    def export(self, fileId, mimeType, **kwargs):
        data = self.store[fileId]["data"]
        return FakeRequest(lambda: data)

    def create(self, body=None, media_body=None, fields=None, **kwargs):
        def _go():
            fid = f"NEW{len(self.store):020d}"
            raw = _media_bytes(media_body)
            self.store[fid] = {
                "meta": {"name": body["name"],
                         "mimeType": body.get("mimeType", "text/markdown"),
                         "parents": body.get("parents", [ROOT]), "size": len(raw)},
                "data": raw,
            }
            return {"id": fid, "name": body["name"]}
        return FakeRequest(_go)

    def update(self, fileId=None, media_body=None, fields=None, **kwargs):
        def _go():
            raw = _media_bytes(media_body)
            self.store[fileId]["data"] = raw
            self.store[fileId]["meta"]["size"] = len(raw)
            return {"id": fileId}
        return FakeRequest(_go)


class FakeRequest:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, **kwargs):
        return self.fn()


def _media_bytes(media_body):
    if not media_body:
        return b""
    try:
        return media_body.getbytes(0, media_body.size())
    except TypeError:
        return media_body.getbytes()


class FakeService:
    def __init__(self, store):
        self._files = FakeFiles(store)

    def files(self):
        return self._files


def build_store():
    return {
        ROOT: {"meta": {"name": "澪創作", "mimeType": D.FOLDER_MIME, "parents": [], "size": 0}, "data": b""},
        SUB: {"meta": {"name": "設定", "mimeType": D.FOLDER_MIME, "parents": [ROOT], "size": 0}, "data": b""},
        F1: {"meta": {"name": "筆記.md", "mimeType": "text/markdown", "parents": [ROOT], "size": 13},
             "data": "舊的內容\n".encode("utf-8")},
        F2: {"meta": {"name": "資料.csv", "mimeType": "text/csv", "parents": [SUB], "size": 5},
             "data": b"a,b\n1,2\n"},
        F3: {"meta": {"name": "圖片.png", "mimeType": "image/png", "parents": [ROOT], "size": 4},
             "data": b"\x89PNG"},
        F4: {"meta": {"name": "大文件.md", "mimeType": "text/markdown", "parents": [ROOT], "size": 200},
             "data": (
                 "# 世界觀\n魔法來自靈魂，每人只能覺醒一種屬性。\n"
                 + "世界各地的魔力濃度不同，越靠近核心越濃。\n" * 3
                 + "## 國家\n三個國家互相對立，分別信奉不同的元素。\n"
                 + "北方以冰為主，南方以火為主，東方以風為主。\n"
                 + "## 歷史\n很久以前發生過大戰，最後由中立國調停。\n"
                 + "戰爭留下了大量遺跡與未解的魔法現象。\n"
                 + "# 角色\n澪是見習魔女，擅長觀測魔力流向。\n"
                 + "她的師傅在戰爭中失蹤，至今沒有下落。\n"
             ).encode("utf-8")},
        OUTSIDE: {"meta": {"name": "secret.md", "mimeType": "text/markdown",
                           "parents": ["OTHER000000000000000000"], "size": 6},
                  "data": b"secret"},
    }


def install(store=None):
    store = store or build_store()
    service = FakeService(store)

    def fake_children(svc, parent_id):
        out = []
        for fid, item in store.items():
            if parent_id in item["meta"]["parents"]:
                mime = item["meta"]["mimeType"]
                out.append({
                    "id": fid, "name": item["meta"]["name"], "mime": mime,
                    "size": item["meta"]["size"], "modified": "2026-11-10T00:00:00Z",
                    "parent": parent_id, "is_folder": mime == D.FOLDER_MIME,
                })
        return out

    old_children, old_service, old_review = (
        D._list_children, D.get_service, D.review_write,
    )
    D._list_children = fake_children
    D.get_service = lambda: service

    async def approve(*args, **kwargs):
        return True, "測試放行"

    D.review_write = approve
    D._reset_service()
    return store, (old_children, old_service, old_review)


def restore(saved):
    D._list_children, D.get_service, D.review_write = saved


def cfg_patch(**kwargs):
    cfg = D.get_config()
    cfg.update(kwargs)
    D._cfg_cache["data"] = cfg
    return cfg


saved_cfg = D.get_config()
store, saved = install()
cfg_patch(folder_id=ROOT, folder_name="澪創作", writable_extensions=[".md", ".txt", ".json", ".csv"])

print("[1] 路徑與越界防護")
entry, parent, name = D._resolve(D.get_service(), "")
check("根目錄解析", entry and entry["id"] == ROOT, str(entry))
entry, parent, name = D._resolve(D.get_service(), "筆記.md")
check("根目錄檔案", entry and entry["id"] == F1, str(entry))
entry, parent, name = D._resolve(D.get_service(), "設定/資料.csv")
check("子資料夾檔案", entry and entry["id"] == F2, str(entry))
entry, parent, name = D._resolve(D.get_service(), "不存在的.md")
check("找不到時回 None", entry is None and name == "不存在的.md")
for bad in ("../secret.md", "設定/../../x.md"):
    try:
        D._resolve(D.get_service(), bad)
        check(f"拒絕 {bad}", False)
    except D.DriveAgentError:
        check(f"拒絕 {bad}", True)
try:
    D._resolve(D.get_service(), OUTSIDE)
    check("拒絕資料夾外的檔案 ID", False)
except (D.DriveAgentError, RuntimeError):
    check("拒絕資料夾外的檔案 ID", True)

print("[2] 讀取")
r = asyncio.run(D.read_file("筆記.md"))
check("讀文字檔", r["ok"] and "舊的內容" in r["text"], str(r)[:120])
r = asyncio.run(D.list_files(""))
check("列根目錄", r["ok"] and any(e["name"] == "設定" for e in r["entries"]))
r = asyncio.run(D.list_files("設定"))
check("列子資料夾", r["ok"] and r["entries"][0]["name"] == "資料.csv", str(r["entries"]))
r = asyncio.run(D.search_files("筆記"))
check("搜尋", r["ok"] and r["count"] == 1, str(r))

print("[3] 寫入")
r = asyncio.run(D.write_file("新檔.md", "第一行", "create"))
check("create 新檔", r["ok"] and r["action"] == "created", str(r))
new_id = r.get("id")
check("新檔內容正確", store[new_id]["data"] == "第一行".encode("utf-8"))
r = asyncio.run(D.write_file("筆記.md", "追加", "append"))
check("append", r["ok"] and r["action"] == "updated", str(r))
check("append 內容", store[F1]["data"].decode("utf-8").endswith("追加"))
r = asyncio.run(D.write_file("筆記.md", "整份換掉", "overwrite"))
check("overwrite", r["ok"] and store[F1]["data"] == "整份換掉".encode("utf-8"))
check("覆蓋有快照紀錄", "snapshot" in r, str(r))

print("[4] 白名單與大小")
try:
    asyncio.run(D.write_file("evil.exe", "x", "create"))
    check("拒絕非白名單副檔名", False)
except D.DriveAgentError as e:
    check("拒絕非白名單副檔名", "不可寫入" in str(e), str(e))
cfg_patch(max_write_bytes=10)
try:
    asyncio.run(D.write_file("筆記.md", "x" * 50, "overwrite"))
    check("拒絕超過大小上限", False)
except D.DriveAgentError as e:
    check("拒絕超過大小上限", "太大" in str(e), str(e))
cfg_patch(max_write_bytes=200000)
try:
    asyncio.run(D.write_file("筆記.md", "", "overwrite"))
    check("拒絕空內容", False)
except D.DriveAgentError as e:
    check("拒絕空內容", "空的" in str(e), str(e))

print("[5] 工具結果轉文字")
folder_result = {"ok": True, "kind": "folder", "name": "澪創作",
                 "entries": [{"name": "a.md", "kind": "file", "size": 3},
                             {"name": "子", "kind": "folder", "size": 0}]}
text = D._tool_result_text(folder_result)
check("資料夾摘要含檔名", "a.md" in text and "資料夾" in text, text)
text = D._tool_result_text({"ok": True, "kind": "text", "name": "a.md",
                            "text": "內容", "truncated": True})
check("文字摘要有截斷提示", "內容" in text and "截斷" in text, text)
text = D._tool_result_text({"ok": False, "error": "找不到"})
check("錯誤轉文字", "找不到" in text, text)

print("[6] 工具分派")
r = asyncio.run(D.call_tool("drive_list", {"path": ""}))
check("drive_list", r["ok"] and r["kind"] == "folder")
r = asyncio.run(D.call_tool("drive_read", {"path": "筆記.md"}))
check("drive_read", r["ok"] and r["text"] == "整份換掉", str(r)[:100])
r = asyncio.run(D.call_tool("drive_nope", {}))
check("未知工具不爆", r["ok"] is False and "未知" in r["error"], str(r))
r = asyncio.run(D.call_tool("drive_read", {"path": "../x"}))
check("工具錯誤被包成 ok=false", r["ok"] is False and "不合法" in r["error"], str(r))

print("[7] 設定")
check("預設有 folder_id 概念", "folder_id" in D.DEFAULTS)
check("預設不可寫 .exe", ".exe" not in D.DEFAULTS["writable_extensions"])
check("預設審核開啟", D.DEFAULTS["review_writes"] is True)
check("工具定義完整",
      {t["function"]["name"] for t in D.TOOL_DEFS}
      == {"drive_list", "drive_read", "drive_write", "drive_search", "drive_grep"},
      str([t["function"]["name"] for t in D.TOOL_DEFS]))

print("[8] 範圍讀取（大文件只讀需要的部分）")
r = asyncio.run(D.read_file("大文件.md"))
check("預設只讀開頭一段", r["text"].startswith("# 世界觀"), str(r)[:120])
check("回報全長", r.get("total_chars", 0) > 30, str(r.get("total_chars")))
check("列出段落目錄", "國家" in r.get("sections", []), str(r.get("sections")))

r = asyncio.run(D.read_file("大文件.md", mode="section", section="國家"))
check("section 只取該段", "三個國家" in r["text"] and "很久以前" not in r["text"], r["text"])

r = asyncio.run(D.read_file("大文件.md", mode="grep", query="很久"))
check("grep 找到關鍵字", "很久以前" in r["text"], r["text"])

r = asyncio.run(D.read_file("大文件.md", limit=200))
check("offset 分頁有 next_offset", r.get("next_offset") == 200, str(r.get("next_offset")))
first = r["text"]
r = asyncio.run(D.read_file("大文件.md", offset=200, limit=200))
check("第二頁接續不同內容", r["text"] and r["text"] != first, r["text"])

cfg_patch(max_read_bytes=50)
try:
    asyncio.run(D.read_file("大文件.md", mode="full"))
    check("全文太大時拒絕 full", False)
except D.DriveAgentError as e:
    check("全文太大時拒絕 full", "太大" in str(e), str(e))
cfg_patch(max_read_bytes=200000)
try:
    asyncio.run(D.read_file("大文件.md", mode="section", section="不存在的段落"))
    check("找不到段落時報錯", False)
except D.DriveAgentError as e:
    check("找不到段落時報錯", "找不到這個段落" in str(e), str(e))

text = D._tool_result_text({
    "ok": True, "kind": "text", "name": "a.md", "text": "內容",
    "total_chars": 9999, "sections": ["世界觀", "國家"], "next_offset": 4000,
    "truncated": True,
})
check("工具摘要含全長與段落", "9999" in text and "國家" in text, text)
check("工具摘要含續讀提示", "offset=4000" in text, text)

print("[10] DSML 與工具別名")
sample = (
    "<｜｜DSML｜｜ calls>\n"
    '<｜｜DSML｜｜ invoke name="drive_read">\n'
    '<｜｜DSML｜｜ parameter name="file" string="true">世界觀.md</｜｜DSML｜｜ parameter>\n'
    '<｜｜DSML｜｜ parameter name="mode" string="true">full</｜｜DSML｜｜ parameter>\n'
    "</｜｜DSML｜｜ invoke>\n"
    "</｜｜DSML｜｜ calls>"
)
calls = D.extract_dsml_calls(sample)
check("解析 DSML 工具呼叫",
      calls == [("drive_read", {"file": "世界觀.md", "mode": "full"})], str(calls))
check("strip 連參數內容一起移除", D.strip_dsml(sample) == "", repr(D.strip_dsml(sample)))
check("一般文字不會誤判", D.extract_dsml_calls("正常回覆") == [])

r = asyncio.run(D.call_tool("drive_create", {"path": "別名.md", "content": "別名測試"}))
check("drive_create 別名 → create",
      r.get("ok") and r.get("action") == "created", str(r))
r = asyncio.run(D.call_tool("drive_save", {"path": "別名.md", "content": "改寫"}))
check("drive_save 別名 → overwrite",
      r.get("ok") and r.get("action") == "updated", str(r))
r = asyncio.run(D.call_tool("drive_read", {"file": "別名.md"}))
check("file 參數當成 path", r.get("ok") and r.get("text") == "改寫", str(r)[:80])

entry, _p, _n = D._resolve(D.get_service(), "澪創作/筆記.md")
check("路徑自帶資料夾名稱也能解析", entry is not None and entry["id"] == F1, str(entry))

print("[11] 子資料夾自動建立（草稿/技師.md）")
r = asyncio.run(D.write_file("草稿/技師.md", "# 技師草稿\n內容", "create"))
check("自動建立資料夾並寫入", r.get("ok") and r.get("action") == "created", str(r))
check("資料夾真的被建立",
      any(v["meta"]["name"] == "草稿" and v["meta"]["mimeType"] == D.FOLDER_MIME
          for v in store.values()),
      str([v["meta"]["name"] for v in store.values()]))
r = asyncio.run(D.read_file("草稿/技師.md"))
check("讀得回新檔", r.get("ok") and "技師草稿" in r.get("text", ""), str(r)[:100])

print("[12] 內容搜尋：只給關鍵字也能找到檔案")
res = asyncio.run(D.search_content("國家"))
check("搜到含關鍵字的檔案", res.get("ok") and res.get("count", 0) >= 1, str(res)[:200])
check("回傳檔案路徑與片段",
      bool(res.get("matches")) and "path" in res["matches"][0]
      and "snippet" in res["matches"][0], str(res)[:200])
res = asyncio.run(D.search_content("不存在的關鍵字xyz"))
check("找不到時回空陣列", res.get("ok") and res.get("count") == 0, str(res)[:120])

print("[13] drive_grep 工具與別名")
check("TOOL_DEFS 有 drive_grep",
      any(t["function"]["name"] == "drive_grep" for t in D.TOOL_DEFS))
check("研究員工具也含 drive_grep",
      any(t["function"]["name"] == "drive_grep" for t in D.READ_TOOL_DEFS))
r = asyncio.run(D.call_tool("drive_search_content", {"query": "國家"}))
check("別名 drive_search_content → drive_grep",
      r.get("ok") and r.get("count", 0) >= 1, str(r)[:160])

print("[14] resolve_hints：空路徑 + 關鍵字 → 補成真實檔案")
hints = asyncio.run(D.resolve_hints([{"path": "", "mode": "grep", "query": "國家"}]))
check("補出檔案路徑", len(hints) == 1 and hints[0].get("path"), str(hints))
check("保留模式為 auto", hints and hints[0].get("mode") == "auto", str(hints))

restore(saved)
D._cfg_cache["data"] = saved_cfg

print("[9] 寫入提示（程式產生）")
notice = D.format_write_notice([
    {"ok": True, "action": "created", "name": "世界觀.md", "bytes": 167},
])
check("單筆新增提示", notice.startswith("-#") and "世界觀.md" in notice and "新增" in notice, notice)

notice = D.format_write_notice([
    {"ok": True, "action": "created", "name": "a.md"},
    {"ok": True, "action": "updated", "name": "b.md"},
])
check("多筆提示", "2 個檔案" in notice and "a.md" in notice and "b.md" in notice, notice)

notice = D.format_write_notice([
    {"ok": False, "error": "審核未通過：內容可疑"},
])
check("失敗提示", "⚠️" in notice and "審核未通過" in notice, notice)

check("沒有寫入就沒有提示", D.format_write_notice([]) == "")
check("工具回傳包含 writes 欄位", "writes" in D.run_agent_loop.__doc__)

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
