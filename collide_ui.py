"""
collide_ui.py — 對撞系統 HTML 管理頁面後端（FastAPI 本地伺服器）

提供：
- 成長 log 檢視 + 人工審查修正（修正新增 source=manual 的新 log，全程可追溯）
- Action Log / 樓層查閱
- 資料表查閱（角色 / 招式 / 結界 / 對撞紀錄）
- Sheets 同步狀態 + 手動觸發同步

啟動：
    python collide_ui.py          # http://127.0.0.1:8787
    python collide_ui.py --port 9000

僅綁定本機（127.0.0.1）；如需外網請自行加反向代理並設定權限。
"""

import argparse
import json
import sys
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import collide_db
import collide_growth
import sheets_sync
from collide_engine import STAT_KEYS, hidden_score

SCRIPT_DIR = __import__("os").path.dirname(__import__("os").path.abspath(__file__))

app = FastAPI(title="招式對撞系統管理頁", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os = __import__("os")


def _conn():
    c = collide_db.open_conn()
    collide_db.init_schema(c)
    return c


# ── API：資料表查閱 ───────────────────────────────────────

def _parse_json(v, default):
    """把 DB 讀出的 JSON 字串欄位解析成 dict/list；已是物件則原樣回傳。"""
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        v = v.strip()
        if v:
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return default
    return default


@app.get("/api/tables/characters")
def api_characters():
    conn = _conn()
    try:
        chars = collide_db.list_characters(conn)
        for ch in chars:
            ch["stats"] = _parse_json(ch.get("stats"), {})
            ch["special_tags"] = _parse_json(ch.get("special_tags"), [])
            ch["special_forms"] = _parse_json(ch.get("special_forms"), {})
            ch["calibrate_roster"] = _parse_json(ch.get("calibrate_roster"), [])
            mods = collide_db.race_mods_for(ch.get("race") or "")
            ch["_race_mods"] = mods
            stats = ch["stats"]
            ch["hidden"] = {}
            for s in STAT_KEYS:
                rating = stats.get(s)
                ch["hidden"][s] = (
                    hidden_score(rating, float(mods.get(s) or 1.0)) if rating else None
                )
        return chars
    finally:
        conn.close()


@app.get("/api/tables/skills")
def api_skills():
    conn = _conn()
    try:
        return collide_db.list_skills(conn)
    finally:
        conn.close()


@app.get("/api/tables/abilities")
def api_abilities():
    conn = _conn()
    try:
        return collide_db.list_abilities(conn)
    finally:
        conn.close()


class SkillUpdateBody(BaseModel):
    name: str = ""
    ability: str = ""
    owner: str = ""
    application: str = ""
    skill_type: str = ""
    magic_system: str = ""
    element: str = ""
    range: str = ""
    mp_cost: int = 0
    power_base: float = 1.0
    effect: str = ""


@app.put("/api/tables/skills/{name}/{owner}")
def api_skill_update(name: str, owner: str, body: SkillUpdateBody):
    """更新一筆招式（欄位以 body 為準；name/owner 改變時搬移資料列）。

    用於管理頁「招式表格修改」；改 key 時會先刪舊列再以新 key 重建，避免 UNIQUE 衝突。
    """
    conn = _conn()
    try:
        row = collide_db.get_skill(conn, name, owner)
        if not row:
            raise HTTPException(status_code=404, detail=f"招式「{name}（{owner}）」不存在")
        data = dict(row)
        for k in ("name", "ability", "owner", "application", "skill_type",
                  "magic_system", "element", "range", "mp_cost", "power_base", "effect"):
            data[k] = getattr(body, k)
        new_name = str(data.get("name") or "").strip()
        new_owner = str(data.get("owner") or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="招式名稱不可為空")
        if (new_name, new_owner) != (name, owner):
            conn.execute(
                "DELETE FROM collide_skills WHERE name = ? AND owner = ?",
                (name, owner),
            )
        data["name"] = new_name
        updated = collide_db.upsert_skill(conn, data)
        conn.commit()
        try:
            sheets_sync.notify_write("skill")
        except Exception:
            pass
        return {"ok": True, "skill": updated}
    finally:
        conn.close()


@app.get("/api/tables/domains")
def api_domains():
    conn = _conn()
    try:
        return collide_db.list_domains(conn)
    finally:
        conn.close()


class DomainUpdateBody(BaseModel):
    name: str = ""
    owner: str = ""
    domain_type: str = ""
    awakening: str = ""
    precision: float = 0.0
    rule_self: str = ""
    rule_enemy: str = ""
    rule_environment: str = ""
    cooldown: str = ""
    sustain_mp: float = 0.0


@app.put("/api/tables/domains/{name}/{owner}")
def api_domain_update(name: str, owner: str, body: DomainUpdateBody):
    """更新一筆結界（欄位以 body 為準；name/owner 改變時搬移資料列）。

    用於管理頁「結界表格修改」；改 key 時會先刪舊列再以新 key 重建，避免 UNIQUE 衝突。
    """
    conn = _conn()
    try:
        row = collide_db.get_domain(conn, name, owner)
        if not row:
            raise HTTPException(status_code=404, detail=f"結界「{name}（{owner}）」不存在")
        data = dict(row)
        for k in ("name", "owner", "domain_type", "awakening", "precision",
                  "rule_self", "rule_enemy", "rule_environment", "cooldown", "sustain_mp"):
            data[k] = getattr(body, k)
        new_name = str(data.get("name") or "").strip()
        new_owner = str(data.get("owner") or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="結界名稱不可為空")
        if (new_name, new_owner) != (name, owner):
            conn.execute(
                "DELETE FROM collide_domains WHERE name = ? AND owner = ?",
                (name, owner),
            )
        updated = collide_db.upsert_domain(conn, data)
        conn.commit()
        try:
            sheets_sync.notify_write("domain")
        except Exception:
            pass
        return {"ok": True, "domain": updated}
    finally:
        conn.close()


@app.get("/api/tables/battles")
def api_battles(status: Optional[str] = None):
    conn = _conn()
    try:
        return collide_db.list_battles(conn, status=status)
    finally:
        conn.close()


@app.delete("/api/tables/battles/{battle_id}")
def api_battle_delete(battle_id: int):
    """永久刪除一筆對撞紀錄（管理頁右鍵/按鈕用）。

    僅刪 collide_battles 該列，不會動到關聯的成長/動作紀錄（它們是獨立審計表）。
    刪除後通知 Sheets 同步重寫對撞紀錄工作表。
    """
    conn = _conn()
    try:
        battle = collide_db.get_battle(conn, battle_id)
        if not battle:
            raise HTTPException(status_code=404, detail=f"對撞紀錄 #{battle_id} 不存在")
        conn.execute("DELETE FROM collide_battles WHERE id = ?", (battle_id,))
        conn.commit()
        try:
            sheets_sync.notify_write("battle")
        except Exception:
            pass
        return {
            "ok": True,
            "deleted": {
                "id": battle["id"],
                "attacker": battle.get("attacker"),
                "defender": battle.get("defender"),
                "status": battle.get("status"),
            },
        }
    finally:
        conn.close()


class TagBody(BaseModel):
    tag: str
    action: str = "add"


class FormBody(BaseModel):
    form: str
    multiplier: float = 1.0
    action: str = "add"


@app.get("/api/races")
def api_races():
    """種族修正表（race_modifiers.json）：種族 × 六維修正 + 待審查標記。"""
    from collide_engine import STAT_KEYS
    mods = collide_db.load_race_modifiers()
    pending = set(collide_db.pending_review_races())
    races = []
    for race, m in mods.items():
        if not isinstance(m, dict):
            continue
        races.append({
            "race": race,
            "mods": {s: m.get(s, 1.0) for s in STAT_KEYS},
            "pending_review": race in pending,
        })
    return {"races": races, "stat_keys": STAT_KEYS}


class RaceBody(BaseModel):
    mods: dict = {}


@app.put("/api/races/{race}")
def api_race_upsert(race: str, body: RaceBody):
    """新增或修改種族的六維修正。修改後自動清除待審查標記。"""
    from collide_engine import STAT_KEYS
    race = race.strip()
    if not race:
        raise HTTPException(status_code=400, detail="種族名不可為空")
    if not body.mods:
        raise HTTPException(status_code=400, detail="缺少六維修正")
    mods = {}
    for s in STAT_KEYS:
        if s not in body.mods:
            raise HTTPException(status_code=400, detail=f"缺少六維修正「{s}」")
        try:
            v = round(float(body.mods[s]), 1)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"「{s}」不是有效數字")
        if not (collide_db.RACE_MOD_MIN <= v <= collide_db.RACE_MOD_MAX):
            raise HTTPException(
                status_code=400,
                detail=f"「{s}」倍率須在 {collide_db.RACE_MOD_MIN}~{collide_db.RACE_MOD_MAX}",
            )
        mods[s] = v
    if not collide_db.save_race_mods(race, mods):
        raise HTTPException(status_code=500, detail="種族表寫入失敗")
    collide_db.mark_race_reviewed(race)
    return {"ok": True, "race": race, "mods": mods, "pending_review": False}


