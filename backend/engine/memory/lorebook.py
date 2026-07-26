"""关键词世界书 —— 按本章实际内容触发的设定检索。

2026-07-26 接线：本模块原本标注 "OFFLINE ONLY / NOT wired into the writer
prompt"，写好了、带 21 个测试，但从未接进写作回路 —— 引擎的写作上下文只有 L2
摘要 + 人物状态的**子串匹配**，没有任何按需检索。同类长篇写作项目的共识是
「摘要 + 检索 + 图谱」多层记忆，检索层缺失正是几十万字后设定漂移的主因。

现在 `build_lorebook_from_setting()` 从 setting_package 派生词条（主角 / 关键
配角 / 世界名 / 力量体系与各等级 / 货币），`writer.build_writer_prompt()` 用
本章任务与近期剧情做触发查询，把命中的设定原文注入 prompt。相比原来那个固定的
【世界观速览】摘要块，这里是**相关性驱动 + 预算受控 + 认别名**的按需注入。

Pure data → pure data：本模块不碰 IO、不调 LLM，可离线测精确率/召回率。

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


# 词条优先级：数字越大越先占预算。主角/关键配角 > 世界名 > 体系 > 等级/货币。
PRIORITY_PROTAGONIST = 5
PRIORITY_CHARACTER = 4
PRIORITY_WORLD = 4
PRIORITY_POWER_SYSTEM = 3
PRIORITY_DETAIL = 2


def _clip(s: object, n: int) -> str:
    return str(s or "").strip()[:n]


def _entry(key: str, content: str, priority: int, aliases: list | None = None) -> dict | None:
    """构造一条词条；key 或 content 为空就返回 None（由调用方过滤）。"""
    key = (key or "").strip()
    content = (content or "").strip()
    if not key or not content:
        return None
    return {
        "key": key,
        "aliases": [a for a in (aliases or []) if a and str(a).strip() and str(a).strip() != key],
        "content": content,
        "priority": priority,
    }


def build_lorebook_from_setting(setting: dict) -> list[dict]:
    """从 setting_package 派生世界书词条。

    只用已有数据，不新增任何字段或存储。字段来源见
    `backend/schema/setting_package.schema.json`。

    刻意**不**把 `world_setting.unique_elements` 做成词条：它们是整句描述
    （如「债务可以具象化为实体」），拿来当关键词会匹配不到任何东西，
    只会白占预算。这类总览信息由 writer 的【世界观速览】块负责。
    """
    if not isinstance(setting, dict):
        return []

    out: list[dict] = []

    mc = setting.get("protagonist") or {}
    if isinstance(mc, dict):
        parts = [
            _clip(mc.get("background"), 120),
            _clip(mc.get("personality"), 80),
            f"初始境界：{_clip(mc.get('initial_power_level'), 30)}"
            if mc.get("initial_power_level") else "",
            "口癖：" + "、".join(str(q) for q in (mc.get("speech_quirks") or [])[:3])
            if mc.get("speech_quirks") else "",
        ]
        out.append(_entry(_clip(mc.get("name"), 30),
                          "｜".join(p for p in parts if p),
                          PRIORITY_PROTAGONIST))

    for c in (setting.get("key_characters") or []):
        if not isinstance(c, dict):
            continue
        parts = [
            _clip(c.get("role"), 20),
            _clip(c.get("background"), 120),
            "口癖：" + "、".join(str(q) for q in (c.get("speech_quirks") or [])[:3])
            if c.get("speech_quirks") else "",
        ]
        out.append(_entry(_clip(c.get("name"), 30),
                          "｜".join(p for p in parts if p),
                          PRIORITY_CHARACTER))

    ws = setting.get("world_setting") or {}
    if isinstance(ws, dict):
        out.append(_entry(_clip(ws.get("surface_world_name"), 30),
                          f"表世界。{_clip(ws.get('hidden_world_history'), 150)}"
                          if ws.get("hidden_world_history") else "表世界（故事主舞台）",
                          PRIORITY_WORLD))
        out.append(_entry(_clip(ws.get("hidden_world_name"), 30),
                          f"里世界。{_clip(ws.get('hidden_world_history'), 150)}"
                          if ws.get("hidden_world_history") else "里世界（隐藏设定）",
                          PRIORITY_WORLD))

    ps = setting.get("power_system") or {}
    if isinstance(ps, dict):
        levels = [lv for lv in (ps.get("levels") or []) if isinstance(lv, dict)]
        level_names = [_clip(lv.get("name"), 20) for lv in levels]
        level_names = [n for n in level_names if n]
        ladder = " → ".join(level_names)
        out.append(_entry(
            _clip(ps.get("name"), 30),
            "｜".join(p for p in [_clip(ps.get("description"), 120),
                                  f"等级：{ladder}" if ladder else ""] if p),
            PRIORITY_POWER_SYSTEM,
            aliases=level_names,
        ))
        for idx, lv in enumerate(levels):
            name = _clip(lv.get("name"), 20)
            desc = _clip(lv.get("description") or lv.get("desc"), 100)
            pos = f"第 {idx + 1} 级/共 {len(levels)} 级"
            out.append(_entry(name, f"{pos}。{desc}" if desc else pos, PRIORITY_DETAIL))
        out.append(_entry(_clip(ps.get("currency"), 20),
                          f"{_clip(ps.get('name'), 30)} 的资源单位" if ps.get("name")
                          else "力量体系的资源单位",
                          PRIORITY_DETAIL))

    # 去重（同 key 保留优先级最高的那条）
    best: dict[str, dict] = {}
    for e in out:
        if e is None:
            continue
        cur = best.get(e["key"])
        if cur is None or e["priority"] > cur["priority"]:
            best[e["key"]] = e
    return list(best.values())


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
