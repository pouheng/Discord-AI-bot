"""
llm_models.py — 集中管理 LLM「來源」（endpoint + API key）與各「場景」要用的模型 ID。

背景：模型 ID 與來源（base_url / api_key）原本散落在 ~9 個檔案。模型會更新、
來源也可能換（相容 OpenAI 的聊天補全），需要集中成一份可用 Manager 調整的設定。

- 設定檔：models.json（Manager「設定」頁維護）。
    {
      "source": { "base_url": "...", "api_key": "" },
      "default": "...", ... 9 個 slot
    }
- 每次呼叫前即時讀檔，改完「下一則訊息」就生效，不用重啟。
- source.base_url 空 → 用 DEFAULT_BASE_URL；source.api_key 空 → 退回 .env 的
  DEEPSEEK_API_KEY。
- 缺 slot / 檔案壞掉 → 回 DEFAULT_MODELS 後備（= 原本行為）。
- 本模組不 import 任何專案模組，避免循環匯入；獨立程序（collide_ui、
  review_prompt）也能用。
"""
import json
import os


def _sanitize_no_proxy():
    """移除 NO_PROXY 裡 httpx 無法解析的括號 IPv6（如 [::1]）。

    Windows 的 NO_PROXY 常含 `[::1]`，httpx 會把它當 URL 解析而在建立 client
    時直接拋 InvalidURL（port ':1]'），導致所有 LLM 呼叫失敗。
    """
    for key in ("NO_PROXY", "no_proxy"):
        val = os.environ.get(key)
        if not val:
            continue
        kept = [
            p.strip()
            for p in val.split(",")
            if p.strip() and not (p.strip().startswith("[") and p.strip().endswith("]"))
        ]
        os.environ[key] = ",".join(kept)


_sanitize_no_proxy()

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MODELS_FILE = os.path.join(SCRIPT_DIR, "models.json")

# 全域備援的鍵：主線路由各場景決定，備援不分場景共用
GLOBAL_FALLBACK_KEY = "*"
ENV_FILE = os.path.join(SCRIPT_DIR, ".env")

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"

# 場景清單（順序即 Manager 顯示順序）；中文標籤給 UI 用。
SLOTS = [
    ("default", "日常/通用"),
    ("plot", "回覆/劇情"),
    ("maintenance", "維護"),
    ("collide", "碰撞"),
    ("tarot", "塔羅"),
    ("autopilot", "NPC托管"),
    ("gods_eye", "神眼"),
    ("say", "/say"),
    ("review", "審查"),
]

SLOT_LABELS = dict(SLOTS)

# 後備值：沿用原本硬編碼的模型 ID（不遷移）。
DEFAULT_MODELS = {
    "default": "deepseek-v4-flash",
    "plot": "deepseek-v4-flash",
    "maintenance": "deepseek-v4-flash",
    "collide": "deepseek-v4-flash",
    "tarot": "deepseek-v4-flash",
    "autopilot": "deepseek-v4-flash",
    "gods_eye": "deepseek-v4-flash",
    "say": "deepseek-v4-flash",
    "review": "deepseek-v4-pro",
}

# (base_url, api_key, timeout, max_retries) → AsyncOpenAI
_client_cache = {}


# ── 原始檔讀寫 ──────────────────────────────────────

