"""test_theme_designer_2026_08_17.py

v1.0 Stage B 验证：theme_designer 必须从 concept + genre_profile 生成结构化 theme_spine。

设计动机（来自 docs/drafts/v1-quality-first-design.md § Stage 1b）：
- 用户指导："设计好一个恒久的共性的主题，能够引起读者的共鸣"
- 用户给的范例（服徭役 / 回家 / 征召 / 家的方向）→ theme_statement 应是 1 句
  共性共鸣，expectation_arc 是这条主线如何展开（播种/强化/兑现）
- 没有 theme_spine，30 章后读者共鸣断档——前期工程的"灵魂"

API：用户可在 UI 直接编辑 JSON（v1.0 决策），所以 load/save 必须支持 user-overridden
版本，且保存时不丢失 source 标记（"这是用户改的 / 这是 AI 生成的"）。
"""

from __future__ import annotations

import pytest


# ── 1. schema 必填字段 ─────────────────

def test_theme_spine_required_fields():
    """theme_spine 必须含 3 个核心字段：theme_statement + expectation_arc + resonance_anchors。"""
    from engine.agents.theme_designer import REQUIRED_THEME_FIELDS

    for f in ("theme_statement", "expectation_arc", "resonance_anchors", "source"):
        assert f in REQUIRED_THEME_FIELDS, f"theme_spine 缺字段: {f}"


# ── 2. design_theme 纯数据模式（不调 LLM） ─────────────────

def test_design_theme_runs_without_llm():
    """theme_designer 必须能纯数据生成 theme_spine（CI 友好）。"""
    from engine.agents.theme_designer import design_theme

    theme = design_theme(
        concept="一个服徭役的主角在回家前夕被征召",
        genre_profile={
            "genre": "历史",
            "genre_key": "lishi",
            "tone_preference": "沉郁克制",
            "reader_persona": {"primary": "30-50 男性"},
        },
        key_characters=[{"name": "主角", "role": "服徭役者"}],
        use_llm=False,
    )
    assert "theme_statement" in theme
    assert "expectation_arc" in theme
    assert "resonance_anchors" in theme
    assert theme["source"] in ("llm", "template")


def test_design_theme_template_has_no_project_specific_names():
    """模板生成的 theme_statement 不能含具体项目专名（CLAUDE.md 红线）。
    模板是 seed 概念（'主角 / 回家 / 家方向'），不是具体作品名/角色名。"""
    from engine.agents.theme_designer import design_theme

    theme = design_theme(
        concept="",
        genre_profile={
            "genre": "历史",
            "genre_key": "lishi",
            "tone_preference": "沉郁克制",
            "reader_persona": {"primary": "30-50 男性"},
        },
        key_characters=[],
        use_llm=False,
    )
    payload = str(theme)
    for name in ("陆承", "周芸", "云州", "林渊", "沈岚"):
        assert name not in payload, f"theme_spine 含项目专名 '{name}': {payload}"


# ── 3. expectation_arc 必须有结构 ─────────────────

def test_expectation_arc_has_seed_payoff_twist():
    """expectation_arc 必含 seed_chapter / payoff_chapter / twist_chapter / description。
    这三个数字是 '期待感管理' 的关键节点。"""
    from engine.agents.theme_designer import design_theme

    theme = design_theme(
        concept="归家主题",
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )
    arc = theme["expectation_arc"]
    for f in ("seed_chapter", "payoff_chapter", "twist_chapter", "description"):
        assert f in arc, f"expectation_arc 缺字段: {f}"

    # 三章数字递增（用户给的范例：ch1 seed → ch25 twist → ch80 payoff）
    assert arc["seed_chapter"] < arc["twist_chapter"]
    assert arc["twist_chapter"] < arc["payoff_chapter"]


# ── 4. resonance_anchors 至少 3 条 ─────────────────

def test_resonance_anchors_at_least_three():
    """resonance_anchors 至少 3 条（用户指导：'几个角度的共鸣锚点'）。
    这些是写每章时让 LLM 反复回到的 '读者会共情的几个维度'。"""
    from engine.agents.theme_designer import design_theme

    theme = design_theme(
        concept="归家",
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )
    anchors = theme["resonance_anchors"]
    assert len(anchors) >= 3, f"resonance_anchors 至少 3 条，实际 {len(anchors)}: {anchors}"


def test_resonance_anchors_are_universal_not_specific():
    """resonance_anchors 必须是共性维度（家/忠诚/孤独），不是具体剧情。
    这是 v1.0 的核心区分：'引起读者共鸣' vs '本章要写什么'。"""
    from engine.agents.theme_designer import design_theme

    theme = design_theme(
        concept="归家",
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )
    # 至少一条锚点应含 '家' / '忠' / '孤独' / '选择' / '生死' / '记忆' 等共性词
    keywords = ("家", "忠", "孤", "选择", "生", "死", "记", "等", "守",
                "回家", "乡", "命", "情", "义")
    for anchor in theme["resonance_anchors"]:
        assert any(kw in anchor for kw in keywords), (
            f"resonance_anchor 缺共性关键词: {anchor!r}"
        )


