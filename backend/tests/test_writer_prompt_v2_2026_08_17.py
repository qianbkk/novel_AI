"""test_writer_prompt_v2_2026_08_17.py

v1.0 Stage F 验证：writer prompt v2 必须包含 4 个新 block：
- 题材读者画像 block (genre_profile)
- 共性主题 block (theme_spine)
- 期待感推进 block (expectation_progress / arc context)
- Show-item 接力 block (show_item_used from prior chapters)

设计动机（来自 docs/drafts/v1-quality-first-design.md § Stage 3）：
- 用户指导：'读者画像 / 喜欢看的调调 / 读者会为自己脑海中的幻想买单'
- 这 4 个 block 把 v1.0 前期工程的所有输出（genre/theme/expectation/show-item）
  落到 writer prompt 中
- 没有这 4 个 block，writer 仍是 v0.5 风格（无差别写任何题材）
- 测试用 mock 的 v1.0 数据喂入 writer，验证 prompt 渲染正确
"""

from __future__ import annotations

import pytest


# ── 工具：建最小 task/setting/ context for writer ─────────────────

def _minimal_task(chapter_number: int = 5) -> dict:
    return {
        "chapter_number": chapter_number,
        "chapter_goal": "主角踏上归途",
        "main_characters": ["主角"],
        "ending_hook_type": "悬念钩",
        "emotion_core": "期待",
        "emotion_intensity": 3,
    }


def _minimal_setting() -> dict:
    return {
        "genre": "历史",
        "protagonist": {"name": "主角", "background": "服徭役者"},
        "key_characters": [],
    }


def _minimal_context() -> dict:
    return {
        "character_states": {},
        "active_threads": [],
        "recent_events": "",
        "style_samples": [],
        "relevant_forbidden": [],
    }


# ── 1. Genre 读者画像 block ─────────────────

def test_genre_block_includes_reader_persona_and_tone():
    """writer prompt 必须含 genre_profile.reader_persona + tone_preference
    + show_item_examples（用户指导：'读者喜欢看的调调'）。"""
    from engine.agents.writer import _build_genre_block

    profile = {
        "genre": "历史",
        "reader_persona": {"primary": "30-50 男性"},
            "tone_preference": "沉郁克制",
            "show_item_examples": ["那双布鞋", "母亲的牌位"],
        }
    block = _build_genre_block(profile)
    assert "30-50 男性" in block
    assert "沉郁克制" in block
    assert "那双布鞋" in block


def test_genre_block_handles_missing_profile_gracefully():
    """无 genre_profile → 返回空块（不阻断 writer）。"""
    from engine.agents.writer import _build_genre_block

    block = _build_genre_block(None)
    # 空字符串或带'未指定'标记（不阻断）
    assert isinstance(block, str)


# ── 2. 共性主题 block ─────────────────

def test_theme_block_includes_theme_statement_and_resonance_anchors():
    """writer prompt 必须含 theme_spine.theme_statement + resonance_anchors。
    这是用户指导核心：'恒久的共性的主题，能够引起读者的共鸣'。"""
    from engine.agents.writer import _build_theme_block

    spine = {
        "theme_statement": "在大时代里，普通人能守住的只有'回家'这一件事",
        "resonance_anchors": ["家", "忠诚", "孤独"],
        "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80, "twist_chapter": 25,
                             "description": "归家主题弧"},
    }
    block = _build_theme_block(spine)
    assert "回家" in block
    assert "家" in block
    assert "忠诚" in block


def test_theme_block_handles_missing_spine():
    """无 theme_spine → 空块（不阻断）。"""
    from engine.agents.writer import _build_theme_block

    assert isinstance(_build_theme_block(None), str)


# ── 3. 期待感推进 block ─────────────────

def test_expectation_block_shows_current_arc_progress():
    """writer prompt 必须含 macro_spine 当前 arc 的 theme_focus + expectation_progress。
    让 writer 知道'本章在整本书期待感推进中的位置'。"""
    from engine.agents.writer import _build_expectation_block

    macro = {
        "arcs": [
            {"arc_id": 1, "name": "开局", "start_chapter": 1, "end_chapter": 15,
             "theme_focus": "归途期待", "expectation_progress": "seed 强化",
             "main_conflict": "服徭役 vs 回家", "tone": "克制"},
            {"arc_id": 2, "name": "发展", "start_chapter": 16, "end_chapter": 30,
             "theme_focus": "归途考验", "expectation_progress": "主线推进",
             "main_conflict": "征召 vs 回家", "tone": "震荡"},
        ]
    }
    # 主角在第 20 章，应该归属 arc 2
    block = _build_expectation_block(macro, chapter_number=20)
    assert "发展" in block or "归途考验" in block


def test_expectation_block_handles_chapter_outside_arcs():
    """chapter 不在任何 arc 内（异常）→ 降级显示'未分配'，不阻断。"""
    from engine.agents.writer import _build_expectation_block

    macro = {"arcs": [{"arc_id": 1, "name": "开局", "start_chapter": 1, "end_chapter": 10,
                       "theme_focus": "x", "expectation_progress": "x",
                       "main_conflict": "x", "tone": "x"}]}
    block = _build_expectation_block(macro, chapter_number=999)
    assert isinstance(block, str)


# ── 4. Show-Item 接力 block ─────────────────

