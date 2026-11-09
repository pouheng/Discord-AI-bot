"""
parsers.py — 統一 JSON 提取與 LEARN 標籤解析

所有 AI 回應的 JSON 擷取和 [LEARN:{...}] 標籤解析都在此處理。
"""

import re
import json


def extract_json(text: str) -> str:
    """強固 JSON 擷取：去除 markdown 標記，巢狀括號計數，容忍前言。"""
    text = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
    start = text.find("{")
    if start == -1:
        return "{}"
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return "{}"


def parse_learn_tags(text: str) -> list[dict]:
    """從 AI 回應中解析所有 [LEARN:{...}] 標籤，回傳 dict 列表。"""
    results = []
    matches = re.findall(r"\[LEARN:(\{.*?\})\]", text, re.DOTALL)
    for m in matches:
        try:
            data = json.loads(m)
            if isinstance(data, dict):
                results.append(data)
        except json.JSONDecodeError:
            pass
    return results


def parse_mem_tag(text: str) -> dict | None:
    """從 AI 回應中解析 [MEM:{...}] 標籤，回傳 dict 或 None。"""
    match = re.search(r"\[MEM:(\{.*?\})\]", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass
    return None


def strip_planning(text: str) -> str:
    """移除 <planning>...</planning> 區塊；未閉合時從 <planning> 起全部切掉。"""
    text = re.sub(r"<planning>.*?</planning>", "", text, flags=re.DOTALL)
    if "<planning>" in text:
        text = text.split("<planning>", 1)[0]
    return text.strip()


def strip_mem_learn_tags(text: str) -> str:
    """移除 [MEM:{...}] 和 [LEARN:{...}] 標籤。"""
    text = re.sub(r"\s*\[MEM:\{.*?\}\]", "", text, flags=re.DOTALL).strip()
    text = re.sub(r"\s*\[LEARN:\{.*?\}\]", "", text, flags=re.DOTALL).strip()
    return text


def strip_html_tags(text: str) -> str:
    """移除意外產生的 HTML 標籤。"""
    return re.sub(r"</?(\w+)[^>]*>", "", text, flags=re.DOTALL).strip()


def strip_body_marker(text: str) -> str:
    """剝除模型從 prompt 模板（main.py 的（正文）區段標記）照抄進正文的字面「（正文）」。

    成因：phase2/3 模板用字面「（正文）」標出「正文從這裡開始」，
    模型在 recency bias 下會原樣抄進回覆開頭；這裡移除該字面與前後多餘換行。
    """
    text = re.sub(r"\s*[（(]正文[）)]\s*\n", "\n", text)
    text = re.sub(r"^\s*[（(]正文[）)]\s*", "", text)
    return text.strip()
