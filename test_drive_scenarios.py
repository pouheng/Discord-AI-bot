"""使用場景測試：用假 Drive + 假模型，跑 Drive Agent 的完整流程。

覆蓋的是「使用者實際會遇到的狀況」，不是單一函式：
1. 問檔案裡的內容 → 只讀需要的段落（不整份讀）
2. 模型把工具呼叫寫成 DSML 文字 → 仍能執行並收尾
3. 模型自己發明工具名（drive_create）→ 別名對應成功
4. 審核不通過 → 不寫入，回報原因
5. 模型判斷不需要檔案 → 一步都不讀
6. 模型給錯路徑（越界）→ 工具回錯誤，模型仍能收尾
7. 寫入成功／失敗的提示文字
"""

import asyncio
import io
import os
import sys
from types import SimpleNamespace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import drive_agent as D
import llm_models

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


# ── 假 Drive ──────────────────────────────────────────────

ROOT = "ROOTFOLDER0000000000000"
BIG = "BIGFILE00000000000000000"
NOTE = "NOTEFILE0000000000000000"

BIG_TEXT = (
    "# 世界觀\n魔法來自靈魂，每人一種屬性。\n"
    "## 國家\n三個國家互相對立，分別信奉不同元素。\n"
    "## 歷史\n很久以前發生過大戰，最後由中立國調停。\n"
    "# 角色\n澪是見習魔女。\n"
)


def make_store():
    return {
        ROOT: {"meta": {"name": "澪創作", "mimeType": D.FOLDER_MIME,
                        "parents": [], "size": 0}, "data": b""},
        BIG: {"meta": {"name": "世界觀.md", "mimeType": "text/markdown",
                       "parents": [ROOT], "size": len(BIG_TEXT)},
              "data": BIG_TEXT.encode("utf-8")},
        NOTE: {"meta": {"name": "筆記.md", "mimeType": "text/markdown",
                        "parents": [ROOT], "size": 3},
               "data": "舊的".encode("utf-8")},
    }


class _Req:
    def __init__(self, fn):
        self.fn = fn

    def execute(self, **kw):
        return self.fn()


def _media_bytes(media):
    if not media:
        return b""
    try:
        return media.getbytes(0, media.size())
    except TypeError:
        return media.getbytes()


class FakeDrive:
    def __init__(self, store):
        self.store = store
        self.reads = []

    # files().xxx()
    def files(self):
        return self

    def get(self, fileId, fields=None, **kw):
        item = self.store[fileId]
        return _Req(lambda: {
            "id": fileId, "name": item["meta"]["name"],
            "mimeType": item["meta"]["mimeType"],
            "parents": item["meta"]["parents"], "size": str(item["meta"]["size"]),
            "modifiedTime": "2026-11-10T00:00:00Z",
        })

    def get_media(self, fileId, **kw):
        self.reads.append(fileId)
        return _Req(lambda: self.store[fileId]["data"])

    def export(self, fileId, mimeType, **kw):
        self.reads.append(fileId)
        return _Req(lambda: self.store[fileId]["data"])

    def create(self, body=None, media_body=None, fields=None, **kw):
        def go():
            fid = f"NEW{len(self.store):020d}"
            raw = _media_bytes(media_body)
            self.store[fid] = {
                "meta": {"name": body["name"], "mimeType": "text/markdown",
                         "parents": body.get("parents", [ROOT]), "size": len(raw)},
                "data": raw,
            }
            return {"id": fid, "name": body["name"]}
        return _Req(go)

    def update(self, fileId=None, media_body=None, fields=None, **kw):
        def go():
            raw = _media_bytes(media_body)
            self.store[fileId]["data"] = raw
            self.store[fileId]["meta"]["size"] = len(raw)
            return {"id": fileId}
        return _Req(go)


