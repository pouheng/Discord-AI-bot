# 🤖 Discord AI RP Bot — 「澪」

一個基於 **DeepSeek API** 的 Discord 角色扮演機器人，採用三階段 pipeline 實現具記憶、成長與自我修正能力的 AI 角色。

## 📋 目錄

- [功能特色](#-功能特色)
- [系統架構](#-系統架構)
- [三階段 Pipeline](#-三階段-pipeline)
- [斜線指令](#-斜線指令一覽)
- [資料庫結構](#-資料庫結構)
- [工具與輔助程式](#-工具與輔助程式)
- [安裝與設定](#-安裝與設定)
- [專案檔案結構](#-專案檔案結構)
- [設計原則](#-設計原則)
- [疑難排解](#-疑難排解)

---

## ✨ 功能特色

- **角色扮演 (IC)** — 以角色「澪」的身份進行沉浸式 RP，遵守文風規則與禁用詞彙
- **中之人模式 (OOC)** — 在 #中之討論串 頻道以中之人身份討論劇情規劃
- **記憶系統** — 每次對話自動學習，支援 learn / edit_self / mem / forget 等記憶操作
- **全庫掃描** — Phase 1 從完整資料庫（世界觀 + 自我記憶 + 參與者記憶）篩選最相關條目
- **自動快照** — 每次對話將 prompt log 打包到日期資料夾，保留 3 天
- **NPC 托管 (Autopilot)** — 自動管理 NPC 角色的發言與記憶
- **神之眼 (God's Eye)** — 以全知旁白視角分析戰鬥與關鍵劇情
- **文風自檢** — AI 自動審查回覆是否符合文風規則，不符則重寫
- **記憶維護** — 每 2 次對話自動整理、合併、刪除過時記憶
- **GUI 管理工具** — 圖形化介面管理 Bot 啟停、編輯設定、瀏覽資料庫

---

## 🏗 系統架構

```
┌─────────────────────────────────────────────────────────────┐
│                    Discord 使用者訊息                         │
└────────────────────────┬────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Phase 1：記憶召回分析 (run_phase1)               │
│  （讀取完整資料庫 → AI 篩選 6-8 條最相關條目 → supplement）   │
└────────────────────────┬────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Phase 2：角色回覆生成 (DeepSeek API)              │
│  （靜態指令 + 動態資料 → IC/OOC 雙路徑 → 文風自檢 → reply）  │
└────────────────────────┬────────────────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              Phase 3：知識檢測與記憶維護 (背景執行)           │
│  （逐條比對 → learn/edit_self/mem/forget → 記憶寫入）        │
└─────────────────────────────────────────────────────────────┘
```

### 核心模組

| 模組 | 位置 | 職責 |
|------|------|------|
| **Core Engine** | `main.py` | Discord bot 主體、三階段 pipeline、資料庫 CRUD、所有斜線指令 |
| **Autopilot** | `autopilot.py` | NPC 角色托管，獨立 SQLite 資料庫，自動發言與記憶處理 |
| **Say Command** | `say_cmd.py` | `/say` 指令，讓 Bot 主動加入指定頻道的對話 |
| **God's Eye** | `gods_eye.py` | `/gods-eye` 指令，全知旁白視角分析劇情 |
| **Manager GUI** | `manager.py` | Tkinter 圖形化管理介面 |
| **DB Viewer** | `db_viewer.py` | 資料庫可視化編輯器（卡片式 UI） |
| **Config Editor** | `editor.py` | config.json 圖形化編輯器 |
| **Prompt Tester** | `test_prompt.py` | 離線三階段流程測試工具 |
| **Prompt Reviewer** | `review_prompt.py` | AI 提示詞審查工具 |

---

## 🔄 三階段 Pipeline

### Phase 1：記憶召回分析

```
1. fetch_reply_chain() → 追蹤 Discord 回覆鏈
2. channel.history(30) → 讀取最近 30 則訊息
3. 判斷頻道類型（IC / OOC）
4. 完整資料庫掃描：
   - get_all_lore_full() → 所有世界觀
   - get_self_memory_raw(50) → 所有自我記憶
   - get_user_memory(每人 5 條) → 參與者記憶
5. run_phase1() → DeepSeek 分析輸出 JSON
   └─ recall（召回哪些使用者）
   └─ lore_topics（相關世界觀條目）
   └─ supplement（6-8 條最相關條目 + 備註）
```

### Phase 2：角色回覆生成

**IC 路徑**（角色扮演頻道）：
- 角色身份 + jailbreak 協議
- 文風規則（對話佔比 40%、第三人稱自稱、禁用詞彙等）
- planning_template_ic（時間/地點/空間/人物/劇情/切入點）
- 社交原則 + 可用表情符號
- 注入故事摘要 + NPC 托管資料

**OOC 路徑**（#中之討論串）：
- 中之人（扮演者）身份 + ooc_persona 人設
- ooc_chat_examples 對話範例
- planning_template_ooc
- 安全規則

**生成後處理**：
1. `strip_japanese_original()` — 移除日文原文（中文）註釋
2. `style_review()` — 文風自檢，不符規則則重寫
3. 剝離 `<planning>` 區塊
4. 移除 `[MEM:]` / `[LEARN:]` 標籤
5. `[TO:]` 分段路由 → 多使用者回覆

### Phase 3：知識檢測與記憶維護（背景執行）

```
1. 解析 MEM/LEARN 標籤 → 立即寫入
2. 知識檢測（DeepSeek, temperature=0.2）：
   └─ 逐條比對 bot 回覆 vs 完整記憶庫
   └─ 輸出 JSON actions
3. 執行 actions：
   ├─ learn        → 寫入新記憶
   ├─ edit_self    → 更新既有記憶
   ├─ mem          → 寫入使用者資訊
   ├─ forget       → 軟刪除記憶
   ├─ profile      → 更新角色檔案
   ├─ rule         → 新增伺服器規則
   └─ edit_msg     → 修改已發送的訊息
4. Autopilot NPC 記憶處理
5. 更新頻道故事摘要（IC 限定）
6. 每 2 次對話 → maintain_self_memories() 整理記憶
```

---

## 📜 斜線指令一覽

| 指令 | 模組 | 說明 |
|------|------|------|
| `/say` | `say_cmd.py` | 讓 Bot 在指定頻道發言 |
| `/db` | `main.py` | 翻頁查詢 memories 記錄 |
| `/summarize` | `main.py` | 手動產生頻道劇情摘要 |
| `/addrule` | `main.py` | 新增伺服器規則 |
| `/rules` | `main.py` | 查看伺服器規則 |
| `/profile` | `main.py` | 查看角色檔案 |
| `/reload_config` | `main.py` | 重新載入設定 |
| `/summaries` | `main.py` | 查看所有頻道摘要 |
| `/read` | `main.py` | 角色閱讀討論串（批次記憶） |
| **修改建議** (右鍵選單) | `main.py` | AI 輔助修改 Bot 已發送的訊息 |
| `/autopilot_on` | `autopilot.py` | 啟用 NPC 托管模式 |
| `/autopilot_off` | `autopilot.py` | 停用 NPC 托管模式 |
| `/autopilot_add` | `autopilot.py` | 新增托管 NPC |
| `/autopilot_list` | `autopilot.py` | 列出所有 NPC（含開關） |
| `/dbnpc` | `autopilot.py` | 瀏覽 NPC 記憶 |
| `/dbnpc_teach` | `autopilot.py` | 用自然語言教 NPC 記憶 |
| `/gods-eye` | `gods_eye.py` | 全知旁白視角分析 |

---

## 🗄 資料庫結構

### `rp_memory.db`（主資料庫）

| 資料表 | 欄位 | 用途 |
|--------|------|------|
| `memories` | id, timestamp, user_id, user_name, topic, content, context, mem_type | 所有角色記憶與使用者記憶 |
| `world_lore` | id, category, topic, content | 世界觀設定（勢力、元素、地點等） |
| `character_profiles` | char_name, gender_age, intro, appearance, items, experience | 角色檔案 |
| `items` | id, timestamp, name, description, quantity, location | 道具清單 |
| `quests` | id, title, description, status, created_at, updated_at | 任務追蹤 |
| `server_rules` | id, server_id, rule_text, added_at | 伺服器自訂規則 |
| `summaries` | channel_id, channel_name, summary | 頻道故事摘要 |
| `autopilot_config` | enabled | NPC 托管全域開關 |
| `autopilot_chars` | id, name, gender, personality, description, ability, active | NPC 角色定義 |

### `auto_pilot_memories/{name}.db`（每個 NPC 獨立）

| 資料表 | 用途 |
|--------|------|
| `memories` | NPC 的使用者記憶 |
| `self_memories` | NPC 的自我記憶 |

---

## 🛠 工具與輔助程式

### 測試工具
| 工具 | 說明 |
|------|------|
| `test_prompt.py` | 離線模擬完整三階段流程，不需 Discord |
| `test_prompts.py` | 離線結構測試（36 項檢查），Mock 所有外部依賴 |
| `review_prompt.py` | 用 DeepSeek 自動審查提示詞品質 |
| `review.bat` | 選單式快速啟動 review_prompt.py |

### 管理工具
| 工具 | 說明 |
|------|------|
| `manager.bat` / `manager.py` | Tkinter 圖形化管理介面（啟動/停止/重啟/開設定/開資料庫）|
| `run.bat` | 直接啟動 Bot |
| `restart.bat` | 強制重啟 Bot（殺掉舊程序 + 啟動新程序） |

### 資料庫工具
| 工具 | 說明 |
|------|------|
| `db_viewer.py` | 卡片式 UI 資料庫瀏覽器（可增刪記錄） |
| `check_db.py` | 快速查詢最近 5 條人物記憶 |
| `check_memory.py` | 搜尋特定主題的記憶（如「義手」） |
| `populate_lore.py` | 初始化世界觀種子資料 |

### 設定工具
| 工具 | 說明 |
|------|------|
| `editor.py` | config.json 圖形化編輯器 |
| `config.json` | 主設定檔（文風規則、安全規則、OOC 人設、表情符號等）|
| `.env` | 金鑰與 Token（已 gitignore） |

---

## ⚙ 安裝與設定

### 前置需求
- Python 3.11+
- Discord Bot Token（[Developer Portal](https://discord.com/developers/applications)）
- DeepSeek API Key（[platform.deepseek.com](https://platform.deepseek.com)）

### 安裝步驟

1. **Clone 專案**
   ```bash
   git clone https://github.com/pouheng/Discord-AI-bot.git
   cd Discord-AI-bot
   ```

2. **安裝依賴套件**
   ```bash
   pip install discord.py openai aiofiles aiosqlite python-dotenv
   ```

3. **設定環境變數**
   ```bash
   # 建立 .env 檔案（已加入 .gitignore，不會被提交）
   echo DISCORD_TOKEN=你的Discord機器人Token >> .env
   echo DEEPSEEK_API_KEY=你的DeepSeekAPI金鑰 >> .env
   ```

4. **設定 config.json**
   - 修改 `allowed_servers` 填入你的 Discord 伺服器 ID
   - 可自訂角色名稱、文風規則、OOC 人設等

5. **初始化資料庫**
   ```bash
   python main.py
   # 第一次執行會自動建立 rp_memory.db 與所有資料表
   # 按 Ctrl+C 停止後，可執行 populate_lore.py 匯入世界觀
   python populate_lore.py
   ```

6. **啟動 Bot**
   ```bash
   # 方式一：直接執行
   python main.py

   # 方式二：使用管理介面
   manager.bat

   # 方式三：一鍵重啟
   restart.bat
   ```

7. **測試運作**（可選）
   ```bash
   # 離線三階段流程測試（不需 Discord）
   python test_prompt.py "你好"
   
   # 離線結構測試
   python test_prompts.py
   ```

### Discord 權限設定

在 Discord Developer Portal 為 Bot 啟用以下權限：

- ✅ Send Messages
- ✅ Read Message History
- ✅ Use Slash Commands
- ✅ Embed Links（選擇性，用於 /db 的 embed 顯示）
- ✅ Attach Files（選擇性）

---

## 📁 專案檔案結構

```
ai bot/
├── main.py                  # 🎯 核心 Bot 引擎（三階段 pipeline、指令、資料庫）
├── autopilot.py             # 🤖 NPC 托管模組
├── say_cmd.py               # 💬 /say 指令
├── gods_eye.py              # 👁 神之眼模組
├── manager.py               # 🖥 Tkinter 管理 GUI
├── editor.py                # ⚙ config.json 設定編輯器
├── db_viewer.py             # 🗄 資料庫瀏覽器
├── review_prompt.py         # 📝 AI 提示詞審查
├── test_prompt.py           # 🧪 離線三階段測試
├── test_prompts.py          # ✅ 離線結構測試（36 項）
│
├── populate_lore.py         # 🌍 世界觀種子資料
├── check_db.py              # 🔍 快速資料庫診斷
├── check_memory.py          # 🔍 記憶搜尋
├── find_summary.py          # 🔍 摘要查詢
├── _find_summary.py         # 🔍 替代摘要查詢
├── del_summary.py           # 🗑 刪除摘要
├── restore_scene.py         # 🔄 還原場景資料
├── insert_bot.py            # 📥 手動插入記憶
├── insert_map.py            # 🗺 匯入地圖場景
│
├── config.json              # ⚙ 主設定檔（已 gitignore）
├── .env                     # 🔑 金鑰與 Token（已 gitignore）
├── .gitignore               # 🚫 Git 忽略規則
│
├── FLOWCHART.md             # 📊 系統流程圖文件
├── prompt_reviewer.md       # 📝 提示詞審查 rubric
├── README.md                # 📖 本文件
│
├── *.bat                    # 🏃 批次檔（manager / run / restart / review）
│
├── prompt_logs/             # 📋 提示詞日誌（自動快照）
├── auto_pilot_memories/     # 💾 NPC 專屬資料庫
├── knowledge_logs/          # 📜 知識檢測日誌
├── maintenance_logs/        # 🔧 維護日誌
├── memory_maintenance_logs/ # 🧠 記憶維護日誌
└── channel_summaries/       # 📄 頻道摘要快取
```

---

## 🎯 設計原則

### 1. 靜態在上、動態在下
所有提示詞中，固定規則、身份定義、格式說明放在上方，每次對話不同的資料（使用者訊息、歷史記錄、已知記憶）放在底部。這確保 AI 的注意力集中在不變的核心指令上。

### 2. IC/OOC 雙路徑分離
角色扮演頻道與中之人討論頻道使用完全不同的提示詞結構，避免身份混淆。OOC 路徑的 prompt 明確指出「你絕不可以用角色身份發言」。

### 3. 積極比對，而非被動接收
Phase 3 不是問「有沒有新資訊」，而是「逐條比對每一條記憶與 bot 回覆，找出差異」。這確保記憶庫持續更新與校正。

### 4. 背景執行不阻塞
Phase 3 作為 `asyncio.create_task()` 在背景執行，使用者看到回覆後不需等待記憶寫入完成。

### 5. 隔離的 NPC 記憶
每個托管 NPC 擁有獨立的 SQLite 資料庫，角色記憶完全隔離，不會與主角色的記憶互相干擾。

### 6. 自動提示詞快照
每次對話結束後，將當時的完整 prompt log 複製到 `YYYY-MM-DD_HH-MM-SS_摘要/` 資料夾，保留 3 天，便於除錯與回溯。

---

## 🔧 疑難排解

### DeepSeek API 回傳空白
- Phase 3 detect 內建 4 次重試機制（3 次 flash 升溫 + 1 次 pro 降級）
- Phase 2 空白時回覆「...」（沉默），不阻斷對話

### 資料庫連線錯誤
- 確保 `rp_memory.db` 沒有被其他程式獨佔鎖定
- 使用 `db_viewer.py` 檢查資料庫是否正常

### 文風自檢失敗
- `style_review()` 失敗時保留原始文字，不影響回覆發送

### 記憶庫膨脹
- 每 2 次對話自動執行 `maintain_self_memories()` 合併/刪除過時記憶
- 可手動執行 `/summarize` 產生頻道摘要壓縮記憶

---

## 📄 授權

MIT License

Copyright (c) 2026 pouheng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
