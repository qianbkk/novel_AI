"""test_macro_spine_2026_08_17.py

v1.0 Stage E 验证：macro_spine（全书宏观弧结构）必须从 theme + opening 生成，
且 arc 边界必须满足 seed < twist < payoff 的预期感管理。

设计动机（来自 docs/drafts/v1-quality-first-design.md § Stage 2a）：
- 用户指导：'我觉得设计好大纲的很重要的'
- 现有 outline.py 已经有 16+ 字段的 chapter schema，但缺'全书宏观弧'层
- 没有宏观弧，10+ 章后读者共鸣断档（ch20 应该是什么？）
- macro_spine 是 per-chapter 的'上游'约束：每章的 arc_id 必须落到某 arc 范围

CLAUDE.md 红线：
- 必填字段缺失 → InvalidMacroSpineError（不让半成品落盘）
- arc 边界必须单调递增
- arc 数量合理（≥2, ≤10）
- 不含具体项目专名
"""

from __future__ import annotations

import pytest


# ── 1. schema 必填字段 ─────────────────

def test_macro_spine_required_fields():
    """macro_spine 必须含 arcs + total_chapters + source。"""
    from engine.agents.macro_spine import REQUIRED_MACRO_FIELDS

    for f in ("arcs", "total_chapters", "source"):
        assert f in REQUIRED_MACRO_FIELDS, f"macro_spine 缺字段: {f}"


# ── 2. 设计 macro_spine 纯数据模式 ─────────────────

def test_design_macro_spine_runs_without_llm():
    """macro_spine 必须能纯模板生成（CI 友好）。"""
    from engine.agents.macro_spine import design_macro_spine

    spine = design_macro_spine(
        theme_spine={
            "theme_statement": "归家",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80,
                                 "twist_chapter": 25, "description": "归家主题"},
            "resonance_anchors": ["家", "忠诚"],
        },
        opening_design={
            "chapter_1_anchor": {"expectation_seed": "要回家了"},
        },
        total_chapters=80,
        use_llm=False,
    )
    assert "arcs" in spine
    assert spine["total_chapters"] == 80
    assert len(spine["arcs"]) >= 2, "至少 2 个 arc（开局 + 主体）"


def test_design_macro_spine_seed_at_chapter_one():
    """第一个 arc 必须从 chapter 1 开始。"""
    from engine.agents.macro_spine import design_macro_spine

    spine = design_macro_spine(
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["x"],
        },
        opening_design={},
        total_chapters=50,
        use_llm=False,
    )
    assert spine["arcs"][0]["start_chapter"] == 1


def test_design_macro_spine_arc_boundaries_continuous():
    """arc 边界必须连续：arc[i].end_chapter + 1 == arc[i+1].start_chapter。
    防止章节范围重叠或留缝。"""
    from engine.agents.macro_spine import design_macro_spine

    spine = design_macro_spine(
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80,
                                 "twist_chapter": 25, "description": "x"},
            "resonance_anchors": ["x"],
        },
        opening_design={},
        total_chapters=80,
        use_llm=False,
    )
    arcs = spine["arcs"]
    for i in range(len(arcs) - 1):
        assert arcs[i]["end_chapter"] + 1 == arcs[i + 1]["start_chapter"], (
            f"arc 边界不连续: arc{i} end={arcs[i]['end_chapter']}, "
            f"arc{i+1} start={arcs[i+1]['start_chapter']}"
        )


def test_design_macro_spine_last_arc_ends_at_total():
    """最后一个 arc 必须到 total_chapters 结束。"""
    from engine.agents.macro_spine import design_macro_spine

    spine = design_macro_spine(
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["x"],
        },
        opening_design={},
        total_chapters=50,
        use_llm=False,
    )
    assert spine["arcs"][-1]["end_chapter"] == 50


def test_design_macro_spine_twist_arc_aligned():
    """theme_spine.expectation_arc.twist_chapter 必须落在某个 arc 范围内。
    这是'期待感管理'从 theme 落地到 macro 的硬约束。"""
    from engine.agents.macro_spine import design_macro_spine

    spine = design_macro_spine(
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80,
                                 "twist_chapter": 25, "description": "x"},
            "resonance_anchors": ["x"],
        },
        opening_design={},
        total_chapters=80,
        use_llm=False,
    )
    twist = 25
    in_arc = any(a["start_chapter"] <= twist <= a["end_chapter"] for a in spine["arcs"])
    assert in_arc, f"twist_chapter={twist} 不在任何 arc 范围内: {spine['arcs']}"


# ── 3. 每个 arc 必填字段 ─────────────────

def test_each_arc_has_required_fields():
    """每个 arc 必含: arc_id / name / start_chapter / end_chapter /
    theme_focus / main_conflict / expectation_progress / tone。"""
    from engine.agents.macro_spine import design_macro_spine

    spine = design_macro_spine(
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["x"],
        },
        opening_design={},
        total_chapters=50,
        use_llm=False,
    )
    for a in spine["arcs"]:
        for f in ("arc_id", "name", "start_chapter", "end_chapter",
                  "theme_focus", "main_conflict", "expectation_progress", "tone"):
            assert f in a, f"arc 缺字段 {f!r}: {a}"


