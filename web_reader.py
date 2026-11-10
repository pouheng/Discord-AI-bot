"""Web Reader — 偵測訊息中的連結、抓取網頁內容並（由主程式）以 LLM 摘要後注入 Phase 2。

仿 image_reader.py 的模式：本模組只負責「抽連結 / 安全檢查 / 抓取 / 解析 / 記錄」，
摘要呼叫由 main 端（call_llm）負責，避免反向依賴 main 形成循環。
"""

import asyncio
import datetime
import ipaddress
import os
import re
import time
from urllib.parse import urlparse

import aiofiles

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_LOG_DIR = os.path.join(SCRIPT_DIR, "web_logs")

DEFAULT_CFG = {
    "enabled": True,
    "timeout": 10,
    "max_bytes": 500_000,
    "max_links": 2,
    "max_text_chars": 6000,
    "block_private": True,
    "user_agent": "MioBot/1.0 (+web-reader)",
}

# 抓 http(s) 連結，遇到空白或中日文標點即停止
_URL_RE = re.compile(r"https?://[^\s<>()\[\]{}\"'，。、；：！？（）【】「」]+", re.IGNORECASE)
# 結尾常被句子的標點黏到，逐一剝除
_TRAILING = ".,;:!?)]}」』】。，、；：！？）"


def extract_urls(text: str, limit: int = 2) -> list[str]:
    """從文字抽出 http(s) 連結（去重、去尾端標點、保序、限數量）。"""
    if not text:
        return []
    out: list[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group().rstrip(_TRAILING)
        if url and url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


_FAKE_IP_NETS = (ipaddress.ip_network("198.18.0.0/15"),)


def _ip_is_blocked(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    # Clash／mihomo 的 fake-IP 模式會把網域解析到 198.18.0.0/15，
    # 這是本機代理自己的位址，不是內網服務；不放行會讓所有連結都讀不到。
    if any(addr in net for net in _FAKE_IP_NETS):
        return False
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


async def _host_is_safe(host: str) -> bool:
    """解析 host 並確認不是「只能連到內網」（防 SSRF）。

    注意：本機可能有 DNS64／代理把 AAAA 解析成 2001::1 之類的位址，
    只要還有一個公開位址可用就放行，交由 httpx 自己選。
    """
    if not host:
        return False
    loop = asyncio.get_event_loop()
    try:
        infos = await asyncio.wait_for(loop.getaddrinfo(host, None), timeout=3)
    except (asyncio.TimeoutError, OSError):
        return True  # 解析失敗交給 httpx 決定（連不上自然失敗）
    ips = [info[4][0] for info in infos if info[4]]
    if not ips:
        return True
    return not all(_ip_is_blocked(ip) for ip in ips)


def _parse_html(raw: bytes, url: str) -> tuple[str, str]:
    """回傳 (title, text)。優先取 article/main/body，去掉 script/style 等。"""
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "lxml")
        for tag in soup(["script", "style", "noscript", "template", "svg", "head"]):
            tag.decompose()
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        node = soup.find("article") or soup.find("main") or soup.body or soup
        text = node.get_text("\n", strip=True)
        return title, text
    except Exception:
        try:
            import charset_normalizer

            best = charset_normalizer.from_bytes(raw).best()
            text = str(best) if best else raw.decode("utf-8", "replace")
        except Exception:
            text = raw.decode("utf-8", "replace")
        return "", text


_YT_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{6,})"
)


def _youtube_id(url: str) -> str:
    m = _YT_ID_RE.search(url or "")
    return m.group(1) if m else ""


