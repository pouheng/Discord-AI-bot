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


async def read_image(image_bytes: bytes, filename: str, ollama_cfg: dict) -> dict:
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

        prompt = (
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

        def _call():
            return client.chat(
                model=model,
                messages=[{
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }],
            )

        # 高解析度圖片容易超時，先縮小到 768px 以內並轉 JPEG 壓縮
        # （gemma 的 vision 按 384px tile 切圖，768px 約 2x2＝4 tiles，比 1024px 的 3x3＝9 tiles 快近半）
        try:
            from PIL import Image as _PILImage
            import io as _io
            _img = _PILImage.open(_io.BytesIO(image_bytes))
            _max_side = max(_img.size)
            if _max_side > 768:
                _ratio = 768 / _max_side
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