def install(store, folder_name="澪創作"):
    drive = FakeDrive(store)

    def children(svc, parent_id):
        out = []
        for fid, item in store.items():
            if parent_id in item["meta"]["parents"]:
                mime = item["meta"]["mimeType"]
                out.append({
                    "id": fid, "name": item["meta"]["name"], "mime": mime,
                    "size": item["meta"]["size"], "modified": "",
                    "parent": parent_id, "is_folder": mime == D.FOLDER_MIME,
                })
        return out

    saved = (D._list_children, D.get_service, D.review_write,
             D._cfg_cache["data"], D._cfg_cache["mtime"])
    D._list_children = children
    D.get_service = lambda: drive
    # 先讓 get_config 讀一次真實設定（把 mtime 記住），再覆蓋成測試資料；
    # 直接改 mtime 會讓下一次 get_config 又把測試設定蓋回去。
    D.get_config()
    D._cfg_cache["data"] = {
        **D.DEFAULTS, "folder_id": ROOT, "folder_name": folder_name,
    }
    return drive, saved


def restore(saved):
    (D._list_children, D.get_service, D.review_write,
     D._cfg_cache["data"], D._cfg_cache["mtime"]) = saved


# ── 假模型 ────────────────────────────────────────────────

def tool_call(name, args, cid="call_1"):
    return SimpleNamespace(
        id=cid, type="function",
        function=SimpleNamespace(name=name, arguments=args if isinstance(args, str) else __import__("json").dumps(args, ensure_ascii=False)),
    )


def model_reply(content="", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg, finish_reason="stop")]
    )


def script_model(responses):
    """把 llm_models.chat_create 換成照腳本回應的假函式，並記錄每次呼叫。"""
    seen = []
    queue = list(responses)

    async def fake(slot, **kwargs):
        seen.append({
            "messages": kwargs.get("messages") or [],
            "tool_choice": kwargs.get("tool_choice"),
            "tools": kwargs.get("tools"),
        })
        if queue:
            return queue.pop(0)
        return model_reply("（腳本結束）")

    llm_models.chat_create = fake
    return seen


def tool_results(entry):
    msgs = entry["messages"] if isinstance(entry, dict) else entry
    return [m["content"] for m in msgs if m.get("role") == "tool"]


# ── 場景 1：只讀需要的段落 ────────────────────────────────

store = make_store()
drive, saved = install(store)
print("[1] 使用者問國家設定 → 只讀「國家」段落")
seen = script_model([
    model_reply(tool_calls=[tool_call("drive_read", {
        "path": "世界觀.md", "mode": "section", "section": "國家",
    })]),
    model_reply("三個國家互相對立，各信奉不同元素。"),
])
result = asyncio.run(D.run_read_agent("世界觀的國家有哪些？"))
results = tool_results(seen[-1])
blob = "\n".join(results)
check("研究員有結論", "三個國家" in result["summary"], result["summary"])
check("工具結果只含指定段落", "三個國家" in blob and "很久以前" not in blob, blob[:200])
check("只花 2 步", result["steps"] == 2, str(result["steps"]))
check("沒有呼叫寫入工具",
      all(c["tool"] != "drive_write" for c in result["tool_calls"]),
      str(result["tool_calls"]))

# ── 場景 2：模型把工具呼叫寫成 DSML 文字 ──────────────────

print("[2] 模型輸出 DSML 文字而不是 tool_calls → 仍能執行")
dsml_text = (
    "<｜｜DSML｜｜ calls>\n"
    '<｜｜DSML｜｜ invoke name="drive_read">\n'
    '<｜｜DSML｜｜ parameter name="file" string="true">世界觀.md</｜｜DSML｜｜ parameter>\n'
    '<｜｜DSML｜｜ parameter name="mode" string="true">full</｜｜DSML｜｜ parameter>\n'
    "</｜｜DSML｜｜ invoke>\n"
    "</｜｜DSML｜｜ calls>"
)
seen = script_model([model_reply(dsml_text), model_reply("讀到世界觀的開頭。")])
result = asyncio.run(D.run_read_agent("世界觀在講什麼？"))
check("DSML 被解析成工具呼叫",
      any(c["tool"] == "drive_read" and c["ok"] for c in result["tool_calls"]),
      str(result["tool_calls"]))
check("DSML 不會漏進結論", "DSML" not in result["summary"], result["summary"][:100])
check("結論來自第二次呼叫", "世界觀" in result["summary"], result["summary"])

