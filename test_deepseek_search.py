"""離線測試：DeepSeek 原生搜尋模組（deepseek_search.py）的回應解析與注入組裝。"""

import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import deepseek_search

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


FAKE = {
    "output": [
        {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "thinking"}]},
        {"type": "web_search_call", "status": "completed",
         "action": {"type": "search", "queries": ["q1", "q2"]}},
        {"type": "web_search_call", "status": "completed",
         "action": {"type": "open_page", "url": "https://example.com/a#ws_call_id=call_x"}},
        {"type": "web_search_call", "status": "failed",
         "action": {"type": "open_page", "url": "https://example.com/failed#ws_call_id=call_y"}},
        {"type": "message", "role": "assistant",
         "content": [{"type": "output_text", "text": "答案在此，來源 https://example.com/b 。"}]},
    ]
}

answer, sources, queries = deepseek_search._split_sections(FAKE)
check("抽出答案", "答案在此" in answer)
check("抽出查詢、去重", queries == ["q1", "q2"], str(queries))
check("成功的 open_page 進來源", "https://example.com/a" in sources, str(sources))
check("失敗的 open_page 不進來源", "https://example.com/failed" not in sources, str(sources))
check("答案內網址也進來源", "https://example.com/b" in sources, str(sources))

blk = deepseek_search.build_injection(
    {"ok": True, "answer": "摘要", "sources": ["https://example.com/a"], "queries": ["q"]},
    "查詢字串",
)
check("注入含標題/查詢/來源",
      "網路搜尋結果" in blk and "查詢字串" in blk and "https://example.com/a" in blk)
check("失敗結果不注入",
      deepseek_search.build_injection({"ok": False, "answer": ""}, "q") == "")
check("空答案不注入",
      deepseek_search.build_injection({"ok": True, "answer": ""}, "q") == "")

check("工具參數：正常 JSON",
      deepseek_search._parse_tool_args('{"query":"abc"}') == {"query": "abc"})
check("工具參數：壞 JSON 用 regex 抽 query",
      deepseek_search._parse_tool_args('oops "query":"颱風" tail') == {"query": "颱風"})
check("工具參數：空字串不爆", deepseek_search._parse_tool_args("") == {})

# ── dispatcher：原生優先、失敗降級 ──
import asyncio

_orig_native = deepseek_search._native_search
_orig_tool = deepseek_search._tool_search
_native_calls = {"n": 0}


async def _native_ok(q, cfg):
    _native_calls["n"] += 1
    return {"ok": True, "answer": "native", "sources": [], "queries": ["q"],
            "error": "", "elapsed": 0.0}


async def _native_nosearch(q, cfg):
    _native_calls["n"] += 1
    return {"ok": True, "answer": "no-search", "sources": [], "queries": [],
            "error": "", "elapsed": 0.0}


async def _tool_ok(q, cfg):
    return {"ok": True, "answer": "tool", "sources": ["u"], "queries": ["q"],
            "error": "", "elapsed": 0.0, "mode": "function"}


deepseek_search._native_search = _native_ok
deepseek_search._tool_search = _tool_ok
r = asyncio.run(deepseek_search.search("x", {"mode": "auto"}))
check("auto：原生有觸發就用原生",
      r.get("mode") == "native" and r.get("answer") == "native", str(r))

_native_calls["n"] = 0
deepseek_search._native_search = _native_nosearch
r = asyncio.run(deepseek_search.search("x", {"mode": "auto"}))
check("auto：原生沒觸發 → 降級 function",
      r.get("mode") == "function" and r.get("answer") == "tool", str(r))

_native_calls["n"] = 0
r = asyncio.run(deepseek_search.search("x", {"mode": "function"}))
check("function 模式不呼叫原生", _native_calls["n"] == 0 and r.get("mode") == "function")

deepseek_search._native_search = _orig_native
deepseek_search._tool_search = _orig_tool

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
