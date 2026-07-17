"""keyword worldbook prototype (task 12 — OFFLINE ONLY)

Pure data → pure data offline keyword/alias trigger retrieval. NOT wired into
the writer prompt. Used for offline precision/recall measurement only.

Capabilities:
- Chinese no-whitespace text matching
- Aliases: each entry may have a list of aliases
- English case-insensitive
- Hit window: positions of matches within the text (used for dedup)
- Priority: higher priority can displace lower priority hits
- Total budget: max total characters returned

Public API:
  Lorebook = list[{key, aliases, content, priority}]
  match(lorebook, text, *, budget=2000, window=120) -> list[{key, aliases,
        content, hits, priority, score}]
"""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable


def normalize(s: str) -> str:
    """NFKC + 折叠空白，方便中文去空格比较。"""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"\s+", "", s)
    return s


def _compile_pattern(key: str) -> re.Pattern:
    """按 key 长度构造子串 regex（NFKC 化 + 大小写不敏感）。"""
    norm = normalize(key)
    return re.compile(re.escape(norm), flags=re.IGNORECASE)


def match(
    lorebook: Iterable[dict],
    text: str,
    *,
    budget: int = 2000,
    window: int = 120,
) -> list[dict]:
    """对 text 跑关键词世界书匹配，按优先级与命中窗口返回去重结果。

    Args:
        lorebook: 形如 [{key, aliases, content, priority}] 的条目列表
        text: 章节正文或其他待检索文本
        budget: 返回总字符上限（超过则截断）
        window: 命中窗口（字符）；在窗口内的同一 key 重复命中只记一次

    Returns:
        [{key, aliases, content, hits, priority, score}]，score 越高越优先
    """
    if not text or not lorebook:
        return []

    norm_text = normalize(text)
    out: list[dict] = []
    seen_keys: set[str] = set()

    # 按 priority 降序
    entries = sorted(lorebook, key=lambda e: -int(e.get("priority", 1)))

    total_chars = 0
    for entry in entries:
        key = entry.get("key", "").strip()
        content = entry.get("content", "")
        priority = int(entry.get("priority", 1))
        aliases = entry.get("aliases", []) or []

        # 空 key 跳过（空白条目不应触发"匹配所有"）
        if not key:
            continue

        candidates = [key] + [a for a in aliases if a]
        hits: list[int] = []
        for cand in candidates:
            norm_cand = normalize(cand)
            if not norm_cand:
                continue
            pat = re.compile(re.escape(norm_cand), flags=re.IGNORECASE)
            for m in pat.finditer(norm_text):
                hits.append(m.start())

        if not hits:
            continue

        if key in seen_keys:
            continue

        # 窗口去重（同 key 多个位置只记最早）
        hits.sort()
        deduped_hits: list[int] = []
        for h in hits:
            if not deduped_hits or h - deduped_hits[-1] >= window:
                deduped_hits.append(h)
        # 记一次该 key，后续 alias 命中不再重复触发
        seen_keys.add(key)

        score = priority * 10 + len(deduped_hits)
        out.append({
            "key": key,
            "aliases": aliases,
            "content": content,
            "hits": deduped_hits,
            "priority": priority,
            "score": score,
        })

    out.sort(key=lambda r: -r["score"])

    # 预算截断
    pruned: list[dict] = []
    used = 0
    for r in out:
        cost = len(r["content"]) + len(r["key"])
        if used + cost > budget:
            break
        pruned.append(r)
        used += cost

    return pruned