@app.delete("/api/races/{race}")
def api_race_delete(race: str):
    """刪除種族（含自待審查清單移除）。"""
    race = race.strip()
    if not race:
        raise HTTPException(status_code=400, detail="種族名不可為空")
    if not collide_db.delete_race(race):
        raise HTTPException(status_code=404, detail=f"種族「{race}」不存在或刪除失敗")
    return {"ok": True}


@app.post("/api/characters/{name}/special_forms")
def api_character_forms(name: str, body: FormBody):
    """新增／移除／改倍率的特殊型態（special_forms）。逐個 form 獨立操作。

    倍率須在 1.1~1.5（與寫入規範一致）；action 為 add / remove。
    """
    conn = _conn()
    try:
        ch = collide_db.get_character(conn, name)
        if not ch:
            raise HTTPException(status_code=404, detail=f"角色「{name}」不存在")
        form = body.form.strip()
        if not form:
            raise HTTPException(status_code=400, detail="型態名稱不可為空")
        if body.action not in ("add", "remove"):
            raise HTTPException(status_code=400, detail="action 只能是 add / remove")
        forms = {k: float(v) for k, v in (ch.get("special_forms") or {}).items()}
        if body.action == "remove":
            forms.pop(form, None)
        else:
            mult = round(float(body.multiplier), 2)
            if not (1.1 <= mult <= 1.5):
                raise HTTPException(status_code=400, detail="型態倍率須在 1.1~1.5")
            forms[form] = mult
        data = dict(ch)
        data["special_forms"] = forms
        updated = collide_db.upsert_character(conn, data)
        conn.commit()
        return {"ok": True, "special_forms": updated["special_forms"]}
    finally:
        conn.close()


