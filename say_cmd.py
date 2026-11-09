"""/say 指令模組 — 讓角色主動加入對話（完整三階段流程）"""

import asyncio
import discord, json, re, os, datetime
from llm_models import chat_create

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SAY_LOG_FILE = os.path.join(SCRIPT_DIR, "say_log.jsonl")
SAY_DEBUG_DIR = os.path.join(SCRIPT_DIR, "say_logs")


def _log_say_debug(stage: str, **fields):
    """/say 的逐步診斷紀錄（每階段都寫，成功或失敗都看得到原因）。"""
    try:
        os.makedirs(SAY_DEBUG_DIR, exist_ok=True)
        path = os.path.join(
            SAY_DEBUG_DIR, f"{datetime.datetime.now():%Y-%m-%d}.jsonl"
        )
        record = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
                  "stage": stage}
        record.update(fields)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _clean_planning(text: str) -> str:
    """移除 <planning> 區塊；未閉合時從 <planning> 起全部切掉，避免漏字。"""
    text = re.sub(r"<planning>.*?</planning>", "", text, flags=re.DOTALL)
    if "<planning>" in text:
        text = text.split("<planning>", 1)[0]
    return text.strip()


def _log_say_usage(interaction: discord.Interaction, reply: str):
    """Append a /say usage record to the persistent log."""
    try:
        record = {
            "timestamp": datetime.datetime.now().isoformat(),
            "user_id": interaction.user.id,
            "user_name": interaction.user.display_name,
            "user_global_name": interaction.user.name,
            "channel_id": interaction.channel.id if interaction.channel else None,
            "channel_name": interaction.channel.name if interaction.channel else None,
            "guild_id": interaction.guild.id if interaction.guild else None,
            "guild_name": interaction.guild.name if interaction.guild else None,
            "reply_preview": reply[:200],
        }
        with open(SAY_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[/say] 記錄使用紀錄失敗: {e}")


def register_say(bot):
    @bot.tree.command(name="say", description="讓角色主動加入對話（完整三階段流程）")
    async def cmd_say(interaction: discord.Interaction):
        from main import (
            DB_FILE,
            get_character_name,
            load_prompt_config,
            get_lore_catalog,
            get_lore_by_topics,
            get_self_memory,
            get_server_rules,
            phase3_process,
            _snapshot_prompt_logs,
            _cleanup_old_sessions,
            PROMPT_LOG_DIR,
        )

        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return

        channel = interaction.channel
        char_name = await get_character_name() or "角色"
        guild = interaction.guild

        all_context = []
        last_user_msg = None
        try:
            async for m in channel.history(limit=12):
                clean = re.sub(r"<@&?\d+>", "", m.content).strip()
                if not clean or clean == ".":
                    continue
                role = "assistant" if m.author == bot.user else "user"
                name = m.author.display_name
                content = f"[{name}]: {clean}" if role == "user" else clean
                all_context.insert(
                    0, {"role": role, "content": content, "msg_id": m.id}
                )
                if role == "user" and last_user_msg is None:
                    last_user_msg = m
        except Exception:
            await interaction.followup.send("❌ 無法讀取頻道訊息", ephemeral=True)
            return

        if len(all_context) < 2:
            await interaction.followup.send(
                f"（{char_name}看看四周，沒什麼好說的）", ephemeral=True
            )
            return

        chain_text = "\n".join(
            f"[{char_name if m['role'] == 'assistant' else '對方'}]: {m['content'][:200]}"
            for m in all_context[-10:]
        )
        pcfg = await load_prompt_config()
        lore_cat = await get_lore_catalog()
        catalog_text = "\n".join(f"- [{e['category']}] {e['topic']}" for e in lore_cat)

        # ──────────────────────────────────────────────
        # Prompt 注入順序：靜態指令在上，動態資料在底部
        # ──────────────────────────────────────────────
        phase1_prompt = f"""你是記憶召回分析器。以下是對話。判斷：
1. 現在氣氛是否適合{char_name}主動加入發言？若對話在進行中、氣氛輕鬆、非吵架/嚴肅話題→適合
2. 應召回哪些世界觀（從目錄挑）

【世界觀條目目錄】
{catalog_text}

【對話】
{chain_text}

輸出 JSON：{{"suitable": true/false, "reason": "原因", "lore_topics": []}}"""

        short_mode = False
        try:
            resp = await chat_create(
                "say",
                messages=[
                    {"role": "system", "content": "只輸出 JSON。"},
                    {"role": "user", "content": phase1_prompt},
                ],
                temperature=0.3,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            raw = resp.choices[0].message.content or '{"suitable":false}'
            phase1 = json.loads(re.sub(r"```(?:json)?\s*|\s*```", "", raw).strip())
            # 氣氛不適合長篇加入時不再完全沉默：改成「只補一句」短台詞。
            short_mode = not phase1.get("suitable", False)
            _log_say_debug(
                "phase1",
                suitable=phase1.get("suitable"),
                reason=str(phase1.get("reason", ""))[:200],
                short_mode=short_mode,
                channel=str(getattr(channel, "name", "")),
            )
        except Exception as e:
            _log_say_debug("phase1_error", error=f"{type(e).__name__}: {e}"[:300])
            await interaction.followup.send(
                f"（{char_name}猶豫了一下...）", ephemeral=True
            )
            return

        lore_text = ""
        try:
            topics = phase1.get("lore_topics", [])
            if topics:
                entries = await get_lore_by_topics(topics)
                if entries:
                    lore_lines = ["<supplement>\n[條目]"]
                    for e in entries:
                        lore_lines.append(
                            f"- [{e['category']}] {e['topic']}：{e['content']}"
                        )
                    lore_text = "\n".join(lore_lines)
        except Exception:
            pass

        context_text = "\n".join(
            f"[{char_name if m['role'] == 'assistant' else '對方'}]: {m['content'][:300]}"
            for m in all_context[-8:]
        )
        self_mem = await get_self_memory(limit=8)
        self_mem_all = await get_self_memory(limit=200)
        server_rules = await get_server_rules(guild.id) if guild else ""
        safety = "\n".join(f"- {r}" for r in pcfg.get("safety_rules", []))

        # ──────────────────────────────────────────────
        # Prompt 注入順序：靜態指令在上，動態資料在底部
        # ──────────────────────────────────────────────
        phase2_system = f"""[角色扮演協議]
你將完全融入你所扮演的角色。你是{char_name}。

【安全規則】
{safety}

# ── 動態資料區（以下為每則對話不同的內容）──

【場景】
伺服器「{guild.name if guild else "?"}」的頻道「#{channel.name if hasattr(channel, "name") else "?"}」

【你對自己的認知】
{self_mem}

{f"【本伺服器規則】\n{server_rules}" if server_rules else ""}

{f"【世界觀資料】\n{lore_text}" if lore_text else ""}

═══════════════════
⚠️ 輸出格式死命令 ⚠️
═══════════════════
你的回覆結構必須是：
<planning>
回顧當前情況：分析氣氛、在場人物、適合說什麼
</planning>
（{char_name}的台詞）

不輸出 <planning> = 錯誤"""

        if short_mode:
            say_user_prompt = (
                f"【目前對話】\n{context_text}\n\n"
                "現在氣氛不適合長篇加入。請只補一句很短的台詞（30 字內），"
                "附和、吐槽或簡短回應即可，不要展開新話題、不要問問題。"
            )
        else:
            say_user_prompt = (
                f"【目前對話】\n{context_text}\n\n"
                f"請以{char_name}的身分自然地加入對話。"
            )

        async def _generate(prompt_text: str, max_tokens: int) -> tuple[str, str, str]:
            resp = await chat_create(
                "say",
                messages=[
                    {"role": "system", "content": phase2_system},
                    {"role": "user", "content": prompt_text},
                ],
                temperature=0.8,
                max_tokens=max_tokens,
            )
            raw = resp.choices[0].message.content or ""
            text = _clean_planning(raw)
            text = re.sub(r"\s*\[MEM:\{.*?\}\]", "", text, flags=re.DOTALL).strip()
            text = re.sub(r"\s*\[LEARN:\{.*?\}\]", "", text, flags=re.DOTALL).strip()
            text = re.sub(r"</?(\w+)[^>]*>", "", text, flags=re.DOTALL).strip()
            finish = getattr(resp.choices[0], "finish_reason", "")
            return raw, text, finish

        try:
            raw_content, reply, finish_reason = await _generate(
                say_user_prompt, 400 if short_mode else 600
            )
            # 只輸出 planning、或整段被截斷時，重試一次「直接給台詞」。
            if not reply:
                _log_say_debug(
                    "phase2_empty",
                    short_mode=short_mode,
                    finish=finish_reason,
                    raw_len=len(raw_content),
                    raw=str(raw_content)[:300],
                )
                retry_prompt = (
                    f"【目前對話】\n{context_text}\n\n"
                    "直接輸出澪要說的一句台詞（30 字內），"
                    "不要 <planning>、不要說明、不要引號。"
                )
                raw_content, reply, finish_reason = await _generate(retry_prompt, 300)
            if short_mode and reply:
                # 保險：只留第一句，避免「補一句」變成整段演講
                first = re.split(r"(?<=[。！？!?…])\s*", reply, maxsplit=1)[0].strip()
                reply = first or reply
            _log_say_debug(
                "phase2",
                short_mode=short_mode,
                finish=finish_reason,
                raw_len=len(raw_content),
                reply_len=len(reply),
                reply=reply[:200],
            )
            if not reply:
                _log_say_debug("give_up", reason="模型兩次都沒給台詞")
                await interaction.followup.send(
                    f"（{char_name}想了想，還是沒開口）", ephemeral=True
                )
                return
            await interaction.delete_original_response()
            await channel.send(reply)
            _log_say_debug("sent", reply=reply[:200], channel=str(getattr(channel, "name", "")))
            _log_say_usage(interaction, reply)
            user_content = last_user_msg.content if last_user_msg else "say指令"
            excerpt = re.sub(r'[\\/:*?"<>|\r\n]', "", user_content[:20]) or "say"
            session_ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            session_dir = os.path.join(PROMPT_LOG_DIR, f"{session_ts}_{excerpt}")
            asyncio.create_task(_snapshot_prompt_logs(session_dir, wait=0))
            await phase3_process(
                last_user_msg,
                raw_content,
                "in_character",
                lore_text,
                self_mem_all,
                session_dir,
                all_context,
            )
            asyncio.create_task(_cleanup_old_sessions())
        except Exception as e:
            import traceback
            print(f"[/say] 失敗: {e}")
            print(traceback.format_exc())
            _log_say_debug(
                "exception", error=f"{type(e).__name__}: {e}"[:400],
                trace=traceback.format_exc()[:1200],
            )
            try:
                from main import _log_error
                await _log_error("say_failure", str(e), traceback=traceback.format_exc()[:2500])
            except Exception:
                pass
            try:
                await interaction.followup.send(
                    "（出了點問題，下次再試吧）", ephemeral=True
                )
            except Exception:
                pass
