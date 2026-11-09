"""離線測試：歷史連結掃描（web context）與記憶日期標示（supplement）。"""

import asyncio
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import main
import web_reader

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


# ── 假的抓頁 / 摘要 ──
_fetch_calls = []


async def _fake_fetch(url, cfg):
    _fetch_calls.append(url)
    return {"ok": True, "url": url, "title": "標題", "text": "正文內容"}


async def _fake_summarize(pages, max_text_chars=6000):
    return "摘要：" + "、".join(p["url"] for p in pages)


web_reader.fetch_page = _fake_fetch
main._summarize_web_pages = _fake_summarize

print("[1] 只掃當前訊息（舊行為）")
main._WEB_PAGE_CACHE.clear()
_fetch_calls.clear()
block, pages, summary = asyncio.run(main._build_web_context(
    "沒有連結", {"web_reader": {"history_messages": 0}},
    [{"role": "user", "content": "[甲]: https://example.com/a", "msg_id": 1}],
))
check("history_messages=0 → 不掃歷史", not _fetch_calls and block == "")

print("[2] 掃最近歷史的連結")
main._WEB_PAGE_CACHE.clear()
_fetch_calls.clear()
block, pages, summary = asyncio.run(main._build_web_context(
    "喵喵喵", {"web_reader": {"history_messages": 6}},
    [
        {"role": "user", "content": "[甲]: https://example.com/a", "msg_id": 1},
        {"role": "system", "content": "【日期分隔】", "msg_id": 0},
        {"role": "assistant", "content": "好", "msg_id": 2},
    ],
))
check("抓到歷史連結", "https://example.com/a" in _fetch_calls, str(_fetch_calls))
check("有注入區塊", "example.com/a" in block, block[:200])
check("回傳頁面", len(pages) == 1)

print("[3] 當前訊息優先、且不重抓")
main._WEB_PAGE_CACHE.clear()
_fetch_calls.clear()
ctx = [{"role": "user", "content": "[甲]: https://example.com/old", "msg_id": 1}]
block, pages, summary = asyncio.run(main._build_web_context(
    "看這個 https://example.com/new", {"web_reader": {"history_messages": 6, "max_links": 2}},
    ctx,
))
check("新舊連結都抓", set(_fetch_calls) == {
    "https://example.com/new", "https://example.com/old"}, str(_fetch_calls))
check("順序：當前訊息先", _fetch_calls[0] == "https://example.com/new", str(_fetch_calls))

calls_before = len(_fetch_calls)
block2, pages2, _ = asyncio.run(main._build_web_context(
    "看這個 https://example.com/new", {"web_reader": {"history_messages": 6, "max_links": 2}},
    ctx,
))
check("快取生效不重抓", len(_fetch_calls) == calls_before, str(_fetch_calls))
check("快取仍能注入", block2 != "" and pages2)

print("[4] 抓取失敗不炸")
main._WEB_PAGE_CACHE.clear()


async def _fail_fetch(url, cfg):
    return {"ok": False, "url": url, "error": "boom"}


web_reader.fetch_page = _fail_fetch
block3, pages3, _ = asyncio.run(main._build_web_context(
    "https://example.com/x", {"web_reader": {"history_messages": 6}}))
check("失敗回空區塊", block3 == "" and pages3 == [])
web_reader.fetch_page = _fake_fetch

print("[5] supplement 日期標示")
sup = [
    {"category": "人物", "topic": "凌空的分靈體",
     "content": "澪說本尊才剛道晚安。", "source_date": "2026-09-10"},
    {"category": "世界觀", "topic": "魔法", "content": "這世界的人都會覺醒魔法。"},
]
rendered = main.format_supplement(sup, include_notes=False)
check("有日期的條目帶日期", "（2026-09-10）" in rendered, rendered)
check("沒日期的標為過去", "（日期不明，屬過去）" in rendered, rendered)
check("內容仍保留", "本尊才剛道晚安" in rendered and "覺醒魔法" in rendered)

check("字串格式不變", "舊格式" in main.format_supplement("<supplement>舊格式</supplement>"))

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
