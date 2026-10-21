"""Image Reader — 透過本地 ollama + Gemma4 分析用戶分享的圖片"""
import asyncio
import base64
import datetime
import hashlib
import os
import time
import aiofiles

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_LOG_DIR = os.path.join(SCRIPT_DIR, "image_logs")

# --- Image cache: { image_hash: {"description": str, "timestamp": float} } ---
_image_cache: dict[str, dict] = {}
CACHE_TTL = 300  # 5 minutes


def _image_hash(image_bytes: bytes) -> str:
    """用 MD5 快速算圖片 hash 做 cache key。"""
    return hashlib.md5(image_bytes).hexdigest()


def get_cached_description(image_bytes: bytes) -> str | None:
    """讀取 cache 中的圖片描述，過期則回傳 None。"""
    key = _image_hash(image_bytes)
    entry = _image_cache.get(key)
    if not entry:
        return None
    if time.time() - entry["timestamp"] > CACHE_TTL:
        _image_cache.pop(key, None)
        return None
    return entry["description"]


def set_cached_description(image_bytes: bytes, description: str):
    """寫入圖片描述到 cache。"""
    _image_cache[_image_hash(image_bytes)] = {
        "description": description,
        "timestamp": time.time(),
    }


def cleanup_expired_cache():
    """惰性清理過期 cache 項目。"""
    now = time.time()
    expired = [k for k, v in _image_cache.items() if now - v["timestamp"] > CACHE_TTL]
    for k in expired:
        _image_cache.pop(k, None)


_ANALYSIS_PROMPT = (
    "分析這張圖片並用以下格式輸出：\n\n"
    "[識別] 如果你認得出這是什麼角色、作品、梗圖、或 meme，直接寫出名稱。不確定就寫「不確定」。\n"
    "[類型] 角色/場景/物品/其他\n"
    "[描述] 一句話描述圖片內容\n"
    "[表情] 角色的表情（如適用）\n"
    "[姿勢] 角色的姿勢（如適用）\n"
    "[環境] 背景環境（如適用）\n"
    "[情緒] 整體氛圍\n\n"
    "如果圖片包含文字（如對話截圖、meme），請在[描述]中說明文字內容。\n"
    "只輸出結構化描述，不要加額外解釋。"
)


def _strip_thinking(raw_text: str) -> tuple[str, str]:
    """剝離 <thinking>...</thinking>，回傳 (thinking, description)。"""
    import re

    thinking = ""
    description = raw_text
    m = re.search(r"<thinking>(.*?)</thinking>", raw_text, re.DOTALL)
    if m:
        thinking = m.group(1).strip()
        description = raw_text[m.end():].strip()
    return thinking, description


