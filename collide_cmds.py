"""
collide_cmds.py — 對撞系統 Discord 指令

指令：
    /clash              對撞計算（Phase A→B→C，產出草稿＋確認按鈕）
    /clash undo <id>    撤銷已確認的對撞紀錄
    /clash rule         查看公式與規範
    /clash log <thread> 查看該討論串的 Action Log + 成長 Log
    /clash sheet        公開試算表連結
    /grow               樓層總結成長
"""

import json
import os
import traceback
from typing import Optional, Union

import discord
from discord import app_commands

import collide_db
import collide_engine
import collide_growth
import collide_parser
import collide_pipeline
import collide_rules
import sheets_sync
from collide_rules import RULES_TEXT


# ── 工具 ──────────────────────────────────────────────────

def _load_style_rules() -> str:
    """讀取 config.json 組裝伺服器文風規則（與主 RP 正文同一套文風）。

    Phase C 文風輸出需要真正的文風規則；讀不到時退回空字串（不擋流程）。
    """
    try:
        from prompts import build_style_block
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"), encoding="utf-8") as f:
            pcfg = json.load(f)
        return build_style_block(pcfg)
    except Exception:
        return ""


def _conn():
    c = collide_db.open_conn()
    collide_db.init_schema(c)
    return c


def _trunc(text: str, limit: int = 1900) -> str:
    return text[:limit] + "..." if len(text) > limit else text


_PAGE_LIMIT = 1800


def _paginate(text: str, limit: int = _PAGE_LIMIT) -> list[str]:
    """把長文切成多頁，避免在行中間切斷。每頁 ≤ limit 字元。"""
    if len(text) <= limit:
        return [text]
    pages = []
    current = []
    current_len = 0
    for line in text.splitlines():
        if current_len + len(line) + 1 > limit and current:
            pages.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        pages.append("\n".join(current))
    return pages


class _RulePaginationView(discord.ui.View):
    """/clash rule 分頁檢視（前/後按鈕，僅發送者可操作）。"""

    def __init__(self, pages: list[str], user_id: int):
        super().__init__(timeout=300)
        self.pages = pages
        self.user_id = user_id
        self.page = 0
        self.total_pages = len(pages)
        self._update_buttons()

    def _update_buttons(self):
        self.clear_items()
        self.add_item(_RulePrevButton(self.user_id, self.page <= 0))
        self.add_item(_RuleNextButton(self.user_id, self.page >= self.total_pages - 1))

    async def _refresh(self, interaction: discord.Interaction):
        self._update_buttons()
        content = f"**招式對撞系統規則**（第 {self.page + 1}/{self.total_pages} 頁）\n\n{self.pages[self.page]}"
        await interaction.response.edit_message(content=content, view=self)


class _RulePrevButton(discord.ui.Button):
    def __init__(self, user_id: int, disabled: bool):
        super().__init__(label="◀ 上一頁", style=discord.ButtonStyle.secondary, disabled=disabled)
        self._user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("只有發送者可以翻頁", ephemeral=True)
            return
        view: _RulePaginationView = self.view
        if view.page > 0:
            view.page -= 1
            await view._refresh(interaction)


class _RuleNextButton(discord.ui.Button):
    def __init__(self, user_id: int, disabled: bool):
        super().__init__(label="下一頁 ▶", style=discord.ButtonStyle.secondary, disabled=disabled)
        self._user_id = user_id

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("只有發送者可以翻頁", ephemeral=True)
            return
        view: _RulePaginationView = self.view
        if view.page < view.total_pages - 1:
            view.page += 1
            await view._refresh(interaction)


# ── /clash 對撞 ──────────────────────────────────────────

_PREFIX = "clashv1"


def _encode_cid(action: str, sender_id: int, battle_id: int) -> str:
    """custom_id 編碼：<prefix>:<action>:<sender_id>:<battle_id>。"""
    return f"{_PREFIX}:{action}:{sender_id}:{battle_id}"


def parse_cid(custom_id: str) -> tuple[str, int, int] | None:
    """解析 custom_id → (action, sender_id, battle_id)。格式不符回 None。"""
    try:
        prefix, action, sender, battle = custom_id.split(":", 3)
        if prefix != _PREFIX:
            return None
        return action, int(sender), int(battle)
    except (ValueError, AttributeError):
        return None


def _add_button(view: discord.ui.View, label: str, style, custom_id: str, callback) -> None:
    """建立按鈕並指定回呼（避免 decorator 自動產生第二份按鈕）。"""
    btn = discord.ui.Button(label=label, style=style, custom_id=custom_id)
    btn.callback = callback
    view.add_item(btn)