@app.post("/api/characters/{name}/tags")
def api_character_tags(name: str, body: TagBody):
    """新增／移除角色的特殊狀態標籤（special_tags），逐個 tag 獨立操作。"""
    conn = _conn()
    try:
        ch = collide_db.get_character(conn, name)
        if not ch:
            raise HTTPException(status_code=404, detail=f"角色「{name}」不存在")
        tag = body.tag.strip()
        if not tag:
            raise HTTPException(status_code=400, detail="標籤不可為空")
        if body.action not in ("add", "remove"):
            raise HTTPException(status_code=400, detail="action 只能是 add / remove")
        tags = [t for t in (ch.get("special_tags") or []) if isinstance(t, str) and t]
        if body.action == "add":
            if tag not in tags:
                tags.append(tag)
        else:
            tags = [t for t in tags if t != tag]
        data = dict(ch)
        data["special_tags"] = tags
        updated = collide_db.upsert_character(conn, data)
        conn.commit()
        return {"ok": True, "special_tags": updated["special_tags"]}
    finally:
        conn.close()


# ── API：成長 / Action / 樓層 ─────────────────────────────

@app.get("/api/growth")
def api_growth(thread_id: Optional[str] = None, limit: int = 500):
    conn = _conn()
    try:
        if thread_id:
            return collide_growth.growth_log_for_floor(conn, thread_id)
        return collide_growth.list_growth_log(conn, limit=limit)
    finally:
        conn.close()