# ── 場景 3：模型自己發明工具名 ────────────────────────────

print("[3] 模型呼叫 drive_create（不存在的工具）→ 別名建立檔案")


async def approve_all(*a, **kw):
    return True, "測試放行"


D.review_write = approve_all
seen = script_model([
    model_reply(tool_calls=[tool_call("drive_create", {
        "path": "新設定.md", "content": "# 新設定\n內容",
    })]),
    model_reply("已建立新設定.md"),
])
result = asyncio.run(D.run_agent_loop([
    {"role": "system", "content": "寫檔案"},
    {"role": "user", "content": "建立 新設定.md"},
]))
writes = result["writes"]
check("別名建立了檔案", len(writes) == 1 and writes[0].get("action") == "created",
      str(writes))
check("檔案真的寫進 Drive", any(
    v["meta"]["name"] == "新設定.md" for v in store.values()
), str([v["meta"]["name"] for v in store.values()]))
check("回報文字正常", "新設定" in result["text"], result["text"])

# ── 場景 4：審核不通過 → 不寫入 ──────────────────────────

print("[4] 審核模型否決 → 檔案保持原樣")
before = store[NOTE]["data"]


async def reject(*a, **kw):
    return False, "內容包含可疑字串"


D.review_write = reject
result = asyncio.run(D.write_file("筆記.md", "被拒絕的內容", "overwrite"))
check("回報未寫入", result.get("ok") is False and "審核" in result.get("error", ""),
      str(result))
check("原檔內容沒變", store[NOTE]["data"] == before, str(store[NOTE]["data"]))
notice = D.format_write_notice([result])
check("失敗提示有 ⚠️", "⚠️" in notice and "沒有寫入" in notice, notice)

# ── 場景 5：不需要檔案 → 一步都不讀 ──────────────────────

print("[5] 模型判斷不需要檔案 → 不呼叫任何工具")
before_reads = len(drive.reads)
seen = script_model([model_reply("這個問題不需要看檔案。")])
result = asyncio.run(D.run_read_agent("你今天心情如何？"))
check("直接給結論", result["steps"] == 1 and "不需要" in result["summary"],
      result["summary"])
check("完全沒有讀取檔案", len(drive.reads) == before_reads,
      f"{before_reads} -> {len(drive.reads)}")

# ── 場景 6：模型給越界路徑 ────────────────────────────────

print("[6] 模型要求讀 ../secret → 工具回錯誤，模型仍能收尾")
seen = script_model([
    model_reply(tool_calls=[tool_call("drive_read", {"path": "../secret.md"})]),
    model_reply("找不到那份資料。"),
])
result = asyncio.run(D.run_read_agent("讀一下外面的檔案"))
blob = "\n".join(tool_results(seen[-1]))
check("工具回報路徑不合法", "不合法" in blob or "找不到" in blob, blob[:150])
check("錯誤不會讓流程爆掉", result["summary"] and result["steps"] == 2,
      str(result))

# ── 場景 7：寫入成功的提示 ────────────────────────────────

print("[7] 寫入成功後由程式產生提示")
D.review_write = saved[2]


async def approve(*a, **kw):
    return True, "測試放行"


D.review_write = approve
result = asyncio.run(D.write_file("完成.md", "# 完成\n內容", "create"))
check("實際建立成功", result.get("ok") and result.get("action") == "created",
      str(result))
notice = D.format_write_notice([result])
check("成功提示含檔名與 📁", "📁" in notice and "完成.md" in notice, notice)
check("提示使用 -# 小字", notice.startswith("-#"), notice)

# ── 場景 8：審核模型第一次回空 → 重試成功 ─────────────────

print("[8] 審核模型第一次回空（思考吃光 token）→ 重試後放行")
D.review_write = saved[2]  # 用真正的 review_write
_orig_chat = llm_models.chat_create
_script = [
    model_reply(""),  # 第一次空內容
    model_reply('{"approve": true, "reason": "正常內容"}'),
]
_seen = []


async def _fake_review_chat(slot, **kwargs):
    _seen.append(slot)
    return _script.pop(0)


