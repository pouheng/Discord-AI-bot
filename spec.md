# 澪（Miō）— Discord AI RP Bot 架構規格書

## 1. Background

澪是一個 Discord 角色扮演 Bot，運行於一個多人 RP 伺服器中。Bot 扮演名為「澪」的角色，與玩家們在同一個虛擬世界中互動。玩家透過 Discord 頻道與 Bot 對話，Bot 會以角色身份回應，同時自動管理記憶、劇情追蹤、NPC 托管等工作。

**技術棧**：
- LLM：DeepSeek V4 Flash（智商較低，需要特殊 prompt 工程）
- 框架：Discord.py
- 資料庫：SQLite（本地）
- 語言：主要中文，支援英文詞彙

**核心挑戰**：
DeepSeek V4 Flash 的推理能力有限，需要透過分階段處理、結構化 prompt、回覆起始引導等技巧來補償模型智商不足的問題。

---

## 2. Goals

### 2.1 核心目標

| 目標 | 說明 | 成功標準 |
|------|------|----------|
| **自然角色扮演** | Bot 的回覆要像真人玩家一樣自然 | 玩家覺得跟 Bot 互動就像跟真人 RP |
| **準確記憶管理** | 記住所有重要劇情和角色設定 | 不會忘記或搞混角色設定和劇情 |
| **穩定運行** | 長時間運行不會 crash | 所有錯誤都被捕捉並記錄 |

### 2.2 次要目標

- **雙重身份**：Bot 既是「工具」（記住事情）也是「陪伴」（自然對話）
- **玩笑/真實區分**：能分辨認真內容和玩笑話
- **自動化管理**：減少人工維護劇情和記憶的負擔

---

## 3. Non-Goals（明確排除）

| 排除項目 | 說明 |
|----------|------|
| **NSFW 主動發起** | Bot 不主動發起色情內容，但不拒絕，轉用健康方式呈現 |
| **多語言支援** | 主要中文，英文詞彙可理解但不主動切換 |
| **多伺服器部署** | 目前只在一個伺服器使用 |
| **跨平台整合** | 只在 Discord 上運作 |

---

## 4. User Stories

### 4.1 玩家视角

```
作為一個 RP 玩家，
我希望能跟 Bot 自然對話，
就像跟真人玩家 RP 一樣，
這樣我的沉浸感才不會被打破。

作為一個 RP 玩家，
我希望 Bot 能記住我之前說過的事情，
這樣我不用每次都重複我的角色設定。

作為一個 RP 玩家，
我希望 Bot 能分辨我什麼時候在開玩笑、什麼時候是認真的，
這樣它才不會把我的玩笑話當成正式設定。

作為一個 RP 玩家，
我希望 Bot 能自動追蹤劇情發展，
這樣我不用擔心忘記重要的劇情細節。
```

### 4.2 管理者视角

```
作為一個伺服器管理者，
我希望 Bot 能穩定運行，不會頻繁 crash，
這樣玩家才有好的體驗。

作為一個伺服器管理者，
我希望所有錯誤都被記錄下來，
這樣我才能快速排查問題。

作為一個伺服器管理者，
我希望 Bot 能自動管理記憶，
這樣我不用花太多時間在維護上。
```

---

## 5. Technical Approach

### 5.1 核心架構：Phase 1/2/3 Pipeline

```
使用者訊息
    ↓
┌─────────────────────┐
│  Phase 1: 召回分析   │ ← 從 SQLite 召回相關記憶
│  (DeepSeek V4 Flash) │    輸出: lore_text (世界觀補充)
└─────────────────────┘
    ↓
┌─────────────────────┐
│  Phase 2: 角色回覆   │ ← 生成自然的角色對話
│  (DeepSeek V4 Flash) │    輸出: <planning> + 正文
└─────────────────────┘
    ↓
┌─────────────────────┐
│  Phase 3: 記憶寫入   │ ← 檢測新資訊並寫入資料庫
│  (DeepSeek V4 Flash) │    輸出: learn/edit_self/forget actions
└─────────────────────┘
    ↓
Bot 回覆 + 記憶更新
```

### 5.2 Phase 1：記憶召回

**目的**：從 SQLite 資料庫中召回與當前對話最相關的記憶，作為 Phase 2 的上下文。

**關鍵設計**：
- 輸出格式：JSON array（從舊的 XML-in-string 格式重構）
- 每筆包含：category（分類）、topic（主題）、content（內容）、note（備註）
- 至少召回 6 條相關記憶
- 格式化工具：`format_supplement()` helper function

**回覆起始引導（Priming Trick）**：
Phase 2 prompt 結尾使用「喔好，收到了，我現在會開始思考、開始 planning：」讓 LLM 自然延續格式，而非用指令語氣強制。

### 5.3 Phase 2：角色回覆生成

**目的**：以角色身份生成自然的對話回覆。

