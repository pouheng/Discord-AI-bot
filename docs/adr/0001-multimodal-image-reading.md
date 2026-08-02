# Multimodal Image Reading via Local Gemma4

Discord RP bot（澪）需要閱讀用戶分享的圖片，但 DeepSeek V4 Flash 是純文字模型，不支援圖片輸入。決定使用本地 ollama + Gemma4 12B（多模態）來分析圖片，將結構化描述注入 prompt，讓 AI 角色「看到」圖片並自然回應。

## Considered Options

- **本地 Gemma4 via ollama** — 免費、隱私、無 API 成本，但佔用本地 GPU（~50 秒/張）
- **DeepSeek Vision API** — 不需要本地 GPU，但有 API 費用且依賴外部服務
- **OpenAI GPT-4o Vision** — 效果最好，但最貴且最不隱私

選擇本地 Gemma4 是因為：免費、無 API 成本、圖片分析需求不頻繁、隱私可控。

## Consequences

- 新增 `ollama` Python client 依賴
- 新增 `image_reader.py` 模組
- 新增 `image_logs/` 目錄
- config.json 新增 `ollama` 設定區塊
- `build_and_call_phase2()` 需要抽出共用函數
- `/readimg` slash command 需要 ~50 秒的 typing indicator
