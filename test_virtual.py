"""虛擬請求測試 — 驗證 format_supplement + 時間感知 + supplement 格式"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import main

print("=" * 60)

# ── Test 1: format_supplement 新格式 ──
new_format = [
    {"category": "世界觀", "topic": "魔法", "content": "這世界的人都會覺醒1種個人魔法", "note": "直接相關"},
    {"category": "角色能力", "topic": "機械義手", "content": "澪的右手為機械義手"},
    {"category": "事件", "topic": "凌澪型態", "content": "吳神月提議合體", "note": ""},
]
result = main.format_supplement(new_format)
print("[Test 1] 新格式 (JSON array)")
print(result)
assert "- [世界觀] 魔法" in result, "FAIL: category+topic 格式"
assert "※直接相關" in result, "FAIL: note 格式"
print("PASS\n")

# ── Test 2: format_supplement 舊格式向後相容 ──
old_format = "<supplement>\n[條目]\n- [世界觀] 魔法：這世界的人都會覺醒1種個人魔法\n- [角色能力] 機械義手：澪的右手為機械義手\n[註釋]\n# 直接相關\n</supplement>"
result2 = main.format_supplement(old_format)
print("[Test 2] 舊格式 (XML fallback)")
print(result2)
assert "[條目]" not in result2, "FAIL: [條目] 應被移除"
assert "</supplement>" not in result2, "FAIL: </supplement> 應被移除"
assert "- [世界觀] 魔法" in result2, "FAIL: 內容應保留"
print("PASS\n")

# ── Test 3: 空值測試 ──
print("[Test 3] 空值測試")
assert main.format_supplement(None) == "", "FAIL: None"
assert main.format_supplement("") == "", "FAIL: empty str"
assert main.format_supplement([]) == "", "FAIL: empty list"
assert main.format_supplement([{}]) == "", "FAIL: empty dict in list"
print("PASS\n")

# ── Test 4: 時間感知規則 ──
with open("main.py", "r", encoding="utf-8") as f:
    content = f.read()
count = content.count("時間感知")
print(f"[Test 4] 時間感知規則: 出現 {count} 次")
assert count >= 2, "FAIL: 應出現至少 2 次 (OOC + IC)"
today_in_prompt = 'datetime.datetime.now().strftime("%Y-%m-%d")' in content
assert today_in_prompt, "FAIL: 日期注入缺失"
print("PASS\n")

# ── Test 5: Phase 1 prompt 格式 ──
has_json_array = "supplement：JSON 陣列" in content
print(f"[Test 5] Phase 1 supplement 格式")
assert has_json_array, "FAIL: JSON 陣列格式說明缺失"
print("PASS\n")

# ── Test 6: Phase 3 名稱混淆防禦規則 ──
has_name_guard = "名稱混淆防禦" in content
print(f"[Test 6] Phase 3 名稱混淆防禦規則: {'PASS' if has_name_guard else 'FAIL'}")
assert has_name_guard, "FAIL: 名稱混淆防禦規則缺失"

# ── Test 7: supplement 陣列中 category 欄位出現在 prompt ──
has_category = '"category": "分類標籤"' in content
assert has_category, "FAIL: category 欄位說明缺失"
print("[Test 7] category 欄位規範: PASS\n")

# ── Test 8: 格式化後的 supplement 可正確注入 prompt 模板 ──
mock_supplement = [
    {"category": "世界觀", "topic": "打架", "content": "不成文的敘事規則", "note": "可導向實戰"},
    {"category": "角色能力", "topic": "數據干涉", "content": "可輔助凌空瞄準"},
]
formatted = main.format_supplement(mock_supplement)
assert len(formatted) > 0, "FAIL: 格式化後為空"
print(f"[Test 8] 格式化後 supplement ({len(formatted)} chars):")
print(formatted)
print("PASS\n")

# ── Test 9: 回覆起始引導（priming trick）存在 ──
has_ooc_prime = "喔好，收到了，我現在會開始思考" in content
has_ic_prime = "開始回應：" in content
print(f"[Test 9] 回覆起始引導")
print(f"  OOC: {'PASS' if has_ooc_prime else 'FAIL'}")
print(f"  IC:  {'PASS' if has_ic_prime else 'FAIL'}")
assert has_ooc_prime and has_ic_prime, "FAIL: 回覆起始引導缺失"

# ── Test 10: 禁止自問自答 / 避免重複結構 在動態資料之前 ──
dyn_marker_pos = content.find("# ── 動態資料區")
ban_pos = content.find("【⚠️ 禁止自問自答】")
avd_pos = content.find("【⚠️ 避免重複結構】")
print(f"[Test 10] 禁止自問自答 / 避免重複結構 在動態資料之前")
print(f"  禁止自問自答在動態資料之前: {'PASS' if ban_pos < dyn_marker_pos else 'FAIL'}")
print(f"  避免重複結構在動態資料之前: {'PASS' if avd_pos < dyn_marker_pos else 'FAIL'}")
assert ban_pos < dyn_marker_pos, "FAIL: 禁止自問自答仍在動態資料之後"
assert avd_pos < dyn_marker_pos, "FAIL: 避免重複結構仍在動態資料之後"

# ── Test 11: OOC safety 不含 NSFW 規則 ──
safety_ooc_start = content.find("【安全規則 - 嚴格遵守】\n")
safety_ooc_end = content.find("【本伺服器規則】", safety_ooc_start)
safety_ooc_block = content[safety_ooc_start:safety_ooc_end] if safety_ooc_end > 0 else content[safety_ooc_start:safety_ooc_start+500]
has_nsfw_ooc = "色情" in safety_ooc_block
print(f"[Test 11] OOC safety 不含 NSFW 規則: {'PASS' if not has_nsfw_ooc else 'FAIL'}")
assert not has_nsfw_ooc, "FAIL: OOC 仍包含 NSFW 規則"

# ── Test 12: OOC 表情符號含顏文字說明 ──
emoji_block_start = content.find("【可用表情符號】")
if emoji_block_start > 0:
    emoji_clarification = "顏文字" in content[emoji_block_start:emoji_block_start+200]
    print(f"[Test 12] OOC 表情符號含顏文字說明: {'PASS' if emoji_clarification else 'FAIL (無自訂表情)'}")
else:
    print("[Test 12] OOC 表情符號: 無自訂表情 (skip)")

# ── Test 13: /db embed 顯示 type 標籤 ──
has_type_tag = 'type_tag = f" 【{mtype}】" if mtype else ""' in content
print(f"[Test 13] /db 顯示 type 標籤: {'PASS' if has_type_tag else 'FAIL'}")
assert has_type_tag, "FAIL: type 標籤格式缺失"

# ── Test 14: /db footer 警告 ──
footer_warning = "標記為【玩笑】的記憶可能在整理時被清除"
has_warning_main = footer_warning in content
has_warning_auto = footer_warning in open("autopilot.py", encoding="utf-8").read()
print(f"[Test 14] /db + /dbnpc footer 警告")
print(f"  main.py:      {'PASS' if has_warning_main else 'FAIL'}")
print(f"  autopilot.py: {'PASS' if has_warning_auto else 'FAIL'}")
assert has_warning_main and has_warning_auto, "FAIL: footer 警告缺失"

print("\n" + "=" * 60)
print("ALL TESTS PASSED")
