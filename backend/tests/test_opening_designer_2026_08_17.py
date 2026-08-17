"""test_opening_designer_2026_08_17.py

v1.0 Stage C 验证：opening_designer 必须从 theme_spine + setting_package 生成
黄金三章结构化设计（ch1_anchor / ch2_question / ch3_escalation）。

设计动机（来自 docs/drafts/v1-quality-first-design.md § Stage 1c）：
- 用户指导："前期黄金三章，设计好切入点"
- 用户给的范例（服徭役 → 回家 → 征召 → 家方向）是经典黄金三章模板：
  ch1 锚定期望 → ch2 建立问题 → ch3 翻转/升级
- 没有 opening_design，故事前 3 章基本靠运气，读者流失率最高

每个 chapter 必有结构化字段：
- scene: where/when/who_present
- hook_type: 7 个钩子之一（来自 prompt_templates.HOOK_TYPES）
- reader_emotion_to_install / reader_question: 读者心理目标
- show_item_seed: 一个具体物件/动作（与 lorebook.show_item_examples 同款）
- expectation_seed / expectation_shift: 期待感推进
"""

from __future__ import annotations

import pytest


# ── 1. opening_spine schema 必填字段 ─────────────────

def test_opening_spine_required_fields():
    """opening_spine 必须含 3 个核心字段：chapter_1_anchor / chapter_2_question / chapter_3_escalation。"""
    from engine.agents.opening_designer import REQUIRED_OPENING_FIELDS

    for f in ("chapter_1_anchor", "chapter_2_question", "chapter_3_escalation", "source"):
        assert f in REQUIRED_OPENING_FIELDS, f"opening_spine 缺字段: {f}"


# ── 2. 黄金三章结构必填子字段 ─────────────────

def test_each_chapter_has_required_subfields():
    """每章必有 scene + hook_type + show_item + expectation 字段。"""
    from engine.agents.opening_designer import design_opening

    opening = design_opening(
        concept="服徭役主角在回家前夕被征召",
        theme_spine={
            "theme_statement": "在大时代里，普通人能守住的只有'回家'这一件事",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80,
                                 "twist_chapter": 25, "description": "归家主题"},
            "resonance_anchors": ["家", "忠诚", "孤独"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史",
                       "show_item_examples": ["那双鞋"]},
        key_characters=[],
        use_llm=False,
    )

    # ch1: 锚定场景
    ch1 = opening["chapter_1_anchor"]
    for f in ("scene", "hook_type", "reader_emotion_to_install",
              "show_item_seed", "expectation_seed"):
        assert f in ch1, f"ch1 缺字段: {f}"

    # ch2: 建立问题
    ch2 = opening["chapter_2_question"]
    for f in ("scene", "hook_type", "reader_question",
              "expectation_shift"):
        assert f in ch2, f"ch2 缺字段: {f}"

    # ch3: 升级/翻转
    ch3 = opening["chapter_3_escalation"]
    for f in ("scene", "hook_type", "reader_emotion_to_install",
              "expectation_shift"):
        assert f in ch3, f"ch3 缺字段: {f}"


# ── 3. hook_type 必须是 7 个合法 hook 之一 ─────────────────

