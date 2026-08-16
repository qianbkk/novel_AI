"""test_effective_pass_score_2026_08_17.py

修复验证：`_effective_pass_score(task)` 引用了 `PASS_SCORE_GOLDEN` 与
`GOLDEN_CHAPTER_COUNT` 两个常量（orchestrator.py:500），但全仓 grep 无
任何赋值。这是一个 NameError —— 任何非 draft 章调用都会崩溃，整本长篇
100% 进 escalate。

本文件锁死以下行为（_effective_pass_score 的契约）：
  1. 必须存在 `PASS_SCORE_GOLDEN` 与 `GOLDEN_CHAPTER_COUNT` 常量
  2. PASS_SCORE_GOLDEN 必须严格大于 PASS_SCORE（黄金章节门槛更高）
  3. GOLDEN_CHAPTER_COUNT 必须是正整数（黄金章节数量有界）
  4. task=None / draft 模式 → 返回 PASS_SCORE（不走黄金门槛）
  5. 黄金章（chapter_number ≤ GOLDEN_CHAPTER_COUNT）非 draft → 返回 PASS_SCORE_GOLDEN
  6. 普通章（chapter_number > GOLDEN_CHAPTER_COUNT）非 draft → 返回 PASS_SCORE
  7. route_after_pipeline 实际调用 _effective_pass_score 不再 NameError：
     ch=1 + score=PASS_SCORE → rewrite（不达黄金门槛）
     ch=1 + score=PASS_SCORE_GOLDEN → save（达黄金门槛）
     ch=99 + score=PASS_SCORE → save（普通章 PASS）

实现：orchestrator.py:67 附近加常量。
"""

from __future__ import annotations

import pytest


# ── 1. 常量存在性与基本形态 ─────────────────────────

def test_pass_score_golden_constant_exists():
    """orchestrator 模块必须定义 PASS_SCORE_GOLDEN（黄金章节更高门槛）。"""
    from engine import orchestrator as orch

    assert hasattr(orch, "PASS_SCORE_GOLDEN"), (
        "orchestrator 必须定义 PASS_SCORE_GOLDEN 常量，"
        "否则 _effective_pass_score 对黄金章 NameError"
    )


def test_golden_chapter_count_constant_exists():
    """orchestrator 模块必须定义 GOLDEN_CHAPTER_COUNT（黄金章节数量）。"""
    from engine import orchestrator as orch

    assert hasattr(orch, "GOLDEN_CHAPTER_COUNT"), (
        "orchestrator 必须定义 GOLDEN_CHAPTER_COUNT 常量，"
        "否则 _effective_pass_score 对黄金章 NameError"
    )


def test_pass_score_golden_strictly_above_pass_score():
    """黄金门槛必须严格大于标准门槛 —— 否则"黄金章节"名存实亡。"""
    from engine import orchestrator as orch

    assert orch.PASS_SCORE_GOLDEN > orch.PASS_SCORE, (
        f"PASS_SCORE_GOLDEN ({orch.PASS_SCORE_GOLDEN}) 必须 > "
        f"PASS_SCORE ({orch.PASS_SCORE})，否则黄金章节门槛无意义"
    )


def test_golden_chapter_count_is_positive_int():
    """黄金章节数量必须是有界正整数：不能 0（全跑黄金门槛 → 永远 escalate），
    也不能负数。"""
    from engine import orchestrator as orch

    assert isinstance(orch.GOLDEN_CHAPTER_COUNT, int)
    assert orch.GOLDEN_CHAPTER_COUNT > 0, (
        f"GOLDEN_CHAPTER_COUNT 必须 > 0，实际: {orch.GOLDEN_CHAPTER_COUNT}"
    )
    # 不应该大得离谱（合理上限：30 章以内）
    assert orch.GOLDEN_CHAPTER_COUNT <= 30, (
        f"GOLDEN_CHAPTER_COUNT ({orch.GOLDEN_CHAPTER_COUNT}) 过大，"
        "黄金门槛覆盖太多章节会显著提高长篇 escalate 率"
    )


def test_pass_score_golden_within_reasonable_range():
    """黄金门槛应在 0-10 评分制的合理区间（不应 ≤ 0 或 > 10）。"""
    from engine import orchestrator as orch

    assert 0 < orch.PASS_SCORE_GOLDEN <= 10, (
        f"PASS_SCORE_GOLDEN ({orch.PASS_SCORE_GOLDEN}) 应在 (0, 10] 区间"
    )


