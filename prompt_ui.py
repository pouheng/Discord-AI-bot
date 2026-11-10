"""prompt_ui.py — Phase 2 提示詞查看／編輯器（本地 FastAPI 伺服器）。

啟動：
    python prompt_ui.py            # http://127.0.0.1:8788
    python prompt_ui.py --port 9000

- 檢視：讀 prompt_logs/last_prompt_blocks.json，顯示上一次 Phase 2 的所有注入區段（含 OOC/IC）；
  讀不到結構化快照時退回解析 prompt_logs/last_prompt.txt 全文。
- 編輯：讀寫 prompt_blocks.json（OOC／IC 兩個分頁），可新增/修改/啟停/排序條目。
- 歷史：每次儲存寫一份快照到 prompt_blocks_history/，可檢視與回滾。

僅綁定 127.0.0.1。
"""
import argparse
import datetime
import json
import os
import re
import sys
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import prompt_blocks  # noqa: E402

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROMPT_LOG_DIR = os.path.join(SCRIPT_DIR, "prompt_logs")
LAST_DUMP = os.path.join(PROMPT_LOG_DIR, "last_prompt_blocks.json")
RAW_PROMPT = os.path.join(PROMPT_LOG_DIR, "last_prompt.txt")

PLACEHOLDERS = ["now", "today", "char_name", "banned_words", "priming"]

app = FastAPI(title="Phase 2 Prompt Editor", version="1.0")


class SaveBody(BaseModel):
    tabs: dict[str, Any]
    priming: dict[str, Any] | None = None
    note: str = ""


@app.get("/api/data")
def get_data():
    data = prompt_blocks.load_blocks()
    return {
        "blocks": data,
        "flags": prompt_blocks.FLAG_LABELS,
        "placeholders": PLACEHOLDERS,
        "priming_defaults": prompt_blocks.DEFAULT_PRIMING,
        "history": prompt_blocks.list_history(),
    }


_SECTION_RE = re.compile(r"^=== (.+?) ===[ \t]*$", re.M)


def _parse_sections(text: str) -> dict[str, str]:
    """把 last_prompt.txt 依 '=== 區段 ===' 標題切成 dict。"""
    marks = list(_SECTION_RE.finditer(text))
    secs: dict[str, str] = {}
    for i, m in enumerate(marks):
        start = m.end()
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        secs[m.group(1).strip()] = text[start:end].strip("\n")
    return secs


def _dump_from_raw_prompt() -> dict:
    """讀不到結構化快照時，改用 last_prompt.txt 全文拼出檢視資料。"""
    with open(RAW_PROMPT, "r", encoding="utf-8") as f:
        secs = _parse_sections(f.read())
    context = []
    for line in secs.get("CONTEXT", "").splitlines():
        mm = re.match(r"^\[(.+?)\]\s?(.*)$", line)
        if mm:
            context.append({"role": mm.group(1), "content": mm.group(2)})
        elif line.strip() and context:
            context[-1]["content"] += "\n" + line
    fmt = secs.get("FORMAT OVERRIDE (injected user msg)",
                   secs.get("FORMAT OVERRIDE", ""))
    priming = secs.get("PRIMING", "")
    _sys = secs.get("SYSTEM", "")
    _p = priming.strip()
    if _p.startswith("開始回應") or "台詞" in _p:
        _ctype = "in_character"
    elif "（正文）" in _sys:
        _ctype = "out_of_character"
    elif _sys:
        _ctype = "in_character"
    else:
        _ctype = "out_of_character"
    mtime = datetime.datetime.fromtimestamp(
        os.path.getmtime(RAW_PROMPT)
    ).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "timestamp": mtime,
        # IC 的 priming 含「台詞」；OOC 的系統尾巴含「（正文）」
        "channel_type": _ctype,
        "channel": "",
        "enable_ic_style": False,
        "segments": [{
            "title": "SYSTEM（完整）",
            "kind": "static",
            "included": True,
            "condition": [],
            "rendered": secs.get("SYSTEM", ""),
        }],
        "context": context,
        "current_message": secs.get("CURRENT MESSAGE", ""),
        "format_override": fmt,
        "priming": priming,
        "raw_fallback": True,
    }