async def _fetch_youtube(video_id: str, cfg: dict, url: str) -> dict:
    """YouTube 頁面是 JS 渲染，抓不到正文；改用 oEmbed 取標題與頻道。"""
    import httpx

    oembed = (
        "https://www.youtube.com/oembed?url="
        f"https://www.youtube.com/watch?v={video_id}&format=json"
    )
    meta = {}
    try:
        async with httpx.AsyncClient(timeout=cfg["timeout"], headers={
            "User-Agent": cfg["user_agent"], "Accept": "application/json",
        }) as client:
            r = await client.get(oembed)
        if r.status_code == 200:
            meta = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    except Exception:
        meta = {}

    title = (meta.get("title") or "").strip()
    author = (meta.get("author_name") or "").strip()
    if not title:
        return {"ok": False, "url": url, "title": "", "text": "",
                "error": "YouTube 影片資訊取得失敗", "elapsed": 0.0}

    lines = [
        f"YouTube 影片標題：{title}",
        f"頻道：{author}" if author else "",
        "（以下為影片資訊，無法取得影片內容本身）",
    ]
    return {
        "ok": True,
        "url": url,
        "title": f"{title} - YouTube",
        "text": "\n".join([ln for ln in lines if ln]),
        "error": "",
        "elapsed": 0.0,
        "kind": "video_meta",
    }


async def fetch_page(url: str, cfg: dict | None = None) -> dict:
    """抓取單一網址，回傳 {ok, url, title, text, error, elapsed}。"""
    cfg = {**DEFAULT_CFG, **(cfg or {})}
    start = time.time()

    def _err(msg: str) -> dict:
        return {"ok": False, "url": url, "title": "", "text": "",
                "error": msg, "elapsed": time.time() - start}

    yt_id = _youtube_id(url)
    if yt_id:
        res = await _fetch_youtube(yt_id, cfg, url)
        res["elapsed"] = time.time() - start
        return res

    try:
        import httpx
    except ImportError:
        return _err("httpx 未安裝")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return _err("非有效的 http(s) 網址")

    if cfg.get("block_private", True) and not await _host_is_safe(parsed.hostname):
        return _err("拒絕對私有/本機位址連線")

    headers = {
        "User-Agent": cfg["user_agent"],
        "Accept": "text/html,application/xhtml+xml,text/plain",
    }
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=cfg["timeout"],
            headers=headers,
        ) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            return _err(f"HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if ctype and "html" not in ctype and "text" not in ctype:
            return _err(f"非文字內容（{ctype.split(';')[0]}）")
        raw = resp.content[: cfg["max_bytes"]]
        title, text = _parse_html(raw, str(resp.url))
        if not text.strip():
            return _err("頁面沒有可讀文字")
        return {
            "ok": True,
            "url": str(resp.url),
            "title": title,
            "text": text,
            "error": "",
            "elapsed": time.time() - start,
        }
    except Exception as e:
        return _err(f"{type(e).__name__}: {e}")


def build_source_text(pages: list[dict], max_text_chars: int = 6000) -> str:
    """把抓到的頁面組成給 LLM 摘要的來源文字。"""
    parts = []
    for i, p in enumerate(pages, 1):
        body = (p.get("text") or "")[:max_text_chars]
        parts.append(
            f"[連結 {i}] 標題：{p.get('title') or '(無)'}\n"
            f"網址：{p.get('url')}\n"
            f"內容：\n{body}"
        )
    return "\n\n".join(parts)


def build_injection(summary: str, pages: list[dict]) -> str:
    """組出注入 Phase 2 的網頁區塊（含前導空行）。"""
    if not summary:
        return ""
    urls = "、".join(p.get("url", "") for p in pages if p.get("url"))
    return f"\n\n【用戶分享的網頁內容（AI 摘要）】\n（來源：{urls}）\n{summary}"


async def log_web_analysis(user_name: str, channel_name: str, results: list[dict],
                           summary: str, ai_reply: str):
    """將網頁讀取紀錄寫入 web_logs/{date}.txt。"""
    os.makedirs(WEB_LOG_DIR, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(WEB_LOG_DIR, f"{today}.txt")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "=" * 50,
        f"時間: {now_str}",
        f"用戶: {user_name}",
        f"頻道: {channel_name}",
        f"網址: {', '.join(p.get('url', '') for p in results)}",
        f"耗時: {sum(p.get('elapsed', 0) for p in results):.1f}s",
        "",
        "=== 網頁標題 ===",
        " / ".join(p.get("title") or "(無)" for p in results),
        "",
        "=== AI 總結 ===",
        summary or "（無）",
        "",
        "=== AI 回覆 ===",
        ai_reply or "（無）",
        "",
    ]
    async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
        await f.write("\n".join(lines) + "\n")