def _read_raw() -> dict:
    try:
        with open(MODELS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _write_raw(data: dict):
    with open(MODELS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ── 模型 slot ───────────────────────────────────────

def load_models() -> dict:
    """回傳完整模型設定：以 DEFAULT_MODELS 為底，models.json 覆蓋。"""
    cfg = dict(DEFAULT_MODELS)
    for k, v in _read_raw().items():
        if k in DEFAULT_MODELS and isinstance(v, str) and v.strip():
            cfg[k] = v.strip()
    return cfg


def get_model(slot: str) -> str:
    """取某場景的模型 ID；即時讀檔，缺漏回 DEFAULT_MODELS 後備。"""
    return load_models().get(slot) or DEFAULT_MODELS.get(slot) or DEFAULT_MODELS["default"]


def save_model(slot: str, model_id: str):
    """寫入單一場景的模型 ID（保留 source 與其他 key）。"""
    raw = _read_raw()
    raw[slot] = (model_id or "").strip() or DEFAULT_MODELS.get(slot, "")
    _write_raw(raw)


def get_fallbacks(model: str) -> list[str]:
    """回傳模型的後備線路（依序嘗試）。

    以「全域備援」`fallback_models["*"]` 為主：主線路由各場景自己選，
    備援則不分場景共用，避免某個場景換了模型就突然沒有第 2、3 線。
    沒有全域設定時，才回退到舊格式 `fallback_models[<模型ID>]`。
    """
    raw = _read_raw()
    fb = raw.get("fallback_models")
    if not isinstance(fb, dict):
        return []
    v = fb.get(GLOBAL_FALLBACK_KEY)
    if not isinstance(v, list):
        v = fb.get(model)
    if not isinstance(v, list):
        return []
    return [str(x).strip() for x in v if isinstance(x, str) and x.strip()]


def save_global_fallbacks(models: list[str]):
    """寫入全域備援線路（models.json 的 fallback_models["*"]）。"""
    cleaned: list[str] = []
    for m in models or []:
        m = (m or "").strip()
        if m and m not in cleaned:
            cleaned.append(m)
    raw = _read_raw()
    fb = raw.get("fallback_models")
    if not isinstance(fb, dict):
        fb = {}
    if cleaned:
        fb[GLOBAL_FALLBACK_KEY] = cleaned
    else:
        fb.pop(GLOBAL_FALLBACK_KEY, None)
    raw["fallback_models"] = fb
    _write_raw(raw)


def global_fallbacks() -> list[str]:
    """目前的全域備援清單。"""
    return get_fallbacks("")


def model_chain(slot: str, model: str | None = None) -> list[str]:
    """回傳依序嘗試的線路 [主模型, *全域備援]，去重保序。"""
    primary = (model or get_model(slot)).strip() or get_model(slot)
    chain = [primary]
    for m in get_fallbacks(primary):
        if m not in chain:
            chain.append(m)
    return chain


def save_fallbacks(model: str, models: list[str]):
    """（舊格式）寫入某模型專屬的後備線路；新設定請用 save_global_fallbacks。"""
    model = (model or "").strip()
    if not model:
        return
    cleaned: list[str] = []
    for m in models or []:
        m = (m or "").strip()
        if m and m != model and m not in cleaned:
            cleaned.append(m)
    raw = _read_raw()
    fb = raw.get("fallback_models")
    if not isinstance(fb, dict):
        fb = {}
    if cleaned:
        fb[model] = cleaned
    else:
        fb.pop(model, None)
    raw["fallback_models"] = fb
    _write_raw(raw)


# ── 來源（endpoint + key）──────────────────────────

def _env_api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEEPSEEK_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def get_source() -> dict:
    """回傳實際生效的來源：base_url（空→預設）、api_key（空→.env）。"""
    raw = _read_raw()
    src = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    base_url = (src.get("base_url") or "").strip() or DEFAULT_BASE_URL
    api_key = (src.get("api_key") or "").strip() or _env_api_key()
    return {"base_url": base_url, "api_key": api_key}


def get_saved_source() -> dict:
    """回傳「存檔值」（可能為空字串；空＝沿用預設 / .env）。給 Manager 填欄位用。"""
    raw = _read_raw()
    src = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return {
        "base_url": (src.get("base_url") or "").strip(),
        "api_key": (src.get("api_key") or "").strip(),
    }


def save_source(base_url: str, api_key: str):
    """寫入來源設定（保留 slot）。空字串代表沿用預設 / .env。"""
    raw = _read_raw()
    raw["source"] = {
        "base_url": (base_url or "").strip(),
        "api_key": (api_key or "").strip(),
    }
    _write_raw(raw)


def has_credentials() -> bool:
    return bool(get_source()["api_key"])


def get_client(timeout: float | None = None, max_retries: int = 2):
    """依目前來源快取一個 AsyncOpenAI；來源/參數一變就重建（即時生效）。"""
    src = get_source()
    key = (src["base_url"], src["api_key"], timeout, max_retries)
    client = _client_cache.get(key)
    if client is None:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            api_key=src["api_key"] or "not-needed",
            base_url=src["base_url"],
            timeout=timeout,
            max_retries=max_retries,
        )
        _client_cache[key] = client
    return client


def is_default_source() -> bool:
    """目前來源是否已經是預設 DeepSeek 端點。"""
    return get_source()["base_url"].rstrip("/") == DEFAULT_BASE_URL.rstrip("/")


def fallback_model(slot: str) -> str:
    """該 slot 的預設模型 ID（回退用）。"""
    return DEFAULT_MODELS.get(slot) or DEFAULT_MODELS["default"]


def get_fallback_client(timeout: float | None = None):
    """預設 DeepSeek（DEFAULT_BASE_URL + .env key）的 client，供回退使用。"""
    from openai import AsyncOpenAI

    key = _env_api_key()
    ck = ("__fallback__", key, timeout)
    client = _client_cache.get(ck)
    if client is None:
        client = AsyncOpenAI(
            api_key=key or "not-needed",
            base_url=DEFAULT_BASE_URL,
            timeout=timeout,
        )
        _client_cache[ck] = client
    return client


async def chat_create(slot: str, *, timeout: float | None = None, **kwargs):
    """以「目前來源 + slot 模型」呼叫；任何例外則退回「預設來源 + 預設模型」一次。

    kwargs 不需（也不應）帶 model，模型由 slot 決定。回退令人為同一個 coroutine。
    """
    kwargs.pop("model", None)
    primary_model = get_model(slot)
    if not is_default_source() or primary_model != fallback_model(slot):
        try:
            return await get_client(timeout=timeout).chat.completions.create(
                model=primary_model, **kwargs
            )
        except Exception as primary_exc:
            try:
                return await get_fallback_client(timeout=timeout).chat.completions.create(
                    model=fallback_model(slot), **kwargs
                )
            except Exception:
                raise primary_exc
    return await get_client(timeout=timeout).chat.completions.create(
        model=primary_model, **kwargs
    )


# ── 拉取模型清單（給 Manager 下拉用）────────────────

def fetch_models(timeout: float = 15.0) -> list:
    """向來源拉取可用模型 id 清單（OpenAI 格式 GET {base_url}/models）。

    失敗時往上拋例外，由呼叫端（Manager）決定如何提示。
    """
    import urllib.request

    src = get_source()
    req = urllib.request.Request(
        src["base_url"].rstrip("/") + "/models",
        headers={"Authorization": f"Bearer {src['api_key']}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8"))
    ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
    return sorted(ids)
