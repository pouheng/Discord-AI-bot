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

**Priming Trick（回覆起始引導）**：
在 prompt 末尾注入起始短語（OOC: 「喔好，收到了...」、IC: 「開始回應：」），利用 deepseek-flash 的 recency bias 引導模型產生正確格式的回覆。

_Await_: Response引导, format引导

**Image Reader（圖片分析）**：
透過本地 ollama + Gemma4 12B 分析用戶分享的圖片，產生結構化描述（[類型]/[描述]/[表情]/[姿勢]/[環境]/[情緒]），注入 prompt 的 context 區塊，讓 AI 角色「看到」圖片。使用 `/readimg` slash command 觸發。

_Await_: Vision analysis, image description
