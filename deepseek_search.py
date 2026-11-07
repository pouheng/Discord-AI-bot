"""deepseek_search.py — DeepSeek 官方 API 的原生網頁搜尋（server-side web_search）。

走官方 `/v1/responses` + `tools=[{"type":"web_search"}]`，由 DeepSeek 伺服器
實際執行搜尋／開頁，回傳最終答案與引用來源。與 workbuddy 閘道無關
（閘道只轉發 chat/completions，不支援這個工具）。

對外介面：
    has_credentials() -> bool
    async search(query, cfg) -> dict   # {ok, answer, sources, queries, elapsed, error}
    build_injection(result, query) -> str
    async log_search(user, channel, query, result, ai_reply)
"""

import datetime
import os
import re
import time
from urllib.parse import urlparse

import httpx
import aiofiles

import llm_models  # 匯入即執行 NO_PROXY 清理（見 llm_models._sanitize_no_proxy）

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_LOG_DIR = os.path.join(SCRIPT_DIR, "web_logs")

DEFAULT_CFG = {
    "enabled": True,
    # auto = 先試原生 server-side web_search，失敗/模型不支援就降級成 function tool
    "mode": "auto",  # auto | native | function
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-v4-pro",
    "search_context_size": "medium",
    "max_tool_calls": 4,
    "timeout": 180,
    "max_chars": 6000,
    "tools_max_iterations": 4,
    # Bing 一般 HTML 在這台機器會被反爬／地區污染；News RSS 穩定且夠用。
    "bing_url": "https://www.bing.com/news/search",
}

_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'，。、；：！？（）【】「」]+")


def has_credentials() -> bool:
    return bool(llm_models._env_api_key())


def _split_sections(data: dict) -> tuple[str, list[str], list[str]]:
    """從 Responses API 的回應取出 (答案文字, 來源網址, 搜尋查詢)。"""
    answer_parts: list[str] = []
    sources: list[str] = []
    queries: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            for c in item.get("content") or []:
                if isinstance(c, dict) and c.get("text"):
                    answer_parts.append(c["text"])
        elif item.get("type") == "web_search_call":
            action = item.get("action") or {}
            if action.get("type") == "search":
                for q in action.get("queries") or []:
                    if q and q not in queries:
                        queries.append(q)
            elif action.get("type") == "open_page":
                url = action.get("url") or ""
                url = url.split("#ws_call_id=")[0]
                if item.get("status") == "completed" and url and url not in sources:
                    sources.append(url)
    answer = "\n".join(p for p in answer_parts if p).strip()
    # 答案裡可能已經寫了來源網址；去重後補進 sources
    for m in _URL_RE.finditer(answer):
        u = m.group().rstrip(".,;:!?)]}」』】。，、；：！？）")
        if u and u not in sources:
            sources.append(u)
    return answer, sources, queries


