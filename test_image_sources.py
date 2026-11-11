"""test_image_sources.py — 讀圖來源：附件／貼圖／連結預覽／圖片直連／追問回看。"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402
import image_reader  # noqa: E402

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


CDN = ("https://cdn.discordapp.com/attachments/1463839150590394400/"
       "1542143325044211732/o4aqChtAEP8FdBqA9gaEYdeAxvjBDAAryf8IRN.gif")


class FakeAtt:
    def __init__(self, filename="a.png", content_type="image/png", size=100):
        self.filename = filename
        self.content_type = content_type
        self.size = size

    async def read(self):
        return b"bytes:" + self.filename.encode()


class FakeImage:
    def __init__(self, url):
        self.url = url


class FakeEmbed:
    def __init__(self, url="", title="預覽"):
        self.image = FakeImage(url) if url else None
        self.thumbnail = None
        self.title = title


class FakeSticker:
    def __init__(self, name="wave", url=CDN):
        self.name = name
        self.url = url


class FakeMsg:
    def __init__(self, content="", attachments=(), stickers=(), embeds=(),
                 reference=None):
        self.content = content
        self.attachments = list(attachments)
        self.stickers = list(stickers)
        self.embeds = list(embeds)
        self.reference = reference


_orig_read = main.image_reader.read_image
_orig_dl = main._download_image
_seen = {"downloads": [], "reads": []}


async def _fake_read(data, filename, ollama_cfg, image_cfg=None):
    _seen["reads"].append(filename)
    return {"success": True, "description": f"描述:{filename}",
            "thinking": "", "error": "", "elapsed": 0.1}


async def _fake_download(url, max_bytes=8_000_000, timeout=20.0):
    _seen["downloads"].append(url)
    return b"dl:" + url.encode()


main.image_reader.read_image = _fake_read
main._download_image = _fake_download
image_reader._image_cache.clear()

PCFG = {"image_reader": {"auto_read": True, "auto_max_images": 1,
                         "auto_max_bytes": 8_000_000}}


async def run():
    print("[1] 圖片網址判斷")
    check("CDN gif 認得", main._url_looks_like_image(CDN))
    check("帶 query 的 png 認得",
          main._url_looks_like_image("https://x.com/a.png?ex=1&is=2&hm=3"))
    check("一般網頁不算", not main._url_looks_like_image("https://example.com/a"))
    check("html 不算", not main._url_looks_like_image("https://example.com/a.html"))
    got = main._image_urls_in(f"看這個 {CDN} 喔，還有 https://example.com/x")
    check("從文字抽出圖片直連", got == [CDN], str(got))

    print("[2] 連結預覽（embed）也算來源")
    _seen["downloads"].clear(); _seen["reads"].clear()
    msg = FakeMsg(content="哈哈", embeds=[FakeEmbed(CDN)])
    text, logs = await main._auto_read_images(msg, PCFG)
    check("有讀到", bool(logs), str(logs))
    check("有用到下載", _seen["downloads"] == [CDN], str(_seen["downloads"]))
    check("描述進注入文字", "描述:" in text, text[:80])

    print("[3] 貼圖（sticker）也算來源")
    _seen["downloads"].clear()
    msg = FakeMsg(stickers=[FakeSticker()])
    text, logs = await main._auto_read_images(msg, PCFG)
    check("貼圖被讀", bool(logs) and _seen["downloads"] == [CDN],
          f"{logs} {_seen['downloads']}")
    check("檔名用貼圖名稱", "wave" in (logs[0]["filename"] if logs else ""),
          str(logs))
    lottie = FakeSticker(name="lottie", url="https://cdn.discordapp.com/stickers/1.json")
    _seen["downloads"].clear()
    text, logs = await main._auto_read_images(FakeMsg(stickers=[lottie]), PCFG)
    check("Lottie 貼圖跳過", not logs and not _seen["downloads"], str(logs))

    print("[4] 文字裡的圖片直連（使用者貼連結）")
    _seen["downloads"].clear()
    msg = FakeMsg(content=f"這張 {CDN}")
    text, logs = await main._auto_read_images(msg, PCFG)
    check("直連被讀", bool(logs) and _seen["downloads"] == [CDN], str(logs))

    print("[5] 追問「我發的這個圖是什麼？」→ 回頭看最近幾則")
    _seen["downloads"].clear()
    ctx = [
        {"role": "user", "content": "隨便聊聊"},
        {"role": "user", "content": CDN},
        {"role": "assistant", "content": "嗯嗯"},
    ]
    msg = FakeMsg(content="我發的這個圖是什麼？")
    text, logs = await main._auto_read_images(msg, PCFG, ctx)
    check("回頭讀到前幾則的圖", bool(logs) and _seen["downloads"] == [CDN],
          str(logs))
    check("標示來源", "前幾則訊息" in (logs[0]["filename"] if logs else ""),
          str(logs))

    print("[6] 沒在問圖就不亂讀歷史")
    _seen["downloads"].clear()
    msg = FakeMsg(content="今天好累")
    text, logs = await main._auto_read_images(msg, PCFG, ctx)
    check("不亂讀", not logs and not _seen["downloads"], str(logs))
    for label, line in [
        ("圖書館", "我在圖書館看書，順便查了一下地圖"),
        ("不是問句", "這張椅子好醜"),
    ]:
        _seen["downloads"].clear()
        text, logs = await main._auto_read_images(FakeMsg(content=line), PCFG, ctx)
        check(f"{label} 不觸發", not logs and not _seen["downloads"], str(logs))
    _seen["downloads"].clear()
    text, logs = await main._auto_read_images(
        FakeMsg(content="這張圖好看嗎"), PCFG, ctx)
    check("真的問圖就讀", bool(logs), str(logs))

    print("[7] auto_read 關閉 → 完全不動作")
    _seen["downloads"].clear()
    text, logs = await main._auto_read_images(
        FakeMsg(attachments=[FakeAtt()]), {"image_reader": {"auto_read": False}})
    check("回空", text == "" and logs == [], f"{text!r} {logs}")

    print("[8] 附件與 embed 同時存在 → 受 auto_max_images 限制")
    _seen["downloads"].clear(); _seen["reads"].clear()
    msg = FakeMsg(attachments=[FakeAtt("a.png")], embeds=[FakeEmbed(CDN)])
    text, logs = await main._auto_read_images(msg, PCFG)
    check("只讀 1 張", len(logs) == 1, str(logs))
    check("附件優先", logs and logs[0]["filename"] == "a.png", str(logs))

    print("[9] 網頁抓取會跳過圖片直連")
    calls = []

    async def _fake_fetch(url, cfg=None):
        calls.append(url)
        return {"ok": False, "url": url, "error": "不該被呼叫"}

    orig_fetch = main.web_reader.fetch_page
    main.web_reader.fetch_page = _fake_fetch
    try:
        out = await main._build_web_context(CDN, {"web_reader": {"enabled": True}})
    finally:
        main.web_reader.fetch_page = orig_fetch
    check("回空且沒去抓", out == ("", [], "") and calls == [],
          f"{out} {calls}")


try:
    asyncio.run(run())
finally:
    main.image_reader.read_image = _orig_read
    main._download_image = _orig_dl
    image_reader._image_cache.clear()

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