**關鍵設計**：
- 靜態指令在上、動態資料在下（違反此鐵則會導致 LLM 忽略規則）
- 禁止自問自答 + 避免重複結構（放在動態資料之前）
- OOC/IC 頻道分開處理不同 prompt
- 分隔回覆系統：`[TO:名字]` 讓 bot 能同時回覆多人
- 文風自檢：IC 模式下自動檢查並修正違規

**Prompt 工程技巧**：
1. **Priming Trick**：prompt 結尾讓 LLM 認為自己已經在說話
2. **靜態指令在上**：規則類指令放在 prompt 頂部
3. **分階段處理**：拆成 Phase 1/2/3 降低單次 LLM 調用複雜度
4. **時間感知規則**：注入今天日期，防止 LLM 使用過時的時間詞彙
5. **名稱混淆防禦**：防止 LLM 將角色名稱「校正」為普通名詞

### 5.4 Phase 3：記憶寫入

**目的**：檢測 bot 回覆中的新資訊，並寫入 SQLite 資料庫。

**關鍵設計**：
- 積極比對原則：找出所有需要新增或更新之處
- 名稱混淆防禦：防止 Phase 3 將角色名稱誤認為普通名詞
- 玩笑/真實區分：`mem_type` 欄位標記「真實」或「玩笑」
- Skip pattern 偵測：記錄 LLM 決定不回覆的情況

**支援的 Action 類型**：
- `learn`：新增記憶
- `edit_self`：更新既有記憶
- `forget`：刪除記憶
- `mem`：記錄使用者資訊
- `profile` / `profile_char`：更新角色檔案
- `rule`：伺服器規則
- `edit_msg`：修改已發送的訊息

### 5.5 記憶系統

**資料庫結構**：
```sql
-- 主記憶表
CREATE TABLE memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT,
    user_id TEXT,
    user_name TEXT,
    topic TEXT,
    content TEXT,
    context TEXT DEFAULT 'ic',
    mem_type TEXT DEFAULT ''  -- '真實' / '玩笑' / '可遺忘'
);

-- 世界觀記憶表
CREATE TABLE world_lore (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    topic TEXT,
    content TEXT
);
```

**記憶維護**：
- 觸發時機：超過上限時自動觸發
- 操作類型：merge（合併）、simplify（簡化）、delete（刪除）
- 保護規則：人物、世界觀、場景等記憶不可刪除
- 玩笑記憶：標記為「玩笑」的記憶在整理時可被清除

**記憶寫入規則**：
- `save_user_memory()`：有 guard，只存「真實」記憶
- `save_self_memory()`：無 guard，可存「玩笑」記憶
- 世界觀記憶：獨立儲存，不受記憶維護影響

### 5.6 Discord 整合

**Slash Commands**：
- `/db`：查詢記憶資料庫（含翻頁、type 標籤顯示）
- `/tarot`：塔羅牌抽取
- `/say`：以角色身份說話（含使用紀錄）
- `/dbnpc`：檢視 NPC 記憶

**頻道類型自動偵測**：
- OOC 關鍵字：討論、中之、ooc、meta、後台、幕後、策劃、閒聊
- IC 關鍵字：劇情、扮演、rp、角色、主線、場景、冒險
- 預設：IC（維持沉浸感）

**分隔回覆系統**：
- 格式：`[TO:名字]\n回覆內容`
- 支援同時回覆多人
- 自動觸發者優先回覆

**表情符號處理**：
- 使用 `<:name:id>` 格式（非 `:name:` 格式）
- 支援顏文字和 www 輔助語氣

### 5.7 NPC 托管系統

**模組**：`autopilot.py`

**功能**：
- 多角色託管：每個 NPC 有自己的記憶和行為邏輯
- 共享世界觀：所有角色在同一個故事中
- 獨立記憶：每個 NPC 有自己的記憶資料庫
- 自動回覆：NPC 可自動回應玩家

---

## 6. Edge Cases

### 6.1 LLM 幻覺

| 幻覺類型 | 處理方式 |
|----------|----------|
| **視覺幻覺** | 禁止假裝看到不存在的圖片 |
| **名稱混淆** | Phase 3 名稱混淆防禦規則 |
| **時間幻覺** | 時間感知規則，計算日期差異 |
| **Skip 回覆** | skip_logs/ 追蹤 LLM 決定不回覆的情況 |

### 6.2 記憶衝突

| 衝突類型 | 處理方式 |
|----------|----------|
| **重複記憶** | 去重機制，相似度 >70% 則更新 |
| **矛盾記憶** | edit_self 更新為正確版本 |
| **過時記憶** | 記憶維護時清理 |

### 6.3 API 失敗

| 失敗類型 | 處理方式 |
|----------|----------|
| **LLM 回應空白** | 4 次重試後跳過 |
| **JSON 解析失敗** | 記錄錯誤並跳過 |
| **資料庫寫入失敗** | 記錄錯誤但不中斷對話 |

### 6.4 Discord 限制