@app.get("/api/last")
def get_last():
    if os.path.exists(LAST_DUMP):
        with open(LAST_DUMP, "r", encoding="utf-8") as f:
            return json.load(f)
    if os.path.exists(RAW_PROMPT):
        return _dump_from_raw_prompt()
    return {"empty": True}


@app.post("/api/save")
def post_save(body: SaveBody):
    data = prompt_blocks.load_blocks()
    data["tabs"] = body.tabs
    if body.priming is not None:
        for ch in ("ooc", "ic"):
            if isinstance(body.priming.get(ch), str):
                prompt_blocks.set_priming(data, ch, body.priming[ch])
    ts = prompt_blocks.save_blocks(data, note=body.note)
    return {"ok": True, "ts": ts}


@app.get("/api/history")
def get_history():
    return {"history": prompt_blocks.list_history()}


@app.get("/api/history/{ts}")
def get_history_item(ts: str):
    try:
        return prompt_blocks.load_history(ts)
    except OSError:
        raise HTTPException(status_code=404, detail="找不到版本")


@app.post("/api/rollback/{ts}")
def post_rollback(ts: str):
    try:
        new_ts = prompt_blocks.rollback(ts)
    except OSError:
        raise HTTPException(status_code=404, detail="找不到版本")
    return {"ok": True, "ts": new_ts}


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML_PAGE


