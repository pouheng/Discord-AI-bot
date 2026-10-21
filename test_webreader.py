"""離線測試：網頁讀取模組（web_reader.py）的抽連結 / 安全檢查 / 組裝 / 記錄。"""

import asyncio
import os
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import web_reader

SUCCESS = 0
FAIL = 0


def check(name, condition, detail=""):
    global SUCCESS, FAIL
    tag = "PASS" if condition else "FAIL"
    if condition:
        SUCCESS += 1
    else:
        FAIL += 1
    out = f"  [{tag}] {name}"
    if detail and not condition:
        out += f"  ({detail})"
    print(out)


async def run():
    # ── extract_urls ──
    got = web_reader.extract_urls("看 https://example.com/a) 和 https://foo.bar/x，再 https://example.com/a")
    check("抽取連結、去尾標點、去重", got == ["https://example.com/a", "https://foo.bar/x"], str(got))
    check("限量", web_reader.extract_urls("a https://x.com/1 b https://y.com/2", limit=1) == ["https://x.com/1"])
    check("無連結回空", web_reader.extract_urls("沒有連結") == [])
    check("空字串安全", web_reader.extract_urls("") == [])

    # ── 安全檢查（私有/本機位址）──
    check("127.0.0.1 不安全", await web_reader._host_is_safe("127.0.0.1") is False)
    check("localhost 不安全", await web_reader._host_is_safe("localhost") is False)
    check("8.8.8.8 可連", await web_reader._host_is_safe("8.8.8.8") is True)
    check("_ip_is_blocked 10.x", web_reader._ip_is_blocked("10.0.0.5") is True)
    check("_ip_is_blocked 8.8.8.8", web_reader._ip_is_blocked("8.8.8.8") is False)

    # ── fetch_page：私有位址應被擋（不連網）──
    r = await web_reader.fetch_page("http://127.0.0.1:9/x")
    check("fetch_page 擋私有位址", r["ok"] is False and "私有" in r["error"], str(r))
    r = await web_reader.fetch_page("ftp://example.com/x")
    check("fetch_page 擋非 http", r["ok"] is False, str(r))

    # ── build_source_text / build_injection ──
    pages = [{"url": "https://e.com/a", "title": "標題A", "text": "內容A" * 10}]
    src = web_reader.build_source_text(pages, max_text_chars=5)
    check("build_source_text 截斷正文", ("內容A" * 10)[:5] in src and ("內容A" * 10) not in src, src[:60])
    check("build_source_text 含標題與網址", "標題A" in src and "https://e.com/a" in src)
    inj = web_reader.build_injection("重點摘要", pages)
    check("build_injection 含摘要與來源", "重點摘要" in inj and "https://e.com/a" in inj and inj.startswith("\n\n"))
    check("build_injection 空摘要回空", web_reader.build_injection("", pages) == "")

    # ── log_web_analysis 寫檔 ──
    tmp = tempfile.mkdtemp(prefix="mio_web_test_")
    web_reader.WEB_LOG_DIR = tmp
    await web_reader.log_web_analysis("測試用戶", "測試頻道", pages, "重點摘要", "AI 回覆內容")
    files = os.listdir(tmp)
    check("log 產生當日檔案", len(files) == 1, str(files))
    content = open(os.path.join(tmp, files[0]), encoding="utf-8").read()
    check("log 含網址/摘要/回覆",
          "https://e.com/a" in content and "重點摘要" in content and "AI 回覆內容" in content)

    print(f"\n{SUCCESS} passed, {FAIL} failed")
    return FAIL


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
