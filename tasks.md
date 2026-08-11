# 招式對撞系統 — 任務分解（tasks.md）

來源規格：`spec.md`（v1.0 草案）
分解原則：一個任務 = 一個可獨立審查的交付物；單任務 ≤ 4 小時；依賴明確標示；依「資料層 → UI → 整合」排序。

---

## 已完成基線（不在本清單）

| 區塊 | 檔案 | 狀態 |
|---|---|---|
| 資料層（部分） | `collide_db.py`（characters/abilities/skills/domains + world_lore 鏡像 + make_db_lookup） | ✅ |
| 寫入解析 | `collide_parser.py`（write_ability/skill/character/domain/entries + 硬驗證） | ✅ |
| 計算引擎 | `collide_engine.py`（隱藏分數/五公式/四階判定/結界融合/Domain_BP） | ✅ |
| 對撞流程 | `collide_pipeline.py`（Phase A/B/C + run_clash） | ✅ |
| 樓層成長資料層 | `collide_growth.py`（floors/growth_log/action_log/ability_usage/future_log 表 + upsert） | ✅ |
| 寫入指令 | `collide_write_cmds.py`（/writechar /writeability /writeskill /writedomain /writebatch） | ✅ |
| 模擬器 | `make_simulator_html.py` + `simulator_template.html` + `simulator_engine.js` → `simulator.html` | ✅ |
| 規則與提示詞 | `collide_rules.py`（WRITE_RULES / CLASH_PHASE_* / CALIBRATE_SYSTEM / FORMULAS / GROWTH） | ✅ |

---

## 資料層

### T1. collide_battles 對撞紀錄表＋生命週期查詢
- **描述**：新增 `collide_battles` 表（spec 5.4 欄位：attacker/defender/atk_output/def_output/element_mod/verdict/result/remaining_damage/domain_fusion/chain_of_thought/confirmed/status/undone_by/undone_reason 等），並提供 insert/get/list、status 變更（pending→active→undone）與撤銷欄位填寫的查詢函式；`collide_db.init_schema` 一併建立。
- **依賴**：無（資料層先行）。
- **複雜度**：S
- **驗收**：`python collide_db.py` quick_test 新增通過：可寫入一筆 pending、更新為 active、再更新為 undone 並寫入 undone_reason；查詢按 status 過濾正確；schema 含 UNIQUE 索引不衝突。

### T2. 樓層成長 apply_growth 補齊溯源欄位（battle_id）
- **描述**：`collide_growth.apply_growth` 目前未寫 battle_id 關聯。補齊：`upsert_growth` 支援 battle_id 參數；成長 log 從「clash 來源的 collide_action_log / collide_ability_usage」可反查該場 battle_id 並填入。
- **依賴**：T1（collide_battles 存在才可關聯）。
- **複雜度**：S
- **驗收**：mock 測試：一筆 clash 來源成長寫入後 `battle_id` 正確填值；非 clash 來源留空；`growth_log_for_floor` 回傳含 battle_id。

---

## UI / 模擬器

### T3. 模擬器對撞結果面板顯示結界張開與融合狀態
- **描述**：`simulator.html` 的 Phase B 結果區（renderPhaseBResult / mechanicalVerify）在 `action_type=domain` 時顯示 Domain_BP、覺醒、Domain_Mod、融合/覆蓋判定；與 Phase B 提示詞輸出的 `domain_fusion` / `domain_override` 對照。
- **依賴**：無（純 UI，引擎已支援）。
- **複雜度**：S
- **驗收**：於 harness 貼入「神月龍王禁地 vs 甲地獄水牢」Phase A/B，結果面板顯示「結界交疊（融合）」且不含「徒手/普通攻擊」字樣；既有回歸測試不破壞。

