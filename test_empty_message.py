"""離線測試：沒有文字的訊息（圖片／貼圖／連結）不再被當成「hi」。"""

import os
import sys
from types import SimpleNamespace

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import main

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


def make_msg(attachments=None, stickers=None, embeds=None):
    return SimpleNamespace(
        attachments=attachments or [],
        stickers=stickers or [],
        embeds=embeds or [],
    )


print("[1] 圖片")
m = make_msg(attachments=[
    SimpleNamespace(content_type="image/png", filename="character.png")
])
out = main._describe_empty_message(m)
check("描述為圖片", "圖片" in out and "character.png" in out, out)
check("不再回 hi", out.strip() != "hi", out)

print("[2] 多個附件")
m = make_msg(attachments=[
    SimpleNamespace(content_type="image/jpeg", filename="a.jpg"),
    SimpleNamespace(content_type="video/mp4", filename="b.mp4"),
    SimpleNamespace(content_type="application/pdf", filename="c.pdf"),
])
out = main._describe_empty_message(m)
check("圖片/影片/檔案都標出", all(k in out for k in ("圖片", "影片", "檔案")), out)
check("檔名保留", "a.jpg" in out and "b.mp4" in out and "c.pdf" in out, out)

print("[3] 貼圖")
out = main._describe_empty_message(make_msg(stickers=[object()]))
check("描述為貼圖", "貼圖" in out, out)

print("[4] 連結嵌入")
m = make_msg(embeds=[SimpleNamespace(title="某個 YouTube 影片")])
out = main._describe_empty_message(m)
check("描述為連結", "連結" in out and "YouTube" in out, out)

m = make_msg(embeds=[SimpleNamespace(title="")])
check("無標題連結不炸", "連結" in main._describe_empty_message(m))

print("[5] 完全沒有內容")
out = main._describe_empty_message(make_msg())
check("描述為非文字內容", "非文字" in out, out)

print("[6] 屬性缺失不炸")
check("缺屬性也能回", main._describe_empty_message(object()) != "")


print("[7] 自動讀圖")
import asyncio
import image_reader


class FakeAtt:
    def __init__(self, filename="a.png", ctype="image/png", size=1234):
        self.filename = filename
        self.content_type = ctype
        self.size = size
        self._data = b"fake-image-bytes"

    async def read(self):
        return self._data


_read_calls = []
_set_calls = []
_orig_read = image_reader.read_image
_orig_get = image_reader.get_cached_description
_orig_set = image_reader.set_cached_description


async def _fake_read(data, filename, ollama_cfg, image_cfg=None):
    _read_calls.append(filename)
    return {"success": True, "description": f"[描述] {filename} 的內容",
            "thinking": "", "elapsed": 1.5, "error": ""}


image_reader.read_image = _fake_read
image_reader.get_cached_description = lambda data: None
image_reader.set_cached_description = lambda data, desc: _set_calls.append(desc)

pcfg = {"image_reader": {"auto_read": True, "auto_max_images": 2,
                         "auto_max_bytes": 8_000_000}}

m = SimpleNamespace(attachments=[FakeAtt()], reference=None)
text, logs = asyncio.run(main._auto_read_images(m, pcfg))
check("有讀圖", "a.png" in text and "[描述]" in text, text)
check("有寫入紀錄", len(logs) == 1 and logs[0]["filename"] == "a.png")
check("描述進快取", len(_set_calls) == 1)

# 被回覆的訊息帶圖
_read_calls.clear()
m2 = SimpleNamespace(
    attachments=[],
    reference=SimpleNamespace(resolved=SimpleNamespace(
        attachments=[FakeAtt(filename="b.png")])),
)
text2, logs2 = asyncio.run(main._auto_read_images(m2, pcfg))
check("讀被回覆訊息的圖", "b.png" in text2 and logs2, text2)

# 關閉
m3 = SimpleNamespace(attachments=[FakeAtt()], reference=None)
text3, logs3 = asyncio.run(main._auto_read_images(
    m3, {"image_reader": {"auto_read": False}}))
check("auto_read=false 不讀", text3 == "" and logs3 == [])

# 非圖片附件
m4 = SimpleNamespace(attachments=[FakeAtt(ctype="application/pdf")], reference=None)
text4, _ = asyncio.run(main._auto_read_images(m4, pcfg))
check("非圖片不讀", text4 == "")

image_reader.read_image = _orig_read
image_reader.get_cached_description = _orig_get
image_reader.set_cached_description = _orig_set

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
