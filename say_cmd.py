"""/say 指令模組 — 讓角色主動加入對話（完整三階段流程）"""

import discord, json, re, os, datetime


def register_say(bot):
    @bot.tree.command(name="say", description="讓角色主動加入對話（完整三階段流程）")
    async def cmd_say(interaction: discord.Interaction):
        from main import (
            client,
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

        try:
            resp = await client.chat.completions.create(
                model="deepseek-v4-flash",
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
            if not phase1.get("suitable", False):
                await interaction.followup.send(
                    f"（{char_name}覺得現在不是發言的好時機）", ephemeral=True
                )
                return
        except Exception:
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

        try:
            resp = await client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": phase2_system},
                    {
                        "role": "user",
                        "content": f"【目前對話】\n{context_text}\n\n請以{char_name}的身分自然地加入對話。",
                    },
                ],
                temperature=0.8,
                max_tokens=400,
            )
            raw_content = resp.choices[0].message.content or ""
            reply = re.sub(
                r"<planning>.*?</planning>", "", raw_content, flags=re.DOTALL
            ).strip()
            reply = re.sub(r"\s*\[MEM:\{.*?\}\]", "", reply, flags=re.DOTALL).strip()
            reply = re.sub(r"\s*\[LEARN:\{.*?\}\]", "", reply, flags=re.DOTALL).strip()
            reply = re.sub(r"</?(\w+)[^>]*>", "", reply, flags=re.DOTALL).strip()
            if not reply:
                reply = f"（{char_name}看了看周圍，沒說什麼）"
            await interaction.delete_original_response()
            await channel.send(reply)
            user_content = last_user_msg.content if last_user_msg else "say指令"
            excerpt = re.sub(r'[\\/:*?"<>|]', "", user_content[:20]) or "say"
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
            )
            asyncio.create_task(_cleanup_old_sessions())
        except Exception as e:
            print(f"[/say] 失敗: {e}")
            try:
                await interaction.followup.send(
                    "（出了點問題，下次再試吧）", ephemeral=True
                )
            except Exception:
                pass