### T4. 模擬器 Phase C 結果面板顯示結界融合資訊
- **描述**：`renderPhaseBResult`（template:970-982）目前只顯示 verdict/summary/BP/projection/reason，未顯示結界資訊。補上：`domain_fusion=true` 時於結果面板顯示「結界交疊（融合）」標記與融合專屬摘要；Phase C 文風提示詞已含「結界融合景象」引導（collide_rules.py:564），不需再加提示詞，僅需 UI 呈現。
- **依賴**：T3。
- **複雜度**：S
- **驗收**：harness 貼入 `domain_fusion=true` 的 Phase B JSON，`renderPhaseBResult` 輸出含「結界」或「融合」字樣；`domain_fusion=false` 時不顯示。

---

## 整合（Discord 指令）

### T5. collide_cmds.py：/clash 指令（Phase A→B→C + 草稿發布）
- **描述**：實作 `collide_cmds.py`。`/clash <討論串?>`：讀取討論串尾部（近 50 則）→ `run_clash` → 以 pending 寫入 `collide_battles` → 發布含思維鏈/計算過程/結果到討論串，並附「確認 / 重試 / 取消」View 按鈕。
  **前置缺陷處理（先修後作）**：`collide_pipeline.compute_sides._one` 依 `skill.get("kind")` 選公式路徑，但 `collide_db.get_skill_named` 回傳的 row 沒有 `kind` 欄位，導致 `manamelee` 分支永遠不可達、`weapon_base`/`mp_invested`/`element_mod`/`proficiency_mod` 全部吃預設值。`/clash` 前須在 `_resolve_skill_or_domain` 依 `skill_type`/`element`/`formula` 推導並附加 `kind` 與必要 Mod 欄位（見驗證報告問題 2）。
- **依賴**：T1（寫入 `collide_battles` 即可，不需 T2；`battle_id` 成長關聯由 T8/T7 使用）。
- **複雜度**：M
- **驗收**：mock db_lookup + 假 LLM 回傳，`/clash` 呼叫後 `collide_battles` 新增一筆 status=pending；討論串訊息含按鈕；無 DEEPSEEK 金鑰時回報「AI 服務未啟用」；具 `skill_type = 通常魔法` + `element` 的招式走 `compute_manamelee_bp` 而非 `compute_atk_bp`（`kind` 缺陷已修）。

### T6. 對撞結果按鈕權限與二次確認（View 回調）
- **描述**：確認/重試/取消按鈕 custom_id 編碼發送者 user_id；View 回調校驗 `interaction.user.id == 發送者`，非發送者回「只有發送者可以確認此結果」並忽略；確認帶二次確認（modal 或 followup 確認）；重試直接重算不寫 log；取消清除 pending。
- **依賴**：T5。
- **複雜度**：M
- **驗收**：單元測試（fake interaction）：非發送者點擊 → 回覆拒絕訊息且資料不變；發送者確認 → status=active 並觸發 Phase C 文風；取消 → pending 清除。

### T7. /clash undo 撤銷＋成長追溯清單
- **描述**：`/clash undo <battle_id>`：僅發送者或管理員可執行；將 active 改為 undone + 公告；依 battle_id 列出關聯 `collide_growth_log`（成長追溯清單）。
- **依賴**：T2、T6。
- **複雜度**：M
- **驗收**：mock 測試：撤銷後 status=undone、undone_by/reason 填寫、頻道公告訊息含 battle_id 與成長清單；非發送者/一般玩家被拒；撤銷不自動回滾數值（僅列清單）。

### T8. /grow 樓層總結成長指令
- **描述**：`/grow <討論串>`：確認 `collide_floors.summary_generated`（首次=總結、之後=更新/重跑）；LLM 依 GROWTH_RULES_TEXT 掃描討論串判斷能力使用並寫入 collide_ability_usage；依 floor_event_id 防呆套用數值變動寫入 collide_growth_log（不重複疊加）。
- **依賴**：T2。
- **複雜度**：L
- **驗收**：mock 測試：首次 grow 產生成長 log 且 floor.summary_generated=1；第二次 grow 對同一 floor_event_id 更新不新增（UNIQUE 不違反）；能力使用寫入 ability_usage（source=thread_scan）；clash 來源成長帶 battle_id。

