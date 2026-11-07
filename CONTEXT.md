# 澪（Discord RP Bot）

Discord 角色扮演機器人，使用 DeepSeek V4 Flash 作為 AI 角色「澪」的中之人（扮演者），支援記憶系統、世界觀管理、多階段 prompt 組裝、NPC 托管（autopilot）、塔羅牌等功能。

## Language

**Phase 1（記憶召回分析）**：
分析對話歷史，決定需要召回哪些記憶、世界觀條目、討論串摘要，並產生結構化 supplement。

_Avoid_: Memory recall, recall phase

**Phase 2（角色扮演回覆生成）**：
組裝完整 prompt（角色設定 + 動態資料 + 對話歷史 + 格式指令），呼叫 DeepSeek API 生成角色回覆。

_Await_: Response generation, reply phase

**Phase 3（知識寫入 + 記憶維護）**：
背景執行，從 AI 回覆中提取記憶標籤（MEM/LEARN），寫入 DB，並執行記憶維護（清理、整理、摘要）。

_Await_: Memory write, knowledge extraction

**Supplement**：
Phase 1 產生的結構化世界觀資料區塊，以 JSON array 格式注入 Phase 2 prompt。每個條目包含 category、topic、content、note。

_Await_: Lore injection, world data

**OOC（Out of Character）**：
中之人討論頻道，扮演者們在此討論劇情走向，不需完全融入角色。prompt 使用較寬鬆的語氣規則。

_Await_: Meta channel, discussion channel

**IC（In Character）**：
劇情扮演頻道，AI 完全融入角色進行沉浸式扮演。prompt 使用嚴格的文風規則。

_Await_: Roleplay channel, character channel

**Autopilot（NPC 托管）**：
自動管理 NPC 角色，依排程觸發記憶維護、每日日誌、場景摘要等背景任務。

_Await_: NPC management, background tasks

**多人回覆（Multi-reply / TO 分段）**：
模型在一則回覆裡要分別回答多人時，用 `[TO:名稱]` 分段；`dispatch_reply` 會把每一段拆成一則**獨立的 Discord 訊息**，並各自 `reply` 到那個人的訊息（名稱用正規化比對，支援「頭皮慶」對「頭皮慶（一般的學弟）」、emoji 與 @ 前綴）。找不到對象時退回回覆觸發訊息，內容不遺失。名稱清單由 `_reply_roster_text` 依最近發言者動態注入，避免模型寫錯名字。沒有 `[TO:]` 或全部解析不到時，維持單則回覆。

_Await_: multi reply, split reply, 分段回覆

**Priming Trick（回覆起始引導）**：
在 prompt 末尾注入起始短語（OOC: 「喔好，收到了...」、IC: 「開始回應：」），利用 deepseek-flash 的 recency bias 引導模型產生正確格式的回覆。

_Await_: Response引导, format引导

**Image Reader（圖片分析）**：
分析用戶分享的圖片並產生結構化描述（[類型]/[描述]/[表情]/[姿勢]/[環境]/[情緒]），注入 prompt 的 context 區塊，讓 AI 角色「看到」圖片。使用 `/readimg` slash command 觸發。讀圖 provider 可切換：`config.json` 的 `image_reader.provider` = `ollama`（本地 Gemma4、不上傳，預設）或 `deepseek`（雲端 vision、較快但圖片會上傳）。

_Await_: Vision analysis, image description

**Web Search（DeepSeek 原生搜尋）**：
Phase 1 判斷需要「資料庫以外的即時／外部資訊」時，改用 DeepSeek 官方 `/v1/responses` + `deepseek-v4-pro` 的 server-side `web_search` 工具（`deepseek_search.py`），取得答案與來源後注入 Phase 2。與 `web_reader`（只讀使用者貼的連結）互補。設定在 `config.json` 的 `deepseek_search`；搜尋按次計費（自 2026-11-01 起）。

_Await_: native search, web search tool

**Web Reader（網頁讀取）**：
自動偵測使用者訊息（含被回覆訊息）中的 http(s) 連結，透過 `web_reader.py` 抓取網頁（httpx + bs4；擋私有/本機位址）→ 交由 LLM 摘要 → 以「用戶分享的網頁內容」區塊注入 Phase 2，讓角色「看得到」連結內容。紀錄寫入 `web_logs/`。由 `config.json` 的 `web_reader` 控制。

_Await_: URL reader, link reader

**時間感知（Time awareness / 日期分隔）**：
在 OOC 頻道的歷史對話中插入 `role="system"` 的分隔訊息，讓模型能分辨「昨天的訊息」與「今天的訊息」。三種分隔：`【歷史起點】`（第一則的日期＋相對天數）、`【日期分隔】`（跨日時插入，含「距上一則 X 天 X 小時」）、`【時間分隔】`（同一天但間隔超過 `same_day_gap_hours`）、以及目前訊息前的 `【現在】`。設定在 `config.json` 的 `time_awareness`（`enabled` / `day_dividers` / `now_divider` / `ooc_only` / `same_day_gap_hours`）。

_Await_: day divider, 日期分隔, 跨日感知

**記憶模組（Memory module）**：
擁有 `memories` 資料表的模組（`memory.py`）：含建表/欄位遷移與所有 SQL 存取。介面是記憶的 save / get / search / recall；實作吸收去重、語意合併、字首相似、`__BOT__` 自記憶疊加與重複分組。角色自身記憶以 `user_id='__BOT__'` 存在同一張表。對外只依賴 db（連線）與 profiles（人物別名解析）。

_Await_: memory table, memory store, recall module
