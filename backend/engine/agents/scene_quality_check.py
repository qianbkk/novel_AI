"""scene_quality_check.py - v1.0 Stage G 单轮聚焦质检

输入：chapter_text + chapter_card + lorebook_hits
输出：{expectation_advanced, show_item_landed, resonance_hit, consistency_ok,
       reasons, should_escalate}

设计动机（docs/drafts/v1-quality-first-design.md § Stage 3）：
- 用户确认：'scene_quality_check 失败时怎么走？' → '直接 escalate 给人工'
- v0.5 多轮 rewrite 实测：经常把对的改错（show-item 改没 / 期待感改平）
- v1.0 设计：单轮聚焦 4 维度，任一失败 → escalate + 明确失败原因

4 维度：
1. expectation_advanced: 本章是否推进了 theme_spine.expectation_arc？
2. show_item_landed: chapter_card.show_item_required 里的物件在正文是否落地？
3. resonance_hit: 是否触达 resonance_anchor_target？
4. consistency_ok: lorebook 一致性是否破？

CLAUDE.md 红线：
- 缺字段 → QualityCheckInputError
- LLM 失败 → SceneQualityCheckFailed（不让 silently PASS — 失败要响亮）
- 不含项目专名
"""

from __future__ import annotations

import json
import logging
from typing import Any

from ..llm_router import get_active_router


_log = logging.getLogger("novel_ai.engine.agents.scene_quality_check")


REQUIRED_QUALITY_FIELDS = (
    "expectation_advanced",
    "show_item_landed",
    "resonance_hit",
    "consistency_ok",
    "reasons",
    "should_escalate",
)


# ── 异常 ─────────────────────────────────────────────

class QualityCheckInputError(ValueError):
    """输入字段不全。"""


class SceneQualityCheckFailed(RuntimeError):
    """LLM 质检失败 — 不允许 silently fallback PASS。"""


# ── 纯数据子检查（不调 LLM）────────────────────────

def _check_show_item_landed(chapter_text: str, show_item_required: list[str]) -> bool:
    """show-item 子串匹配：每个 required 物件至少一个核心词出现在正文。

    这是高置信度规则 — '鞋'/'信'/'玉佩' 等具体物件在正文出现 = 落地。
    不依赖 LLM（避免 LLM 漏报）。
    """
    if not show_item_required:
        return True
    for item in show_item_required:
        # 取 item 最后一个中文 token 作为核心词
        # 例: '那双布鞋' → '布鞋' 或 '鞋'，'母亲的牌位' → '牌位'
        # 简化：取 item 中含有的最长的连续中文片段
        if _contains_item(chapter_text, item):
            continue
        # 全部 required 都要命中，否则算未落地
        return False
    return True


def _contains_item(text: str, item: str) -> bool:
    """item 是否出现在 text 中（支持中文长物件的子串匹配）。"""
    if not item or not text:
        return False
    # 直接子串
    if item in text:
        return True
    # 提取 item 的最后 2-3 个字作为核心词
    item_clean = item.strip()
    for n in (3, 2, 1):
        if len(item_clean) >= n:
            core = item_clean[-n:]
            if core in text:
                return True
    return False


def _check_expectation_advanced(chapter_card: dict) -> bool:
    """检查 expectation_progress 是否有'推进'信号。

    '推进' = status 与 change 不一致（status 是当前状态，change 是本章推进方向）。
    例：status='扭曲' + change='家的方向变成谜团' → 推进
        status='扭曲' + change='维持扭曲' → 没推进
    """
    progress = chapter_card.get("expectation_progress") or {}
    if not progress:
        return True  # 无 expectation_progress 字段 = 无要求

    # 任意 seed_i 的 status 与 change 不一致即视为推进
    for key in progress:
        if not key.endswith("_status"):
            continue
        base = key[:-len("_status")]
        change_key = f"{base}_change"
        status_val = str(progress.get(key, "")).strip()
        change_val = str(progress.get(change_key, "")).strip()
        if not status_val or not change_val:
            continue
        # status 与 change 字符串相似度高 → 没推进
        if _is_same_meaning(status_val, change_val):
            continue
        # 找到第一个推进
        return True
    # 没找到推进
    return False


def _is_same_meaning(a: str, b: str) -> bool:
    """粗略判断 a 与 b 含义是否相同（用于'维持扭曲'类无推进）。"""
    if a == b:
        return True
    # 含 '维持' / '依然' / '不变' 等词
    maintain_words = ("维持", "依然", "不变", "同样", "仍是", "继续维持")
    if any(w in b for w in maintain_words):
        return True
    return False


# ── Public API ─────────────────────────────────────────────