@app.get("/api/actionlog")
def api_actionlog(thread_id: Optional[str] = None, limit: int = 500):
    conn = _conn()
    try:
        rows = collide_growth.list_action_log(conn, limit=limit)
        if thread_id:
            rows = [r for r in rows if r.get("thread_id") == thread_id]
        return rows
    finally:
        conn.close()


@app.get("/api/floors")
def api_floors():
    conn = _conn()
    try:
        return collide_growth.list_floors(conn)
    finally:
        conn.close()


class ReviewBody(BaseModel):
    reviewed_by: str = "admin"
    approved: bool = False
    new_value: Optional[str] = None
    note: str = ""


@app.post("/api/growth/{log_id}/review")
def api_growth_review(log_id: int, body: ReviewBody):
    conn = _conn()
    try:
        result = collide_growth.review_growth(
            conn,
            log_id,
            reviewed_by=body.reviewed_by or "admin",
            approved=body.approved,
            new_value=body.new_value,
            note=body.note,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    finally:
        conn.close()


# ── API：Sheets 同步 ──────────────────────────────────────

@app.get("/api/sheets/status")
def api_sheets_status():
    buf = sheets_sync.get_buffer()
    sheet_id = sheets_sync.get_spreadsheet_id()
    return {
        "configured": bool(sheet_id),
        "spreadsheet_id": sheet_id,
        "credentials": os.path.exists(sheets_sync.CREDENTIALS_FILE),
        "token": os.path.exists(sheets_sync.TOKEN_FILE),
        "dirty": sorted(buf._dirty) if buf else [],
        "last_error": getattr(buf, "_last_error", None),
        "call_count": getattr(buf, "_call_count", 0),
    }


class SyncBody(BaseModel):
    all: bool = True


@app.post("/api/sheets/sync")
def api_sheets_sync(body: SyncBody = SyncBody()):
    try:
        results = sheets_sync.sync_now()
        return {"ok": True, "results": results}
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步失敗：{e}")


# ── 靜態頁 ────────────────────────────────────────────────

@app.get("/")
def index():
    return FileResponse(os.path.join(SCRIPT_DIR, "admin.html"))


def main():
    parser = argparse.ArgumentParser(description="對撞系統管理頁")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-browser", action="store_true", help="不自動開啟瀏覽器")
    args = parser.parse_args()

    import threading
    import time
    import urllib.request
    import webbrowser
    import uvicorn

    port = args.port
    url = f"http://127.0.0.1:{port}"

    # 啟動背景自動同步（每 30 秒檢查 dirty，自我修復）
    try:
        if sheets_sync.get_spreadsheet_id():
            sheets_sync.start_auto_flush()
            sheets_sync.mark_all_dirty()
            print("✅ Google Sheets 自動同步已啟動（30 秒檢查一次）")
    except Exception as e:
        print(f"⚠️ Sheets 自動同步啟動失敗: {e}")

    server = threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=port, log_level="info"),
        daemon=True,
    )
    server.start()

    # 等伺服器就緒（最多 10 秒），然後自動開瀏覽器
    if not args.no_browser:
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{url}/api/sheets/status", timeout=1):
                    break
            except Exception:
                time.sleep(0.1)
        webbrowser.open(url)
        print(f"✅ 管理頁已開啟：{url}")
        print("   按下 Ctrl+C 停止伺服器。")

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
