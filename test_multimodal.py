"""Multimodal 圖片分析測試 — 驗證 image_reader + cache + run_phase2_direct"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

passed = 0
total = 0

def check(name, ok):
    global passed, total
    total += 1
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}")
    if ok:
        passed += 1

# ── Test 1: image_reader module exists ──
import image_reader
check("image_reader module importable", hasattr(image_reader, "read_image"))
check("get_cached_description exists", callable(image_reader.get_cached_description))
check("set_cached_description exists", callable(image_reader.set_cached_description))
check("cleanup_expired_cache exists", callable(image_reader.cleanup_expired_cache))
check("log_image_analysis exists", callable(image_reader.log_image_analysis))

# ── Test 2: cache basic operations ──
# Clear cache first
image_reader._image_cache.clear()

img_a = b"image content A"
img_b = b"image content B"
img_c = b"image content C"

result = image_reader.get_cached_description(img_a)
check("cache miss returns None", result is None)

image_reader.set_cached_description(img_a, "[類型] 測試\n[描述] 單元測試圖片")
result = image_reader.get_cached_description(img_a)
check("cache hit returns description", result == "[類型] 測試\n[描述] 單元測試圖片")

# ── Test 3: cache miss for different image ──
result = image_reader.get_cached_description(img_b)
check("cache miss for different image", result is None)

# ── Test 4: cache hit for same image bytes ──
result = image_reader.get_cached_description(img_a)
check("cache hit for same image", result == "[類型] 測試\n[描述] 單元測試圖片")

# ── Test 5: cache TTL expiry ──
import time
fake_hash = image_reader._image_hash(b"expired_img")
image_reader._image_cache[fake_hash] = {
    "description": "expired entry",
    "timestamp": time.time() - 400,  # > 300s TTL
}
result = image_reader.get_cached_description(b"expired_img")
check("expired cache returns None", result is None)
check("expired cache cleaned up", fake_hash not in image_reader._image_cache)

# ── Test 6: cleanup_expired_cache ──
old_hash = image_reader._image_hash(b"old_img")
new_hash = image_reader._image_hash(b"new_img")
image_reader._image_cache[old_hash] = {"description": "old", "timestamp": time.time() - 400}
image_reader._image_cache[new_hash] = {"description": "new", "timestamp": time.time()}
image_reader.cleanup_expired_cache()
check("cleanup removes expired entries", old_hash not in image_reader._image_cache)
check("cleanup keeps fresh entries", new_hash in image_reader._image_cache)

# ── Test 7: run_phase2_direct exists in main ──
import main
check("run_phase2_direct exists", callable(getattr(main, "run_phase2_direct", None)))

# ── Test 8: build_and_call_phase2 has image_description param ──
import inspect
sig = inspect.signature(main.build_and_call_phase2)
check("build_and_call_phase2 has image_description param",
      "image_description" in sig.parameters)

# ── Test 9: read_image with no ollama (should return error gracefully) ──
import asyncio
async def test_read_image_no_ollama():
    result = await image_reader.read_image(b"fake image bytes", "test.png", {})
    return result

result = asyncio.get_event_loop().run_until_complete(test_read_image_no_ollama())
check("read_image returns error when ollama unavailable",
      result["success"] is False and "error" in result)

# ── Test 10: log_image_analysis writes file ──
import tempfile, datetime
async def test_log():
    test_dir = os.path.join(os.path.dirname(__file__), "image_logs")
    os.makedirs(test_dir, exist_ok=True)
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    test_path = os.path.join(test_dir, f"{today}.txt")
    existed = os.path.exists(test_path)
    await image_reader.log_image_analysis(
        "test_user", "test_channel", "test.png",
        {"description": "test desc", "thinking": "test think", "elapsed": 1.5},
        "test reply",
    )
    return os.path.exists(test_path)

log_ok = asyncio.get_event_loop().run_until_complete(test_log())
check("log_image_analysis writes to file", log_ok)

print(f"\n{'='*40}")
print(f"Results: {passed}/{total} passed")
if passed == total:
    print("ALL TESTS PASSED")
else:
    print("SOME TESTS FAILED")
    sys.exit(1)