# ── 2. _effective_pass_score 行为契约 ─────────────────────────

def test_effective_pass_score_with_none_task():
    """task=None → 返回 PASS_SCORE（默认安全值）。"""
    from engine import orchestrator as orch

    assert orch._effective_pass_score(None) == orch.PASS_SCORE


def test_effective_pass_score_draft_mode_returns_pass_score():
    """draft 模式不适用黄金门槛：chapter=1 仍是黄金章范围但 draft 返回标准。"""
    from engine import orchestrator as orch

    task = {"chapter_number": 1, "audit_mode": "draft"}
    assert orch._effective_pass_score(task) == orch.PASS_SCORE


def test_effective_pass_score_golden_chapter_returns_golden():
    """chapter_number ≤ GOLDEN_CHAPTER_COUNT 且非 draft → 返回 PASS_SCORE_GOLDEN。"""
    from engine import orchestrator as orch

    for ch in (1, orch.GOLDEN_CHAPTER_COUNT):
        task = {"chapter_number": ch, "audit_mode": "full"}
        assert orch._effective_pass_score(task) == orch.PASS_SCORE_GOLDEN, (
            f"第{ch}章非 draft 应走黄金门槛，实际未走"
        )


def test_effective_pass_score_normal_chapter_returns_pass_score():
    """chapter_number > GOLDEN_CHAPTER_COUNT 且非 draft → 返回 PASS_SCORE。"""
    from engine import orchestrator as orch

    task = {
        "chapter_number": orch.GOLDEN_CHAPTER_COUNT + 1,
        "audit_mode": "full",
    }
    assert orch._effective_pass_score(task) == orch.PASS_SCORE


def test_effective_pass_score_missing_chapter_number():
    """chapter_number 缺失 → 默认 999（远超黄金），走标准 PASS_SCORE。"""
    from engine import orchestrator as orch

    task = {"audit_mode": "full"}
    assert orch._effective_pass_score(task) == orch.PASS_SCORE


# ── 3. 路由函数端到端验证（修好之后能跑通） ─────────────────────────

def test_route_after_pipeline_golden_chapter_below_golden_escalates():
    """黄金章 score 介于 PASS_SCORE 与 PASS_SCORE_GOLDEN 之间 → rewrite（非 save）。
    修复前此调用会 NameError。"""
    from engine import orchestrator as orch

    ch = 1  # 黄金章
    # 选 PASS_SCORE 与 PASS_SCORE_GOLDEN 中间的分数（确保 ≥ PASS_SCORE 但 < PASS_SCORE_GOLDEN）
    middle = (orch.PASS_SCORE + orch.PASS_SCORE_GOLDEN) / 2
    state = {
        "current_phase": "writing",
        "current_task": {
            "chapter_number": ch,
            "audit_mode": "full",
            "_checker_result": {"score": middle},
        },
        "rewrite_count_current": 0,
    }
    decision = orch.route_after_pipeline(state)
    assert decision == "rewrite", (
        f"黄金章 score={middle}（标准 < 中间分 < 黄金）应 rewrite，"
        f"实际: {decision!r}"
    )


def test_route_after_pipeline_golden_chapter_at_golden_saves():
    """黄金章 score == PASS_SCORE_GOLDEN → save。"""
    from engine import orchestrator as orch

    state = {
        "current_phase": "writing",
        "current_task": {
            "chapter_number": 1,
            "audit_mode": "full",
            "_checker_result": {"score": orch.PASS_SCORE_GOLDEN},
        },
        "rewrite_count_current": 0,
    }
    assert orch.route_after_pipeline(state) == "save"


def test_route_after_pipeline_normal_chapter_at_pass_score_saves():
    """普通章 score == PASS_SCORE → save（标准门槛未被黄金门槛污染）。"""
    from engine import orchestrator as orch

    state = {
        "current_phase": "writing",
        "current_task": {
            "chapter_number": 100,
            "audit_mode": "full",
            "_checker_result": {"score": orch.PASS_SCORE},
        },
        "rewrite_count_current": 0,
    }
    assert orch.route_after_pipeline(state) == "save"