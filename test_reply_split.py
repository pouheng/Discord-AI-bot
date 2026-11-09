"""離線測試：多人回覆（[TO:] 分段）與回覆對象解析。

對應功能：模型用 [TO:名稱] 分段時，每一段各自變成一則獨立 Discord
訊息，並回覆到那個人的訊息（而不是合併成一則）。
"""

import asyncio
import os
import sys
from types import SimpleNamespace
from unittest import mock

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


print("[1] 切分 [TO:]")
segs = main.split_to_segments("[TO:甲]\n給甲\n[TO:乙]\n給乙")
check("兩段", segs == [("甲", "給甲"), ("乙", "給乙")], str(segs))

check("沒有 [TO:] 視為單段",
      main.split_to_segments("哈囉") == [("", "哈囉")])
check("全形冒號/括號",
      main.split_to_segments("【TO：甲】\n嗨") == [("甲", "嗨")],
      str(main.split_to_segments("【TO：甲】\n嗨")))
check("小寫 to",
      main.split_to_segments("[to:甲]\n嗨") == [("甲", "嗨")],
      str(main.split_to_segments("[to:甲]\n嗨")))
check("冒號後空白",
      main.split_to_segments("[TO: 甲 ]\n嗨") == [("甲", "嗨")],
      str(main.split_to_segments("[TO: 甲 ]\n嗨")))
bare = main.split_to_segments("TO：甲\n嗨\nTO：乙\n唷")
check("行首無括號 TO：", bare == [("甲", "嗨"), ("乙", "唷")], str(bare))
lead = main.split_to_segments("先說一句。\n[TO:甲]\n嗨")
check("前言併成第一段", lead == [("", "先說一句。"), ("甲", "嗨")], str(lead))
check("空的區段被丟掉",
      main.split_to_segments("[TO:甲]\n[TO:乙]\n嗨") == [("乙", "嗨")],
      str(main.split_to_segments("[TO:甲]\n[TO:乙]\n嗨")))
check("空字串不爆", main.split_to_segments("") == [("", "")])

print("[2] 名稱比對")
check("完全相同", main._name_matches("頭皮慶", "頭皮慶"))
check("含括號補充", main._name_matches("頭皮慶", "頭皮慶（一般的學弟）"))
check("反向含括號", main._name_matches("頭皮慶（一般的學弟）", "頭皮慶"))
check("emoji 不影響", main._name_matches("星狐凌空", "星狐凌空🌟（空米🌾）"))
check("@ 前綴", main._name_matches("@頭皮慶", "頭皮慶"))
check("不同人", not main._name_matches("頭皮慶", "吳神月"))
check("空名稱", not main._name_matches("", "頭皮慶"))

print("[3] 參與者清單")
ctx = [
    {"role": "user", "content": "[甲]: 安安", "msg_id": 1, "name": "甲"},
    {"role": "assistant", "content": "嗨", "msg_id": 2, "name": "澪"},
    {"role": "system", "content": "【日期分隔】", "msg_id": 0, "ts": ""},
    {"role": "user", "content": "[乙]: 你好啊", "msg_id": 3, "name": "乙"},
]
roster = main._reply_roster_text(ctx, "丙")
check("含觸發者", "丙" in roster and "本輪觸發者" in roster, roster)
check("含歷史發言者", "甲" in roster and "乙" in roster, roster)
check("不含 bot", "澪" not in roster, roster)
check("不含分隔訊息", "日期分隔" not in roster, roster)
check("觸發者排第一", roster.index("丙") < roster.index("甲"), roster)

ctx_noname = [{"role": "user", "content": "[小名 ↩澪「嗨」]: 內容", "msg_id": 1}]
roster2 = main._reply_roster_text(ctx_noname, "我")
check("沒有 name 欄位也能抽出名稱", "小名" in roster2, roster2)
check("↩ 標記不會混進名稱", "↩" not in roster2.split("小名")[1].split("：")[0], roster2)

print("[4] 格式指令")
text, planning, fmt, priming = main._reply_format_instruction(
    "out_of_character", "澪", "頭皮慶", roster)