def _guess_mime(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    return {
        "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
        "webp": "image/webp", "gif": "image/gif", "bmp": "image/bmp",
    }.get(ext, "image/jpeg")


def _resized_jpeg_b64(image_bytes: bytes, max_side: int) -> tuple[str, str]:
    """縮到 max_side 內並轉 JPEG；失敗則回原圖 base64（mime 為空）。"""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime = ""
    try:
        from PIL import Image as _PILImage
        import io as _io

        img = _PILImage.open(_io.BytesIO(image_bytes))
        if max(img.size) > max_side:
            ratio = max_side / max(img.size)
            img = img.resize(
                (int(img.size[0] * ratio), int(img.size[1] * ratio)),
                _PILImage.LANCZOS,
            )
        if img.mode in ("RGBA", "P", "LA"):
            img = img.convert("RGB")
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        mime = "image/jpeg"
    except Exception:
        pass
    return b64, mime


async def _read_with_deepseek(image_bytes: bytes, filename: str, image_cfg: dict) -> dict:
    """用 DeepSeek（OpenAI 相容 vision）讀圖。圖片會上傳到服務端。"""
    start = time.time()
    try:
        from llm_models import get_client, get_model
    except Exception as e:
        return {"success": False, "description": "", "thinking": "",
                "error": f"無法載入 LLM 用戶端: {e}", "elapsed": 0.0}

    max_side = int(image_cfg.get("max_side", 1024))
    b64, mime = _resized_jpeg_b64(image_bytes, max_side)
    mime = mime or _guess_mime(filename)
    model = (image_cfg.get("deepseek_model") or "").strip() or get_model("default")
    max_tokens = int(image_cfg.get("deepseek_max_tokens", 800))
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": _ANALYSIS_PROMPT},
            {"type": "image_url",
             "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    # 讀圖不需要思考鏈；thinking 常把 token 吃光導致 content 空字串。
    # 先關 thinking，仍空就把預算放大再試一次。
    attempts = [
        {"max_tokens": max_tokens, "extra_body": {"thinking": {"type": "disabled"}}},
        {"max_tokens": max(max_tokens, 2000),
         "extra_body": {"thinking": {"type": "disabled"}}},
    ]
    last_note = ""
    try:
        client = get_client(timeout=image_cfg.get("timeout", 120))
        for i, opts in enumerate(attempts, 1):
            resp = await client.chat.completions.create(
                model=model,
                temperature=0.2,
                messages=messages,
                **opts,
            )
            choice = resp.choices[0] if resp.choices else None
            msg = choice.message if choice else None
            raw = (getattr(msg, "content", None) or "").strip()
            reasoning = getattr(msg, "reasoning_content", None) or ""
            finish = getattr(choice, "finish_reason", None)
            if raw:
                thinking, description = _strip_thinking(raw)
                if description:
                    return {"success": True, "description": description,
                            "thinking": thinking, "error": "",
                            "elapsed": time.time() - start}
            last_note = (
                f"finish_reason={finish}, content_len={len(raw)}, "
                f"reasoning_len={len(reasoning)}"
            )
            print(f"[讀圖] DeepSeek 空內容 attempt {i}/{len(attempts)} ({last_note})")
        return {"success": False, "description": "", "thinking": "",
                "error": f"DeepSeek 回傳空內容（{last_note}）",
                "elapsed": time.time() - start}
    except Exception as e:
        return {"success": False, "description": "", "thinking": "",
                "error": f"DeepSeek 讀圖失敗: {type(e).__name__}: {e}",
                "elapsed": time.time() - start}


async def read_image(image_bytes: bytes, filename: str, ollama_cfg: dict,
                     image_cfg: dict | None = None) -> dict:
    """讀圖入口：依 image_cfg['provider'] 分派 ollama（預設，本地）或 deepseek（雲端）。"""
    image_cfg = image_cfg or {}
    if (image_cfg.get("provider") or "ollama").lower() == "deepseek":
        return await _read_with_deepseek(image_bytes, filename, image_cfg)
    return await _read_with_ollama(image_bytes, filename, ollama_cfg or {})


async def _read_with_ollama(image_bytes: bytes, filename: str, ollama_cfg: dict) -> dict:
    """
    透過 ollama + Gemma4 分析圖片，回傳結構化描述。

    Args:
        image_bytes: 原始圖片 bytes
        filename: 圖片檔名
        ollama_cfg: config.json 裡的 ollama 設定

    Returns:
        {"success": True/False, "description": str, "thinking": str, "error": str, "elapsed": float}
    """
    start = time.time()
    try:
        import ollama as _ollama

        host = ollama_cfg.get("host", "http://localhost:11434")
        model = ollama_cfg.get("model", "gemma4:12b")
        timeout = ollama_cfg.get("timeout", 180)

        client = _ollama.Client(host=host)
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        prompt = _ANALYSIS_PROMPT
        keep_alive = ollama_cfg.get("keep_alive", "30m")
        num_predict = ollama_cfg.get("num_predict", 768)

        def _call():
            return client.chat(
                model=model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }],
                keep_alive=keep_alive,
                options={"num_predict": num_predict},
            )

        # 高解析度圖片容易超時，先縮小到 768px 以內並轉 JPEG 壓縮
        # （gemma 的 vision 按 384px tile 切圖，768px 約 2x2＝4 tiles，比 1024px 的 3x3＝9 tiles 快近半）
        try:
            from PIL import Image as _PILImage
            import io as _io
            _img = _PILImage.open(_io.BytesIO(image_bytes))
            _max_side = max(_img.size)
            _limit = int(ollama_cfg.get("max_side", 768))
            if _max_side > _limit:
                _ratio = _limit / _max_side
                _new_size = (int(_img.size[0] * _ratio), int(_img.size[1] * _ratio))
                _img = _img.resize(_new_size, _PILImage.LANCZOS)
                if _img.mode in ("RGBA", "P", "LA"):
                    _img = _img.convert("RGB")
                _buf = _io.BytesIO()
                _img.save(_buf, format="JPEG", quality=80)
                image_b64 = base64.b64encode(_buf.getvalue()).decode("utf-8")
        except Exception:
            pass  # resize 失敗就用原圖

        resp = await asyncio.wait_for(
            asyncio.to_thread(_call),
            timeout=timeout,
        )

        raw_text = resp.get("message", {}).get("content", "")
        elapsed = time.time() - start

        # 剝離 <thinking>...</thinking> 標籤
        thinking = ""
        description = raw_text
        import re
        think_match = re.search(r"<thinking>(.*?)</thinking>", raw_text, re.DOTALL)
        if think_match:
            thinking = think_match.group(1).strip()
            description = raw_text[think_match.end():].strip()

        if not description:
            return {
                "success": False,
                "description": "",
                "thinking": thinking,
                "error": "Gemma 回傳空內容",
                "elapsed": elapsed,
            }

        return {
            "success": True,
            "description": description,
            "thinking": thinking,
            "error": "",
            "elapsed": elapsed,
        }

    except ImportError:
        return {
            "success": False,
            "description": "",
            "thinking": "",
            "error": "ollama 套件未安裝，請執行 pip install ollama",
            "elapsed": time.time() - start,
        }
    except asyncio.TimeoutError:
        return {
            "success": False,
            "description": "",
            "thinking": "",
            "error": f"圖片分析逾時（{timeout}s），圖片可能過大或 ollama 載入模型中，請稍後重試",
            "elapsed": time.time() - start,
        }
    except Exception as e:
        return {
            "success": False,
            "description": "",
            "thinking": "",
            "error": f"圖片分析失敗: {type(e).__name__}: {e}",
            "elapsed": time.time() - start,
        }


async def log_image_analysis(user_name: str, channel_name: str, filename: str,
                             result: dict, ai_reply: str):
    """將圖片分析結果寫入 image_logs/{date}.txt"""
    os.makedirs(IMAGE_LOG_DIR, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    log_path = os.path.join(IMAGE_LOG_DIR, f"{today}.txt")
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        f"{'='*50}",
        f"時間: {now_str}",
        f"用戶: {user_name}",
        f"頻道: {channel_name}",
        f"圖片: {filename}",
        f"耗時: {result.get('elapsed', 0):.1f}s",
        "",
        "=== Gemma 思考鏈 ===",
        result.get("thinking", "（無）") or "（無）",
        "",
        "=== Gemma 描述 ===",
        result.get("description", "（無）") or "（無）",
        "",
        "=== AI 回覆 ===",
        ai_reply or "（無）",
        "",
    ]

    async with aiofiles.open(log_path, "a", encoding="utf-8") as f:
        await f.write("\n".join(lines) + "\n")
