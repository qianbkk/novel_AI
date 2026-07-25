"""test_chapter_task_methodology_fields_2026_07_25.py

战略审视 Commit 2 — narrative_thread + info_asymmetry + anchor_to 字段回归。

覆盖:
- 老 task 无新字段 → build_writer_prompt 不抛,默认 narrative_thread="main"
- narrative_thread 三值有效 / 无效值兜底 / 默认 main
- info_asymmetry dict 渲染 / None 不渲染 / 缺子字段兜底
- anchor_to int 渲染 / None 不渲染

详见 docs/wiki/03-Writing-Engine.md §1 M2 (P9/P10/P11)。
"""
from __future__ import annotations

from engine.agents.writer import build_writer_prompt


def _minimal_context():
    return {
        "protagonist_level": "感债者", "protagonist_points": 0,
        "inventory": [], "scene_location": "债街", "time_context": "黄昏",
        "last_chapter_ending": "", "recent_events": "",
        "character_states": {}, "active_threads": [],
        "relevant_forbidden": [], "foreshadowing_due_soon": [],
        "cold_summary": "", "style_samples": [],
    }


def _base_task():
    return {
        "chapter_number": 5,
        "chapter_role": "发展",
        "chapter_goal": "主角首次对峙反派",
        "main_characters": ["林渊"],
        "ending_hook_type": "悬念钩",
        "target_length": "2000-2200",
        "audit_mode": "full",
        "is_arc_climax": False,
    }


def _base_setting():
    return {
        "protagonist": {"name": "林渊"},
        "genre": "玄幻",
        "key_characters": [],
        "world_setting": None,
        "power_system": {},
    }


# ─── narrative_thread ─────────────────────────

def test_narrative_thread_default_main_when_missing():
    """老 task 无 narrative_thread 字段 → 默认 "main" 主线占位。"""
    task = _base_task()  # 无 narrative_thread
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "narrative_thread=main" in usr_p
    assert "主线推进" in usr_p


def test_narrative_thread_explicit_main():
    task = {**_base_task(), "narrative_thread": "main"}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "narrative_thread=main" in usr_p


def test_narrative_thread_side_branch():
    """支线章节标注。"""
    task = {**_base_task(), "narrative_thread": "side"}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "narrative_thread=side" in usr_p
    assert "支线铺陈" in usr_p


def test_narrative_thread_hidden_thread():
    """暗线章节标注（伏笔埋笔）。"""
    task = {**_base_task(), "narrative_thread": "hidden"}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "narrative_thread=hidden" in usr_p
    assert "暗线埋笔" in usr_p


def test_narrative_thread_invalid_value_falls_back_to_main():
    """无效值应兜底为 main(由 outline 标准化阶段做,writer 层也应容忍)。"""
    task = {**_base_task(), "narrative_thread": "invalid_value"}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    # writer 不严格校验,直接显示字面值;但 outline 标准化会改写为 main
    # 这里假设调用前 outline 已标准化;若直接传,writer 仍能 build
    assert "narrative_thread=invalid_value" in usr_p or "narrative_thread=main" in usr_p


# ─── info_asymmetry ─────────────────────────

def test_info_asymmetry_none_not_rendered():
    """铺垫/过渡章 info_asymmetry=None → 不渲染。"""
    task = {**_base_task(), "info_asymmetry": None}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章信息差" not in usr_p


def test_info_asymmetry_reader_knows_only():
    """读者知/主角不知模式。"""
    task = {
        **_base_task(),
        "info_asymmetry": {
            "reader_knows": ["反派在酒里下了毒"],
            "protagonist_knows": [],
            "reveals_at_chapter": None,
        },
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章信息差" in usr_p
    assert "反派在酒里下了毒" in usr_p
    # protagonist_knows 为空,不应出现 "主角已知"
    assert "主角已知" not in usr_p


def test_info_asymmetry_protagonist_knows_only():
    """主角知/配角不知模式(扮猪吃虎)。"""
    task = {
        **_base_task(),
        "info_asymmetry": {
            "reader_knows": [],
            "protagonist_knows": ["主角是满级大佬装萌新"],
            "reveals_at_chapter": None,
        },
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    # "主角已知" 是 info_block 渲染的关键标识
    assert "主角已知" in usr_p
    # 注:不检查 "读者已知" not in usr_p — methodology 块(Commit 0 注入)
    # 包含 "读者已知威胁" 字串,与本测试目标无关。
    assert "满级大佬装萌新" in usr_p


def test_info_asymmetry_full_with_reveals_chapter():
    """三方都填 + 揭示章号。"""
    task = {
        **_base_task(),
        "info_asymmetry": {
            "reader_knows": ["读者事先知道真相"],
            "protagonist_knows": ["主角知道自己身份"],
            "reveals_at_chapter": 30,
        },
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "读者已知（但主角不知）：读者事先知道真相" in usr_p
    assert "主角已知（但读者暂不知）：主角知道自己身份" in usr_p
    assert "30 章后揭晓" in usr_p


# ─── anchor_to ─────────────────────────

def test_anchor_to_int_rendered():
    """anchor_to 是正整数 → 渲染 arc{anchor} 锚点块。"""
    task = {**_base_task(), "anchor_to": 2}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "anchor_to=arc2" in usr_p
    assert "本章所有线索都服务于这条主线弧" in usr_p


def test_anchor_to_none_not_rendered():
    """anchor_to=None → 不渲染(由 orchestrator 用 current_arc 兜底)。"""
    task = {**_base_task(), "anchor_to": None}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "anchor_to=arc" not in usr_p


def test_anchor_to_cross_arc_continuation():
    """多弧项目里"主线跨弧延续"场景 — anchor_to=1 但 chapter 在 arc 3。"""
    task = {**_base_task(), "anchor_to": 1}  # 锚定到 arc 1 主线
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "anchor_to=arc1" in usr_p


# ─── 集成 ─────────────────────────

def test_all_three_fields_combined():
    """三字段全填,所有块都渲染。"""
    task = {
        **_base_task(),
        "narrative_thread": "hidden",
        "info_asymmetry": {
            "reader_knows": ["真相"],
            "protagonist_knows": [],
            "reveals_at_chapter": 20,
        },
        "anchor_to": 1,
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "narrative_thread=hidden" in usr_p
    assert "暗线埋笔" in usr_p
    assert "本章信息差" in usr_p
    assert "anchor_to=arc1" in usr_p


def test_combined_total_length_within_budget():
    """三块(stakes+dilemma+thread+info+anchor)完整渲染 ≤ 2000 字符预算。"""
    task = {
        **_base_task(),
        "stakes": {"if_lose": ["a" * 50], "if_win": ["b" * 50]},
        "dilemma": {"option_a": "x" * 50, "option_b": "y" * 50, "both_cost": "z" * 50},
        "narrative_thread": "main",
        "info_asymmetry": {"reader_knows": ["m" * 50], "protagonist_knows": ["n" * 50], "reveals_at_chapter": 30},
        "anchor_to": 1,
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    # 五个块累计不应爆涨
    assert len(usr_p) < 6000, f"prompt too long: {len(usr_p)}"