class ClashConfirmView(discord.ui.View):
    """對撞結果確認/重試/取消 + 未登記招式一鍵記錄按鈕。custom_id 編碼發送者 user_id。"""

    def __init__(self, sender_id: int, battle_id: int, thread_id: str,
                 unregistered: Optional[list] = None):
        super().__init__(timeout=300)
        self.sender_id = sender_id
        self.battle_id = battle_id
        self.thread_id = thread_id
        self.unregistered = unregistered or []
        _add_button(self, "確認", discord.ButtonStyle.success,
                    _encode_cid("confirm", sender_id, battle_id), self._on_confirm)
        _add_button(self, "重試", discord.ButtonStyle.secondary,
                    _encode_cid("retry", sender_id, battle_id), self._on_retry)
        _add_button(self, "取消", discord.ButtonStyle.danger,
                    _encode_cid("cancel", sender_id, battle_id), self._on_cancel)
        for info in self.unregistered:
            side = info.get("side", "")
            skill = info.get("skill", "")
            _add_button(
                self,
                f"🗂 一鍵記錄「{skill}」",
                discord.ButtonStyle.primary,
                f"{_PREFIX}:record:{sender_id}:{battle_id}:{side}",
                lambda i, s=side: self._on_record(i, s),
            )
            _add_button(
                self,
                f"📝 填表記錄「{skill}」",
                discord.ButtonStyle.secondary,
                f"{_PREFIX}:form:{sender_id}:{battle_id}:{side}",
                lambda i, s=side: self._on_form(i, s),
            )

    def _check_sender(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.sender_id

    async def _on_form(self, interaction: discord.Interaction, side: str):
        """開啟「當場填表」視窗手動記錄招式（不用猜，直接填）。"""
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("只有發送者可以操作此結果", ephemeral=True)
            return
        info = next((u for u in self.unregistered if u.get("side") == side), None)
        if not info:
            await interaction.response.send_message("沒有可記錄的招式", ephemeral=True)
            return
        await interaction.response.send_modal(SkillRecordModal(info))

    async def _on_record(self, interaction: discord.Interaction, side: str):
        """一鍵把未登記招式寫入招式庫（不詢問，直接記錄）。"""
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("只有發送者可以操作此結果", ephemeral=True)
            return
        info = next((u for u in self.unregistered if u.get("side") == side), None)
        if not info:
            await interaction.response.send_message("沒有可記錄的招式", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        skill = info.get("skill") or ""
        desc = f"招式名稱：{skill}；所有者：{info.get('char') or ''}"
        if info.get("ability"):
            desc += f"；所屬能力：{info['ability']}"
        if info.get("application"):
            desc += f"；擴展應用：{info['application']}"
        desc += "。請依此輸出該招式的完整招式資料（含威力基數、耗魔、元素、距離）。"
        try:
            result = await collide_parser.write_skill(desc)
        except Exception as e:
            await interaction.followup.send(
                f"❌ 自動記錄失敗：{e}\n（可改用旁邊的「📝 填表記錄」手動填寫）",
                ephemeral=True,
            )
            return
        try:
            sheets_sync.notify_write("skill")
        except Exception:
            pass
        row = result.get("skill") or {}
        status = "新增" if result.get("status") == "insert" else "更新"
        deriv = result.get("derivation") or {}
        lines = [
            f"✅ 招式「{row.get('name', skill)}」已記錄（{status}）",
            f"所屬能力：{row.get('ability', '（通用魔法）')}｜威力基數：{row.get('power_base', '')}｜耗魔：{row.get('mp_cost', 0)}｜元素：{row.get('element', '無')}",
        ]
        if deriv.get("valid") is not None:
            mark = "✅ 合理" if deriv.get("valid") else "⚠️ 不符能力原理"
            lines.append(f"擴展推導：{mark}（{str(deriv.get('reason', ''))[:100]}）")
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    async def _on_confirm(self, interaction: discord.Interaction):
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("只有發送者可以操作此結果", ephemeral=True)
            return
        await interaction.response.send_message(
            "確認後將生成對撞文本並寫入紀錄，是否繼續？（再次點擊確認）",
            view=ClashFinalConfirmView(self.sender_id, self.battle_id, self.thread_id),
            ephemeral=True,
        )

    async def _on_retry(self, interaction: discord.Interaction):
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("只有發送者可以操作此結果", ephemeral=True)
            return
        await interaction.response.send_modal(
            ClashRetryModal(self.sender_id, self.battle_id, self.thread_id, self)
        )

    async def disable(self):
        """停用全部按鈕（重試成功後避免重複操作同一草稿）。"""
        for item in self.children:
            item.disabled = True
        try:
            if getattr(self, "message", None):
                await self.message.edit(view=self)
        except Exception:
            pass

    async def _on_cancel(self, interaction: discord.Interaction):
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("只有發送者可以操作此結果", ephemeral=True)
            return
        conn = _conn()
        collide_db.update_battle_status(conn, self.battle_id, "cancelled")
        conn.close()
        await interaction.response.send_message("已取消該次對撞草稿。", ephemeral=True)
        self.stop()


class ClashFinalConfirmView(discord.ui.View):
    """二次確認：正式寫入 active 並觸發 Phase C。"""

    def __init__(self, sender_id: int, battle_id: int, thread_id: str):
        super().__init__(timeout=60)
        self.sender_id = sender_id
        self.battle_id = battle_id
        self.thread_id = thread_id
        _add_button(self, "確認寫入", discord.ButtonStyle.success,
                    _encode_cid("final", sender_id, battle_id), self._on_final)

    async def _on_final(self, interaction: discord.Interaction):
        if interaction.user.id != self.sender_id:
            await interaction.response.send_message("只有發送者可以確認", ephemeral=True)
            return
        conn = _conn()
        collide_db.update_battle_status(conn, self.battle_id, "active")
        battle = collide_db.get_battle(conn, self.battle_id)
        clash_line = (
            _commit_clash_settlement(conn, _parse_json_field(battle.get("clash_meta") or {}))
            if battle else ""
        )
        conn.close()
        await interaction.response.send_message(
            f"對撞紀錄 #{self.battle_id} 已確認（status=active）。文風輸出 Phase C 正在生成…",
            ephemeral=True,
        )
        self.stop()

        text = (battle.get("phase_c") or "").strip() if battle else ""
        if not text:
            await interaction.followup.send(
                "⚠️ 找不到該場的文風正文（phase_c 為空）。可用「重試」重新對撞。",
                ephemeral=True,
            )
            return
        try:
            channel = interaction.client.get_channel(int(self.thread_id)) or interaction.channel
        except (TypeError, ValueError):
            channel = interaction.channel
        if clash_line:
            text = f"{text}\n\n──────────────\n{clash_line}"
        if channel and hasattr(channel, "send"):
            await channel.send(text)
        else:
            await interaction.followup.send("⚠️ 無法找到原討論串發布正文。", ephemeral=True)


class SkillRecordModal(discord.ui.Modal, title="記錄招式（當場填表）"):
    """手動填寫未登記招式資料並寫入招式庫（不靠 LLM 猜，保證寫入）。"""

    def __init__(self, info: dict):
        super().__init__(timeout=300)
        self.info = info or {}
        skill = info.get("skill") or ""
        ability = info.get("ability") or ""
        application = info.get("application") or ""
        est = info.get("estimate") if isinstance(info.get("estimate"), dict) else {}
        power = est.get("power_base") or "5.0"
        mp = est.get("mp_cost") or "10"
        element = est.get("element") or ""
        msys = est.get("magic_system") or ""
        rng = est.get("range") or ""
        stype = est.get("skill_type") or "個人魔法"
        effect = est.get("effect") or ""
        self.f1 = discord.ui.TextInput(
            label="招式名稱／所屬能力（用／分隔；通常魔法能力留空）",
            default=f"{skill}／{ability}",
            max_length=100, required=True,
        )
        self.f2 = discord.ui.TextInput(
            label="威力基數／耗魔（1.0~10.0／1~25）",
            default=f"{power}／{mp}",
            max_length=30, required=True,
        )
        self.f3 = discord.ui.TextInput(
            label="元素／魔法體系（如 火／自然系；超能系或通常魔法元素留空）",
            default=f"{element}／{msys}",
            max_length=60, required=False,
        )
        self.f4 = discord.ui.TextInput(
            label="有效距離／擴展應用（如 極限60公尺／擴展一：…）",
            default=f"{rng}／{application}",
            max_length=160, required=False,
        )
        self.f5 = discord.ui.TextInput(
            label="技能類型／附加效果（個人魔法或通常魔法／效果描述）",
            default=f"{stype}／{effect}",
            max_length=160, required=True,
        )
        self.add_item(self.f1)
        self.add_item(self.f2)
        self.add_item(self.f3)
        self.add_item(self.f4)
        self.add_item(self.f5)

    @staticmethod
    def _split(v: str) -> tuple[str, str]:
        for sep in ("／", "/", "|"):
            if sep in (v or ""):
                a, b = (v or "").split(sep, 1)
                return a.strip(), b.strip()
        return (v or "").strip(), ""

    def _build(self) -> dict:
        name, ability = self._split(self.f1.value)
        power, mp = self._split(self.f2.value)
        elem, msys = self._split(self.f3.value)
        rng, app = self._split(self.f4.value)
        stype, effect = self._split(self.f5.value)
        if not name:
            raise ValueError("招式名稱不可為空")
        try:
            power = round(float(power), 2)
        except (TypeError, ValueError):
            raise ValueError("威力基數不是有效數字")
        if not (1.0 <= power <= 10.0):
            raise ValueError("威力基數須在 1.0~10.0")
        try:
            mp = int(float(mp))
        except (TypeError, ValueError):
            raise ValueError("耗魔不是有效數字")
        if not (1 <= mp <= 25):
            raise ValueError("耗魔須在 1~25")
        if not stype:
            stype = "個人魔法"
        if stype == "通常魔法":
            ability, msys, elem = "", "", ""
        return {
            "name": name,
            "ability": ability,
            "owner": self.info.get("char") or "",
            "application": app or "擴展一：自訂擴展",
            "skill_type": stype,
            "magic_system": msys,
            "element": elem,
            "range": rng,
            "mp_cost": mp,
            "power_base": power,
            "effect": effect,
        }

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            data = self._build()
            conn = _conn()
            row = collide_db.upsert_skill(conn, data)
            conn.commit()
            conn.close()
        except ValueError as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ 記錄失敗：{e}", ephemeral=True)
            return
        try:
            sheets_sync.notify_write("skill")
        except Exception:
            pass
        await interaction.followup.send(
            f"✅ 招式「{row.get('name')}」已記錄\n"
            f"所屬能力：{row.get('ability') or '（通用魔法）'}｜威力基數：{row.get('power_base')}｜"
            f"耗魔：{row.get('mp_cost')}｜元素：{row.get('element') or '無'}｜類型：{row.get('skill_type')}",
            ephemeral=True,
        )


class ClashRetryModal(discord.ui.Modal, title="重試對撞"):
    """重試時輸入額外資訊（補充說明），附加到討論串歷史後重新計算。"""

    def __init__(self, sender_id: int, battle_id: int, thread_id: str, view: "ClashConfirmView"):
        super().__init__()
        self._sender_id = sender_id
        self._battle_id = battle_id
        self._thread_id = thread_id
        self._view = view
        self._supplement = discord.ui.TextInput(
            label="補充說明（可空）",
            placeholder="例：甲在張開結界前先凝聚水牢範圍、雙方距離拉到 30 公尺、甲已受傷、這次雙方直接對撞…",
            style=discord.TextStyle.paragraph,
            max_length=1000,
            required=False,
        )
        self.add_item(self._supplement)

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.user.id != self._sender_id:
            await interaction.response.send_message("只有發送者可以操作此結果", ephemeral=True)
            return
        await self._view.disable()
        await interaction.response.defer()
        out = await _perform_clash(
            interaction, self._thread_id, supplement=self._supplement.value or ""
        )
        if not out:
            return
        result, conn = out
        # 原草稿被重試取代 → 標記 cancelled，避免堆疊未確認草稿
        collide_db.update_battle_status(conn, self._battle_id, "cancelled")
        await _post_draft(interaction, result, self._thread_id, conn)


# ── /clash undo ───────────────────────────────────────────

async def _do_undo(interaction: discord.Interaction, battle_id: int, reason: str):
    conn = _conn()
    battle = collide_db.get_battle(conn, battle_id)
    if not battle:
        await interaction.followup.send(f"找不到對撞紀錄 #{battle_id}", ephemeral=True)
        conn.close()
        return
    if battle["status"] != "active":
        await interaction.followup.send(
            f"對撞紀錄 #{battle_id} 狀態為「{battle['status']}」，只有 active 可撤銷", ephemeral=True
        )
        conn.close()
        return
    collide_db.update_battle_status(
        conn, battle_id, "undone",
        undone_by=str(interaction.user), undone_reason=reason,
    )
    growth_list = collide_db.growth_log_for_battle(conn, battle_id)
    conn.close()
    lines = [f"**對撞 #{battle_id} 已撤銷**（{interaction.user}：{reason or '無'}）"]
    if growth_list:
        lines.append(f"\n**該場衍生的成長紀錄（{len(growth_list)} 筆），請管理員逐筆確認是否需回滾：**")
        for g in growth_list[:20]:
            lines.append(
                f"- {g.get('target_name','')} `{g.get('field','')}` "
                f"{g.get('old_value','')}→{g.get('new_value','')}（{g.get('delta','')}）"
                f"｜floor_event_id:`{g.get('floor_event_id','')}`"
            )
    else:
        lines.append("（無關聯成長紀錄）")
    await interaction.followup.send(_trunc("\n".join(lines)))


# ── /grow ─────────────────────────────────────────────────

async def _do_grow(interaction: discord.Interaction, thread_id: str):
    """樓層成長：掃描討論串→記錄能力使用→套用成長（首次=總結、之後=重跑）。

    LLM 掃描需要 DEEPSEEK_API_KEY；未設定時給出說明，不寫入。
    """
    conn = _conn()
    try:
        # 讀取討論串尾部
        history_lines = []
        channel = interaction.channel
        if channel and getattr(channel, "history", None) and callable(channel.history):
            async for msg in channel.history(limit=200):
                if not msg.author.bot:
                    history_lines.append(f"{msg.author.display_name}: {msg.content}")
        history = "\n".join(reversed(history_lines)) or "（無法讀取討論串）"

        if not os.environ.get("DEEPSEEK_API_KEY"):
            await interaction.followup.send(
                "⚠️ `/grow` 需要 LLM 掃描討論串（DEEPSEEK_API_KEY）。"
                "資料層已就緒（`collide_growth.apply_growth` / `record_ability_usage_bulk`），"
                "設定金鑰後即可使用。",
                ephemeral=True,
            )
            return

        try:
            parsed = await _scan_growth(history, thread_id)
        except Exception as e:
            await interaction.followup.send(
                f"❌ 成長掃描失敗：{e}\n```\n{traceback.format_exc(limit=3)[-400:]}\n```",
                ephemeral=True,
            )
            return

        usages = parsed.get("usages", []) or []
        growths = parsed.get("growths", []) or []

        usage_res = collide_growth.record_ability_usage_bulk(
            conn, thread_id=thread_id, usages=usages,
        )
        grow_res = collide_growth.apply_growth(
            conn,
            thread_id=thread_id,
            thread_name=(interaction.channel.name if channel else thread_id),
            growths=growths,
        )
        try:
            import sheets_sync
            sheets_sync.notify_write("character")
            sheets_sync.notify_write("skill")
        except Exception:
            pass
        lines = [
            f"**樓層成長完成**（{thread_id}｜{grow_res['mode']}）",
            f"能力使用：新增 {usage_res['inserted']} 筆，更新 {usage_res['updated']} 筆",
            f"數值成長：新增 {grow_res['inserted']} 筆，覆寫 {grow_res['updated']} 筆",
            f"summary_generated：{grow_res['summary_generated']}",
        ]
        if usages:
            names = ", ".join(
                f"{u.get('actor','')}·{u.get('ability','')}" for u in usages[:15]
            )
            lines.append(f"\n**本樓層使用能力：**{names}")
        if growths:
            lines.append("\n**成長項目：**")
            for g in growths[:15]:
                lines.append(
                    f"- {g.get('target_name','')} `{g.get('field','')}` "
                    f"{g.get('old_value','')}→{g.get('new_value','')}（{g.get('delta','')}）"
                )
        await interaction.followup.send(_trunc("\n".join(lines)), ephemeral=True)
    finally:
        conn.close()


async def _scan_growth(history: str, thread_id: str) -> dict:
    """以 LLM 掃描討論串，回傳 {usages: [{actor, ability, detail?}], growths: [...]}。"""
    system = (
        "你是樓層成長分析器。根據討論串內容，判斷："
        "1) usages：本樓層使用過的能力（actor + ability，來自角色自述或招式名）\n"
        "2) growths：依 GROWTH_RULES_TEXT 規則產生的數值成長項目，每筆為 "
        "{category, target_name, field, old_value, new_value, delta, reason}；"
        "成長的 old_value 以角色當前值為準，無法確定時 reason 註明「待管理員確認」。\n"
        "只輸出 JSON：{\"usages\": [...], \"growths\": [...]}，不要額外文字。\n\n"
        "規則摘要：" + collide_rules.GROWTH_RULES_TEXT[:1200]
    )
    return await collide_pipeline._call_json(
        system, f"討論串（{thread_id}）：\n{history[-4000:]}", temperature=0.2,
        max_tokens=2500, label="growth scan",
    )


# ── 對撞共用流程（/clash 與 重試 modal 共用）──────────────

async def _read_clash_history(channel, limit: int = 30, max_chars: int = 8000) -> str:
    """讀取討論串尾部供對撞分析。

    對撞只需最近幾則（雙方出招的動作），舊訊息是背景噪音；限制訊息數並壓縮
    總長度，避免深色引擎收到過多資訊而思考過久。保留**最新訊息完整**，
    預算超標時只截斷最舊的訊息。
    """
    lines = []
    if channel and hasattr(channel, "history"):
        async for msg in channel.history(limit=limit):
            content = (msg.content or "").strip()
            if not content:
                continue
            lines.append(f"{msg.author.display_name}: {content}")
    if not lines:
        return "（無法讀取討論串）"
    # channel.history 回傳「最新在前」；從最新開始保留完整，超預算時截斷最舊
    kept: list[str] = []
    total = 0
    for line in lines:
        if total + len(line) + 1 <= max_chars:
            kept.append(line)
            total += len(line) + 1
        else:
            remaining = max_chars - total - 1
            if remaining > 20:
                kept.append(line[:remaining] + "…")
            break
    return "\n".join(reversed(kept))


async def _perform_clash(
    interaction: discord.Interaction,
    thread_id: str,
    supplement: str = "",
    channel=None,
) -> tuple[dict, object] | None:
    """讀歷史→run_clash→回傳 (result, conn)。失敗時已送出錯誤訊息並回 None。

    channel 指定對撞來源（選定的頻道/討論串）；省略時依 thread_id 嘗試解析，
    仍失敗則退回指令所在頻道。
    """
    if channel is None:
        try:
            channel = interaction.client.get_channel(int(thread_id)) or interaction.channel
        except (TypeError, ValueError):
            channel = interaction.channel
    conn = _conn()
    db_lookup = collide_db.make_db_lookup(conn)
    try:
        history = await _read_clash_history(channel)
        if supplement:
            history += f"\n（補充說明：{supplement}）"
        result = await collide_pipeline.run_clash(history, _load_style_rules(), db_lookup, conn=conn)
    except collide_pipeline.ClashError as e:
        await interaction.followup.send(f"❌ 對撞失敗：{e}")
        conn.close()
        return None
    except Exception as e:
        await interaction.followup.send(
            f"❌ 對撞失敗：{e}\n```\n{traceback.format_exc(limit=3)[-500:]}\n```"
        )
        conn.close()
        return None

    if result.get("no_clash"):
        await interaction.followup.send("無對撞發生（雙方皆為非對抗性動作）。")
        conn.close()
        return None
    return result, conn


def _parse_json_field(v):
    """把 DB 欄位的 JSON 字串解析回 dict/list；空/壞檔回空 dict。"""
    if isinstance(v, str) and v.strip():
        try:
            return json.loads(v)
        except (ValueError, TypeError):
            return {}
    if isinstance(v, (dict, list)):
        return v
    return {}


def _commit_clash_settlement(conn, clash_meta: dict) -> str:
    """Clash v2：確認對撞時把草稿期的血條投影正式寫入 Ledger（原子，重放安全）。

    回傳 commit 後的血條＋理由行；無 Clash 結算或重放（重複節點）回空字串。
    """
    if not isinstance(clash_meta, dict) or not clash_meta:
        return ""
    bid = str(clash_meta.get("battle_id") or "").strip()
    if not bid:
        return ""
    battle = collide_db.get_clash_battle(conn, bid)
    if not battle or str(battle.get("outcome") or "") != "active":
        return ""
    player_bps = [float(x) for x in (clash_meta.get("player_bps") or [])]
    if not player_bps:
        return ""
    holding_back = [bool(x) for x in (clash_meta.get("holding_back") or [])]
    settled = collide_engine.resolve_clash_node(
        player_bps,
        float(battle["guard_value"]),
        cap_ticks=int(battle["cap_ticks"]),
        current_hp_ticks=int(battle["current_hp_ticks"]),
        retreat_floor=int(battle["retreat_floor_ticks"]),
        holding_back=holding_back,
    )
    node_id = (
        str(clash_meta.get("node_id") or "").strip()
        or collide_db.next_clash_node_id(conn, bid)
    )
    from datetime import datetime

    outcome = (
        None
        if settled["outcome"] == collide_engine.CLASH_OUTCOME_ACTIVE
        else settled["outcome"]
    )
    updated = collide_db.record_clash_settlement(
        conn,
        event_id=f"{bid}-{node_id}",
        battle_id=bid,
        node_id=node_id,
        occurred_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        delta_ticks=int(settled["total_delta"]),
        chip=bool(settled["chip"]),
        aggressors=[str(x) for x in (clash_meta.get("aggressors") or [])],
        source="parsed",
        confidence=1.0,
        confirmed=True,
        new_hp_ticks=int(settled["new_hp_ticks"]),
        outcome=outcome,
    )
    if updated is None:
        return ""
    bar = collide_engine.format_clash_hp_bar(
        int(updated["current_hp_ticks"]), int(updated["max_hp_ticks"])
    )
    return collide_pipeline._clash_reason_line(
        bar,
        total_delta=int(settled["total_delta"]),
        chip=bool(settled["chip"]),
        at_retreat_floor=bool(settled["at_retreat_floor"]),
        outcome=str(updated.get("outcome") or "active"),
        villain=str(updated.get("villain_name") or clash_meta.get("villain") or "反派"),
    )


def _phase_b_from_battle(b: dict) -> dict:
    """由 collide_battles 一列重建 phase_b（供 Phase C 重新生成文風正文）。

    只含 Phase C 需要的判定結果欄位（不含計算細節）。result 以 JSON 字串存入 DB，
    這裡解析回 dict；解析失敗回空 dict（Phase C 仍可依 verdict/attacker 等欄位運作）。
    """
    result = _parse_json_field(b.get("result"))
    if not isinstance(result, dict):
        result = {}
    return {
        "verdict": b.get("verdict", ""),
        "result": result,
        "attacker": b.get("attacker", ""),
        "defender": b.get("defender", ""),
        "computation": {
            "attacker": {"char": b.get("attacker", ""), "skill": b.get("attacker_skill", "")},
            "defender": {"char": b.get("defender", ""), "skill": b.get("defender_skill", "")},
        },
        "domain_fusion": bool(b.get("domain_fusion")),
        "domain_override": {},
        "domains": None,
        "fusion_effects": None,
        "remaining_damage": b.get("remaining_damage", ""),
        "effects": _parse_json_field(b.get("effects")),
    }


def _clash_detail_lines(phase_b: dict) -> list[str]:
    """草稿的完整計算結果明細（BP 比對＋完整計算過程＋距離/偷襲/結界/警告）。"""
    r = phase_b.get("result") or {}
    comp = phase_b.get("computation") or {}
    atk = comp.get("attacker") or {}
    def_ = comp.get("defender") or {}
    lines = [
        f"**判定：{r.get('verdict', phase_b.get('verdict', ''))}**",
        f"**摘要：**{r.get('summary', '')}",
        f"{atk.get('char', '?')}（BP {atk.get('bp', '?')}） vs {def_.get('char', '?')}（BP {def_.get('bp', '?')}）",
    ]
    if r.get("projection"):
        lines.append(f"**預估結果：**{r['projection']}")
    if r.get("reason"):
        lines.append(f"**理由：**{r['reason']}")

    cot = phase_b.get("chain_of_thought") or ""
    if cot.strip():
        lines.append("")
        lines.append("**完整計算過程（供審查）：**")
        lines.append(cot.strip())

    if phase_b.get("remaining_damage"):
        lines.append("")
        lines.append(f"**剩餘衝擊：**{phase_b['remaining_damage']}")

    fx = phase_b.get("effects") or {}
    fx_applied = fx.get("applied") or []
    if fx_applied:
        lines.append("")
        lines.append("**招式效果（機械套用）：**")
        lines.extend(f"- {l}" for l in fx_applied)
    for side, label in (("attacker", "攻擊方"), ("defender", "防禦方")):
        effects = fx.get(side) or []
        if effects:
            names = "、".join(str(e.get("note") or e.get("type") or "?") for e in effects)
            lines.append(f"- {label}效果：{names}")

    rng = phase_b.get("range") or {}
    for label, char in (("攻擊方", atk.get("char")), ("防禦方", def_.get("char"))):
        info = rng.get(char) or {}
        mod = info.get("mod")
        if mod is not None and float(mod) != 1.0:
            lines.append(
                f"**距離修正（{label}・{char}）：**Range_Mod = {mod}"
                + (f"；{info.get('reason', '')}" if info.get("reason") else "")
            )

    ambush = phase_b.get("ambush") or {}
    if ambush.get("active"):
        lines.append(
            f"**偷襲劣勢：**{ambush.get('target', '')} 被偷襲（Ambush_Mod = 0.8）"
            + (f"：{ambush.get('desc', '')}" if ambush.get("desc") else "")
        )

    dom_ovr = phase_b.get("domain_override") or {}
    if dom_ovr.get("applied"):
        lines.append(
            f"**結界覆蓋/彌補：**投入魔力 {dom_ovr.get('mp_invested', 0)}，"
            f"有效差距 {dom_ovr.get('effective_gap', 0)} → {dom_ovr.get('result', '')}"
        )

    warnings = phase_b.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("**警告：**")
        lines.extend(f"- {w}" for w in warnings)

    return lines


async def _post_draft(
    interaction: discord.Interaction,
    result: dict,
    thread_id: str,
    conn,
):
    """把對撞結果寫入 pending 紀錄並貼出草稿＋確認按鈕（會關閉 conn）。"""
    phase_b = result.get("phase_b") or {}
    phase_a = result.get("phase_a") or {}
    comp = phase_b.get("computation") or {}
    atk = comp.get("attacker") or {}
    def_ = comp.get("defender") or {}

    # 記錄雙方招式來源資訊（供「一鍵記錄」按鈕重建招式資料）
    skills_meta = {}
    unregistered = []
    for side in ("attacker", "defender"):
        cand = phase_a.get(f"{side}_candidate") or {}
        est = (phase_a.get("skill_estimates") or {}).get(side)
        info = {
            "skill": (cand.get("skill") or "").strip(),
            # 所有者以「出招的角色」為準（Phase A candidate），
            # 不能用 comp（計算後會依威力交換攻守，導致持有者配錯）
            "char": (cand.get("char") or "").strip()
                    or (est.get("owner") if isinstance(est, dict) else "") or "",
            "ability": (phase_a.get("abilities") or {}).get(side) or "",
            "application": (phase_a.get("applications") or {}).get(side) or "",
            "estimate": est if isinstance(est, dict) else None,
        }
        sname = info["skill"]
        if sname and not collide_db.get_skill_named(conn, sname):
            info["unregistered"] = True
            unregistered.append({**info, "side": side})
        skills_meta[side] = info

    battle = collide_db.insert_battle(conn, {
        "thread_id": thread_id,
        "attacker": atk.get("char", ""),
        "attacker_skill": atk.get("skill", ""),
        "defender": def_.get("char", ""),
        "defender_skill": def_.get("skill", ""),
        "atk_output": atk.get("bp"),
        "def_output": def_.get("bp"),
        "verdict": phase_b.get("verdict", ""),
        "result": phase_b.get("result"),
        "remaining_damage": phase_b.get("remaining_damage", ""),
        "phase_c": result.get("phase_c", ""),
        "skills_meta": skills_meta,
        "domain_fusion": phase_b.get("domain_fusion", False),
        "chain_of_thought": phase_b.get("chain_of_thought", ""),
        "effects": phase_b.get("effects") or {},
        "clash_meta": result.get("clash") or {},
        "status": "pending",
    })
    conn.close()
    try:
        import sheets_sync
        sheets_sync.notify_write("battle")
    except Exception:
        pass

    lines = [f"**對撞計算結果 #{battle['id']}**（草稿，待確認）"]
    lines += _clash_detail_lines(phase_b)
    clash = result.get("clash") or {}
    if clash.get("warning"):
        lines.append("")
        lines.append(str(clash["warning"]))
    elif clash.get("reason_line"):
        lines.append("")
        lines.append(f"**反派血條（草稿投影，確認後寫入）：**{clash['reason_line']}")
        parts = clash.get("aggressors") or []
        if len(parts) > 1:
            lines.append(f"**參與者（{len(parts)} 人）：**{'、'.join(str(p) for p in parts)}")
    pages = _paginate("\n".join(lines))

    view = ClashConfirmView(interaction.user.id, battle["id"], thread_id, unregistered=unregistered)
    for i, page in enumerate(pages):
        await interaction.followup.send(page, view=view if i == 0 else None)


# ── 註冊 ─────────────────────────────────────────────────

def register_commands(bot):
    @bot.tree.command(name="clash", description="對撞計算（讀取討論串→Phase A/B/C→草稿＋確認按鈕）")
    @app_commands.describe(
        討論串="要對撞的討論串（從選單選，省略=目前頻道/討論串）",
        頻道="要對撞的文字頻道/論壇頻道（討論串優先，省略=目前頻道）",
    )
    async def cmd_clash(
        interaction: discord.Interaction,
        討論串: discord.Thread = None,
        頻道: Union[discord.TextChannel, discord.ForumChannel] = None,
    ):
        try:
            await interaction.response.defer()
        except Exception:
            return
        source = 討論串 or 頻道 or interaction.channel
        thread_id = str(source.id)
        out = await _perform_clash(interaction, thread_id, channel=source)
        if not out:
            return
        result, conn = out
        await _post_draft(interaction, result, thread_id, conn)

    @bot.tree.command(name="clash_undo", description="撤銷已確認的對撞紀錄")
    @app_commands.describe(battle_id="對撞紀錄 ID", 原因="撤銷原因")
    async def cmd_clash_undo(interaction: discord.Interaction, battle_id: int, 原因: Optional[str] = ""):
        try:
            await interaction.response.defer()
        except Exception:
            return
        await _do_undo(interaction, battle_id, 原因 or "")

    @bot.tree.command(name="clash_repost", description="重新生成並發布某場對撞的 Phase C 文風正文（修復確認後沒出文的狀況）")
    @app_commands.describe(battle_id="對撞紀錄 ID")
    async def cmd_clash_repost(interaction: discord.Interaction, battle_id: int):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        conn = _conn()
        battle = collide_db.get_battle(conn, battle_id)
        conn.close()
        if not battle:
            await interaction.followup.send(f"找不到對撞紀錄 #{battle_id}", ephemeral=True)
            return
        if not (battle.get("phase_c") or "").strip():
            try:
                text = await collide_pipeline.phase_c(_phase_b_from_battle(battle), _load_style_rules())
            except collide_pipeline.ClashError as e:
                await interaction.followup.send(f"❌ Phase C 生成失敗：{e}", ephemeral=True)
                return
        else:
            text = battle["phase_c"]
        try:
            channel = interaction.client.get_channel(int(battle["thread_id"])) or interaction.channel
        except (TypeError, ValueError):
            channel = interaction.channel
        if channel and hasattr(channel, "send"):
            try:
                await channel.send(text)
            except Exception as e:
                await interaction.followup.send(f"發布失敗：{e}", ephemeral=True)
                return
        conn = _conn()
        conn.execute("UPDATE collide_battles SET phase_c = ? WHERE id = ?", (text, battle_id))
        conn.commit()
        conn.close()
        try:
            import sheets_sync
            sheets_sync.notify_write("battle")
        except Exception:
            pass
        await interaction.followup.send(
            f"已發布對撞紀錄 #{battle_id} 的 Phase C 正文。", ephemeral=True
        )

    @bot.tree.command(name="clash_rule", description="查看對撞公式與規範")
    async def cmd_clash_rule(interaction: discord.Interaction):
        pages = _paginate(RULES_TEXT)
        if len(pages) == 1:
            await interaction.response.send_message(
                f"**招式對撞系統規則**\n\n{pages[0]}", ephemeral=True
            )
            return
        view = _RulePaginationView(pages, interaction.user.id)
        await interaction.response.send_message(
            f"**招式對撞系統規則**（第 1/{len(pages)} 頁）\n\n{pages[0]}",
            view=view, ephemeral=True,
        )

    @bot.tree.command(name="clash_log", description="查看討論串的 Action Log + 成長 Log")
    @app_commands.describe(討論串="目標討論串 ID")
    async def cmd_clash_log(interaction: discord.Interaction, 討論串: str):
        conn = _conn()
        logs = collide_growth.growth_log_for_floor(conn, 討論串)
        conn.close()
        if not logs:
            await interaction.response.send_message(f"討論串 {討論串} 無成長紀錄。", ephemeral=True)
            return
        lines = [f"**{討論串} 成長紀錄（{len(logs)} 筆）**"]
        for g in logs[:30]:
            lines.append(
                f"- {g.get('target_name','')} `{g.get('field','')}` "
                f"{g.get('old_value','')}→{g.get('new_value','')}（{g.get('delta','')}）"
                f"｜{g.get('reason','')}｜battle_id:{g.get('battle_id','')}"
            )
        await interaction.response.send_message(_trunc("\n".join(lines)), ephemeral=True)

    @bot.tree.command(name="clash_sheet", description="公開試算表連結")
    async def cmd_clash_sheet(interaction: discord.Interaction):
        sheet_id = sheets_sync.get_spreadsheet_id()
        if sheet_id:
            url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit?usp=sharing"
            await interaction.response.send_message(f"公開試算表：{url}")
        else:
            await interaction.response.send_message(
                "尚未設定 Google Sheets（config.json google_sheets.spreadsheet_id 或環境變數 GOOGLE_SHEETS_ID）",
                ephemeral=True,
            )

    @bot.tree.command(name="grow", description="樓層總結成長（依草案成長規範）")
    @app_commands.describe(討論串="目標討論串 ID（省略=當前頻道）")
    async def cmd_grow(interaction: discord.Interaction, 討論串: Optional[str] = None):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        thread_id = 討論串 or str(interaction.channel_id)
        await _do_grow(interaction, thread_id)

    @bot.tree.command(name="clash_purge", description="刪除本頻道/討論串內 bot 發出的對撞草稿訊息（洗版清理）")
    @app_commands.describe(數量="往回掃描幾則訊息（預設 100，最多 500）")
    async def cmd_clash_purge(interaction: discord.Interaction, 數量: Optional[int] = None):
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            return
        channel = interaction.channel
        if not channel or not hasattr(channel, "history"):
            await interaction.followup.send("此頻道無法讀取訊息。", ephemeral=True)
            return
        limit = min(max(數量 or 100, 1), 500)
        me = interaction.client.user.id
        deleted = 0
        async for msg in channel.history(limit=limit):
            if msg.author.id != me:
                continue
            content = (msg.content or "")
            if content.startswith("**對撞計算結果") or "（草稿，待確認）" in content:
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass
        await interaction.followup.send(
            f"已刪除 {deleted} 則對撞草稿訊息（掃描最近 {limit} 則）。",
            ephemeral=True,
        )