check("含預設只回覆觸發者", "預設只回覆 頭皮慶" in text)
check("含獨立訊息說明", "獨立的 Discord 訊息" in text)
check("含參與者清單", "最近發言的人" in text)
check("含 planning", planning in text and fmt in text)
check("OOC priming", priming.startswith("喔好"))
text_ic, planning_ic, fmt_ic, priming_ic = main._reply_format_instruction(
    "in_character", "澪", "頭皮慶", roster)
check("IC 台詞格式", "澪的台詞" in fmt_ic)
check("IC priming", priming_ic.startswith("開始回應"))

print("[5] dispatch：每人一則、各自回覆到對方訊息")


class FakeMsg:
    def __init__(self, name, msg_id, channel=None):
        self.id = msg_id
        self.author = SimpleNamespace(display_name=name, bot=False)
        self.channel = channel
        self.replies = []
        self.sent = []

    async def reply(self, text, files=None):
        self.replies.append((text, files))

    async def send(self, text, files=None):
        self.sent.append((text, files))


class FakeChannel:
    def __init__(self, msgs):
        self._by_id = {m.id: m for m in msgs}
        self.sent = []

    async def send(self, text, files=None):
        self.sent.append((text, files))

    async def fetch_message(self, mid):
        if mid in self._by_id:
            return self._by_id[mid]
        raise RuntimeError("Unknown Message")

    def history(self, limit=30):
        async def _gen():
            for _ in ():
                yield None
        return _gen()


def run_dispatch(reply_text, context, existing=(), reply_mode="always"):
    a = FakeMsg("甲", 101)
    b = FakeMsg("乙", 102)
    channel = FakeChannel([a, b, *existing])
    trigger = FakeMsg("丙", 999, channel=channel)
    asyncio.run(main.dispatch_reply(trigger, reply_text, None, context, reply_mode))
    return trigger, a, b


ctx2 = [
    {"role": "user", "content": "[甲]: hi", "msg_id": 101, "name": "甲"},
    {"role": "user", "content": "[乙]: yo", "msg_id": 102, "name": "乙"},
]
trigger, a, b = run_dispatch("[TO:甲]\n給甲\n[TO:乙]\n給乙", ctx2)
check("甲收到自己的回覆", a.replies and a.replies[0][0] == "給甲", str(a.replies))
check("乙收到自己的回覆", b.replies and b.replies[0][0] == "給乙", str(b.replies))
check("觸發訊息沒有被合併回覆", not trigger.replies, str(trigger.replies))

trigger, a, b = run_dispatch("哈囉", ctx2)
check("單段回覆觸發訊息", trigger.replies and trigger.replies[0][0] == "哈囉",
      str(trigger.replies))
check("單段不亂回別人", not a.replies and not b.replies)

trigger, a, b = run_dispatch("[TO:甲]\n給甲\n[TO:路人]\n給路人", ctx2)
check("解析不到對象 → 回覆觸發訊息", trigger.replies and trigger.replies[0][0] == "給路人",
      str(trigger.replies))
check("解析到的仍各自回覆", a.replies and a.replies[0][0] == "給甲")

trigger, a, b = run_dispatch("[TO:甲]\n給甲", ctx2)
check("單一 [TO:] 也會回覆到對方", a.replies and a.replies[0][0] == "給甲",
      str(a.replies))

print("[6] 要不要使用「回覆」功能（模型自己選）")
check("auto 沒標記 → 不引用", main.wants_reply("哈囉", "auto") is False)
check("auto + [REPLY] → 引用", main.wants_reply("[REPLY]\n哈囉", "auto") is True)
check("auto + [回覆] → 引用", main.wants_reply("【回覆】\n哈囉", "auto") is True)
check("auto + [NO_REPLY] 優先", main.wants_reply("[REPLY]\n[no_reply]\n哈囉", "auto") is False)
check("always 強制引用", main.wants_reply("哈囉", "always") is True)
check("never 強制不引用", main.wants_reply("[REPLY]\n哈囉", "never") is False)
check("[REPLY] 不會被當正文送出",
      main.strip_reply_markers("[REPLY]\n哈囉") == "哈囉",
      repr(main.strip_reply_markers("[REPLY]\n哈囉")))
check("【不引用】也會被移除",
      main.strip_reply_markers("【不引用】\n哈囉") == "哈囉")

trigger, a, b = run_dispatch("哈囉", ctx2, reply_mode="auto")
check("auto 無標記 → 送普通訊息",
      trigger.channel.sent and trigger.channel.sent[0][0] == "哈囉",
      str(trigger.channel.sent))
