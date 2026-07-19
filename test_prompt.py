"""提示詞測試工具 -- 模擬完整三階段流程，顯示各階段 prompt 與 AI 回覆"""

import asyncio, json, re, os, sys
from openai import AsyncOpenAI

from dotenv import load_dotenv

load_dotenv()
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from main import (
    get_character_name,
    load_prompt_config,
    get_self_memory,
    get_lore_catalog,
    phase3_process,
)


async def test_pipeline(
    user_message: str, channel_type="in_character", user_name="測試用戶"
):
    print("=" * 70)
    print(f"使用者：{user_name}")
    print(f"訊息：{user_message}")
    print(f"頻道類型：{channel_type}")
    print("=" * 70)

    char_name = await get_character_name() or "角色"
    pcfg = await load_prompt_config()

    all_context = [
        {"role": "user", "content": f"[{user_name}]: {user_message}", "msg_id": 0},
    ]
    chain_text = "\n".join(
        f"[{char_name if m['role'] == 'assistant' else '對方'}]: {m['content'][:200]}"
        for m in all_context[-10:]
    )
    clean_input = user_message

    # Phase 1
    print("\n" + "-" * 70)
    print("PHASE 1 -- 記憶召回分析")
    print("-" * 70)

    lore_cat = await get_lore_catalog()
    catalog_text = "\n".join(f"- [{e['category']}] {e['topic']}" for e in lore_cat)
    candidates_text = f"- {user_name} (id: test)"

    phase1_prompt = f"""你是記憶召回分析器。根據對話內容判斷三件事：

1. 要查詢「哪些參與者」的歷史記憶
2. 要查詢「哪些世界觀條目」（從下方目錄中挑選，用「精確的 topic 名稱」）
3. 若對話與特定劇情討論串有關，指定要召回哪些討論串的內容
4. 針對每個召回條目，寫一句「為什麼相關、Phase 2 該如何運用」的註釋；另外也可以寫一些與對話脈絡相關的自由註釋（例如某人的情緒、對話走向建議等）。
注意在註釋中提到人時，必須用具體名字（如「頭皮慶」「吳神月」），禁止使用「使用者」「用戶」等泛稱。

【參與者召回規則】
- 觸發者必定召回。最多召回 3 人。
- 若對話中沒有其他參與者，就只召回觸發者。

【世界觀召回規則】
- 從下方目錄中選出與對話「語意相關」的條目（即使用詞不完全相同，語意相關就選）。
- 若對話未涉及任何世界觀，lore_topics 設為空陣列。

【討論串召回規則 - 重要】
- 下方列出了各討論串的名稱與劇情摘要。若用戶正在討論某個討論串的劇情，請在 recall_threads 中指定該討論串的名稱。
- 若用戶沒有指定或對話不涉及任何討論串，recall_threads 設為空陣列。
- 若用戶只是在閒聊、測試功能、討論系統設定等「非劇情討論」，設定 load_plot 為 false，不要召回任何討論串。

【劇情相關判斷 - load_plot】
- 當對話與角色扮演劇情、世界觀設定、角色能力、道具裝備、劇情走向有關時 -> load_plot: true
- 當對話只是閒聊、打招呼、測試機器人功能、討論系統或程式碼時 -> load_plot: false
- 當不確定時 -> load_plot: false

【文風切換判斷 - enable_ic_style】（僅 OOC 模式生效）
- 在中之人頻道中，若使用者指派了寫故事/寫場景/寫劇情/描述角色等「創作任務」-> enable_ic_style: true（讓 Phase 2 啟用 IC 文風規則）
- 若只是討論設定、問問題、閒聊 -> enable_ic_style: false
- 不確定時 -> enable_ic_style: false

【世界觀條目目錄】
{catalog_text}

【可用的劇情討論串】
（無）

【可查詢的參與者】
{candidates_text}

【對話歷史】
{chain_text}

【當前用戶訊息】
{clean_input}

輸出純 JSON（不加 ```）。重要：recall 裡面用「id」（數字），不是 name。
lore_topics 只要 topic 名稱，不要加 [分類] 前綴。
recall_threads 是討論串名稱陣列（如 ["時間:2月1號"]），無則空陣列。
load_plot 是布林值，enable_ic_style 是布林值（OOC時是否需要啟用IC文風）。"""

    print("Phase 1 Prompt：")
    print(phase1_prompt[:2000] + ("\n...（截斷）" if len(phase1_prompt) > 2000 else ""))

    print("\n呼叫 DeepSeek Phase 1...")
    try:
        resp = await client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {
                    "role": "system",
                    "content": "你只輸出 JSON，不要加任何解釋或 markdown。",
                },
                {"role": "user", "content": phase1_prompt},
            ],
            temperature=0.3,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
        phase1_raw = resp.choices[0].message.content or "{}"
        print(f"Phase 1 回傳：{phase1_raw[:500]}")
    except Exception as e:
        print(f"Phase 1 失敗：{e}")
        return

    # Phase 2
    print("\n" + "-" * 70)
    print("PHASE 2 -- 角色回覆生成")
    print("-" * 70)

    self_mem = await get_self_memory(limit=8)
    safety_str = "\n".join(f"- {r}" for r in pcfg.get("safety_rules", []))
    expr_str = "\n".join(f"  - {p}" for p in pcfg.get("expression_prefs", []))
    banned_str = "、".join(pcfg.get("banned_words", []))
    dialogue_ratio = pcfg.get("dialogue_ratio", "")
    naming_rule = pcfg.get("naming_rule", "")
    planning_tpl = pcfg.get("planning_template", "")

    identity_block = (
        f"【你的身份 - 這是你自己的事，最優先遵守！】\n"
        f"你的名字是「{char_name}」。在正文中你必須用「{char_name}」自稱，絕不可使用「我」。\n"
        f"這是你自己的核心設定，永遠不可違反。\n\n"
    )

    combined_memories = self_mem or "（無已知人物資訊）"

    phase2_system = f"""[角色扮演協議]
{pcfg["jailbreak"]}

{identity_block}【安全規則 - 嚴格遵守，違反將導致嚴重後果】
{safety_str}

【文風規則】
- 對話佔比：{dialogue_ratio}
- 稱呼規則：{naming_rule}
- 表達偏好：
{expr_str}
- 絕對禁用詞彙（出現即為違規）：
  {banned_str}

【場景】
伺服器「測試」的頻道「#test」

【你對眼前人物的已知資訊】  
{combined_memories}

【你對自己角色的認知】
（無）

【世界觀資料】
<supplement>
[註釋]
# 測試模式。
</supplement>

<planning>
{planning_tpl}
</planning>
（{char_name or "角色"}的台詞）

不輸出 <planning> = 錯誤"""

    context_text = "\n".join(
        f"[{char_name if m['role'] == 'assistant' else '對方'}]: {m['content'][:300]}"
        for m in all_context[-8:]
    )

    print("Phase 2 System Prompt：")
    print(phase2_system[:3000] + ("\n...（截斷）" if len(phase2_system) > 3000 else ""))
    print(f"\nPhase 2 User Message：\n{context_text}")

    print("\n呼叫 DeepSeek Phase 2...")
    try:
        resp = await client.chat.completions.create(
            model="deepseek-v4-flash",
            messages=[
                {"role": "system", "content": phase2_system},
                {
                    "role": "user",
                    "content": f"【目前對話】\n{context_text}\n\n請以{char_name}的身份自然地對話。",
                },
            ],
            temperature=0.8,
            max_tokens=400,
        )
        raw_content = resp.choices[0].message.content or ""
        reply = re.sub(
            r"<planning>.*?</planning>", "", raw_content, flags=re.DOTALL
        ).strip()
        print(f"Phase 2 回覆：\n{reply[:600]}")
    except Exception as e:
        print(f"Phase 2 失敗：{e}")
        return

    # Phase 3
    print("\n" + "-" * 70)
    print("PHASE 3 -- 記憶檢測")
    print("-" * 70)
    try:
        await phase3_process(
            None,
            raw_content,
            channel_type,
            "",
            await get_self_memory(limit=12),
        )
        print("Phase 3 執行完成（詳情見 prompt_logs/last_phase3.txt）")
    except Exception as e:
        print(f"Phase 3 失敗：{e}")

    print("\n" + "=" * 70)
    print("測試完成")
    print("=" * 70)


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "你好"
    asyncio.run(test_pipeline(msg))
