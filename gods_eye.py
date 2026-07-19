"""
神之眼模組 — 獨立 .py，以旁白視角解讀戰鬥/關鍵劇情

指令：
  /gods_eye [頻道] [討論串] [補充說明]
     — 讀取指定頻道/討論串的最新劇情
     — AI 以「神之眼視角」輸出旁白解說

神之眼核心理念：
  介於客觀敘述與主觀洞察之間的視角。
  在魔法戰鬥中，情報等於生命——魔法的原理、結界的邊界、魔力的流動，
  這些超出常人理解的範疇需要旁白來告訴觀眾。
"""

import discord
from discord.ext import commands
from discord import app_commands
from openai import AsyncOpenAI
import os
import datetime
import json

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_client = (
    AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com/v1")
    if DEEPSEEK_API_KEY
    else None
)

_GODS_EYE_SYSTEM = """你是一個「神之眼」解說員。你的職責是在戰鬥或關鍵劇情中，提供一個介於客觀敘述與主觀洞察之間的視角。

【核心定位】
- 你**不是**任何角色，你是一個超越故事層級的存在
- 你的話語以「旁白：」為前綴
- 語調冰冷、學術、客觀，像在剖析物理定律
- 不解釋「他很害怕」，而是解構恐懼的來源與機制

【適用場景】
1. 初見殺／底牌揭曉：敵我雙方首次展露核心能力或術式機制的瞬間
2. 生死分界線：角色即將死亡，或即將突破肉體極限完成反殺的毫秒級停頓
3. 魔力／能力解析：解釋不合理現象背後的原理
4. 關係的客觀敘述：不帶情感地描述角色之間的牽連

【表現形式】
- 時間凍結：物理世界的流動被強行拉長。拳頭停在半空，血液懸浮在傷口邊緣。
- 冰冷解說：用近乎學術論文或物理定律的語調，剖析荒誕的魔力現象。
- 心境剖析：不寫「他很害怕」或「他很憤怒」。而是解構情緒的來源。
  例：「在那一刻，比起死亡的恐懼，更先佔據他大腦的是一種因未能完成約定的荒謬感。」

【輸出格式】
旁白：（你的解說內容，100-300 字，精煉為原則）"""


async def _fetch_history(channel, limit: int = 40) -> str:
    """讀取頻道/討論串最近訊息，排除 bot 自己"""
    lines = []
    try:
        async for msg in channel.history(limit=limit):
            if msg.author.bot:
                continue
            name = msg.author.display_name
            content = msg.clean_content[:300] if msg.clean_content else ""
            if content:
                lines.append(f"[{name}]: {content}")
    except Exception as e:
        return f"（無法讀取歷史: {e}）"
    return "\n".join(reversed(lines))


def register_commands(bot: commands.Bot):
    if not _client:
        print("[神之眼] 警告：DeepSeek API 未設定，指令將無法使用")

    @bot.tree.command(
        name="gods-eye",
        description="以神之眼視角解讀指定頻道/討論串的最新劇情",
    )
    @app_commands.describe(
        頻道="要讀取的頻道（預設為當前頻道）",
        討論串="可選，指定要讀取的討論串",
        補充說明="可選，指定關注的重點或角色",
    )
    async def gods_eye(
        interaction: discord.Interaction,
        頻道: discord.TextChannel = None,
        討論串: discord.Thread = None,
        補充說明: str = "",
    ):
        await interaction.response.defer(ephemeral=False)

        if not _client:
            await interaction.followup.send("❌ DeepSeek API 未設定")
            return

        target: discord.TextChannel | discord.Thread = (
            討論串 or 頻道 or interaction.channel
        )

        history = await _fetch_history(target)

        if not history.strip():
            await interaction.followup.send(f"❌ 「{target.name}」中沒有足夠的對話記錄")
            return

        prompt = f"""以下是最新的劇情對話（來自頻道「{target.name}」）：

{history}

{"【補充關注點】" + 補充說明 if 補充說明 else ""}

請以神之眼視角輸出旁白，解讀當前局勢、能力原理、角色心境。"""

        try:
            resp = await _client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": _GODS_EYE_SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                max_tokens=800,
            )
            result = (resp.choices[0].message.content or "").strip()
            if not result:
                result = "旁白：（神之眼未能捕捉到值得解讀的場面。）"

            embed = discord.Embed(
                title="👁️ 神之眼",
                color=0xFFD700,
                description=result,
                timestamp=datetime.datetime.now(),
            )
            embed.set_footer(text=f"解析來源：{target.name}")
            await interaction.followup.send(embed=embed)

        except Exception as e:
            await interaction.followup.send(f"❌ 神之眼失效：{e}")