HTML_PAGE = r"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<title>Phase 2 Prompt Editor</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family: "Segoe UI", "Microsoft JhengHei", sans-serif; background:#1e1f22; color:#dcddde; }
  header { padding:8px 14px; background:#2b2d31; border-bottom:1px solid #111; display:flex; gap:14px; align-items:center; }
  header h1 { font-size:15px; margin:0; }
  .tabs button { background:#313338; color:#dcddde; border:1px solid #444; padding:6px 12px; cursor:pointer; border-radius:6px; }
  .tabs button.active { background:#5865f2; border-color:#5865f2; color:#fff; }
  main { padding:14px; max-width:1100px; }
  .block { border:1px solid #3a3c42; border-radius:8px; margin:8px 0; background:#2b2d31; }
  .block.priming { border-color:#5865f2; background:#26283a; }
  .block.priming textarea { min-height:70px; }
  .block .head { display:flex; gap:8px; align-items:center; padding:6px 8px; border-bottom:1px solid #3a3c42; flex-wrap:wrap; }
  .block .head input[type=text] { flex:1; min-width:160px; background:#1e1f22; color:#eee; border:1px solid #444; border-radius:4px; padding:4px 6px; }
  .badge { font-size:11px; padding:1px 7px; border-radius:10px; }
  .badge.static { background:#3b6e4b; }
  .badge.dynamic { background:#6e5a3b; }
  .block .body { padding:8px; }
  textarea { width:100%; min-height:120px; background:#1e1f22; color:#e6e6e6; border:1px solid #444; border-radius:4px; font-family: "Consolas", monospace; font-size:12px; white-space:pre; padding:6px; }
  .dynmark { color:#c9a26b; font-family: Consolas, monospace; font-size:12px; }
  .conds { display:flex; gap:10px; flex-wrap:wrap; padding:4px 0; font-size:12px; }
  .conds label { display:flex; gap:4px; align-items:center; }
  .row { display:flex; gap:6px; align-items:center; }
  .muted { color:#888; font-size:12px; }
  .warn { color:#f0a020; font-size:12px; }
  .ok { color:#43b581; }
  button.mini { background:#3a3c42; color:#ddd; border:1px solid #555; border-radius:4px; cursor:pointer; padding:2px 7px; }
  .toolbar { display:flex; gap:10px; align-items:center; margin:10px 0; }
  .toolbar input[type=text] { flex:1; background:#1e1f22; color:#eee; border:1px solid #444; border-radius:4px; padding:6px; }
  .seg { border-left:3px solid #555; padding:6px 10px; margin:8px 0; background:#232428; border-radius:4px; }
  .seg h3 { margin:0 0 4px; font-size:13px; }
  .seg pre { white-space:pre-wrap; word-break:break-word; margin:0; font-family: Consolas, monospace; font-size:12px; color:#cfcfcf; }
  .seg.off { opacity:.45; border-left-color:#a33; }
  .histitem { padding:6px 8px; border-bottom:1px solid #333; display:flex; gap:10px; align-items:center; }
</style>
</head>
<body>
<header>
  <h1>Phase 2 Prompt Editor</h1>
  <div class="tabs" id="tabs"></div>
  <span class="muted" id="status"></span>
</header>
<main id="main"></main>
<script>
let DATA = null, LAST = null, CUR = "view";
const FLAGS = () => DATA.flags, PLACEHOLDERS = () => DATA.placeholders;

async function boot() {
  DATA = await (await fetch("/api/data")).json();
  try { LAST = await (await fetch("/api/last")).json(); } catch(e) { LAST = {empty:true}; }
  renderTabs();
  render();
}
function renderTabs() {
  const t = document.getElementById("tabs");
  const items = [["view","上次提示詞"],["ooc","編輯 OOC"],["ic","編輯 IC"],["hist","歷史"]];
  t.innerHTML = "";
  for (const [id,label] of items) {
    const b = document.createElement("button");
    b.textContent = label; b.className = (CUR===id?"active":"");
    b.onclick = () => { CUR = id; renderTabs(); render(); };
    t.appendChild(b);
  }
}
function esc(s){ return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;"); }
function dupTitle(t) {
  t = t || "";
  const m = t.match(/（複本(?: (\d+))?）$/);
  if (m) {
    const n = m[1] ? parseInt(m[1], 10) + 1 : 2;
    return t.replace(/（複本(?: \d+)?）$/, `（複本 ${n}）`);
  }
  return t + "（複本）";
}
function render() {
  const m = document.getElementById("main"); m.innerHTML = "";
  if (CUR === "view") renderView(m);
  else if (CUR === "hist") renderHist(m);
  else renderEditor(m, CUR);
}
function renderView(m) {
  if (!LAST || LAST.empty) { m.innerHTML = '<p class="muted">尚無紀錄（bot 還沒產生過 Phase 2 提示詞）。</p>'; return; }
  const badge = `<span class="badge ${LAST.channel_type==='in_character'?'static':'dynamic'}">${LAST.channel_type} / ${LAST.channel||''}${LAST.enable_ic_style?' · IC風格':''}</span>`;
  const srcNote = LAST.raw_fallback ? ' &nbsp;<span class="warn">（來源：last_prompt.txt 全文，非結構化快照）</span>' : '';
  m.innerHTML = `<p>時間：${LAST.timestamp} &nbsp; ${badge} &nbsp;<span class="muted">一次 OOC/IC 只能看最近一次</span>${srcNote}</p>`;
  for (const s of LAST.segments) {
    const d = document.createElement("div"); d.className = "seg" + (s.included===false?" off":"");
    const cond = (s.condition&&s.condition.length)? ` · 條件：${s.condition.join(" 且 ")}` : "";
    d.innerHTML = `<h3>${esc(s.title)} <span class="badge ${s.kind}">${s.kind}</span> ${s.included===false?'<span class="warn">未注入</span>':''}<span class="muted">${cond}</span></h3><pre>${esc(s.rendered||"")}</pre>`;
    m.appendChild(d);
  }
  const tail = document.createElement("div"); tail.className="seg";
  const chKey = (LAST.channel_type === "in_character") ? "ic" : "ooc";
  const curP = ((DATA.blocks.priming || {})[chKey]) || "";
  const pNote = (!LAST.raw_fallback && curP && curP !== (LAST.priming || ""))
    ? ` &nbsp;<span class="warn">（目前設定已改，下次生效）</span>` : "";
  tail.innerHTML = `<h3>CONTEXT（歷史對話）<span class="badge dynamic">message</span></h3><pre>${(LAST.context||[]).map(c=>`[${esc(c.role)}] ${esc(c.content)}`).join("\n")}</pre>
    <h3>CURRENT MESSAGE</h3><pre>${esc(LAST.current_message)}</pre>
    <h3>FORMAT OVERRIDE</h3><pre>${esc(LAST.format_override)}</pre>
    <h3>PRIMING${pNote}</h3><pre>${esc(LAST.priming)}</pre>`;
  m.appendChild(tail);
}
function validate(content) {
  const bad = [];
  for (const mt of (content||"").matchAll(/\{\{(\w+)\}\}/g)) {
    if (!PLACEHOLDERS().includes(mt[1])) bad.push(mt[1]);
  }
  return bad;
}
function renderEditor(m, channel) {
  const blocks = DATA.blocks.tabs[channel] || (DATA.blocks.tabs[channel] = []);
  const priming = DATA.blocks.priming || (DATA.blocks.priming = {});
  if (typeof priming[channel] !== "string") priming[channel] = "";

  // ── 開場提示句（priming）：唯一可改的地方 ──
  const pbox = document.createElement("div"); pbox.className = "block priming";
  pbox.innerHTML = `
    <div class="head">
      <span class="badge" style="background:#5865f2">PRIMING</span>
      <b>開場提示句（${channel.toUpperCase()}）· 全系統唯一來源</b>
      <span style="flex:1"></span>
      <button class="mini" data-preset>還原預設</button>
    </div>
    <div class="body">
      <textarea data-priming>${esc(priming[channel])}</textarea>
      <div class="muted">接在「使用者訊息」最後引導模型直接進正文；系統尾巴會用 {{priming}} 引用同一段。
        模型若把它複述出來，程式會自動從回覆開頭刪掉。可用 {{char_name}}。</div>
      <div class="warn" data-pwarn></div>
    </div>`;
  m.appendChild(pbox);
  const pta = pbox.querySelector("[data-priming]");
  pta.oninput = () => {
    priming[channel] = pta.value;
    const bad = validate(pta.value);
    pbox.querySelector("[data-pwarn]").textContent = bad.length
      ? "⚠️ 未知 placeholder：{{" + bad.join("}} {{") + "}}（已知：" + PLACEHOLDERS().join(", ") + "）"
      : (pta.value.trim() ? "" : "⚠️ 留空＝不注入開場提示句（模型可能不容易直接進正文）");
  };
  pbox.querySelector("[data-preset]").onclick = () => {
    const d = (DATA.priming_defaults || {})[channel] || "";
    pta.value = d; priming[channel] = d; setStatus("已還原預設（記得儲存）");
  };

  const bar = document.createElement("div"); bar.className="toolbar";
  bar.innerHTML = `<button class="mini" id="addb">+ 新增條目</button>
    <span class="muted">動態條目唯讀；靜態條目可按「複製」做版本（複本預設停用）。標題模型看不到。</span>`;
  m.appendChild(bar);
  bar.querySelector("#addb").onclick = () => {
    const id = "_custom_" + Date.now();
    blocks.push({id, title:"新條目（⚠️自訂）", kind:"static", enabled:true, condition:[], content:""});
    render();
  };
  blocks.forEach((b, i) => {
    const d = document.createElement("div"); d.className="block";
    const isStatic = b.kind === "static";
    const flagsHtml = Object.entries(FLAGS()).map(([k,v]) => {
      const on = (b.condition||[]).includes(k) ? "checked" : "";
      return `<label><input type="checkbox" data-flag="${k}" ${on}> ${v}</label>`;
    }).join("");
    d.innerHTML = `
      <div class="head">
        <span class="row">
          <button class="mini" data-up>▲</button>
          <button class="mini" data-dn>▼</button>
        </span>
        <input type="checkbox" data-en ${b.enabled?"checked":""} title="啟用">
        <input type="text" value="${esc(b.title)}" data-title>
        <span class="badge ${b.kind}">${b.kind}</span>
        <span class="muted">#${i}</span>
        ${isStatic?'<button class="mini" data-dup title="複製成新版本（插在下面、預設停用）">複製</button>':''}
        ${b.id.startsWith("_custom_")?'<button class="mini" data-del>刪除</button>':''}
      </div>
      <div class="body">
        <div class="conds">出現條件（全部成立才注入，不勾＝永遠）：${flagsHtml}</div>
        ${isStatic
          ? `<textarea data-content>${esc(b.content||"")}</textarea><div class="warn" data-warn></div>`
          : `<div class="dynmark">「執行期注入：${esc(b.id)}」— 內容由程式生成，唯讀（完整內容見「上次提示詞」）</div>`}
      </div>`;
    m.appendChild(d);
    d.querySelector("[data-up]").onclick = () => { if(i>0){[blocks[i-1],blocks[i]]=[blocks[i],blocks[i-1]]; render();} };
    d.querySelector("[data-dn]").onclick = () => { if(i<blocks.length-1){[blocks[i+1],blocks[i]]=[blocks[i],blocks[i+1]]; render();} };
    d.querySelector("[data-en]").onchange = (e) => { b.enabled = e.target.checked; };
    d.querySelector("[data-title]").oninput = (e) => { b.title = e.target.value; };
    d.querySelectorAll("[data-flag]").forEach(cb => cb.onchange = () => {
      b.condition = [...d.querySelectorAll("[data-flag]:checked")].map(x=>x.dataset.flag);
    });
    const dup = d.querySelector("[data-dup]");
    if (dup) dup.onclick = () => {
      const copy = {
        id: "_custom_" + Date.now() + "_" + Math.random().toString(36).slice(2, 7),
        title: dupTitle(b.title),
        kind: "static",
        enabled: false,
        condition: [...(b.condition || [])],
        content: b.content || "",
      };
      blocks.splice(i + 1, 0, copy);
      render();
      setStatus("已複製條目（預設停用，方便切換版本；記得儲存）");
    };
    const del = d.querySelector("[data-del]");
    if (del) del.onclick = () => { blocks.splice(i,1); render(); };
    const ta = d.querySelector("[data-content]");
    if (ta) {
      ta.oninput = () => {
        b.content = ta.value;
        const bad = validate(b.content);
        d.querySelector("[data-warn]").textContent = bad.length? "⚠️ 未知 placeholder：{{"+bad.join("}} {{")+"}}（已知："+PLACEHOLDERS().join(", ")+"）" : "";
      };
    }
  });
  const save = document.createElement("div"); save.className="toolbar";
  save.innerHTML = `<input type="text" id="note" placeholder="這版改了什麼（備註，可空）">
    <button class="mini" id="saveb" style="background:#5865f2;border-color:#5865f2;color:#fff;padding:6px 14px">儲存（下一則生效）</button>`;
  m.appendChild(save);
  save.querySelector("#saveb").onclick = async () => {
    const note = save.querySelector("#note").value;
    const r = await fetch("/api/save", {method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify({tabs: DATA.blocks.tabs, priming: DATA.blocks.priming, note})});
    const j = await r.json();
    setStatus("已儲存 · 快照 " + j.ts);
    DATA = await (await fetch("/api/data")).json();
  };
}
async function renderHist(m) {
  const {history} = await (await fetch("/api/history")).json();
  if (!history.length) { m.innerHTML = '<p class="muted">尚無歷史。</p>'; return; }
  m.innerHTML = `<p class="muted">最近 ${history.length} 版（保留 50）。回滾＝以該版內容再存一版。</p>`;
  for (const h of history) {
    const d = document.createElement("div"); d.className="histitem";
    d.innerHTML = `<span>${h.ts}</span><span class="muted" style="flex:1">${esc(h.note)}</span>
      <button class="mini" data-rb>回滾</button>`;
    d.querySelector("[data-rb]").onclick = async () => {
      await fetch("/api/rollback/"+h.ts, {method:"POST"});
      setStatus("已回滾 → " + h.ts);
      DATA = await (await fetch("/api/data")).json();
    };
    m.appendChild(d);
  }
}
function setStatus(s){ const el=document.getElementById("status"); el.textContent=s; setTimeout(()=>el.textContent="",4000); }
boot();
</script>
</body>
</html>
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8788)
    args = ap.parse_args()
    import uvicorn
    print(f"Prompt Editor: http://127.0.0.1:{args.port}")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