| 限制類型 | 處理方式 |
|----------|----------|
| **Embed 長度限制** | field value 上限 1024 字元 |
| **表情符號格式** | 使用 `<:name:id>` 格式 |
| **訊息長度限制** | 自動分段或截斷 |

---

## 7. Dependencies

### 7.1 外部服務

| 服務 | 用途 | 備註 |
|------|------|------|
| **DeepSeek API** | LLM 推理 | V4 Flash，智商較低 |
| **Discord API** | Bot 運作 | Discord.py 框架 |

### 7.2 本地依賴

| 依賴 | 用途 | 備註 |
|------|------|------|
| **SQLite** | 資料儲存 | 本地資料庫 |
| **config.json** | Bot 設定 | 含 prompt 模板、表情符號等 |
| **rp_memory.db** | 記憶資料庫 | 主記憶表 + 世界觀表 |
| **tarot_images/** | 塔羅牌圖片 | 78 張卡牌 |
| **msjh.ttc** | 中文字型 | 用於 PIL 圖片生成 |

### 7.3 程式碼模組

| 檔案 | 職責 |
|------|------|
| **main.py** | 核心引擎（Phase 1/2/3、Discord 整合） |
| **autopilot.py** | NPC 托管系統 |
| **tarot.py** | 塔羅牌模組 |
| **say_cmd.py** | /say 指令 |
| **gods_eye.py** | 神之眼分析 |

### 7.4 日誌系統

| 目錄 | 用途 |
|------|------|
| **error_logs/** | 錯誤日誌（每日一個檔案） |
| **prompt_logs/** | Prompt 和回覆日誌（每次對話一個資料夾） |
| **skip_logs/** | Skip pattern 追蹤 |
| **knowledge_logs/** | 每日知識更新日誌 |
| **memory_maintenance_logs/** | 記憶維護日誌 |
| **channel_summaries/** | 頻道/討論串大總結 |

---

## 8. 開發規範

### 8.1 Prompt 設計鐵則

1. **靜態指令在上、動態資料在下**
2. **Priming Trick**：prompt 結尾讓 LLM 自然延續格式
3. **分階段處理**：降低單次 LLM 調用複雜度
4. **時間感知**：注入今天日期，防止時間幻覺
5. **名稱防禦**：防止角色名稱被「校正」

### 8.2 記憶寫入規則

1. **使用者記憶**：只存「真實」記憶（有 guard）
2. **自我記憶**：可存「玩笑」記憶（無 guard）
3. **世界觀記憶**：獨立儲存，不受維護影響
4. **去重機制**：相似度 >70% 則更新

### 8.3 錯誤處理規則

1. **所有錯誤都要記錄**：error_logs/
2. **Skip pattern 要追蹤**：skip_logs/
3. **Prompt log 要保留**：prompt_logs/
4. **不中斷對話**：API 失敗時優雅降級

---

## 9. 未來擴展

### 9.1 短期改進

- Phase 2 prompt 結構優化（review 7.5/10）
- Phase 3 矛盾指令修復（review 6/10）
- Phase 3 動態資料補「既有自我記憶」區塊

### 9.2 中期擴展

- 多伺服器支援
- 更多 NPC 角色
- 更複雜的劇情追蹤

### 9.3 長期願景

- 跨平台整合
- 更自然的記憶維護
- 玩家自訂角色

---

## Appendix A: 設計決策紀錄

| 決策 | 原因 | 日期 |
|------|------|------|
| Supplement 從 XML 改為 JSON array | 降低 LLM 格式錯誤率 | 2026-07-23 |
| Phase 2 格式守則前置 | 靜態指令在上鐵則 | 2026-07-23 |
| Priming Trick 取代指令語氣 | 讓 LLM 自然延續格式 | 2026-07-23 |
| 時間感知規則 | 防止 LLM 使用過時時間詞彙 | 2026-07-23 |
| 名稱混淆防禦 | 防止 Phase 3 誤改角色名稱 | 2026-07-23 |
| /db type 標籤顯示 | 讓玩家區分真實/玩笑記憶 | 2026-07-23 |
| skip_logs/ 追蹤 | 追蹤 LLM 決定不回覆的情況 | 2026-07-23 |
| 表情符號 ID 格式修正 | Discord 自訂表情必須用 <:name:id> | 2026-07-23 |

---

## Appendix B: 關鍵程式碼位置

| 功能 | 檔案位置 |
|------|----------|
| Phase 1 召回 | `main.py` `run_phase1()` |
| Phase 2 回覆 | `main.py` `build_phase2_system_prompt()` |
| Phase 3 記憶寫入 | `main.py` `phase3_process()` |
| /db 指令 | `main.py` `cmd_db()` + `DBPageView` |
| /dbnpc 指令 | `autopilot.py` `_build_npc_memory_embed()` |
| 記憶維護 | `main.py` `maintain_self_memories()` |
| Skip pattern 偵測 | `main.py` on_message handler |
| format_supplement | `main.py` `format_supplement()` |