### T9. /clash rule、/clash log、/clash sheet 指令
- **描述**：`/clash rule` 輸出 RULES_TEXT；`/clash log <討論串>` 輸出該樓層全部 collide_action_log 與 collide_growth_log（old→new→delta）；`/clash sheet` 輸出公開試算表連結（連結來自 config `google_sheets.spreadsheet_id`，非 T10）。
- **依賴**：T2（growth_log 含 battle_id 供 log 顯示）；T13（rule 內容資料源）。
- **複雜度**：S
- **驗收**：三指令於 bot 註冊成功；rule 內容含五公式與武器判定表；log 列出指定樓層紀錄；sheet 回覆連結（無設定時回報「尚未設定」）。

### T10. main.py 註冊 collide_cmds
- **描述**：在 `main.py` 的 collide_write_cmds 註冊旁加入 `collide_cmds.register_commands(bot)`（依既有 pattern）。
- **依賴**：T5（collide_cmds.py 模組與 register_commands 存在即可 import；T8/T9 的指令在同模組，建議一併完成後再註冊以免註冊後缺指令）。
- **複雜度**：S
- **驗收**：`python -m py_compile main.py collide_cmds.py` 通過；啟動時無 import 錯誤。

---

## Sheets 同步

### T11. sheets_sync.py：OAuth + 全表同步
- **描述**：實作 `sheets_sync.py`：credentials.json/token.json OAuth、單一試算表多工作表（角色檔案/招式/結界/對撞紀錄）重寫式同步；config 新增 google_sheets 區塊。
- **依賴**：T1（對撞紀錄表）。需使用者提供 credentials.json 與 spreadsheet_id（spec M0）。
- **複雜度**：M
- **驗收**：無 credentials.json 時明確回報設定指引；有 mock 憑證時對 sheets API 的寫入呼叫參數正確（工作表名、列標題、行數）。

### T12. 批量寫入緩衝（5 秒 coalescing）
- **描述**：每次資料寫入後標記工作表 dirty 並啟動 5 秒 trailing debounce，視窗結束一次重寫 dirty 工作表；同步中又變動 → 再標記重跑。
- **依賴**：T11。
- **複雜度**：M
- **驗收**：單元測試（假 clock）：一次連續 10 筆寫入僅觸發 1 次 API 呼叫；跨視窗新變動觸發第 2 次；同步失敗後 dirty 保留並在下次重試。

---

## 文件 / 公開

### T13. rules_collide 文件與 /clash rule 資料源
- **描述**：產出 `rules.md` / `rules_collide.json`（五公式、武器判定表、四階判定、結界融合規則、MP_Invested、成長規範），與 collide_rules.py 內容一致；作為 `/clash rule` 的資料來源。
- **依賴**：無（可與 T9 平行，T9 引用其內容）。
- **複雜度**：S
- **驗收**：`rules_collide.json` 可被 JSON parse；其公式與 collide_rules.FORMULAS_TEXT 一致（比對測試）；`/clash rule` 顯示內容與文件一致。

---

## 依賴圖（摘要）

```
T1 ─→ T5 ─→ T6 ─→ T7
T1 ─→ T2 ─→ T8 ────────────┬──→ T10
          T2 ─→ T9 ────────┘
T3（無依賴）→ T4
T1 ─→ T11 ─→ T12
T13（無依賴，供 T9 引用內容）
```

審查順序建議：T1 → T2 → T3/T4（模擬器）→ T5 → T6 → T8 → T7 → T9 → T11/T12 → T10 → T13。
（T5 只依 T1；T9 依 T2 與 T13；T10 依 T5 並建議 T8/T9 完成後註冊。）