async def _native_search(query: str, cfg: dict | None = None) -> dict:
    """用 DeepSeek 原生 server-side web_search 查詢（/v1/responses）。"""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    start = time.time()

    def _err(msg: str) -> dict:
        return {"ok": False, "answer": "", "sources": [], "queries": [],
                "error": msg, "elapsed": time.time() - start}

    key = llm_models._env_api_key()
    if not key:
        return _err("沒有 DEEPSEEK_API_KEY（.env）")

    url = cfg["base_url"].rstrip("/") + "/responses"
    body = {
        "model": cfg["model"],
        "input": [{
            "role": "user",
            "content": (
                "請使用網路搜尋回答以下問題。"
                "答案要具體，並在相關處附上來源網址。\n\n"
                f"{query}"
            ),
        }],
        "tools": [{
            "type": "web_search",
            "search_context_size": cfg.get("search_context_size", "medium"),
        }],
    }
    try:
        mtc = int(cfg.get("max_tool_calls") or 0)
        if mtc > 0:
            body["max_tool_calls"] = mtc
    except (TypeError, ValueError):
        pass
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=cfg["timeout"], trust_env=True) as client:
            resp = await client.post(url, headers=headers, json=body)
        if resp.status_code != 200:
            return _err(f"HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        answer, sources, queries = _split_sections(data)
        if not answer:
            return _err("搜尋回傳空答案")
        return {
            "ok": True,
            "answer": answer[: int(cfg.get("max_chars", 6000))],
            "sources": sources,
            "queries": queries,
            "error": "",
            "elapsed": time.time() - start,
        }
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


async def _bing_search(query: str, cfg: dict) -> list[dict]:
    """用 Bing News RSS 搜尋（function tool 的搜尋後端）。"""
    if not query:
        return []
    try:
        import re as _re
        import xml.etree.ElementTree as ET

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
            )
        }
        async with httpx.AsyncClient(
            timeout=min(int(cfg.get("timeout", 180)), 60),
            trust_env=True,
            headers=headers,
        ) as client:
            r = await client.get(
                cfg.get("bing_url", "https://www.bing.com/news/search"),
                params={"q": query, "format": "RSS"},
            )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        out: list[dict] = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = item.findtext("description") or ""
            desc = _re.sub(r"<[^>]+>", " ", desc)
            desc = _re.sub(r"\s+", " ", desc).strip()
            if title and link:
                out.append({"title": title, "url": link, "snippet": desc[:300]})
            if len(out) >= 6:
                break
        return out
    except Exception as e:
        print(f"[搜尋] Bing RSS 失敗: {type(e).__name__}: {e}")
        return []


_TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜尋網路，回傳標題、網址與摘要。需要最新或外部資訊時使用。",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "讀取指定網址的網頁正文。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
]


def _parse_tool_args(raw: str) -> dict:
    """解析工具參數；模型偶爾回非 JSON，退化成 regex 抽 query/url。"""
    try:
        import json as _json
        v = _json.loads(raw or "{}")
        if isinstance(v, dict):
            return v
    except Exception:
        pass
    out: dict = {}
    m = re.search(r'"query"\s*:\s*"([^"]*)"', raw or "")
    if m:
        out["query"] = m.group(1)
    m = re.search(r'"url"\s*:\s*"([^"]*)"', raw or "")
    if m:
        out["url"] = m.group(1)
    return out


async def _tool_search(query: str, cfg: dict) -> dict:
    """降級路徑：讓目前模型用 function tool 呼叫 web_search/web_fetch，由我們執行。"""
    start = time.time()

    def _err(msg: str) -> dict:
        return {"ok": False, "answer": "", "sources": [], "queries": [],
                "error": msg, "elapsed": time.time() - start, "mode": "function"}

    try:
        import json as _json

        from llm_models import get_client, get_model

        import web_reader
    except Exception as e:
        return _err(f"無法載入工具相依: {e}")

    messages = [
        {"role": "system", "content": (
            "你可以使用 web_search / web_fetch 查資料。需要即時或外部資訊時先呼叫工具，"
            "最多使用 2 輪工具；取得足夠資訊就直接回答，不要為了完整而無限查證。"
            "最後用繁體中文回答並附上來源網址，不要憑空編造。"
        )},
        {"role": "user", "content": query},
    ]
    sources: list[str] = []
    queries: list[str] = []
    used_tool = False
    try:
        client = get_client(timeout=cfg.get("timeout", 180))
        model = get_model("default")
        max_iter = max(1, int(cfg.get("tools_max_iterations", 4)))
        for iteration in range(max_iter):
            kwargs = {
                "model": model, "messages": messages, "tools": _TOOL_DEFS,
                "temperature": 0.3, "max_tokens": 1500,
            }
            if iteration == max_iter - 1:
                # 最後一輪強制給答案，避免模型無限搜尋
                kwargs["tool_choice"] = "none"
            resp = await client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            tcs = getattr(msg, "tool_calls", None) or []
            if not tcs:
                if not used_tool:
                    return _err("模型未呼叫搜尋工具")
                answer = (msg.content or "").strip()
                if not answer:
                    return _err("模型未回覆內容")
                return {"ok": True,
                        "answer": answer[: int(cfg.get("max_chars", 6000))],
                        "sources": sources, "queries": queries, "error": "",
                        "elapsed": time.time() - start, "mode": "function"}
            used_tool = True
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name,
                                  "arguments": tc.function.arguments}}
                    for tc in tcs
                ],
            })
            for tc in tcs:
                name = tc.function.name
                args = _parse_tool_args(tc.function.arguments)
                if name == "web_search":
                    q = str(args.get("query") or "").strip() or query
                    if q and q not in queries:
                        queries.append(q)
                    results = await _bing_search(q, cfg)
                    for r in results:
                        if r["url"] not in sources:
                            sources.append(r["url"])
                    payload = _json.dumps(results, ensure_ascii=False)
                elif name == "web_fetch":
                    u = str(args.get("url") or "").strip()
                    page = (await web_reader.fetch_page(
                        u, {"timeout": min(int(cfg.get("timeout", 180)), 30)}
                    )) if u else {"ok": False, "error": "缺少 url"}
                    if page.get("ok"):
                        payload = (
                            f"標題：{page.get('title', '')}\n"
                            f"網址：{page.get('url', '')}\n\n{page.get('text', '')[:6000]}"
                        )
                    else:
                        payload = f"讀取失敗：{page.get('error', '')}"
                else:
                    payload = f"未知工具：{name}"
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": payload[:8000],
                })
        return _err("工具呼叫次數用盡仍未產生答案")
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


