"""Generic utilities used by agents.

Migrated from novel_AI/utils.py. Currently provides parse_llm_json_response
which strips markdown fences and falls back to a default on parse failure.
"""
from __future__ import annotations
import json
import logging
import re
from typing import Any

log = logging.getLogger("novel_ai.utils")


def _coerce_type(parsed: Any, default: Any) -> Any:
    """类型保护：parse 出来的对象必须跟 default 类型一致。

    历史上（你独立验证）：tracker 等 agent 假设 parse 返回 dict，
    但 LLM 偶尔返回 list/None/str → 后续 `updates.get(...)` 抛
    `'list' object has no attribute 'get'`，60+ 章连续报错。

    修法（系统级）：如果类型不匹配，自动把 parsed 转成 default 的
    形状——dict 缺失就回 default、list 缺失就回 default。如果是 None
    而 default 是 dict，回 {}，list 回 []，str 回 ""。

    严格场景下（schema 强校验），agent 应该传入 TypedDict 或 Pydantic
    模型；这里只做"软保护"避免下游整个崩。
    """
    if parsed is None:
        if isinstance(default, dict):
            return {}
        if isinstance(default, list):
            return []
        if isinstance(default, str):
            return ""
        return default
    # 类型匹配 → 直接返回（dict / list / str 分别检查，因为 isinstance(dict, object) 不会混淆）
    if isinstance(default, dict) and isinstance(parsed, dict):
        return parsed
    if isinstance(default, list) and isinstance(parsed, list):
        return parsed
    if isinstance(default, str) and isinstance(parsed, str):
        return parsed
    # 类型不匹配 → 警告 + 回 default
    log.warning(
        "parse_llm_json_response: type mismatch (default=%s, got=%s) — falling back to default",
        type(default).__name__, type(parsed).__name__,
    )
    return default


def parse_llm_json_response(resp: str, default):
    """Best-effort JSON parse of an LLM response.

    Strips ```json ... ``` fences, regex-searches the first balanced JSON
    object/array, and returns the parsed value. Falls back to `default`
    on any failure (returns `default` as-is, including None).

    类型保护（参见 _coerce_type）：返回前会校验 parsed 是否跟 default
    同型，否则警告 + 退回 default。
    """
    if not resp:
        return default

    s = resp.strip()

    # Strip ``` fences (any language tag)
    if s.startswith("```"):
        lines = s.split("\n")
        # Drop first line (```json or ```)
        lines = lines[1:]
        # Drop trailing ``` if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()

    parsed: Any = None

    # Try direct parse
    try:
        parsed = json.loads(s)
    except Exception:
        pass

    # Try to find the first balanced JSON object/array
    if parsed is None:
        for opener, closer in (('{', '}'), ('[', ']')):
            start = s.find(opener)
            if start < 0:
                continue
            depth = 0
            for i in range(start, len(s)):
                ch = s[i]
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        candidate = s[start:i+1]
                        try:
                            parsed = json.loads(candidate)
                            break
                        except Exception:
                            break
            if parsed is not None:
                break

    # Try a forgiving cleanup (remove trailing commas in objects/arrays)
    if parsed is None:
        cleaned = re.sub(r",\s*([}\]])", r"\1", s)
        try:
            parsed = json.loads(cleaned)
        except Exception:
            pass

    # 全部失败 → default
    if parsed is None:
        return default

    # 类型保护
    return _coerce_type(parsed, default)