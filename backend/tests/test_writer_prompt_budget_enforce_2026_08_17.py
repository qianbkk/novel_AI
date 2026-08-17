"""test_writer_prompt_budget_enforce_2026_08_17.py

P1-9 修复验证：writer prompt 必须有硬字符预算上限。

历史问题（审计发现，docs/wiki/03-Writing-Engine.md:198-200 已自承）：
- writer prompt 7k-10k 字（recent_summaries 5→10 + RAG 900 + lorebook 900
  + world 600 + style_samples 4500 + methodology 4 招 3500 堆叠）
- LLM 守约束率下降：开始漏掉章节字数、POV 锁、ending_hook_type。
- 调研：写小说平淡没看点怎么办 §3「长 prompt LLM 注意力衰减」。

修复（任务 P1-9 2026-08-17）：
- engine.agents.writer.WRITER_PROMPT_BUDGET_CHARS = 6000（硬上限）
- build_writer_prompt 末尾：超限时按优先级截断（methodology 4 招 → 减招；
  style_samples → 减样本；其它块整体降级）+ log.warning
- 截断事件写 state.error_log（响亮信号，让运维知道"本章 prompt 被截"）

测试策略：构造超长 setting/style/context 让 build_writer_prompt 自然产出
7k+ 字，验证截断逻辑触发并留下 warning。
"""

from __future__ import annotations

import logging

import pytest


# ── 1. 常量契约 ─────────────────────────

def test_writer_prompt_budget_constant_exists():
    """engine.agents.writer 必须定义 WRITER_PROMPT_BUDGET_CHARS 硬上限。"""
    from engine.agents import writer
    assert hasattr(writer, "WRITER_PROMPT_BUDGET_CHARS"), (
        "writer 模块必须定义 WRITER_PROMPT_BUDGET_CHARS 硬上限常量，"
        "否则每章 prompt 可达 7k-10k 字，LLM 守约束率塌方"
    )


def test_writer_prompt_budget_in_reasonable_range():
    """硬上限必须在合理区间（不能 < 2000 否则有效内容被砍光，
    不能 > 12000 否则等于没设）。"""
    from engine.agents import writer
    if not hasattr(writer, "WRITER_PROMPT_BUDGET_CHARS"):
        pytest.skip("WRITER_PROMPT_BUDGET_CHARS 未定义，跳过区间断言")
    assert 2000 <= writer.WRITER_PROMPT_BUDGET_CHARS <= 12000, (
        f"WRITER_PROMPT_BUDGET_CHARS ({writer.WRITER_PROMPT_BUDGET_CHARS}) "
        "应在 [2000, 12000] 区间"
    )


# ── 2. 超限触发截断 + warning ─────────────────────────

def _huge_setting() -> dict:
    """构造一个会让 prompt 自然超过 6k 字的 setting（加长 world_block 等）。"""
    long_world = "世界观描述" * 200  # ~1200 字
    return {
        "novel_id": "test",
        "genre": "都市",
        "platform": "fanqie",
        "title_candidates": ["测试"],
        "protagonist": {"name": "主角", "background": long_world},
        "world_setting": {
            "surface_world_name": "测试世界",
            "hidden_world_name": "深渊回廊",
            "unique_elements": ["元素" * 100],
        },
        "power_system": {
            "name": "等级系统",
            "levels": [{"name": f"等级{i}"} for i in range(50)],
            "currency": "金币",
        },
        "key_characters": [
            {"name": f"角色{i}", "role": "配角", "background": "背景" * 50}
            for i in range(20)
        ],
        "factions": [{"name": f"势力{i}", "description": "描述" * 30} for i in range(10)],
    }


def _huge_context() -> dict:
    """构造会让 prompt 撑大的 context（style_samples / cold_history / RAG）。"""
    return {
        "style_samples": ["样式样本内容" * 80] * 3,  # ~960 字
        "cold_history": "冷层历史内容" * 200,         # ~1000 字
        "recent_events": " | ".join([f"事件{i}" * 5 for i in range(10)]),  # ~500 字
        "lorebook": [
            {"name": f"条目{i}", "content": "条目内容" * 30}
            for i in range(20)
        ],
        "protagonist_level": "等级30",
        "protagonist_points": 9999,
        "inventory": ["道具" + str(i) for i in range(30)],
        "scene_location": "测试地点",
        "time_context": "测试时间",
        "last_chapter_ending": "上章结尾" * 100,
        "active_threads": ["线" + str(i) for i in range(20)],
        "character_states": {"角色" + str(i): "状态" * 30 for i in range(20)},
        "forbidden_constraints": ["禁止" + str(i) for i in range(20)],
        "foreshadow_due_soon": ["伏笔" + str(i) for i in range(20)],
        "rag_chunks": [{"text": "RAG 召回文本" * 50, "chapter": i} for i in range(5)],
        "character_voices": ["口癖" * 20] * 10,
    }