llm_models.chat_create = _fake_review_chat
approved, reason = asyncio.run(D.review_write("完成.md", "", "# 內容", "create"))
check("重試後通過", approved and "正常" in reason, f"{approved} / {reason}")
check("確實呼叫了兩次", len(_seen) == 2, str(_seen))

# ── 場景 9：審核模型一直回空 → 預設擋下（fail closed）──────

print("[9] 審核模型兩次都回空 → 預設不寫入")


async def _always_empty(slot, **kwargs):
    return model_reply("")


llm_models.chat_create = _always_empty
approved, reason = asyncio.run(D.review_write("完成.md", "", "# 內容", "create"))
check("預設擋下", approved is False, str(approved))
check("原因說明審核失敗", "審核失敗" in reason, reason)
llm_models.chat_create = _orig_chat

# ── 場景 10：大文件分兩次讀（offset 續讀）─────────────────

print("[10] 大文件先讀一段，再照 next_offset 續讀")
long_text = "# 長篇\n" + "這是一段很長的設定內容。\n" * 30
LONG = "LONGFILE0000000000000000"
store[LONG] = {
    "meta": {"name": "長篇.md", "mimeType": "text/markdown",
             "parents": [ROOT], "size": len(long_text)},
    "data": long_text.encode("utf-8"),
}
seen = script_model([
    model_reply(tool_calls=[tool_call("drive_read", {
        "path": "長篇.md", "limit": 100,
    }, cid="c1")]),
    model_reply(tool_calls=[tool_call("drive_read", {
        "path": "長篇.md", "offset": 100, "limit": 100,
    }, cid="c2")]),
    model_reply("這份長篇主要在講設定內容。"),
])
result = asyncio.run(D.run_read_agent("長篇.md 大概在講什麼？"))
blob = "\n".join(tool_results(seen[-1]))
check("第一段有告訴模型還有後續", "offset=" in blob, blob[:200])
check("兩段內容不同", blob.count("這是一段") > 1, "兩段都有內容")
check("最後有結論", "設定內容" in result["summary"], result["summary"])

# ── 場景 11：只讀不寫 → 強制再寫一次 ──────────────────────

print("[11] 模型只讀不寫 → require_write 會強制再寫一次")
D.review_write = approve_all
seen = script_model([
    model_reply(tool_calls=[tool_call("drive_read", {"path": "世界觀.md"})]),
    model_reply("我看完了，內容大概是三國對立。"),  # 只回文字，沒有寫入
    model_reply(tool_calls=[tool_call("drive_write", {
        "path": "強制寫入.md", "content": "# 強制寫入\n內容", "mode": "create",
    })]),
])
result = asyncio.run(D.run_agent_loop(
    [
        {"role": "system", "content": "寫檔案"},
        {"role": "user", "content": "整理成 強制寫入.md"},
    ],
    require_write=True,
))
check("最後真的有寫入", len(result["writes"]) == 1
      and result["writes"][0].get("action") == "created", str(result["writes"]))
check("強制那輪只給 drive_write 並用 required",
      seen[-1]["tool_choice"] == "required"
      and [t["function"]["name"] for t in (seen[-1]["tools"] or [])] == ["drive_write"],
      f"{seen[-1]['tool_choice']} / {[t['function']['name'] for t in (seen[-1]['tools'] or [])]}")
check("檔案真的存在",
      any(v["meta"]["name"] == "強制寫入.md" for v in store.values()),
      str([v["meta"]["name"] for v in store.values()]))

# ── 場景 12：Agent 寫入被審核否決 → 提示要顯示失敗 ─────────

print("[12] Agent 寫入被審核否決 → 回報失敗而不是靜默")


async def reject_this(*a, **kw):
    return False, "內容含可疑字串"


D.review_write = reject_this
seen = script_model([
    model_reply(tool_calls=[tool_call("drive_write", {
        "path": "被擋.md", "content": "可疑內容", "mode": "create",
    })]),
    model_reply("我想寫入但被擋下了。"),
])
result = asyncio.run(D.run_agent_loop(
    [
        {"role": "system", "content": "寫檔案"},
        {"role": "user", "content": "寫入 被擋.md"},
    ],
    require_write=False,
))
check("有記錄失敗的寫入", len(result["writes"]) == 1
      and result["writes"][0].get("ok") is False, str(result["writes"]))