check("auto 無標記 → 沒有引用觸發訊息", not trigger.replies, str(trigger.replies))

trigger, a, b = run_dispatch("[REPLY]\n哈囉", ctx2, reply_mode="auto")
check("auto 有標記 → 引用觸發訊息",
      trigger.replies and trigger.replies[0][0] == "哈囉",
      str(trigger.replies))
check("標記已從正文移除", not trigger.channel.sent, str(trigger.channel.sent))

trigger, a, b = run_dispatch("[TO:甲]\n[REPLY]\n給甲", ctx2, reply_mode="never")
check("[TO:] 一律回覆對方（不受 never 影響）",
      a.replies and a.replies[0][0] == "給甲", str(a.replies))

print("[7] 模型結構出錯時的修補")
check("全形字母 TO",
      main.split_to_segments("[ＴＯ：甲]\n嗨") == [("甲", "嗨")],
      str(main.split_to_segments("[ＴＯ：甲]\n嗨")))
check("空 [TO:] 不留在正文",
      main.strip_reply_markers("[TO:]\n嗨") == "嗨",
      repr(main.strip_reply_markers("[TO:]\n嗨")))
check("空【TO：】不留在正文",
      main.strip_reply_markers("【TO：】嗨") == "嗨")
trail = main.split_to_segments("給甲的內容\n[TO:甲]")
check("TO 寫在正文後面 → 改掛到甲", trail == [("甲", "給甲的內容")], str(trail))
check("[REPLY：說明] 也認得", main.wants_reply("[REPLY：上面那則]\n嗨", "auto") is True)
check("[REPLY：說明] 會被移除",
      main.strip_reply_markers("[REPLY：上面那則]\n嗨") == "嗨")
check("【普通訊息】＝不引用",
      main.wants_reply("【普通訊息】\n嗨", "auto") is False)
check("只有標記 → 沒有正文", main.strip_reply_markers("[REPLY]") == "")

trigger, a, b = run_dispatch("[TO:甲、乙]\n你們好", ctx2, reply_mode="always")
check("多人標籤 → 回覆第一個找得到的",
      a.replies and a.replies[0][0] == "你們好", str(a.replies))
check("多人標籤不會重複回乙", not b.replies, str(b.replies))

trigger, a, b = run_dispatch("[REPLY]\n[NO_REPLY]", ctx2, reply_mode="auto")
check("全是標記 → 不送空訊息",
      not trigger.replies and not trigger.channel.sent,
      f"{trigger.replies} / {trigger.channel.sent}")

trigger, a, b = run_dispatch("給甲的內容\n[TO:甲]", ctx2, reply_mode="always")
check("TO 在後面 → 仍回覆到甲",
      a.replies and a.replies[0][0] == "給甲的內容", str(a.replies))

print("[8] fetch 失敗時不丟內容")
class BrokenChannel(FakeChannel):
    async def fetch_message(self, mid):
        raise RuntimeError("boom")


broken_target = FakeMsg("甲", 101)
broken_channel = BrokenChannel([broken_target])
trigger2 = FakeMsg("丙", 999, channel=broken_channel)
asyncio.run(main.dispatch_reply(
    trigger2, "[TO:甲]\n給甲", None,
    [{"role": "user", "content": "[甲]: hi", "msg_id": 101, "name": "甲"}],
))
check("取訊息失敗 → 退回回覆觸發訊息", trigger2.replies and trigger2.replies[0][0] == "給甲",
      str(trigger2.replies))

print("[9] 文風自檢不破壞分段")
_orig_call_llm = main.call_llm


async def _fake_call_llm(effort, msgs, **kwargs):
    prompt = msgs[1]["content"]
    body = prompt.split("【角色回覆】", 1)[1].split("請直接輸出", 1)[0].strip()
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=body))]
    )


main.call_llm = _fake_call_llm
raw = "[TO:甲]\n給甲\n[TO:乙]\n給乙"
out = asyncio.run(main.style_self_check(
    raw, "in_character", False, {"expression_prefs": [], "banned_words": []},
))
check("逐段送檢後仍能切出兩人",
      main.split_to_segments(out) == [("甲", "給甲"), ("乙", "給乙")], out)
main.call_llm = _orig_call_llm

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