async def search(query: str, cfg: dict | None = None) -> dict:
    """搜尋入口：auto/native 先試原生 web_search，失敗再降級成 function tool。"""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    mode = (cfg.get("mode") or "auto").lower()
    native_err = ""
    if mode in ("auto", "native"):
        res = await _native_search(query, cfg)
        if res.get("ok") and res.get("queries"):
            res["mode"] = "native"
            return res
        native_err = res.get("error") or "模型未觸發原生搜尋"
        if mode == "native":
            res["mode"] = "native"
            return res
        print(f"[搜尋] 原生不可用（{native_err}）→ 降級 function tool")
    if mode in ("auto", "function"):
        res = await _tool_search(query, cfg)
        if not res.get("ok") and native_err:
            res["error"] = f"{res.get('error')}（原生：{native_err}）"
        return res
    return {"ok": False, "answer": "", "sources": [], "queries": [],
            "error": f"未知 mode: {mode}", "elapsed": 0.0, "mode": "unknown"}


def build_injection(result: dict, query: str) -> str:
    """組出注入 Phase 2 的搜尋結果區塊（含前導空行）。"""
    if not result or not result.get("ok") or not result.get("answer"):
        return ""
    block = (
        "\n\n【網路搜尋結果（DeepSeek 原生搜尋）】\n"
        f"（查詢：{query}）\n{result['answer']}"
    )
    sources = result.get("sources") or []
    if sources:
        block += "\n\n【來源】\n" + "\n".join(f"- {u}" for u in sources[:8])
    return block


async def log_search(user_name: str, channel_name: str, query: str,
                     result: dict, ai_reply: str):
    """把搜尋紀錄寫入 web_logs/{date}.txt。"""
    os.makedirs(WEB_LOG_DIR, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(WEB_LOG_DIR, f"{today}.txt")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "=" * 50,
        f"時間: {now_str}",
        f"用戶: {user_name}",
        f"頻道: {channel_name}",
        f"類型: DeepSeek 原生搜尋",
        f"查詢: {query}",
        f"耗時: {result.get('elapsed', 0):.1f}s",
        "",
        "=== 搜尋查詢 ===",
        " / ".join(result.get("queries") or []) or "（無）",
        "",
        "=== 來源 ===",
        "\n".join(result.get("sources") or []) or "（無）",
        "",
        "=== AI 回覆 ===",
        ai_reply or "（無）",
        "",
    ]
    async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
        await f.write("\n".join(lines) + "\n")
