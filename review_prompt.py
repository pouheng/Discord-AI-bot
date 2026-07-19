"""快速提示詞自檢工具

用法：
  python review_prompt.py                  → 審查最新 Phase 2
  python review_prompt.py last_phase3      → 審查最新 Phase 3
  python review_prompt.py last_phase1      → 審查最新 Phase 1
  python review_prompt.py all              → 一次審查全部三階段
  python review_prompt.py <檔案路徑>        → 審查指定檔案
"""

import asyncio, sys, os, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from openai import AsyncOpenAI
from dotenv import load_dotenv

load_dotenv()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SHORTCUTS = {
    "last_phase3": "prompt_logs/last_phase3_prompt.txt",
    "last_phase1": "prompt_logs/last_phase1_prompt.txt",
    "last_prompt": "prompt_logs/last_prompt.txt",
}

REVIEWER_FILE = os.path.join(SCRIPT_DIR, "prompt_reviewer.md")
ALL_TARGETS = [
    ("Phase 1 (recall)", SHORTCUTS["last_phase1"]),
    ("Phase 2 (response)", SHORTCUTS["last_prompt"]),
    ("Phase 3 (detect)", SHORTCUTS["last_phase3"]),
]


async def review_one(
    client: AsyncOpenAI, reviewer_system: str, name: str, path: str
) -> str | None:
    path = os.path.join(SCRIPT_DIR, path)
    if not os.path.exists(path):
        msg = f"[{name}] 找不到: {path}"
        print(f"\n{msg}")
        return msg
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    header = f"\n{'=' * 60}\n[{name}] 審查中... ({len(content)} 字元)\n{'=' * 60}"
    print(header)
    try:
        resp = await client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[
                {"role": "system", "content": reviewer_system},
                {
                    "role": "user",
                    "content": f"這是一個 {name} 的提示詞，請審查它：\n\n========== 審查目標開始 ==========\n{content}\n========== 審查目標結束 ==========",
                },
            ],
            temperature=0.3,
            max_tokens=4000,
            timeout=300,
        )
        result = resp.choices[0].message.content or ""
        print(result)
        return f"{header}\n{result}"
    except Exception as e:
        msg = f"[{name}] 審查失敗: {e}"
        print(msg)
        return f"{header}\n{msg}"


LOG_FILE = os.path.join(SCRIPT_DIR, "prompt_logs", "last_review_log.txt")


async def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "all"

    with open(REVIEWER_FILE, "r", encoding="utf-8") as f:
        reviewer_system = f.read()

    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com/v1",
    )

    lines = []
    if arg == "all":
        for name, path in ALL_TARGETS:
            result = await review_one(client, reviewer_system, name, path)
            lines.append(result or "")
    else:
        target = SHORTCUTS.get(arg, arg)
        target_path = os.path.join(SCRIPT_DIR, target)
        if not os.path.exists(target_path):
            print(f"找不到: {target_path}")
            print("可用: last_prompt / last_phase3 / last_phase1 / all，或直接給路徑")
            sys.exit(1)
        result = await review_one(
            client, reviewer_system, arg if arg in SHORTCUTS else target, target
        )
        lines.append(result or "")

    # 寫入 log
    log_text = "\n\n".join(lines)
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(log_text)
    print(f"\nLog 已寫入: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
