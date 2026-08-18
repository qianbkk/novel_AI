"""research_notes.py - v1.0 Stage D 资料助手

输入：genre_profile + concept
输出：research_notes（落 output/research_notes.json）

按 genre_profile.research_strength 三档分流：
- strong（历史）: 5 维度 baseline（朝代/地理/职官/物价/服饰）
- medium（玄幻/仙侠/科幻）: system_consistency（力量/科技体系约束）
- weak（都市）: minimal baseline（现实题材无需查资料）

按章 query 接口：query_notes(novel_id, chapter=2) → 拼接 baseline + 该章 notes。
writer 写每章前调一次，把相关资料注入 prompt。

CLAUDE.md 红线：
- 必填字段缺失 → InvalidResearchNotesError（不让半成品落盘）
- research_strength 必须是 strong/medium/weak 之一
- LLM 失败 → 保留 baseline + log.warning
- 不含具体项目专名
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..llm_router import get_active_router
from ..utils import parse_llm_json_response


_log = logging.getLogger("novel_ai.engine.agents.research_notes")


REQUIRED_NOTES_FIELDS = (
    "research_strength",
    "baseline",
    "per_chapter_notes",
    "source",
)

VALID_STRENGTHS = ("strong", "medium", "weak")


class InvalidResearchNotesError(ValueError):
    """research_notes 字段不全或结构错误。"""


# ── 三档 baseline 模板 ─────────────────────────

_STRONG_BASELINE_TEMPLATE: dict[str, str] = {
    "朝代": "（请填写朝代名 + 在位年号 / 公元对照）",
    "地理": "（请填写主要地理范围：州府/县城/驿站分布）",
    "职官": "（请填写本作品涉及的官职体系：县令/驿丞/校尉等）",
    "物价": "（请填写核心物资价格：米/盐/布/铜钱比价）",
    "服饰": "（请填写主角阶级服饰：粗布/绸缎/甲胄等）",
}

_MEDIUM_BASELINE_TEMPLATE: dict[str, str] = {
    "system_consistency": "（请填写本作品力量/科技体系的核心约束：等级/货币/装备体系）",
}

_WEAK_BASELINE_TEMPLATE: dict[str, str] = {}


_STRENGTH_TO_TEMPLATE: dict[str, dict[str, str]] = {
    "strong": _STRONG_BASELINE_TEMPLATE,
    "medium": _MEDIUM_BASELINE_TEMPLATE,
    "weak": _WEAK_BASELINE_TEMPLATE,
}


# ── Public API ─────────────────────────────────────────────

def init_research_notes(
    genre_profile: dict,
    concept: str,
    *,
    use_llm: bool = False,
    novel_id: str | None = None,
) -> dict:
    """初始化 research_notes（按 research_strength 走对应模板）。

    Args:
        genre_profile: 来自 genre_profiler 的 profile dict
        concept: 用户初始概念（context 给 LLM 用）
        use_llm: 是否用 LLM 在模板基础上细化（CI 友好默认 False）
        novel_id: 项目 ID（落盘用）

    Returns:
        notes dict，含 REQUIRED_NOTES_FIELDS 全部 4 个字段
    """
    strength = (genre_profile or {}).get("research_strength") or "weak"
    if strength not in VALID_STRENGTHS:
        strength = "weak"  # fallback（CLAUDE.md 允许 weak 兜底，但落盘时校验）

    notes = _template_to_notes(strength, source="template")

    if use_llm and strength != "weak":
        notes = _refine_with_llm(notes, concept, genre_profile)

    if novel_id:
        save_notes(novel_id, notes)

    return notes


def save_notes(novel_id: str, notes: dict) -> None:
    """落盘 research_notes（用户编辑或 AI 生成都走这条路径）。

    Raises:
        InvalidResearchNotesError: 缺字段 / 类型错 / strength 非法
    """
    if not isinstance(notes, dict):
        raise InvalidResearchNotesError(
            f"research_notes 必须是 dict，实际 {type(notes).__name__}"
        )

    for f in REQUIRED_NOTES_FIELDS:
        if f not in notes:
            raise InvalidResearchNotesError(
                f"research_notes 缺字段 {f!r}；落盘前必须含 {REQUIRED_NOTES_FIELDS}"
            )

    strength = notes["research_strength"]
    if strength not in VALID_STRENGTHS:
        raise InvalidResearchNotesError(
            f"research_strength 必须是 {VALID_STRENGTHS} 之一，实际 {strength!r}"
        )

    if not isinstance(notes["baseline"], dict):
        raise InvalidResearchNotesError(
            f"baseline 必须是 dict，实际 {type(notes['baseline']).__name__}"
        )

    if not isinstance(notes["per_chapter_notes"], dict):
        raise InvalidResearchNotesError(
            f"per_chapter_notes 必须是 dict（key=章号），实际 "
            f"{type(notes['per_chapter_notes']).__name__}"
        )

    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "research_notes.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    target.write_text(_json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("research_notes 落盘: %s (strength=%s)", target, strength)


def load_notes(novel_id: str) -> dict | None:
    """加载已落盘的 research_notes（启动时检测，老项目 bootstrap 按 default 初始化）。"""
    from ..config.paths import novel_ai_dir

    target = Path(novel_ai_dir(novel_id)) / "output" / "research_notes.json"
    if not target.is_file():
        return None
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        for f_name in REQUIRED_NOTES_FIELDS:
            if f_name not in data:
                _log.warning("research_notes 缺字段 %s，视为损坏: %s", f_name, target)
                return None
        return data
    except Exception:
        return None


def query_notes(novel_id: str, chapter: int) -> str:
    """按章 query research_notes — writer 写每章前调一次。

    Returns:
        拼接后的字符串：baseline（strong/medium 含核心事实）+ 该章 per_chapter_notes。
        未生成 research_notes 时返回空串（不阻断 writer）。
    """
    notes = load_notes(novel_id)
    if notes is None:
        return ""

    parts: list[str] = []

    # baseline
    baseline = notes.get("baseline") or {}
    strength = notes.get("research_strength", "weak")
    if baseline and strength != "weak":
        parts.append(f"=== {strength} 题材核心事实 baseline ===")
        for k, v in baseline.items():
            parts.append(f"[{k}] {v}")

    # 该章 notes
    ch_key = str(chapter)
    per_chapter = notes.get("per_chapter_notes") or {}
    ch_notes = per_chapter.get(ch_key)
    if ch_notes:
        parts.append(f"=== 第 {chapter} 章 资料 ===")
        parts.append(ch_notes if isinstance(ch_notes, str) else str(ch_notes))

    return "\n".join(parts)


# ── Internal helpers ─────────────────────────────────────────────

def _template_to_notes(strength: str, *, source: str) -> dict:
    """模板 → notes dict。"""
    baseline = dict(_STRENGTH_TO_TEMPLATE.get(strength, {}))
    return {
        "research_strength": strength,
        "baseline": baseline,
        "per_chapter_notes": {},  # 用户 / LLM 在写作过程中填
        "source": source,
    }


def _refine_with_llm(notes: dict, concept: str, genre_profile: dict) -> dict:
    """用 LLM 在 baseline 模板基础上补充细节（weak 跳过）。

    LLM 失败 → 保留模板 + log.warning。
    """
    router = get_active_router()
    if router is None:
        _log.warning("LLM router 未初始化，跳过 research_notes 细化（保持模板）")
        return notes

    genre_name = (genre_profile or {}).get("genre", "未知")
    strength = notes["research_strength"]

    if strength == "strong":
        baseline_keys = list(_STRONG_BASELINE_TEMPLATE.keys())
        system_prompt = (
            f"你是{genre_name}题材的资料助手，帮我填一份 5 维度 baseline。"
            f"维度（key 严格保持）：{baseline_keys}。"
            "我会给你用户初始概念，你需要根据题材常识给出合理默认值。"
            "约束：不含具体项目专名（角色名/地名/世界名），"
            "统一用'主角/配角/世界名'中性词。"
            "直接输出 JSON: {\"" + "\": \"...\", \"".join(baseline_keys) + "\": \"...\"}"
        )
    elif strength == "medium":
        system_prompt = (
            f"你是{genre_name}题材的资料助手，帮我填 system_consistency baseline。"
            "我会给你用户初始概念，你需要给出本作品力量/科技体系的核心约束。"
            "约束：不含具体项目专名（角色名/地名/世界名），统一用'主角/配角/世界名'。"
            "直接输出 JSON: {\"system_consistency\": \"...\"}"
        )
    else:
        return notes

    user_prompt = f"概念：{concept or '（未填）'}\n模板 baseline：{notes['baseline']}"

    try:
        out, cost = router.call(
            agent_name="research_notes",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=600,
            temperature=0.5,
        )
        refined = parse_llm_json_response(out, default={})
        if refined and isinstance(refined, dict):
            # 合并：模板 key 保留，LLM 值填具体
            for k, v in refined.items():
                if isinstance(v, str) and v:
                    notes["baseline"][k] = v
            notes["source"] = "llm"
            _log.info("research_notes LLM 细化完成 strength=%s cost=%.4f", strength, cost)
            return notes
        else:
            _log.warning("research_notes LLM 输出无法解析，保持模板")
            return notes
    except Exception as exc:
        _log.warning("research_notes LLM 细化失败 err=%s — 保持模板", exc)
        return notes


# 2026-08-18 修复（CLAUDE.md「失败要响亮」）：删除 _parse_llm_json wrapper。
# utils.parse_llm_json_response 本身已 log + 处理失败；wrapper 反而吞了 import error。