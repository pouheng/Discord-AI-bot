# Clash 多打一血條系統（Multi-vs-One HP Ledger）

Clash 系統 v2 支援「多位玩家 vs 單一反派」，並為反派建立自動、可視、最長半年的暫時性血條。血條歸零 = 打敗或擊退。核心問題是：**如何在不增加玩家負擔的前提下，把敘事表現嚴謹地換算成血量扣減，且不讓 AI 直接寫數值（信任崩壞）**。

## Considered Options

### verdict→delta 純函數（採用）

每位玩家對同一反派獨立計算 `r = Atk_BP / Villain_Guard`，機械換算扣血：

```
r ≥ 2.0        → 碾壓：2B = 20 ticks
1.0 < r ≤ 2.0  → 壓制：1B = 10 ticks
0.9 ≤ r ≤ 1.0  → 僵持：chip = 0.2B = 2 ticks
r < 0.9        → 無效：0 ticks
```

- 敘事分數**永不進入公式**；Phase A 只判斷「誰打了誰、結果像什麼等級」，扣多少血由比值機械決定。
- **優點**：血條是「計算值」而非「估計值」；錯誤來源從「整段文本理解」縮小為「事件是否被漏抓」；審計可複現（同 r → 同 delta）。
- **代價**：需要穩定的 Villain_Guard 與階級參數（皆凍結於 battle_id）。

### guard 直接寫入反派設定檔（捨棄）

由 AI/管理員直接把防禦閾值寫進角色設定，當作可調參數。

- **捨棄原因**：閾值成為自由輸入 → 無法驗證、無法複現，重蹈「AI 隨意給值」的覆轍（與 Weapon_Base 機械取值同一動機）。改為**純計算**：`Villain_Guard = stat_constitution_hidden × Domain_Mod`，全部來自既有欄位，零新增輸入。

### AI 直接寫血條數值（捨棄）

生成器直接輸出扣血量並寫入 DB。

- **捨棄原因**：信任崩壞——模型既出事件又出數值，無法區分「誤讀」與「亂給」。改為**雙軌制**：生成器只提 `combat.events[]` 提案（含信心值），**Ledger 驗證後才寫入**，模型永不直接寫血條；解析器只補空隙、不覆寫高信心事件。

## 關鍵機制

- **Ticks 儲存、B 顯示**：`TICKS_PER_B = 10`、`CHIP_TICKS = 2`。所有數值以 ticks 儲存，B 只是血條進度條的顯示單位。B 在節點數除法中消去（MaxHP/delta = 24B/(k×B) = 24/k），因此只有 Villain_Guard、Cap_tier、MaxHP 承載數值。
- **聚合與去重**：一次對撞 = 一個節點；同節點多位玩家各自算 delta 後聚合成單一事件，扣減 `min(Σdelta, Cap_tier)`；`event_id` + `(battle_id, node_id)` 去重；剩餘血量截斷至 0。**破防節點只聚合破防玩家**（見補充共識 A1）。
- **chip 無法擊殺**：chip 最低只到擊退線（MaxHP 的 20%，`retreat_floor_ticks`），不跨線歸零；僅 碾壓/壓制 可跨線扣至 0。雙軌結局：chip 路徑 → 停在擊退線（`active` + `at_retreat_floor`，**不自動終局**，見補充共識 A2）；碾壓/壓制路徑 → 歸零 → 打敗/封印。
- **凍結粒度 = battle_id**：「不可重新校準」指同一場 battle_id 內；Villain_Guard、Cap_tier、MaxHP 於建立時凍結，`current_hp_ticks` 是唯一可變動欄位。新章節再登場 = 新 battle_id，血量由 `retreat_snapshot_ticks` 續接。
- **自動偵測反派 `dynamic_difficulty = 0`**：guard 純計算，不進 `calibrate_roster` 校準流程（規避 spec.md:741 拒絕規則）；僅管理員註冊的篇章 Boss 可為 1。
- **事件信心門檻**：≥ 0.90 自動提交；0.70 ~ 0.89 標記 pending（等待同 battle_id 下一結算節點）；< 0.70 丟棄。必填欄位齊全是硬門檻。
- **TTL 雙軌**：絕對上限 = 建立時間 + 180 天（硬刪除）；軟性展期 = 最後一次更新 + 滑動延長，永不超過絕對上限。
- **只有反派有血條**：玩家受傷是純敘事，無玩家 HP pool（單向）。
- **留手不影響威力**：玩家意圖只影響 碾壓→壓制 降級，不進 BP 公式。

## 補充共識（A1 / A2，實作落地）

### A1 — 破防節點不給 chip

同一節點多位玩家各自算 `r` 後聚合時，**先判定該節點是否有任何玩家破防（`r > 1.0`，即 delta ≥ 10 ticks）**。

- 有破防 → 節點總扣減**只累加破防玩家的 delta**；僵持（chip 2）／無效（0）玩家的扣減**不加入**。正確順序是先算 `has_breach`，再決定要不要套 chip。
- 全員僵持／無效（無破防）→ 才走 chip 路徑，一次性扣 `CHIP_TICKS = 2`。
- **動機**：避免「一人破防放大、其他人僵持的 2 ticks 被順便灌入」而高估總傷害。

### A2 — 擊退由中之人宣告，不自動終局

chip 路徑扣到擊退線（`retreat_floor_ticks`）時，血量停在線上、`outcome = active` 且 `at_retreat_floor = True`——**引擎不自動寫 `retreated`**。

- `retreated` 只在**中之人（真實玩家）於劇情中宣告擊退**後才寫入；此時才把 `current_hp_ticks` 存入 `retreat_snapshot_ticks`。
- 只有 碾壓／壓制 路徑跨過擊退線扣至 0 才會自動 `defeated`。
- **動機**：擊退是敘事決定，不是數值下限的副作用；避免系統替玩家「代打」結局。

## Consequences

- `collide_clash_battles` / `collide_clash_nodes` 兩張新表（ticks-only，B 不入 schema）
- `collide_engine.py` 新增 `verdict_to_delta` 純函數 + 聚合/截斷
- `collide_pipeline.py` Phase A 產出 `combat.events[]` 提案；Phase B 逐玩家計算；Ledger 驗證寫入
- `collide_parser.py` 反派註冊模板（guard、max_hp_ticks、cap_ticks）；自動偵測路徑強制 `dynamic_difficulty = 0`
- `collide_ui.py` 訊息文字血條進度條 + 一行理由
- `collide_battles.remaining_damage` 標記 deprecated，不再新增寫入（避免第二個真相來源）
- 階級常數（雜兵 50 / 重點 120 / Boss 240 ticks、cap 25 / 40 / 50 ticks）進程式碼常數，公開可查
