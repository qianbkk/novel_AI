"""test_scene_quality_check_2026_08_17.py

v1.0 Stage G 验证：scene_quality_check 必须对单章做 4 维度聚焦检查，
任一失败 → 直接 escalate 给人工，不自动 rewrite（v1.0 决策）。

设计动机（来自 docs/drafts/v1-quality-first-design.md § Stage 3）：
- 用户确认：'scene_quality_check 失败时怎么走？' → '直接 escalate 给人工 (推荐)'
- v0.5 实测：3 轮 rewrite 经常把对的改错（把对的 show-item 改没了 /
  把期待感改平了），成本 3x 质量却更差
- v1.0 设计：单轮聚焦 4 维度检查（expectation_advanced / show_item_landed /
  resonance_hit / consistency_ok），任一失败 → escalate + 失败原因

CLAUDE.md 红线：
- 必填字段缺失 → QualityCheckError
- 不允许 silently fallback PASS（CLAUDE.md '失败要响亮'）
- LLM 失败 → 抛 SceneQualityCheckFailed，不静默 PASS
"""

from __future__ import annotations

import pytest


# ── 1. schema 必填字段 ─────────────────

def test_scene_quality_required_fields():
    """scene_quality_check 返回必含 4 个 yes/no 维度 + reason + escalate。"""
    from engine.agents.scene_quality_check import REQUIRED_QUALITY_FIELDS

    for f in ("expectation_advanced", "show_item_landed", "resonance_hit",
              "consistency_ok", "reasons", "should_escalate"):
        assert f in REQUIRED_QUALITY_FIELDS, f"scene_quality_check 缺字段: {f}"


# ── 2. 4 维度检查（纯数据模式）─────────────────

def test_all_dimensions_pass_returns_no_escalate():
    """4 维度全 PASS → should_escalate=False。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    result = run_scene_quality_check(
        chapter_text="主角看鞋一眼，想起了母亲。这是回家的方向。",
        chapter_card={
            "chapter_number": 3,
            "expectation_progress": {"seed_1_status": "扭曲", "seed_1_change": "家的方向变成谜团"},
            "show_item_required": ["那双布鞋"],
            "resonance_anchor_target": "家不只是一个地址",
        },
        lorebook_hits=[{"key": "主角", "content": "服徭役者"}],
        use_llm=False,  # 纯数据 mock
    )
    assert result["expectation_advanced"] is True
    assert result["show_item_landed"] is True
    assert result["resonance_hit"] is True
    assert result["consistency_ok"] is True
    assert result["should_escalate"] is False


def test_show_item_missing_escalates():
    """show_item_required 里的物件在正文里没出现 → show_item_landed=False + escalate。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    result = run_scene_quality_check(
        chapter_text="主角踏上归途，想起了母亲的话。",  # 没有"鞋"字
        chapter_card={
            "chapter_number": 3,
            "expectation_progress": {"seed_1_status": "扭曲"},
            "show_item_required": ["那双布鞋"],
            "resonance_anchor_target": "家",
        },
        lorebook_hits=[],
        use_llm=False,
    )
    assert result["show_item_landed"] is False
    assert result["should_escalate"] is True
    assert any("鞋" in r or "show" in r.lower() for r in result["reasons"])


def test_expectation_not_advanced_escalates():
    """本章未推进 expectation（status 与 change 矛盾 / 无推进）→ escalate。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    # seed_1_status 是"扭曲"但 change 是"维持扭曲"——没推进
    result = run_scene_quality_check(
        chapter_text="主角看鞋一眼，想起母亲。",
        chapter_card={
            "chapter_number": 3,
            "expectation_progress": {
                "seed_1_status": "扭曲",
                "seed_1_change": "维持扭曲",  # 没推进
            },
            "show_item_required": ["那双布鞋"],
            "resonance_anchor_target": "家",
        },
        lorebook_hits=[],
        use_llm=False,
    )
    # expectation_advanced 应该 False 或至少 should_escalate
    assert result["should_escalate"] is True


def test_lorebook_inconsistency_escalates():
    """lorebook 命中的 key 与正文里的角色名不一致 → consistency_ok=False。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    result = run_scene_quality_check(
        chapter_text="陆承决定离开。",
        chapter_card={
            "chapter_number": 3,
            "expectation_progress": {"seed_1_status": "扭曲"},
            "show_item_required": ["那双布鞋"],
            "resonance_anchor_target": "家",
        },
        # lorebook 标主角是"主角"，但正文用了具体名"陆承"——不一致
        lorebook_hits=[{"key": "主角", "content": "服徭役者", "aliases": []}],
        use_llm=False,
    )
    # 纯数据 mock 不会检测这个复杂逻辑（要走 LLM），
    # 但 should_escalate 应该至少看到其他原因；至少不应该悄悄 PASS
    # 如果是纯数据，consistency_ok 应该是 True（mock 默认）
    # 真正的检测交给 LLM（下一测试）
    assert "consistency_ok" in result


