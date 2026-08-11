"""
collide_write_cmds.py — 自然語言寫入指令（角色/能力/招式/結界）

把 collide_parser 的 write_* 流程接上 Discord slash command：
    /writechar 描述      寫入或更新角色（collide_characters）
    /writeability 描述   寫入或更新能力（collide_abilities，並鏡像 world_lore）
    /writeskill 描述     寫入或更新招式（collide_skills，含能力原理推導檢查）
    /writedomain 描述    寫入或更新結界（collide_domains）

描述可以是自然語言或完整 JSON；解析與硬驗證都在 collide_parser 內完成，
不合格直接擋下，不寫入。
"""

import traceback

import discord

import collide_parser


def _summary(result: dict, label: str) -> str:
    row = result.get(label) or {}
    status = "新增" if result.get("status") == "insert" else "更新"
    lines = [f"**{label}「{row.get('name', '')}」已寫入（{status}）**"]
    if label == "character":
        lines.append(
            f"種族：{row.get('race','')}｜exp {row.get('exp',0)}｜martial {row.get('martial',0)}｜max_mp {row.get('max_mp',0)}"
        )
        stats = row.get("stats") or {}
        if stats:
            lines.append("六維：" + " / ".join(f"{k} {v}" for k, v in stats.items()))
        if row.get("is_villain"):
            lines.append("（反派，動態難度：" + ("開啟" if row.get("dynamic_difficulty") else "未開啟") + "）")
        prop = result.get("race_mod_proposal") or {}
        if prop.get("error"):
            lines.append(f"⚠️ 種族修正附註：{prop['error']}")
        elif prop:
            mods = prop.get("mods") or {}
            mod_txt = " / ".join(f"{k} {v}" for k, v in mods.items())
            lines.append(
                f"⚠️ 種族「{prop.get('race','')}」修正由 AI 提案已填入種族表（待人工審查）：{mod_txt}"
            )
    elif label == "ability":
        lines.append(f"擁有者：{row.get('owner','（通用）')}")
        if row.get("principle"):
            lines.append(f"能力原理：{row['principle'][:80]}")
    elif label == "skill":
        lines.append(
            f"所屬能力：{row.get('ability','')}｜元素：{row.get('element','無/近戰')}｜威力基數：{row.get('power_base','')}｜耗魔：{row.get('mp_cost',0)}"
        )
        derivation = result.get("derivation") or {}
        if derivation.get("valid") is not None:
            mark = "✅ 合理" if derivation.get("valid") else "⚠️ 不符能力原理"
            lines.append(f"擴展推導：{mark}（{str(derivation.get('reason',''))[:100]}）")
    elif label == "domain":
        lines.append(
            f"擁有者：{row.get('owner','（通用）')}｜類型：{row.get('domain_type','')}｜覺醒：{row.get('awakening','')}｜精度：{row.get('precision','')}"
        )
    return "\n".join(lines)


def _cmd(write_fn, label: str):
    async def handler(interaction: discord.Interaction, 描述: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        text = (描述 or "").strip()
        if not text:
            await interaction.followup.send("❌ 請附上自然語言描述（或完整 JSON）", ephemeral=True)
            return
        try:
            result = await write_fn(text)
        except collide_parser.ParseError as e:
            await interaction.followup.send(f"❌ 解析/驗證失敗：{e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ 寫入失敗：{e}\n```\n{traceback.format_exc(limit=3)[-600:]}\n```",
                ephemeral=True,
            )
            return
        row = result.get(label) or {}
        msg = _summary(result, label)
        extra = (row or {}).get("_errors")
        if extra:
            msg += "\n\n⚠️ 附註（未阻擋）：" + "；".join(extra)
        if len(msg) > 1900:
            msg = msg[:1850] + "..."
        await interaction.followup.send(msg, ephemeral=True)
        try:
            import sheets_sync
            sheets_sync.notify_write(label)
        except Exception:
            pass

    handler.__name__ = f"cmd_{label}"
    return handler


def register_commands(bot):
    @bot.tree.command(name="writechar", description="自然語言寫入/更新角色（招式對撞資料庫）")
    async def cmd_writechar(interaction: discord.Interaction, 描述: str):
        await _cmd(collide_parser.write_character, "character")(interaction, 描述)

    @bot.tree.command(name="writeability", description="自然語言寫入/更新能力（含能力原理，鏡像到 world_lore）")
    async def cmd_writeability(interaction: discord.Interaction, 描述: str):
        await _cmd(collide_parser.write_ability, "ability")(interaction, 描述)

    @bot.tree.command(name="writeskill", description="自然語言寫入/更新招式（含能力原理推導檢查）")
    async def cmd_writeskill(interaction: discord.Interaction, 描述: str):
        await _cmd(collide_parser.write_skill, "skill")(interaction, 描述)

    @bot.tree.command(name="writedomain", description="自然語言寫入/更新結界")
    async def cmd_writedomain(interaction: discord.Interaction, 描述: str):
        await _cmd(collide_parser.write_domain, "domain")(interaction, 描述)

    @bot.tree.command(name="writebatch", description="批次寫入招式/角色/能力/結界（自然語言或多筆含 type 的 JSON 陣列）")
    async def cmd_writebatch(interaction: discord.Interaction, 描述: str):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        text = (描述 or "").strip()
        if not text:
            await interaction.followup.send(
                "❌ 請輸入描述。可用自然語言一次錄入多筆，例：\n"
                "「新增角色凌空（人類，力量B…）；新增能力蒼雷，凌空的能力，原理是…；"
                "新增招式蒼雷轟，屬蒼雷的擴展應用…」\n"
                "或直接貼上含 type 的 JSON 陣列。",
                ephemeral=True,
            )
            return
        try:
            if text.lstrip().startswith("[") or "{\"" in text[:20] or text.lstrip().startswith("{"):
                result = await collide_parser.write_entries(text)
            else:
                result = await collide_parser.write_entries_nl(text)
        except collide_parser.ParseError as e:
            await interaction.followup.send(f"❌ 批次寫入失敗：{e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(
                f"❌ 批次寫入失敗：{e}\n```\n{traceback.format_exc(limit=3)[-600:]}\n```",
                ephemeral=True,
            )
            return
        labels = {"character": "角色", "ability": "能力", "skill": "招式", "domain": "結界"}
        lines = ["**批次寫入完成**"]
        for r in result.get("entries", []):
            t = r.get("type")
            row = r.get("row") or {}
            status = "新增" if r.get("status") == "insert" else "更新"
            name = row.get("name", "")
            line = f"- {labels.get(t, t)}「{name}」已寫入（{status}）"
            derivation = r.get("derivation")
            if derivation is not None:
                mark = "✅ 合理" if derivation.get("valid") else "⚠️ 不符能力原理"
                reason = str(derivation.get("reason", ""))
                line += f"｜擴展推導 {mark}"
                if reason:
                    line += f"（{reason[:80]}）"
            extra = (row or {}).get("_errors")
            if extra:
                line += "｜⚠️ 附註：" + "；".join(extra)
            prop = r.get("race_mod_proposal")
            if prop and not prop.get("error"):
                line += f"｜⚠️ 種族「{prop.get('race','')}」修正 AI 提案已填入（待審查）"
            lines.append(line)
        msg = "\n".join(lines)
        if len(msg) > 1900:
            msg = msg[:1850] + "..."
        await interaction.followup.send(msg, ephemeral=True)
