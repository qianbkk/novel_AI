"""backend/tests/test_utils_helpers.py — Phase 9 simplify 公共 helper 单测

测试 engine/utils.py 这次新增/提取的两个 helper：
  - truncate_preserving_ends：长章节保留头 + 尾（避免质检/状态抽取截掉弧高潮）
  - strip_markdown_fence：剥 ```json ... ``` fence（之前 4 个 agent 各自 inline）
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


from engine.utils import truncate_preserving_ends, strip_markdown_fence


# ────────────────────────────────────────────────────────────
# truncate_preserving_ends
# ────────────────────────────────────────────────────────────

def test_truncate_returns_unchanged_when_short():
    """≤threshold 原样返回，避免无意义截断。"""
    text = "abc" * 100  # 300 chars
    assert truncate_preserving_ends(text) == text
    assert truncate_preserving_ends(text, head_chars=100, tail_chars=100, threshold=4000) == text


def test_truncate_keeps_head_and_tail_when_long():
    """>threshold 保留头 + 尾，中间用 placeholder 占位。"""
    text = "HEAD_BLOCK" * 200 + "MIDDLE" * 1000 + "TAIL_BLOCK" * 200
    out = truncate_preserving_ends(text, head_chars=200, tail_chars=300, threshold=4000)
    assert text[:200] in out  # 头 200 chars 完整保留
    assert text[-300:] in out  # 尾 300 chars 完整保留
    assert "中段省略" in out


def test_truncate_default_params():
    """默认 head=1500 + tail=2000 + threshold=4000 — 跟之前 tracker 默认行为一致。"""
    text = "X" * 5000
    out = truncate_preserving_ends(text)
    # 头 1500 个 X 在头
    assert out.startswith("X" * 1500)
    # 尾 2000 个 X 在尾
    assert out.endswith("X" * 2000)
    # 长度 = 1500 + placeholder (≈14) + 2000
    assert "中段省略" in out


def test_truncate_chapter_climax_with_checker_defaults():
    """模拟 checker.py：弧高潮章节 3300 字 ≤4000 → 原样送。"""
    text = "钩子结尾" + "X" * (3300 - 4)
    out = truncate_preserving_ends(text, head_chars=2000, tail_chars=2000, threshold=4000)
    assert out == text  # ≤threshold 不截断


def test_truncate_warns_when_head_plus_tail_exceeds_threshold(caplog):
    """head + tail >= threshold 时应该 log warning 并原样返回——fail-soft。

    修法：code-review-2026-07-13 报告指出 helper 把阈值参数化但没校验参数合理性。
    当前两个调用方都满足约束，未来 caller 不守规矩时静默产出比原文更长的"截断"
    会喂给 LLM 超 token。这次加 warning + 原样返回作为兜底。
    """
    import logging
    text = "X" * 5000
    with caplog.at_level(logging.WARNING, logger="novel_ai.utils"):
        out = truncate_preserving_ends(text, head_chars=3000, tail_chars=3000, threshold=4000)
    # fail-soft：原样返回而不是"截断"出更长文本
    assert out == text
    # warning 被记下
    assert any("head_chars" in r.message and ">= threshold" in r.message
               for r in caplog.records)


# ────────────────────────────────────────────────────────────
# strip_markdown_fence
# ────────────────────────────────────────────────────────────

def test_strip_fence_simple_json_block():
    """```json ... ``` 形式。"""
    resp = '```json\n{"a": 1}\n```'
    out = strip_markdown_fence(resp)
    assert out == '{"a": 1}'


def test_strip_fence_no_language_tag():
    """无 ```json 标签，只有 ``` fence。"""
    resp = '```\n{"a": 1}\n```'
    out = strip_markdown_fence(resp)
    assert out == '{"a": 1}'


def test_strip_fence_returns_input_when_no_fence():
    """响应没有 fence 时原样返回，不破坏正常 JSON。"""
    resp = '{"a": 1}'
    out = strip_markdown_fence(resp)
    assert out == '{"a": 1}'


def test_strip_fence_handles_multiline():
    """多行内容 + fence。"""
    resp = '```json\n[\n  "a",\n  "b",\n  "c"\n]\n```'
    out = strip_markdown_fence(resp)
    assert out == '[\n  "a",\n  "b",\n  "c"\n]'


def test_strip_fence_empty():
    assert strip_markdown_fence("") == ""
    assert strip_markdown_fence(None) is None


def test_outline_and_manager_use_helper_not_inline():
    """code-review-2026-07-13 #1：outline._extract_json_array 和
    memory_manager._secondary_summarize_cold_history 必须用共享 helper，
    不能再 inline fence 剥离逻辑（这是 Phase 9 commit 漏做的"替换调用方"
    那半，本次补完）。
    """
    import re
    from pathlib import Path
    # backend/tests/test_utils_helpers.py → parents[1] = backend/
    backend_root = Path(__file__).resolve().parents[1]
    outline_src = (backend_root / "engine/agents/outline.py").read_text(encoding="utf-8")
    manager_src = (backend_root / "engine/memory/manager.py").read_text(encoding="utf-8")
    utils_src = (backend_root / "engine/utils.py").read_text(encoding="utf-8")

    # outline.py::_extract_json_array 应 import helper
    assert "from ..utils import strip_markdown_fence" in outline_src, \
        "outline.py 必须 import strip_markdown_fence helper"

    # memory/manager.py::_secondary_summarize_cold_history 应 import helper
    assert "from ..utils import strip_markdown_fence" in manager_src, \
        "manager.py 必须 import strip_markdown_fence helper"

    # parse_llm_json_response 内部应改为调 helper，不再 inline fence 剥离
    # (原 inline 块约 8 行：if s.startswith("```"): lines = s.split(...); ... )
    parse_fn_block = utils_src[utils_src.index("def parse_llm_json_response"):]
    # 找到下一个 def 边界
    next_def = re.search(r"^def ", parse_fn_block[len("def parse_llm_json_response"):],
                          re.MULTILINE)
    parse_fn_block = parse_fn_block[:next_def.start() + len("def parse_llm_json_response")] \
        if next_def else parse_fn_block
    assert "strip_markdown_fence(resp)" in parse_fn_block, \
        "parse_llm_json_response 必须内部调用 strip_markdown_fence"
    # 不应再有 "Drop first line (```json or ```)" 这种 inline fence 注释
    assert "Drop first line" not in parse_fn_block, \
        "parse_llm_json_response 不应再有 inline fence 剥离代码"


def test_strip_fence_fence_on_first_line_only():
    """``` 在文件中间开 fence 不算 — 只识别第一行。"""
    resp = '哈喽 ```json\n{"a":1}\n```'
    out = strip_markdown_fence(resp)
    assert out.startswith("哈喽 ")  # 不会误认
