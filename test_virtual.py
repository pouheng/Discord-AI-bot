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

print("=" * 60)
print("ALL TESTS PASSED")