# ── 5. 题材影响 theme（不同 genre 模板不同） ─────────────────

def test_different_genre_produces_different_theme_template():
    """不同 genre 必须用不同的 theme 模板（玄幻重'血脉/逆袭'，历史重'家国/选择'）。
    这是前期工程差异化的核心。"""
    from engine.agents.theme_designer import design_theme

    xuanhuan = design_theme(
        concept="",
        genre_profile={"genre_key": "xuanhuan", "genre": "玄幻"},
        key_characters=[],
        use_llm=False,
    )
    lishi = design_theme(
        concept="",
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )

    # theme_statement 至少一个含关键词差异
    xh_t = xuanhuan["theme_statement"]
    ls_t = lishi["theme_statement"]
    assert xh_t != ls_t, f"玄幻 / 历史 theme 应不同: xh={xh_t}, ls={ls_t}"


# ── 6. save/load 用户编辑版 ─────────────────

def test_save_and_load_user_overridden_theme(tmp_path, monkeypatch):
    """用户可在 UI 编辑后保存（带 source='user'）；load 必须返回 user 版而非 LLM 重生。"""
    from engine.agents import theme_designer as td_mod

    # 重定向 novel_ai_dir 到 tmp_path
    from engine.config import paths as paths_mod
    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    user_theme = {
        "theme_statement": "我手工改的主题",
        "expectation_arc": {
            "seed_chapter": 1, "payoff_chapter": 50, "twist_chapter": 20,
            "description": "我手工写的弧",
        },
        "resonance_anchors": ["家", "忠诚", "孤独"],
        "source": "user",
    }

    td_mod.save_theme("test-novel", user_theme)
    loaded = td_mod.load_theme("test-novel")
    assert loaded is not None
    assert loaded["theme_statement"] == "我手工改的主题"
    assert loaded["source"] == "user"


def test_load_returns_none_when_no_theme(tmp_path, monkeypatch):
    """未生成过 theme_spine → load 返回 None（让上层走 generate 路径）。"""
    from engine.agents import theme_designer as td_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path / "empty"))

    assert td_mod.load_theme("never-set-novel") is None


# ── 7. LLM 模式合并 ─────────────────

def test_design_theme_llm_path_overrides_template_fields(monkeypatch):
    """LLM 模式：用户在 prompt 里提供 candidate → 模板作为 base，LLM 覆盖。
    LLM 失败 → 保留模板 + log.warning。"""
    from engine.agents import theme_designer as td_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            return (
                '{"theme_statement": "LLM 改写的主题", '
                '"expectation_arc": {"seed_chapter": 1, "payoff_chapter": 100, '
                '"twist_chapter": 30, "description": "LLM 弧"}, '
                '"resonance_anchors": ["LLM锚点1", "LLM锚点2", "LLM锚点3"]}',
                0.01,
            )

    monkeypatch.setattr(td_mod, "get_active_router", lambda: _FakeRouter())

    theme = td_mod.design_theme(
        concept="归家",
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=True,
    )
    assert theme["theme_statement"] == "LLM 改写的主题"
    assert theme["source"] == "llm"


def test_design_theme_llm_failure_keeps_template(monkeypatch):
    """LLM 抛异常 → 保留模板 + source='template'（CLAUDE.md '失败要响亮' 但细化不阻断主线）。"""
    from engine.agents import theme_designer as td_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            raise RuntimeError("模拟 LLM 失败")

    monkeypatch.setattr(td_mod, "get_active_router", lambda: _FakeRouter())

    theme = td_mod.design_theme(
        concept="归家",
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=True,
    )
    # 模板字段保留
    assert "theme_statement" in theme
    assert theme["source"] == "template"


# ── 8. 缺失字段校验 ─────────────────

def test_save_theme_rejects_incomplete_payload(tmp_path, monkeypatch):
    """theme_spine 缺必填字段 → save 抛 InvalidThemeError（不能让损坏数据落盘）。"""
    from engine.agents import theme_designer as td_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    bad = {"theme_statement": "缺其它字段"}
    with pytest.raises(td_mod.InvalidThemeError):
        td_mod.save_theme("test-novel", bad)


def test_save_theme_rejects_non_dict():
    """save_theme 必须接 dict（schema 强制）。"""
    from engine.agents.theme_designer import save_theme, InvalidThemeError

    with pytest.raises(InvalidThemeError):
        save_theme("test-novel", "not a dict")  # type: ignore[arg-type]


def test_save_theme_rejects_empty_theme_statement(tmp_path, monkeypatch):
    """theme_statement 不能空串（CLAUDE.md '失败要响亮' — 半成品不落盘）。"""
    from engine.agents.theme_designer import save_theme, InvalidThemeError
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    bad = {
        "theme_statement": "",
        "expectation_arc": {
            "seed_chapter": 1, "payoff_chapter": 50, "twist_chapter": 20,
            "description": "test",
        },
        "resonance_anchors": ["家", "忠", "孤"],
        "source": "user",
    }
    with pytest.raises(InvalidThemeError):
        save_theme("test-novel", bad)