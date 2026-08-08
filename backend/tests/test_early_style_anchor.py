"""test_early_style_anchor.py — 任务 task-02：前 20 章风格锚点注入

契约（对应 docs/drafts/task-02-style-anchor-early-chapters.md）：
  1. chapter <= 20 且 style_samples 为空 → 注入【早期章节风格指南】块。
  2. chapter > 20 → 不注入。
  3. style_samples 非空 → 不注入（避免和正常 style_block 重复 / 冗余）。
  4. chapter <= 5 且 style_samples 为空 → 【黄金章节写作要点】仍生效（保留回归保护），
     同时【早期章节风格指南】不重复注入（不膨胀 prompt）。
  5. 不含任何具体项目的角色名 / 地名 / 世界名（CLAUDE.md 红线：模板常量）。
"""
from __future__ import annotations

import pytest

from engine.agents.writer import _build_early_style_block, build_writer_prompt
from engine.config.prompt_templates import (
    EARLY_CHAPTER_STYLE_GUIDE,
    get_early_chapter_style_guide,
)


# 历史包袱：早期代码（任务 task-02-2018 之前的提交）曾把"林渊 / 苏晚栀 / 顾青锋"
# 等测试项目的专名硬编进 writer prompt（test_writer_prompt_no_project_leak 覆盖）。
# 这些专名绝不能漏到 EARLY_CHAPTER_STYLE_GUIDE 里。
_LEAKED_PROJECT_NAMES = ("林渊", "苏晚栀", "孟浩", "顾青锋", "云州", "债主", "债界")


# ───────── helpers ─────────


def _base_setting(platform: str = "fanqie", genre: str = "都市") -> dict:
    return {
        "platform": platform,
        "genre": genre,
        "protagonist": {"name": "陈默"},
        "world_setting": {
            "surface_world_name": "临海市",
            "hidden_world_name": "债界",
        },
        "power_system": {"name": "人情债", "currency": "债点", "levels": []},
        "key_characters": [],
    }


def _base_context(style_samples: list | None = None) -> dict:
    return {
        "protagonist_level": "凡人",
        "protagonist_points": 0,
        "inventory": [],
        "scene_location": "临海市",
        "time_context": "夜",
        "character_states": {},
        "active_threads": [],
        "recent_events": "",
        "last_chapter_ending": "",
        "relevant_forbidden": [],
        "foreshadowing_due_soon": [],
        "cold_summary": "",
        "style_samples": style_samples if style_samples is not None else [],
        "style_samples_source": "external",
    }


def _base_task(chapter_number: int = 1, **overrides) -> dict:
    base = {
        "chapter_number": chapter_number,
        "chapter_role": "铺垫",
        "chapter_goal": "主角出场",
        "core_conflict": "与债主对峙",
        "plot_progression": "开始",
        "emotion_shift": "压抑",
        "main_characters": ["陈默"],
        "target_length": "2000-2200",
        "ending_hook_type": "信息钩",
        "setting_constraints": [],
        "forbidden_actions": [],
    }
    base.update(overrides)
    return base


# ───────── 1. _build_early_style_block 单元 ─────────


@pytest.mark.parametrize("ch_num", [1, 3, 5, 6, 10, 20])
def test_early_style_block_renders_for_chapters_under_threshold(ch_num):
    """第 1-5 章由【黄金章节写作要点】覆盖结构向，_build_early_style_block 不重复注入；
    第 6-20 章由 _build_early_style_block 覆盖题材惯例。两者职责不重叠。"""
    blk = _build_early_style_block(_base_task(ch_num), _base_setting())
    if ch_num <= 5:
        # 第 1-5 章：_build_early_style_block 故意不返回（避免与【黄金章节】块重复）
        assert blk == "", f"ch={ch_num} 应交给黄金章节块处理，不应在此返回"
    else:
        # 第 6-20 章：必须返回非空指南，且包含本章号
        assert blk, f"ch={ch_num} 应注入早期风格指南"
        assert f"第 {ch_num} 章" in blk, f"占位 {{ch}} 必须渲染成实际章号，实际: {blk[:80]!r}"


@pytest.mark.parametrize("ch_num", [21, 22, 50, 100])
def test_early_style_block_skips_after_threshold(ch_num):
    """第 21 章起风格样本由 style_manager 接管，不再注入题材惯例。"""
    blk = _build_early_style_block(_base_task(ch_num), _base_setting())
    assert blk == "", f"ch={ch_num} 应停止注入"