def test_hook_type_is_one_of_seven():
    """hook_type 必须是 prompt_templates.HOOK_TYPES 的 7 个之一，
    否则下游 hook 渲染会乱套。"""
    from engine.agents.opening_designer import design_opening
    from engine.config.prompt_templates import HOOK_TYPES

    opening = design_opening(
        concept="",
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["x"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )

    valid_hooks = set(HOOK_TYPES.keys())
    for ch in ("chapter_1_anchor", "chapter_2_question", "chapter_3_escalation"):
        ht = opening[ch]["hook_type"]
        assert ht in valid_hooks, (
            f"{ch}.hook_type {ht!r} 不在 HOOK_TYPES 7 个合法 hook 内: {sorted(valid_hooks)}"
        )


# ── 4. scene 字段结构（where/who/time/weather）─────────────

def test_scene_field_structure():
    """scene 字段必含 where/who_present（其他可选）。"""
    from engine.agents.opening_designer import design_opening

    opening = design_opening(
        concept="",
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["x"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )

    for ch in ("chapter_1_anchor", "chapter_2_question", "chapter_3_escalation"):
        scene = opening[ch]["scene"]
        assert "where" in scene, f"{ch} scene 缺 where"
        assert "who_present" in scene, f"{ch} scene 缺 who_present"
        # who_present 必须是 list[str]
        assert isinstance(scene["who_present"], list)


# ── 5. show_item 必须具体（与 lorebook 同标准）─────────────

def test_show_items_are_specific_not_abstract():
    """show_item_seed / show_item_used 必须是具体物件/动作，不能是抽象概念。
    这是 show-don't-tell 落到黄金三章结构上的硬约束。"""
    from engine.agents.opening_designer import design_opening

    opening = design_opening(
        concept="归家",
        theme_spine={
            "theme_statement": "归家",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["家"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )

    # 抽 8 个具体物件词（与 lorebook show_item 测试同口径）
    concrete_objects = ("鞋", "信", "刀", "灯", "水", "门", "窗", "桌", "碗",
                        "玉佩", "符", "茶", "灶台", "灰", "牌位", "鸡蛋",
                        "水壶", "茶", "内衬", "铠甲", "照片", "植物", "茶杯",
                        "鞋底", "泥", "烟")
    for ch in ("chapter_1_anchor", "chapter_2_question", "chapter_3_escalation"):
        item = opening[ch].get("show_item_seed") or opening[ch].get("show_item_used") or ""
        assert isinstance(item, str) and len(item) >= 4, (
            f"{ch} show_item 太短: {item!r}"
        )
        assert any(obj in item for obj in concrete_objects), (
            f"{ch} show_item 太抽象（缺具体物件）: {item!r}"
        )


# ── 6. 期待感推进链（ch1 → ch2 → ch3 必须有推进）─────────────

def test_expectation_progression_ch1_to_ch3():
    """ch1 播种期望 → ch2 强化/加问号 → ch3 翻转/升级，三章必须形成推进链。
    这是用户指导'期待感管理'的核心：'读者在读的时候已经有了一个预期的结果了，
    然后会期待故事进展到预期的方向时候的不确定性'。"""
    from engine.agents.opening_designer import design_opening

    opening = design_opening(
        concept="归家主题",
        theme_spine={
            "theme_statement": "归家",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["家"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )

    # ch1 必含 expectation_seed（首次播种）
    assert "expectation_seed" in opening["chapter_1_anchor"]
    seed = opening["chapter_1_anchor"]["expectation_seed"]
    assert seed, "ch1 expectation_seed 不能空"

    # ch2 必含 expectation_shift（推进）
    assert "expectation_shift" in opening["chapter_2_question"]
    shift2 = opening["chapter_2_question"]["expectation_shift"]
    assert shift2, "ch2 expectation_shift 不能空"

    # ch3 必含 expectation_shift（升级/翻转）
    assert "expectation_shift" in opening["chapter_3_escalation"]
    shift3 = opening["chapter_3_escalation"]["expectation_shift"]
    assert shift3, "ch3 expectation_shift 不能空"


# ── 7. 题材差异化（不同 genre 黄金三章不同）─────────────

def test_different_genre_produces_different_opening_template():
    """不同 genre 黄金三章模板不同（玄幻 = 觉醒开局 / 历史 = 服徭役开局）。
    这是 genre_profile → opening_designer 的级联差异化。"""
    from engine.agents.opening_designer import design_opening

    base_theme = {
        "theme_statement": "x",
        "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                             "twist_chapter": 20, "description": "x"},
        "resonance_anchors": ["x"],
    }
    base_kc = []

    xh = design_opening(
        concept="", theme_spine=base_theme,
        genre_profile={"genre_key": "xuanhuan", "genre": "玄幻"},
        key_characters=base_kc, use_llm=False,
    )
    ls = design_opening(
        concept="", theme_spine=base_theme,
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=base_kc, use_llm=False,
    )

    # ch1 场景描述必须不同
    assert xh["chapter_1_anchor"]["scene"] != ls["chapter_1_anchor"]["scene"], (
        f"玄幻 / 历史 ch1 场景应不同: xh={xh['chapter_1_anchor']['scene']}, "
        f"ls={ls['chapter_1_anchor']['scene']}"
    )


# ── 8. save/load 用户编辑版 ─────────────────

def test_save_and_load_user_overridden_opening(tmp_path, monkeypatch):
    """用户可在 UI 编辑后保存（带 source='user'）；load 必须返回 user 版。"""
    from engine.agents import opening_designer as od_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    user_opening = {
        "chapter_1_anchor": {
            "scene": {"where": "我的开场", "who_present": ["主角"]},
            "hook_type": "悬念钩",
            "reader_emotion_to_install": "期待",
            "show_item_seed": "那双布鞋",
            "expectation_seed": "我手工改的期望",
        },
        "chapter_2_question": {
            "scene": {"where": "路上", "who_present": ["主角"]},
            "hook_type": "对抗钩",
            "reader_question": "我手工改的问题",
            "show_item_used": "那双布鞋还在路上被踢了一脚",
            "expectation_shift": "我手工改的推进",
        },
        "chapter_3_escalation": {
            "scene": {"where": "县衙", "who_present": ["主角", "县令"]},
            "hook_type": "反转钩",
            "reader_emotion_to_install": "矛盾",
            "show_item_used": "那双布鞋被县令拿起看了一眼",
            "expectation_shift": "我手工改的翻转",
        },
        "source": "user",
    }
    od_mod.save_opening("test-novel", user_opening)
    loaded = od_mod.load_opening("test-novel")
    assert loaded is not None
    assert loaded["source"] == "user"
    assert loaded["chapter_1_anchor"]["show_item_seed"] == "那双布鞋"


def test_load_returns_none_when_no_opening(tmp_path, monkeypatch):
    """未生成过 opening → load 返回 None（让上层走 generate 路径）。"""
    from engine.agents import opening_designer as od_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path / "empty"))

    assert od_mod.load_opening("never-set-novel") is None


# ── 9. InvalidOpeningError 校验 ─────────────────

def test_save_opening_rejects_incomplete_payload(tmp_path, monkeypatch):
    """缺字段 → InvalidOpeningError（不能让损坏数据落盘）。"""
    from engine.agents import opening_designer as od_mod
    from engine.config import paths as paths_mod

    monkeypatch.setattr(paths_mod, "novel_ai_dir", lambda _id: str(tmp_path))

    bad = {
        "chapter_1_anchor": {"scene": "x"},
        "source": "user",
    }
    with pytest.raises(od_mod.InvalidOpeningError):
        od_mod.save_opening("test-novel", bad)


def test_save_opening_rejects_non_dict():
    """save_opening 必须接 dict。"""
    from engine.agents.opening_designer import save_opening, InvalidOpeningError

    with pytest.raises(InvalidOpeningError):
        save_opening("test-novel", "not a dict")  # type: ignore[arg-type]


# ── 10. LLM 模式合并 ─────────────────

def test_design_opening_llm_path_overrides_template(monkeypatch):
    """LLM 模式：模板作为 base，LLM 覆盖（场景/hook 等）。"""
    from engine.agents import opening_designer as od_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            return (
                '{"chapter_1_anchor": {"scene": {"where": "LLM 改写的开场", '
                '"who_present": ["主角"]}, "hook_type": "悬念钩", '
                '"reader_emotion_to_install": "期待", "show_item_seed": "一封信", '
                '"expectation_seed": "LLM 期望"}, '
                '"chapter_2_question": {"scene": {"where": "路上", '
                '"who_present": ["主角"]}, "hook_type": "但是法则", '
                '"reader_question": "LLM 问题", "expectation_shift": "LLM 推进"}, '
                '"chapter_3_escalation": {"scene": {"where": "县衙", '
                '"who_present": ["主角", "县令"]}, "hook_type": "巧合即命运", '
                '"reader_emotion_to_install": "矛盾", "expectation_shift": "LLM 翻转"}}',
                0.01,
            )

    monkeypatch.setattr(od_mod, "get_active_router", lambda: _FakeRouter())

    opening = od_mod.design_opening(
        concept="",
        theme_spine={
            "theme_statement": "归家",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["家"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=True,
    )
    assert opening["chapter_1_anchor"]["scene"]["where"] == "LLM 改写的开场"
    assert opening["source"] == "llm"


def test_design_opening_llm_failure_keeps_template(monkeypatch):
    """LLM 抛异常 → 保留模板 + source='template'（CLAUDE.md '失败要响亮' 但细化不阻断主线）。"""
    from engine.agents import opening_designer as od_mod

    class _FakeRouter:
        def call(self, *args, **kwargs):
            raise RuntimeError("模拟 LLM 失败")

    monkeypatch.setattr(od_mod, "get_active_router", lambda: _FakeRouter())

    opening = od_mod.design_opening(
        concept="",
        theme_spine={
            "theme_statement": "归家",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["家"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=True,
    )
    # 模板字段保留
    assert opening["chapter_1_anchor"]
    assert opening["source"] == "template"


# ── 11. 不含项目专名（CLAUDE.md 红线）─────────────

def test_opening_template_no_project_specific_names():
    """模板生成的 opening_design 不能含具体项目专名（CLAUDE.md 红线）。"""
    from engine.agents.opening_designer import design_opening

    opening = design_opening(
        concept="",
        theme_spine={
            "theme_statement": "x",
            "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 50,
                                 "twist_chapter": 20, "description": "x"},
            "resonance_anchors": ["x"],
        },
        genre_profile={"genre_key": "lishi", "genre": "历史"},
        key_characters=[],
        use_llm=False,
    )
    payload = str(opening)
    for name in ("陆承", "周芸", "云州", "林渊", "沈岚"):
        assert name not in payload, f"opening_design 含项目专名 '{name}'"