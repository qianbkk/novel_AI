"""backend/tests/test_title_extraction_2026_07_23.py — 验证问题 #8 修复

5 步修复的核心：writer._extract_title 第 0 级能解析 LLM 半合法 JSON 包装
（开头是 { 且含 "body"，body 字段含真换行符让 json.loads 失败）。
"""
from __future__ import annotations
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from engine.agents.writer import _extract_title


def test_extract_title_strict_json():
    title, body = _extract_title('{"title": "账本翻开", "body": "正文第一句..."}')
    assert title == "账本翻开", f"got {title!r}"
    assert body == "正文第一句..."


def test_extract_title_json_with_real_newlines():
    """核心修复：LLM 在 body 字段里塞了真换行符（旧 json.loads 失败 → 降级到第 4 级把 JSON 包装当首行截 30 字）。"""
    raw = '{"title": "九二铜钱暗记", "body": "九二铜钱暗记\n\n铜钱从内袋摸出来，摊在掌心。\n\n林渊蹙了眉。"}'
    title, body = _extract_title(raw)
    assert title == "九二铜钱暗记", f"got {title!r}"
    assert "铜钱从内袋摸出来" in body, f"body 应当含正文, got {body!r}"


def test_extract_title_json_with_quotes_in_body():
    raw = '{"title": "标题", "body": "他说：\\"你好\\"\n下一段"}'
    title, body = _extract_title(raw)
    assert title == "标题", f"got {title!r}"
    assert "你好" in body, f"got {body!r}"


def test_extract_title_pure_text_falls_through():
    """纯文本（非 JSON）应该走第 4 级用首句当 title。"""
    raw = "标题首句\n\n后续正文..."
    title, body = _extract_title(raw)
    assert title == "标题首句", f"got {title!r}"
    assert body == raw


def test_extract_title_markdown_fence_json():
    raw = '```json\n{"title": "fence标题", "body": "正文内容"}\n```'
    title, body = _extract_title(raw)
    assert title == "fence标题"
    assert body == "正文内容"


def test_extract_title_escaped_chars_in_title():
    raw = '{"title": "带\\"引号的标题", "body": "正文"}'
    title, body = _extract_title(raw)
    # unescape 后 title 应含 "
    assert "引号" in title, f"got {title!r}"


def test_extract_title_empty():
    title, body = _extract_title("")
    # fallback 用 chapter_goal 派生（fallback_goal 为空 → "未命名章节"）
    assert title == "未命名章节"
    assert body == ""


def test_extract_title_truncates_long_title():
    raw = '{"title": "这个标题非常非常非常非常非常非常非常非常非常长", "body": "正文"}'
    title, body = _extract_title(raw)
    # _extract_title 第 1 级截断 50 字
    assert len(title) <= 50


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
