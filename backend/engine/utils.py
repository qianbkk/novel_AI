"""Generic utilities used by agents.

Migrated from novel_AI/utils.py. Currently provides parse_llm_json_response
which strips markdown fences and falls back to a default on parse failure.
"""
from __future__ import annotations
import json
import re


def parse_llm_json_response(resp: str, default):
    """Best-effort JSON parse of an LLM response.

    Strips ```json ... ``` fences, regex-searches the first balanced JSON
    object/array, and returns the parsed value. Falls back to `default`
    on any failure (returns `default` as-is, including None).
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

    # Try direct parse
    try:
        return json.loads(s)
    except Exception:
        pass

    # Try to find the first balanced JSON object/array
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
                        return json.loads(candidate)
                    except Exception:
                        break

    # Try a forgiving cleanup (remove trailing commas in objects/arrays)
    cleaned = re.sub(r",\s*([}\]])", r"\1", s)
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    return default