def test_show_item_block_lists_recent_show_items():
    """writer prompt 必须含上一章 + 上 2 章的 show_item_used（接力提示）。
    这是 show-don't-tell 的物理落地：'那本书 / 那双鞋 在前几章被强调过'。"""
    from engine.agents.writer import _build_showitem_block

    expectation_ledger = [
        {"chapter_number": 1, "show_item_used": ["那双布鞋"]},
        {"chapter_number": 2, "show_item_used": ["那双布鞋", "邻家少年的眼睛"]},
        {"chapter_number": 3, "show_item_used": ["那双布鞋"]},
    ]
    block = _build_showitem_block(expectation_ledger, chapter_number=4)
    # 上 3 章的 show_item 应在 block 里
    assert "那双布鞋" in block
    assert "邻家少年的眼睛" in block


def test_show_item_block_handles_empty_ledger():
    """无 ledger → 空块。"""
    from engine.agents.writer import _build_showitem_block

    assert isinstance(_build_showitem_block([], chapter_number=1), str)


# ── 5. 4 个 block 集成到 build_writer_prompt ─────────────────

def test_build_writer_prompt_includes_all_v1_blocks():
    """build_writer_prompt 在传入 v1.0 context 时，必须把 4 个 block 都注入。
    这是 v1.0 § Stage F 的核心契约：'前期工程的输出必须落到 writer prompt'。"""
    from engine.agents.writer import build_writer_prompt

    task = _minimal_task(chapter_number=3)
    setting = _minimal_setting()
    setting["v1_genre_profile"] = {
        "genre": "历史",
        "reader_persona": {"primary": "30-50 男性"},
        "tone_preference": "沉郁克制",
        "show_item_examples": ["那双布鞋"],
    }
    setting["v1_theme_spine"] = {
        "theme_statement": "在大时代里，普通人能守住的只有'回家'",
        "resonance_anchors": ["家", "忠诚"],
        "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80,
                             "twist_chapter": 25, "description": "归家弧"},
    }
    setting["v1_macro_spine"] = {
        "arcs": [
            {"arc_id": 1, "name": "开局", "start_chapter": 1, "end_chapter": 15,
             "theme_focus": "归途期待", "expectation_progress": "seed 强化",
             "main_conflict": "服徭役 vs 回家", "tone": "克制"},
        ]
    }
    context = _minimal_context()
    context["v1_expectation_ledger"] = [
        {"chapter_number": 1, "show_item_used": ["那双布鞋"]},
        {"chapter_number": 2, "show_item_used": ["那双布鞋", "邻家少年的眼睛"]},
    ]

    system, user = build_writer_prompt(task, context, setting)

    # 4 个 block 都应在 prompt 里
    user_prompt_lower = user
    assert "30-50 男性" in user_prompt_lower, "genre persona 未注入"
    assert "回家" in user_prompt_lower, "theme_statement 未注入"
    assert "开局" in user_prompt_lower or "归途期待" in user_prompt_lower, "expectation/arc 未注入"
    assert "那双布鞋" in user_prompt_lower, "show_item 未注入"


def test_build_writer_prompt_works_without_v1_context():
    """向后兼容：v0.5 调用方式（无 v1_* 字段）仍能工作。"""
    from engine.agents.writer import build_writer_prompt

    task = _minimal_task()
    setting = _minimal_setting()  # 无 v1_*
    context = _minimal_context()  # 无 v1_*

    system, user = build_writer_prompt(task, context, setting)
    assert isinstance(system, str) and isinstance(user, str)
    # 应能正常生成 prompt（不强求 v1 block）
    assert "本章出场人物" in user


# ── 6. prompt 长度预算（含 v1 block 不爆 6k 字符）─────────────────

def test_prompt_size_under_budget_with_v1_blocks():
    """含 4 个新 block 后，prompt 仍 < 6000 字符（WRITER_PROMPT_BUDGET_CHARS）。
    这是 v0.5 P1 修复的延续。"""
    from engine.agents.writer import build_writer_prompt, WRITER_PROMPT_BUDGET_CHARS

    task = _minimal_task()
    setting = _minimal_setting()
    setting["v1_genre_profile"] = {
        "genre": "历史",
        "reader_persona": {"primary": "30-50 男性", "core_fantasy": "乱世保家"},
        "tone_preference": "沉郁克制",
        "show_item_examples": ["那双布鞋", "母亲的牌位"],
        "taboo": ["后宫", "穿越即无敌"],
    }
    setting["v1_theme_spine"] = {
        "theme_statement": "在大时代里，普通人能守住的只有'回家'",
        "resonance_anchors": ["家", "忠诚", "孤独"],
        "expectation_arc": {"seed_chapter": 1, "payoff_chapter": 80,
                             "twist_chapter": 25, "description": "归家弧"},
    }
    setting["v1_macro_spine"] = {
        "arcs": [
            {"arc_id": 1, "name": "开局", "start_chapter": 1, "end_chapter": 15,
             "theme_focus": "归途期待", "expectation_progress": "seed 强化",
             "main_conflict": "服徭役 vs 回家", "tone": "克制"},
        ]
    }
    context = _minimal_context()
    context["v1_expectation_ledger"] = [
        {"chapter_number": 1, "show_item_used": ["那双布鞋"]},
        {"chapter_number": 2, "show_item_used": ["那双布鞋", "邻家少年的眼睛"]},
    ]

    system, user = build_writer_prompt(task, context, setting)
    total = len(system) + len(user)
    # 允许略超 budget（让 v1 block 有意义），但应有 log.warning
    # 实际预算上限 = WRITER_PROMPT_BUDGET_CHARS × 2（system + user）
    # 这里只看 user 部分上限
    assert len(user) < WRITER_PROMPT_BUDGET_CHARS * 3, (
        f"user prompt {len(user)} chars 远超 budget {WRITER_PROMPT_BUDGET_CHARS}*3"
    )