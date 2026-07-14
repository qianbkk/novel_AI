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
    """code-review-2026-07-13 #1 + /simplify-2026-07-13 跟进：outline /
    manager / compliance / parse_llm_json_response 必须用共享 helper，
    不能再 inline fence 剥离逻辑。

    用 AST 而非 regex——AST 是 Python 自己定义的代码结构抽象，不受注释/
    缩进/空行变体影响。
    """
    import ast
    from pathlib import Path
    backend_root = Path(__file__).resolve().parents[1]

    def _find_strip_calls(source: str) -> list[ast.Call]:
        """返回源码里所有调用 strip_markdown_fence 的 AST.Call 节点。"""
        tree = ast.parse(source)
        return [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "strip_markdown_fence"
        ]

    def _imports_strip(source: str) -> bool:
        """源码是否 import 了 strip_markdown_fence（from ..utils import ...）。"""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and \
               node.module.endswith("utils"):
                if any(alias.name == "strip_markdown_fence" for alias in node.names):
                    return True
        return False

    outline_src = (backend_root / "engine/agents/outline.py").read_text(encoding="utf-8")
    manager_src = (backend_root / "engine/memory/manager.py").read_text(encoding="utf-8")
    compliance_src = (backend_root / "engine/agents/compliance.py").read_text(encoding="utf-8")
    utils_src = (backend_root / "engine/utils.py").read_text(encoding="utf-8")

    # 三个 caller 必须 import helper
    for label, src in [("outline.py", outline_src), ("manager.py", manager_src),
                       ("compliance.py", compliance_src)]:
        assert _imports_strip(src), \
            f"{label} 必须 import strip_markdown_fence helper"

    # outline.py / manager.py / compliance.py 内部必须调用 helper
    # （import 了不用 → UnboundLocalError；用 1 次及以上 → 替换发生）
    for label, src in [("outline.py", outline_src), ("manager.py", manager_src),
                       ("compliance.py", compliance_src)]:
        assert len(_find_strip_calls(src)) >= 1, \
            f"{label} 必须调用 strip_markdown_fence"

    # parse_llm_json_response 内部 fence 剥离必须用 helper（且不应再有
    # "Drop first line" 这种 inline fence 注释）
    parse_strip_calls = _find_strip_calls(utils_src)
    assert len(parse_strip_calls) >= 1, \
        "parse_llm_json_response 必须内部调用 strip_markdown_fence"
    # 锁定 future 不能回退：utils.py 里不应出现 inline fence stripping 标志
    # （"Drop first line" 注释 + lines[1:] fence-strip pattern）
    assert "Drop first line" not in utils_src, \
        "utils.py parse_llm_json_response 不应再有 inline fence 剥离代码"


def test_strip_fence_fence_on_first_line_only():
    """``` 在文件中间开 fence 不算 — 只识别第一行。"""
    resp = '哈喽 ```json\n{"a":1}\n```'
    out = strip_markdown_fence(resp)
    assert out.startswith("哈喽 ")  # 不会误认


# ────────────────────────────────────────────────────────────
# parse_llm_json_response — default=None 哨兵语义（P6 修复回归测试）
# ────────────────────────────────────────────────────────────

def test_parse_llm_json_response_none_default_dict_returns_dict():
    """default=None + LLM 返 dict → 返回该 dict（正常路径）。"""
    from engine.utils import parse_llm_json_response
    out = parse_llm_json_response('{"a":1, "b":2}', None)
    assert out == {"a": 1, "b": 2}


def test_parse_llm_json_response_none_default_list_returns_none():
    """default=None + LLM 返 list → 视为 parse 失败返回 None。

    30 章实验发现：LLM 偶尔返 list 而不是 dict，下游 `updates.get(...)`
    报 "'list' object has no attribute 'get'"。修法：非 dict 视为失败。
    """
    from engine.utils import parse_llm_json_response
    out = parse_llm_json_response('[{"event":"x"}]', None)
    assert out is None, f"非 dict 应返回 None，实际 {out!r}"


def test_parse_llm_json_response_none_default_str_returns_none():
    """default=None + LLM 返 str → 也视为 parse 失败返回 None。"""
    from engine.utils import parse_llm_json_response
    out = parse_llm_json_response('"just a plain string"', None)
    assert out is None


def test_parse_llm_json_response_none_default_int_returns_none():
    """default=None + LLM 返 int → 也视为 parse 失败返回 None。"""
    from engine.utils import parse_llm_json_response
    out = parse_llm_json_response('42', None)
    assert out is None


def test_parse_llm_json_response_invalid_json_none_default_returns_none():
    """default=None + parse 失败 → 返回 None（已有行为，不变）。"""
    from engine.utils import parse_llm_json_response
    out = parse_llm_json_response('not json at all', None)
    assert out is None


def test_parse_llm_json_response_dict_default_list_returns_empty_dict():
    """default={} + LLM 返 list → 走 _coerce_type 类型不匹配分支返 {}（已有行为）。"""
    from engine.utils import parse_llm_json_response
    out = parse_llm_json_response('[1, 2, 3]', {})
    assert out == {}, f"type mismatch 应回 default，实际 {out!r}"
