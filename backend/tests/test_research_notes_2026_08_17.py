"""test_research_notes_2026_08_17.py

v1.0 Stage D 验证：research_notes 必须按 genre_profile.research_strength 三档分流
（strong/medium/weak），且可按章 query。

设计动机（来自 docs/drafts/v1-quality-first-design.md § Stage 1d）：
- 用户指导：'资料助手，有查资料/记录本章资料/个人便笺/创作记录，
  有些情节可能需要结合查询到底资料使逻辑合理或者历史类使得情节合理等，
  至于纯玄幻/科幻之类的这一块就弱一点'
- 历史类（strong）需要严谨（朝代/地理/职官/物价）
- 玄幻/科幻（medium）需要体系一致性约束
- 都市（weak）现实题材默认跳过

CLAUDE.md 红线：
- 缺字段 → InvalidResearchNotesError（不让半成品落盘）
- LLM 失败 → 保留基础事实 + log.warning
- 不含具体项目专名
"""

from __future__ import annotations

import pytest


# ── 1. schema 必填字段 ─────────────────

def test_research_notes_required_fields():
    """research_notes 必须含 research_strength + baseline + per_chapter_notes + source。"""
    from engine.agents.research_notes import REQUIRED_NOTES_FIELDS

    for f in ("research_strength", "baseline", "per_chapter_notes", "source"):
        assert f in REQUIRED_NOTES_FIELDS, f"research_notes 缺字段: {f}"


# ── 2. 三档分流：strong / medium / weak ─────────────────

def test_strength_strong_initializes_full_baseline():
    """strong（历史）必须初始化完整 baseline：朝代/地理/职官/物价/服饰。
    这是用户指导'历史文可能需要结合查询的资料使逻辑合理'的具体落地。"""
    from engine.agents.research_notes import init_research_notes

    notes = init_research_notes(
        genre_profile={"research_strength": "strong", "genre": "历史"},
        concept="架空王朝末年",
        use_llm=False,
    )
    assert notes["research_strength"] == "strong"
    # baseline 必须含 5 个维度
    baseline = notes["baseline"]
    for d in ("朝代", "地理", "职官", "物价", "服饰"):
        assert d in baseline, f"strong baseline 缺 {d} 维度"


def test_strength_medium_initializes_system_consistency():
    """medium（玄幻/科幻）必须初始化体系一致性约束。"""
    from engine.agents.research_notes import init_research_notes

    for genre in ("玄幻", "仙侠", "科幻"):
        notes = init_research_notes(
            genre_profile={"research_strength": "medium", "genre": genre},
            concept="力量体系升级",
            use_llm=False,
        )
        assert notes["research_strength"] == "medium"
        assert "system_consistency" in notes["baseline"]


def test_strength_weak_minimal_baseline():
    """weak（都市）baseline 为空（现实题材不需要查资料）。"""
    from engine.agents.research_notes import init_research_notes

    notes = init_research_notes(
        genre_profile={"research_strength": "weak", "genre": "都市"},
        concept="职场翻盘",
        use_llm=False,
    )
    assert notes["research_strength"] == "weak"
    # baseline 是 dict（可空），但必须有该字段
    assert "baseline" in notes
    assert isinstance(notes["baseline"], dict)


# ── 3. save/load 用户编辑版 ─────────────────

def test_save_and_load_user_overridden_notes(tmp_path, monkeypatch):
    """用户可在 UI 编辑 per_chapter_notes（个人便笺 / 创作记录）。"""
    from engine.agents import research_notes as rn_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    notes = {
        "research_strength": "strong",
        "baseline": {"朝代": "架空王朝末年", "地理": "江淮流域",
                      "职官": "县令/驿丞", "物价": "米 5 文一斗",
                      "服饰": "粗布麻衣"},
        "per_chapter_notes": {
            "1": "本章关键资料 = 服徭役期限 3 年（明制）",
            "2": "本章关键资料 = 驿站 30 里一置",
        },
        "source": "user",
    }
    rn_mod.save_notes("test-novel", notes)
    loaded = rn_mod.load_notes("test-novel")
    assert loaded is not None
    assert loaded["source"] == "user"
    assert loaded["per_chapter_notes"]["1"] == "本章关键资料 = 服徭役期限 3 年（明制）"


def test_load_returns_none_when_no_notes(tmp_path, monkeypatch):
    """未生成 → None。"""
    from engine.agents import research_notes as rn_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path / "empty"))

    assert rn_mod.load_notes("never-set") is None


# ── 4. InvalidResearchNotesError 校验 ─────────────────

def test_save_rejects_incomplete_payload(tmp_path, monkeypatch):
    """缺字段 → InvalidResearchNotesError。"""
    from engine.agents import research_notes as rn_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    bad = {"research_strength": "strong", "baseline": {}}
    with pytest.raises(rn_mod.InvalidResearchNotesError):
        rn_mod.save_notes("test-novel", bad)


def test_save_rejects_non_dict():
    """save_notes 必须接 dict。"""
    from engine.agents.research_notes import save_notes, InvalidResearchNotesError

    with pytest.raises(InvalidResearchNotesError):
        save_notes("test-novel", "not a dict")  # type: ignore[arg-type]