notice = D.format_write_notice(result["writes"])
check("提示顯示 ⚠️ 與原因", "⚠️" in notice and "可疑" in notice, notice)
check("檔案沒有被建立",
      not any(v["meta"]["name"] == "被擋.md" for v in store.values()),
      str([v["meta"]["name"] for v in store.values()]))

# ── 場景 13：讀了三次就被催「開始寫」────────────────────

print("[13] 連續讀 3 次 → 系統提醒模型開始寫入")
D.review_write = approve_all
seen = script_model([
    model_reply(tool_calls=[tool_call("drive_read", {"path": "世界觀.md"}, cid="c1")]),
    model_reply(tool_calls=[tool_call("drive_read", {"path": "國家設定.md"}, cid="c2")]),
    model_reply(tool_calls=[tool_call("drive_read", {"path": "國家設定_整理.md"}, cid="c3")]),
    model_reply("我讀完了。"),
    model_reply(tool_calls=[tool_call("drive_write", {
        "path": "草稿.md", "content": "# 草稿", "mode": "create",
    })]),
])
result = asyncio.run(D.run_agent_loop(
    [
        {"role": "system", "content": "寫檔案"},
        {"role": "user", "content": "整理成 草稿.md"},
    ],
    require_write=True,
))
blob = str(seen[-1]["messages"])
check("有插入「資料已經足夠」提醒", "資料已經足夠" in blob, blob[-400:])
check("最終仍完成寫入", len(result["writes"]) == 1
      and result["writes"][0].get("ok"), str(result["writes"]))

# ── 場景 14：模型死都不寫 → 程式代寫 fallback_path ─────────

print("[14] 模型堅持不呼叫工具 → 程式指定路徑代寫")
D.review_write = approve_all
seen = script_model([
    model_reply("好的，我先看看。"),
    model_reply("我覺得直接說明就好。"),
    model_reply("# 自動草稿\n蒸氣龐克角色：黃銅護目鏡、皮質大衣。"),
])
result = asyncio.run(D.run_agent_loop(
    [
        {"role": "system", "content": "寫檔案"},
        {"role": "user", "content": "寫一份角色草稿到 自動草稿.md"},
    ],
    require_write=True,
    fallback_path="自動草稿.md",
))
check("程式代寫成功", len(result["writes"]) == 1
      and result["writes"][0].get("ok"), str(result["writes"]))
check("檔案真的存在",
      any(v["meta"]["name"] == "自動草稿.md" for v in store.values()),
      str([v["meta"]["name"] for v in store.values()]))
back = asyncio.run(D.read_file("自動草稿.md"))
check("內容是模型產生的", "黃銅護目鏡" in back.get("text", ""), back.get("text", "")[:80])

# ── 場景 15：代寫內容夾帶「路徑／create」與程式碼圍籬 → 要清乾淨 ──

print("[15] 代寫內容夾帶雜訊（路徑、create、```）→ 清洗後才寫入")
D.review_write = approve_all
seen = script_model([
    model_reply("好的。"),
    model_reply("我不想寫。"),
    model_reply("```markdown\n角色草稿/測試.md\ncreate\n# 標題\n內容本體\n```"),
])
result = asyncio.run(D.run_agent_loop(
    [
        {"role": "system", "content": "寫檔案"},
        {"role": "user", "content": "寫到 角色草稿/測試.md"},
    ],
    require_write=True,
    fallback_path="角色草稿/測試.md",
))
check("代寫成功", len(result["writes"]) == 1 and result["writes"][0].get("ok"),
      str(result["writes"]))
back = asyncio.run(D.read_file("角色草稿/測試.md"))
text = back.get("text", "")
check("沒有殘留路徑與 mode", "角色草稿/測試.md" not in text and "create" not in text,
      text[:80])
check("沒有程式碼圍籬", "```" not in text, text[:80])
check("開頭就是標題", text.lstrip().startswith("# 標題"), text[:40])

restore(saved)

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
