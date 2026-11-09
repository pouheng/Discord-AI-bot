"""離線測試：時間感知（日期分隔）相關工具。

對應功能：OOC 時在歷史對話中插入「日期分隔 / 現在」訊息，
讓模型能分辨昨天的訊息與今天的訊息。
"""

import datetime
import os
import sys

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


def msg(ts, role="user", content="hi", msg_id=1):
    return {"role": role, "content": content, "msg_id": msg_id, "ts": ts}


print("[1] 日期標籤與相對天數")
d = datetime.date(2026, 11, 3)  # 週二
check("週幾", main._day_label(d) == "2026-11-03（週二）", main._day_label(d))
check("今天", main._relative_day_label(d, d) == "今天")
check("昨天", main._relative_day_label(datetime.date(2026, 11, 2), d) == "昨天")
check("前天", main._relative_day_label(datetime.date(2026, 11, 1), d) == "前天")
check("N 天前", main._relative_day_label(datetime.date(2026, 10, 30), d) == "4 天前")

print("[2] 間隔文字")
gap = datetime.timedelta(days=1, hours=3, minutes=20)
check("天/小時", main._format_gap(gap) == "1 天 3 小時", main._format_gap(gap))
check("只有分鐘", main._format_gap(datetime.timedelta(minutes=45)) == "45 分鐘")
check("不到一分鐘", main._format_gap(datetime.timedelta(seconds=30)) == "")

print("[3] 跨日插入日期分隔")
ctx = [
    msg("2026-11-01 10:00:00", msg_id=1),
    msg("2026-11-01 11:00:00", msg_id=2),
    msg("2026-11-02 09:00:00", msg_id=3),
    msg("2026-11-03 08:30:00", msg_id=4),
]
out = main.insert_day_dividers(ctx, now=datetime.datetime(2026, 11, 3, 12, 0),
                              gap_hours=0)
dividers = [m for m in out if m.get("role") == "system"]
check("3 個分隔（起點 + 兩次跨日）", len(dividers) == 3, str(len(dividers)))
check("歷史起點標記", "【歷史起點】" in dividers[0]["content"])
check("起點標記 2 天前（前天）", "前天" in dividers[0]["content"], dividers[0]["content"])
check("第二個分隔＝昨天", "昨天" in dividers[1]["content"], dividers[1]["content"])
check("第三個分隔＝今天", "今天" in dividers[2]["content"], dividers[2]["content"])
check("分隔標明以上/以下", "以上" in dividers[1]["content"] and "以下" in dividers[1]["content"])
check("原始訊息全數保留", len([m for m in out if m["role"] != "system"]) == 4)
check("跨日分隔帶間隔", "距上一則" in dividers[1]["content"], dividers[1]["content"])
check("分隔帶第一則時間", "10:00" in dividers[0]["content"], dividers[0]["content"])
check("新的一天帶時間", "09:00" in dividers[1]["content"], dividers[1]["content"])

print("[4] 同日長間隔")
ctx2 = [
    msg("2026-11-03 01:00:00", msg_id=1),
    msg("2026-11-03 11:00:00", msg_id=2),
]
out2 = main.insert_day_dividers(ctx2, now=datetime.datetime(2026, 11, 3, 12, 0),
                                gap_hours=6)
sys_msgs = [m for m in out2 if m.get("role") == "system"]
check("起點 + 時間分隔共 2 條", len(sys_msgs) == 2, str(len(sys_msgs)))
check("時間分隔文字", "【時間分隔】" in sys_msgs[1]["content"], sys_msgs[1]["content"])
check("時間分隔含 10 小時", "10 小時" in sys_msgs[1]["content"], sys_msgs[1]["content"])
check("時間分隔帶時間", "11:00" in sys_msgs[1]["content"], sys_msgs[1]["content"])

out3 = main.insert_day_dividers(ctx2, now=datetime.datetime(2026, 11, 3, 12, 0),
                                gap_hours=12)
check("閾值未到不插入", len([m for m in out3 if m.get("role") == "system"]) == 1)

print("[5] 沒有時間的訊息")
ctx3 = [{"role": "user", "content": "無時間", "msg_id": 0}]
out4 = main.insert_day_dividers(ctx3, now=datetime.datetime(2026, 11, 3, 12, 0))
check("無 ts 原樣保留", out4 == ctx3, str(out4))
check("空 context 不爆", main.insert_day_dividers([]) == [])

print("[6] 現在分隔")
ctx4 = [msg("2026-11-02 20:00:00", msg_id=1)]
now_text = main._now_divider_text(ctx4, now=datetime.datetime(2026, 11, 3, 21, 5))
check("含現在時間", "2026-11-03（週二） 21:05" in now_text, now_text)
check("標記現在", "【現在】" in now_text)
check("距上一則 1 天", "距上一則 1 天" in now_text, now_text)

print("[7] 設定開關")
pcfg = {"time_awareness": {"enabled": True, "ooc_only": True}}
check("OOC 生效", main._time_awareness_for(pcfg, "out_of_character") is not None)
check("IC 不生效", main._time_awareness_for(pcfg, "in_character") is None)
check("關閉後全部不生效",
      main._time_awareness_for({"time_awareness": {"enabled": False}},
                               "out_of_character") is None)
check("ooc_only=false 時 IC 也生效",
      main._time_awareness_for(
          {"time_awareness": {"ooc_only": False}}, "in_character") is not None)
check("沒有設定時預設啟用（OOC）",
      main._time_awareness_for({}, "out_of_character") is not None)
check("設定壞掉不爆",
      main._time_awareness_for({"time_awareness": "bad"}, "out_of_character") is not None)

print("[8] 解析時間")
ok = main._parse_ts("2026-11-03 08:30:00")
check("解析字串", ok == datetime.datetime(2026, 11, 3, 8, 30), str(ok))
check("解析空值", main._parse_ts("") is None)
check("解析壞值", main._parse_ts("not-a-date") is None)
check("解析 datetime", main._parse_ts(datetime.datetime(2026, 11, 3, 8, 30))
      == datetime.datetime(2026, 11, 3, 8, 30))

print(f"\n{SUCCESS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