def test_save_rejects_unknown_strength():
    """未知 research_strength → InvalidResearchNotesError（不让 silently 落盘）。"""
    from engine.agents.research_notes import save_notes, InvalidResearchNotesError
    from engine.config import paths as paths_mod
    from pathlib import Path

    notes = {
        "research_strength": "ultra_strong",  # 不在 strong/medium/weak 内
        "baseline": {},
        "per_chapter_notes": {},
        "source": "user",
    }
    with pytest.raises(InvalidResearchNotesError):
        save_notes("test-novel", notes)


# ── 5. query API（按章查询）─────────────────

def test_query_chapter_returns_notes_for_chapter(tmp_path, monkeypatch):
    """query_research_notes(chapter=2) 必须返回该章的所有 notes。
    这是 writer prompt 写每章前查资料的核心 API。"""
    from engine.agents import research_notes as rn_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    notes = {
        "research_strength": "strong",
        "baseline": {"朝代": "架空", "物价": "米 5 文"},
        "per_chapter_notes": {
            "1": "ch1 资料",
            "2": "ch2 资料：驿站 30 里",
            "3": "ch3 资料：征兵规则",
        },
        "source": "user",
    }
    rn_mod.save_notes("test-novel", notes)

    # 按章查询
    result = rn_mod.query_notes("test-novel", chapter=2)
    assert "ch2 资料" in result
    assert "驿站 30 里" in result
    # 不应返回其他章
    assert "ch1 资料" not in result
    assert "ch3 资料" not in result


def test_query_includes_baseline_for_strong(tmp_path, monkeypatch):
    """strong 题材查询必须含 baseline 关键事实（朝代/物价），便于 writer 检索。"""
    from engine.agents import research_notes as rn_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    notes = {
        "research_strength": "strong",
        "baseline": {"朝代": "架空王朝末年", "物价": "米 5 文一斗"},
        "per_chapter_notes": {"1": "ch1"},
        "source": "user",
    }
    rn_mod.save_notes("test-novel", notes)

    result = rn_mod.query_notes("test-novel", chapter=1)
    assert "米 5 文" in result
    assert "架空王朝末年" in result


def test_query_unknown_chapter_returns_baseline_only(tmp_path, monkeypatch):
    """查不存在的章节 → 返回 baseline（writer 仍能拿到核心事实）。"""
    from engine.agents import research_notes as rn_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    notes = {
        "research_strength": "strong",
        "baseline": {"朝代": "架空", "物价": "米 5 文"},
        "per_chapter_notes": {"1": "ch1 资料"},
        "source": "user",
    }
    rn_mod.save_notes("test-novel", notes)

    result = rn_mod.query_notes("test-novel", chapter=99)
    assert "米 5 文" in result
    assert "ch1 资料" not in result


def test_query_no_notes_returns_empty_string(tmp_path, monkeypatch):
    """未生成 research_notes → query 返回空串（writer 拿不到资料不报错）。"""
    from engine.agents import research_notes as rn_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path / "empty"))

    assert rn_mod.query_notes("never-set", chapter=1) == ""


# ── 6. LLM 模式合并 ─────────────────

def test_init_research_notes_llm_failure_keeps_baseline(monkeypatch):
    """LLM 抛异常 → 保留 baseline + log.warning（CLAUDE.md '失败要响亮' 但细化不阻断）。"""
    from engine.agents import research_notes as rn_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            raise RuntimeError("模拟 LLM 失败")

    monkeypatch.setattr(rn_mod, "get_active_router", lambda: _FakeRouter())

    notes = rn_mod.init_research_notes(
        genre_profile={"research_strength": "strong", "genre": "历史"},
        concept="",
        use_llm=True,
    )
    assert notes["research_strength"] == "strong"
    # baseline 5 维度保留（模板 seed）
    for d in ("朝代", "地理", "职官", "物价", "服饰"):
        assert d in notes["baseline"]


def test_init_research_notes_llm_path_refines_baseline(monkeypatch):
    """LLM 模式：模板 base，LLM 在基础上补充细节。"""
    from engine.agents import research_notes as rn_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            # LLM 直接输出 inner keys（与 system_prompt 一致："直接输出 JSON: {朝代:..., 地理:...}")
            return (
                '{"朝代": "架空王朝末年（具体由 LLM 改写）", '
                '"地理": "江淮", "职官": "县令/驿丞", "物价": "米 5 文", '
                '"服饰": "粗布麻衣", "extra_detail": "LLM 加的细节"}',
                0.01,
            )

    monkeypatch.setattr(rn_mod, "get_active_router", lambda: _FakeRouter())

    notes = rn_mod.init_research_notes(
        genre_profile={"research_strength": "strong", "genre": "历史"},
        concept="架空",
        use_llm=True,
    )
    assert "具体由 LLM 改写" in notes["baseline"]["朝代"]
    assert notes["baseline"].get("extra_detail") == "LLM 加的细节"
    assert notes["source"] == "llm"


# ── 7. 不含项目专名 ─────────────────

def test_baseline_template_no_project_specific_names():
    """baseline 模板不应含具体项目专名（CLAUDE.md 红线）。"""
    from engine.agents.research_notes import init_research_notes

    notes = init_research_notes(
        genre_profile={"research_strength": "strong", "genre": "历史"},
        concept="",
        use_llm=False,
    )
    payload = str(notes)
    for name in ("陆承", "周芸", "云州", "林渊", "沈岚", "归航"):
        assert name not in payload, f"research_notes baseline 含项目专名 '{name}'"