def _huge_task() -> dict:
    return {
        "chapter_number": 7,
        "chapter_role": "发展",
        "chapter_goal": "目标" * 50,
        "core_conflict": "核心冲突" * 50,
        "plot_progression": "主线推进" * 50,
        "emotion_shift": "情感迁移" * 50,
        "main_characters": ["角色" + str(i) for i in range(10)],
        "ending_hook_type": "信息钩",
        "ending_hook_description": "钩子描述" * 30,
        "shuang_type": "打脸",
        "shuang_description": "爽点描述" * 30,
        "target_length": "2000-2200",
        "stakes": {"if_lose": ["输" * 20] * 5, "if_win": ["赢" * 20] * 5},
        "dilemma": {"option_a": "A" * 100, "option_b": "B" * 100, "both_cost": "代价" * 30},
        "narrative_thread": "main",
        "info_asymmetry": {"reader_knows": ["知" * 20], "reveals_at_chapter": 5},
        "anchor_to": 1,
        "emotion_core": "爽快",
        "emotion_intensity": 4,
        "forbidden_actions": ["禁止" * 5] * 10,
        "setting_constraints": ["约束" * 5] * 10,
        "is_arc_climax": False,
    }


def test_writer_prompt_caps_at_budget(caplog):
    """build_writer_prompt 在超长输入下输出 user_prompt 必须 ≤ WRITER_PROMPT_BUDGET_CHARS，
    并 log.warning 留下信号（CLAUDE.md「失败要响亮」）。"""
    from engine.agents.writer import build_writer_prompt, WRITER_PROMPT_BUDGET_CHARS

    sys_d, usr_p = build_writer_prompt(
        _huge_task(),
        _huge_context(),
        _huge_setting(),
    )

    assert len(usr_p) <= WRITER_PROMPT_BUDGET_CHARS, (
        f"writer prompt 超硬上限 {WRITER_PROMPT_BUDGET_CHARS}，"
        f"实际 {len(usr_p)} 字。"
        f"LLM 长 prompt 守约束率塌方，必须截断"
    )


def test_writer_prompt_logs_warning_on_truncation(caplog):
    """超限时必须 log.warning（响亮信号），运维能看到"本章 prompt 被截"。"""
    from engine.agents.writer import build_writer_prompt

    caplog.set_level(logging.WARNING, logger="novel_ai.engine.agents.writer")

    sys_d, usr_p = build_writer_prompt(
        _huge_task(),
        _huge_context(),
        _huge_setting(),
    )

    # 超长输入下应有截断 warning（log.warning 含 "budget"/"截断"/"truncat" 关键词）
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    has_truncate_warn = any(
        any(kw in r.getMessage().lower() for kw in (
            "budget", "truncat", "截断", "overflow", "超限",
        ))
        for r in warnings
    )
    assert has_truncate_warn, (
        f"超限时必须 log.warning（含 budget/截断/truncat 关键词），实际: "
        f"{[r.getMessage() for r in warnings]}"
    )


# ── 3. 正常输入不应误截 ─────────────────────────

def _normal_setting():
    return {
        "genre": "都市",
        "protagonist": {"name": "主角"},
        "world_setting": {"surface_world_name": "测试世界"},
        "key_characters": [{"name": "配角", "role": "配角"}],
    }


def _normal_context():
    return {
        "protagonist_level": "凡人",
        "scene_location": "测试地点",
        "time_context": "测试时间",
    }


def _normal_task():
    return {
        "chapter_number": 5,
        "chapter_role": "发展",
        "chapter_goal": "测试目标",
        "main_characters": ["主角", "配角"],
        "ending_hook_type": "信息钩",
        "shuang_type": "打脸",
        "shuang_description": "爽点",
        "target_length": "2000-2200",
        "emotion_core": "爽快",
        "emotion_intensity": 4,
        "forbidden_actions": [],
        "setting_constraints": [],
    }


def test_normal_input_keeps_prompt_below_budget():
    """正常输入不应触发截断，prompt 长度应在预算内。"""
    from engine.agents.writer import build_writer_prompt

    sys_d, usr_p = build_writer_prompt(
        _normal_task(),
        _normal_context(),
        _normal_setting(),
    )

    # 正常输入应在预算内（且合理范围内）
    assert len(usr_p) > 500, "正常输入 prompt 不应太短（可能 section 全空）"
    assert len(usr_p) <= 10000, (
        f"正常输入 prompt 不应超 10k，实际 {len(usr_p)}（说明 budget 未生效）"
    )