# ── 4. arc 数量合理 ─────────────────

def test_arc_count_reasonable():
    """arc 数量应在 [2, 10] 之间。少 = 单调，多 = 节奏碎。"""
    from engine.agents.macro_spine import design_macro_spine

    for total in (20, 50, 80, 120):
        spine = design_macro_spine(
            theme_spine={
                "theme_statement": "x",
                "expectation_arc": {"seed_chapter": 1, "payoff_chapter": total,
                                     "twist_chapter": total // 4, "description": "x"},
                "resonance_anchors": ["x"],
            },
            opening_design={},
            total_chapters=total,
            use_llm=False,
        )
        n = len(spine["arcs"])
        assert 2 <= n <= 10, f"total={total} → {n} arcs (不在 2-10 范围)"


# ── 5. save/load ─────────────────

def test_save_and_load_macro_spine(tmp_path, monkeypatch):
    from engine.agents import macro_spine as ms_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    user_spine = {
        "arcs": [
            {
                "arc_id": 1, "name": "归途", "start_chapter": 1, "end_chapter": 30,
                "theme_focus": "回家期待", "main_conflict": "服徭役 vs 回家",
                "expectation_progress": "seed-1 强化", "tone": "克制",
            },
            {
                "arc_id": 2, "name": "征途", "start_chapter": 31, "end_chapter": 80,
                "theme_focus": "归途考验", "main_conflict": "征召 vs 回家",
                "expectation_progress": "twist 推进", "tone": "震荡",
            },
        ],
        "total_chapters": 80,
        "source": "user",
    }
    ms_mod.save_macro_spine("test-novel", user_spine)
    loaded = ms_mod.load_macro_spine("test-novel")
    assert loaded is not None
    assert loaded["arcs"][0]["name"] == "归途"
    assert loaded["source"] == "user"


def test_load_returns_none_when_no_macro_spine(tmp_path, monkeypatch):
    from engine.agents import macro_spine as ms_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path / "empty"))

    assert ms_mod.load_macro_spine("never-set") is None


# ── 6. InvalidMacroSpineError 校验 ─────────────────

def test_save_rejects_arc_overlap():
    """arc 边界不能重叠 → InvalidMacroSpineError。"""
    from engine.agents.macro_spine import save_macro_spine, InvalidMacroSpineError

    bad = {
        "arcs": [
            {"arc_id": 1, "name": "a", "start_chapter": 1, "end_chapter": 15,
             "theme_focus": "x", "main_conflict": "x",
             "expectation_progress": "x", "tone": "x"},
            {"arc_id": 2, "name": "b", "start_chapter": 10, "end_chapter": 30,  # 重叠 10-15
             "theme_focus": "x", "main_conflict": "x",
             "expectation_progress": "x", "tone": "x"},
        ],
        "total_chapters": 80,
        "source": "user",
    }
    with pytest.raises(InvalidMacroSpineError):
        save_macro_spine("test-novel", bad)


def test_save_rejects_too_few_arc():
    """arc 数量 < 2 → InvalidMacroSpineError。"""
    from engine.agents.macro_spine import save_macro_spine, InvalidMacroSpineError

    bad = {
        "arcs": [
            {"arc_id": 1, "name": "a", "start_chapter": 1, "end_chapter": 80,
             "theme_focus": "x", "main_conflict": "x",
             "expectation_progress": "x", "tone": "x"},
        ],
        "total_chapters": 80,
        "source": "user",
    }
    with pytest.raises(InvalidMacroSpineError):
        save_macro_spine("test-novel", bad)


def test_save_rejects_non_dict():
    from engine.agents.macro_spine import save_macro_spine, InvalidMacroSpineError

    with pytest.raises(InvalidMacroSpineError):
        save_macro_spine("test-novel", "not a dict")  # type: ignore[arg-type]


# ── 7. LLM 模式 ─────────────────

def test_design_macro_spine_llm_failure_keeps_template(monkeypatch):
    """LLM 失败 → 保留模板 + log.warning。"""
    from engine.agents import macro_spine as ms_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            raise RuntimeError("模拟 LLM 失败")

    monkeypatch.setattr(ms_mod, "get_active_router", lambda: _FakeRouter())

    spine = ms_mod.design_macro_spine(
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["x"],
        },
        opening_design={},
        total_chapters=50,
        use_llm=True,
    )
    assert spine["arcs"]
    assert spine["source"] == "template"


# ── 8. 不含项目专名 ─────────────────

def test_macro_spine_template_no_project_specific_names():
    from engine.agents.macro_spine import design_macro_spine

    spine = design_macro_spine(
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80,
                                 "twist_chapter": 25, "description": "x"},
            "resonance_anchors": ["x"],
        },
        opening_design={},
        total_chapters=80,
        use_llm=False,
    )
    payload = str(spine)
    for name in ("陆承", "周芸", "云州", "林渊", "沈岚"):
        assert name not in payload, f"macro_spine 含项目专名 '{name}'"