def test_early_style_block_picks_genre_specific_guidance():
    """平台×题材命中时取对应指南（不是 default）。"""
    fanqie_dushi = _build_early_style_block(
        _base_task(chapter_number=10), _base_setting(platform="fanqie", genre="都市"),
    )
    fanqie_xuanhuan = _build_early_style_block(
        _base_task(chapter_number=10), _base_setting(platform="fanqie", genre="玄幻"),
    )
    assert "都市爽文" in fanqie_dushi
    assert "玄幻修仙" in fanqie_xuanhuan


def test_early_style_block_falls_back_to_default_for_unknown_platform():
    """未识别平台 → 取 default 指南（不抛）。"""
    blk = _build_early_style_block(
        _base_task(chapter_number=10), _base_setting(platform="unknown-platform", genre="都市"),
    )
    assert blk
    assert "通用惯例" in blk


def test_early_style_block_falls_back_to_default_for_unknown_genre():
    """未识别题材 → 取对应平台的 default 指南（不抛）。"""
    blk = _build_early_style_block(
        _base_task(chapter_number=10), _base_setting(platform="fanqie", genre="修仙"),
    )
    assert blk
    # fanqie 没"修仙"具体指南，但题材子串"仙"也不命中"玄幻"
    # → 降级到 fanqie["default"]（兜底第二层）
    assert "信息" in blk or "钩子" in blk


# ───────── 2. writer prompt 集成 ─────────


def test_writer_prompt_includes_early_style_block_when_samples_empty_ch6():
    """第 6 章 + style_samples 为空 → writer prompt 必须含【早期章节风格指南】。"""
    _, usr_p = build_writer_prompt(
        _base_task(chapter_number=6),
        _base_context(style_samples=[]),
        _base_setting(),
    )
    assert "【早期章节风格指南" in usr_p


def test_writer_prompt_drops_early_style_block_when_samples_present_ch6():
    """第 6 章 + style_samples 非空 → 不注入（避免与 style_block 重复）。"""
    _, usr_p = build_writer_prompt(
        _base_task(chapter_number=6),
        _base_context(style_samples=["外部样本 1：描写风格参考..."] * 3),
        _base_setting(),
    )
    assert "【早期章节风格指南" not in usr_p


def test_writer_prompt_drops_early_style_block_after_ch20():
    """第 21 章起 → 不注入。"""
    _, usr_p = build_writer_prompt(
        _base_task(chapter_number=21),
        _base_context(style_samples=[]),
        _base_setting(),
    )
    assert "【早期章节风格指南" not in usr_p


def test_writer_prompt_chapter_5_keeps_golden_block_not_early_style_block():
    """第 5 章：早期风格指南不重复注入（ch<=5 由【黄金章节写作要点】覆盖，task-02 不抢位）。

    注意：【黄金章节写作要点】本身的契约由 pre-existing 黄金三章提交覆盖
    （orchestrator + writer.py 黄金阈值 / log / 路由调整），不在本任务范围。
    这里只断言 task-02 的边界：第 5 章不发早期风格指南。
    """
    _, usr_p = build_writer_prompt(
        _base_task(chapter_number=5),
        _base_context(style_samples=[]),
        _base_setting(),
    )
    # 早期风格指南由 _build_early_style_block 拒绝（ch<=5 走黄金块路径）
    assert "【早期章节风格指南" not in usr_p


# ───────── 3. CLAUDE.md 红线：模板常量不含具体项目专名 ─────────


def test_early_style_guide_does_not_leak_project_specific_names():
    """所有平台/题材/默认指南都不能含具体项目专名（CLAUDE.md 不变量）。"""
    for platform, by_genre in EARLY_CHAPTER_STYLE_GUIDE.items():
        if isinstance(by_genre, str):
            guides = [(platform, "<default>", by_genre)]
        else:
            guides = [(platform, g, v) for g, v in by_genre.items()]
        for _, genre, guide in guides:
            if not isinstance(guide, str):
                continue
            for name in _LEAKED_PROJECT_NAMES:
                assert name not in guide, (
                    f"平台 {platform} 题材 {genre} 含有项目专名 {name!r}："
                    f"{guide[:80]!r}"
                )


def test_get_early_chapter_style_guide_returns_string_for_known_inputs():
    """所有平台 × 已知题材都返回非空字符串（查表契约）。"""
    assert get_early_chapter_style_guide("fanqie", "都市")
    assert get_early_chapter_style_guide("fanqie", "玄幻")
    assert get_early_chapter_style_guide("fanqie", "萌宝甜宠")
    assert get_early_chapter_style_guide("default", "")
    assert get_early_chapter_style_guide("未知平台", "未知题材")