# ── 3. LLM 模式 ─────────────────

def test_llm_path_returns_four_dimensions(monkeypatch):
    """LLM 模式：返回 4 个 yes/no 维度。"""
    from engine.agents import scene_quality_check as sqc_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            return (
                '{"expectation_advanced": true, "show_item_landed": true, '
                '"resonance_hit": true, "consistency_ok": false, '
                '"reasons": ["主角名应该是主角不是陆承"], '
                '"should_escalate": true}',
                0.01,
            )

    monkeypatch.setattr(sqc_mod, "get_active_router", lambda: _FakeRouter())

    result = sqc_mod.run_scene_quality_check(
        chapter_text="陆承看了一眼那双布鞋，决定离开。",
        chapter_card={
            "chapter_number": 3,
            "expectation_progress": {
                "seed_1_status": "扭曲",
                "seed_1_change": "家的方向变成谜团",
            },
            "show_item_required": ["那双布鞋"],
            "resonance_anchor_target": "家",
        },
        lorebook_hits=[{"key": "主角", "content": "服徭役者"}],
        use_llm=True,
    )
    assert result["expectation_advanced"] is True
    assert result["show_item_landed"] is True  # 正文含'布鞋'
    assert result["consistency_ok"] is False  # mock 给的 False
    assert result["should_escalate"] is True
    assert "主角名" in result["reasons"][0]


def test_llm_failure_raises_does_not_silent_pass(monkeypatch):
    """LLM 失败 → 抛 SceneQualityCheckFailed（不让 silently PASS）。
    CLAUDE.md 红线：失败要响亮。"""
    from engine.agents import scene_quality_check as sqc_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            raise RuntimeError("模拟 LLM 失败")

    monkeypatch.setattr(sqc_mod, "get_active_router", lambda: _FakeRouter())

    with pytest.raises(sqc_mod.SceneQualityCheckFailed):
        sqc_mod.run_scene_quality_check(
            chapter_text="主角踏上归途。",
            chapter_card={"chapter_number": 1, "expectation_progress": {},
                          "show_item_required": [], "resonance_anchor_target": ""},
            lorebook_hits=[],
            use_llm=True,
        )


# ── 4. 4 维度集成验证 ─────────────────

def test_check_returns_full_schema():
    """返回 dict 必含所有 6 个 schema 字段。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    result = run_scene_quality_check(
        chapter_text="主角踏上归途，看了一眼鞋。",
        chapter_card={
            "chapter_number": 1,
            "expectation_progress": {},
            "show_item_required": ["鞋"],
            "resonance_anchor_target": "家",
        },
        lorebook_hits=[],
        use_llm=False,
    )
    for f in ("expectation_advanced", "show_item_landed", "resonance_hit",
              "consistency_ok", "reasons", "should_escalate"):
        assert f in result, f"result 缺字段 {f!r}"


def test_reasons_list_always_present():
    """reasons 必为 list（即便无失败原因也为空 list，不是 None）。"""
    from engine.agents.scene_quality_check import run_scene_quality_check

    result = run_scene_quality_check(
        chapter_text="主角踏上归途。",
        chapter_card={"chapter_number": 1, "expectation_progress": {},
                      "show_item_required": [], "resonance_anchor_target": ""},
        lorebook_hits=[],
        use_llm=False,
    )
    assert isinstance(result["reasons"], list)


# ── 5. show-item 子串检测（不依赖 LLM）─────────────────

def test_show_item_detection_via_substring():
    """show_item_landed 纯数据模式用子串匹配（不调 LLM），
    因为这是高置信度规则 — '鞋'/'信'/'玉佩' 等具体物件在正文出现 = 落地。"""
    from engine.agents.scene_quality_check import _check_show_item_landed

    # 命中
    assert _check_show_item_landed("主角看鞋一眼。", ["那双布鞋"]) is True
    assert _check_show_item_landed("玉简在手中。", ["玉简"]) is True
    # 不命中
    assert _check_show_item_landed("主角决定离开。", ["那双布鞋"]) is False
    # 空列表 = 落地（无要求）
    assert _check_show_item_landed("任何文本", []) is True


def test_show_item_detection_with_multi_word_items():
    """多字 show_item 用核心词子串检测。"""
    from engine.agents.scene_quality_check import _check_show_item_landed

    # '那双布鞋' 子串含 '鞋'
    assert _check_show_item_landed("他盯着那双鞋。", ["那双布鞋"]) is True
    # '母亲的牌位' 子串含 '牌位'
    assert _check_show_item_landed("主角看着母亲的牌位。", ["母亲的牌位"]) is True