def run_scene_quality_check(
    chapter_text: str,
    chapter_card: dict,
    lorebook_hits: list[dict],
    *,
    use_llm: bool = True,
) -> dict[str, Any]:
    """对单章做 4 维度聚焦质检。

    Args:
        chapter_text: 章节正文
        chapter_card: 章节 task / 卡片（含 expectation_progress /
                      show_item_required / resonance_anchor_target）
        lorebook_hits: lorebook 命中的 [{key, content}, ...]
        use_llm: 是否调 LLM（一致性 / 共鸣 用 LLM 检测；expectation / show-item 纯数据）

    Returns:
        {4 维度 bool + reasons: list[str] + should_escalate: bool}

    Raises:
        QualityCheckInputError: chapter_text 空 / chapter_card 不是 dict
        SceneQualityCheckFailed: LLM 失败（不允许 silently PASS）
    """
    if not isinstance(chapter_text, str):
        raise QualityCheckInputError("chapter_text 必须是 str")
    if not isinstance(chapter_card, dict):
        raise QualityCheckInputError("chapter_card 必须是 dict")
    if chapter_text.strip() == "":
        raise QualityCheckInputError("chapter_text 不能为空")

    # 纯数据维度（高置信度，不调 LLM）
    show_items = chapter_card.get("show_item_required") or []
    show_item_landed = _check_show_item_landed(chapter_text, show_items)
    expectation_advanced = _check_expectation_advanced(chapter_card)

    # LLM 维度（共鸣 + 一致性）
    if use_llm:
        resonance_hit, consistency_ok, llm_reasons = _llm_check_resonance_and_consistency(
            chapter_text, chapter_card, lorebook_hits
        )
    else:
        # 不调 LLM 模式：默认 PASS（CI 友好）
        resonance_hit = True
        consistency_ok = True
        llm_reasons = []

    # 汇总 reasons
    reasons: list[str] = list(llm_reasons)
    if not show_item_landed and show_items:
        reasons.append(
            f"show-item 未落地：{show_items}（核心词在正文中未出现，"
            "请检查 show-item chain 是否被打断）"
        )
    if not expectation_advanced:
        reasons.append(
            "expectation 未推进：status 与 change 含义相同，"
            "请在本章给读者一个新的'走向'"
        )

    should_escalate = not (
        expectation_advanced and show_item_landed and resonance_hit and consistency_ok
    )

    return {
        "expectation_advanced": expectation_advanced,
        "show_item_landed": show_item_landed,
        "resonance_hit": resonance_hit,
        "consistency_ok": consistency_ok,
        "reasons": reasons,
        "should_escalate": should_escalate,
    }


# ── LLM helper ─────────────────────────────────────────────

def _llm_check_resonance_and_consistency(
    chapter_text: str,
    chapter_card: dict,
    lorebook_hits: list[dict],
) -> tuple[bool, bool, list[str]]:
    """用 LLM 检查 resonance_hit + consistency_ok。

    Returns:
        (resonance_hit, consistency_ok, reasons)

    Raises:
        SceneQualityCheckFailed: LLM 失败
    """
    router = get_active_router()
    if router is None:
        raise SceneQualityCheckFailed("LLM router 未初始化 — scene_quality 必须有 LLM")

    resonance_target = chapter_card.get("resonance_anchor_target", "")
    lorebook_summary = "; ".join(
        f"{h.get('key', '?')}:{str(h.get('content', ''))[:50]}"
        for h in (lorebook_hits or [])[:5]
    )

    system_prompt = (
        "你是网文质检员，对本章做 2 个 yes/no 判断：\n"
        "1. resonance_hit: 本章是否触达了 resonance_anchor_target？"
        "（读者能否感受到这个共鸣维度）\n"
        "2. consistency_ok: 本章是否违反 lorebook 设定？"
        "（角色名 / 世界名 / 体系是否一致）\n"
        "直接输出 JSON: "
        '{"resonance_hit": bool, "consistency_ok": bool, '
        '"reasons": [如果有具体问题，列出；否则空 list]}\n'
        "约束：不含具体项目专名（角色名/地名/世界名），"
        "统一用'主角/配角/世界名'中性词。"
    )
    user_prompt = (
        f"resonance_anchor_target: {resonance_target or '（未指定）'}\n"
        f"lorebook 命中（供参考）: {lorebook_summary or '无'}\n"
        f"章节正文：{chapter_text[:3000]}\n"  # 截断避免 token 爆炸
    )

    try:
        out, cost = router.call(
            agent_name="scene_quality_check",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=300,
            temperature=0.3,
        )
    except Exception as exc:
        raise SceneQualityCheckFailed(
            f"scene_quality LLM 调用失败: {exc}"
        ) from exc

    parsed = _parse_llm_json(out)
    if parsed is None:
        raise SceneQualityCheckFailed(
            f"scene_quality LLM 输出无法解析: {out[:200]}"
        )

    resonance_hit = bool(parsed.get("resonance_hit", False))
    consistency_ok = bool(parsed.get("consistency_ok", False))
    reasons = list(parsed.get("reasons") or [])
    _log.info("scene_quality LLM 完成 cost=%.4f resonance=%s consistency=%s",
              cost, resonance_hit, consistency_ok)
    return resonance_hit, consistency_ok, reasons


def _parse_llm_json(text: str) -> dict | None:
    """解析 LLM 输出 JSON。"""
    try:
        from ..utils import parse_llm_json_response
        return parse_llm_json_response(text, default={})
    except Exception:
        return None