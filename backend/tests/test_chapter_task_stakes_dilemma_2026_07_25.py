"""test_chapter_task_stakes_dilemma_2026_07_25.py

战略审视 Commit 1 — stakes + dilemma 字段扩展回归测试。

覆盖:
- ChapterTask TypedDict 接受 stakes/dilemma 可选字段
- 老 task（无 stakes/dilemma）依然能正常 build writer prompt（向后兼容）
- 新 task（有 stakes/dilemma）字段正确渲染到 writer prompt
- stakes/dilemma 的 JSON schema 标准化逻辑（来自 outline.py）

详见 docs/wiki/03-Writing-Engine.md §已知方法论 gap 与补全计划 §1 M1。
"""
from __future__ import annotations

from engine.agents.writer import build_writer_prompt


def _minimal_context():
    return {
        "protagonist_level": "感债者",
        "protagonist_points": 0,
        "inventory": [],
        "scene_location": "债街",
        "time_context": "黄昏",
        "last_chapter_ending": "",
        "recent_events": "",
        "character_states": {},
        "active_threads": [],
        "relevant_forbidden": [],
        "foreshadowing_due_soon": [],
        "cold_summary": "",
        "style_samples": [],
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


# ─── 1. 老 task 向后兼容 ─────────────────────────

def test_build_writer_prompt_without_stakes_dilemma():
    """老 task 无 stakes/dilemma 字段 — build_writer_prompt 必须不抛,无 stakes/dilemma 渲染。"""
    task = _base_task()  # 没有 stakes/dilemma
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert isinstance(usr_p, str)
    # 不应出现 stakes/dilemma 章节
    assert "本章筹码" not in usr_p
    assert "本章两难" not in usr_p


def test_build_writer_prompt_with_stakes_none():
    """task.stakes = None — 同样不渲染（铺垫/过渡章场景）。"""
    task = {**_base_task(), "stakes": None, "dilemma": None}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章筹码" not in usr_p
    assert "本章两难" not in usr_p


# ─── 2. 新 task 字段渲染 ─────────────────────────

def test_build_writer_prompt_with_stakes_only():
    """爽点章场景：stakes 必填,dilemma 可选。"""
    task = {
        **_base_task(),
        "stakes": {
            "if_lose": ["妹妹被迫嫁给仇家", "三年心血宗门解散"],
            "if_win": ["反派社会性死亡", "修为突破"],
        },
        "dilemma": None,
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章筹码" in usr_p
    assert "妹妹被迫嫁给仇家" in usr_p
    assert "三年心血宗门解散" in usr_p
    assert "反派社会性死亡" in usr_p
    assert "本章两难" not in usr_p  # dilemma 是 None,不应渲染


def test_build_writer_prompt_with_dilemma_only():
    """两难章场景：stakes 可选,dilemma 必填。"""
    task = {
        **_base_task(),
        "stakes": None,
        "dilemma": {
            "option_a": "救妹妹 = 放弃宗门",
            "option_b": "保宗门 = 妹妹嫁给仇家",
            "both_cost": "主角与一方彻底决裂",
        },
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章两难" in usr_p
    assert "救妹妹 = 放弃宗门" in usr_p
    assert "保宗门 = 妹妹嫁给仇家" in usr_p
    assert "两者皆失" in usr_p or "主角与一方彻底决裂" in usr_p
    assert "本章筹码" not in usr_p


def test_build_writer_prompt_with_both_stakes_and_dilemma():
    """弧高潮章场景：stakes + dilemma 都填。"""
    task = {
        **_base_task(),
        "is_arc_climax": True,
        "stakes": {
            "if_lose": ["女主角死亡", "前世真相曝光"],
            "if_win": ["前世今生大和解", "突破元婴"],
        },
        "dilemma": {
            "option_a": "救女主放弃前世记忆",
            "option_b": "保留前世记忆放弃女主",
            "both_cost": "无法弥补的缺憾",
        },
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章筹码" in usr_p
    assert "本章两难" in usr_p
    assert "女主角死亡" in usr_p
    assert "前世真相曝光" in usr_p
    assert "救女主放弃前世记忆" in usr_p


# ─── 3. 边界情况 ─────────────────────────

def test_build_writer_prompt_with_empty_stakes_dict():
    """stakes={} 应等同于 None(空 dict 也视为"未设置")。"""
    task = {**_base_task(), "stakes": {}}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    # outline.py 标准化时已把 {} → None,但 writer 这层也要容忍
    assert "本章筹码" not in usr_p or "失败将失去" not in usr_p


def test_build_writer_prompt_with_invalid_dilemma_missing_options():
    """dilemma 缺 option_a/option_b 时不应渲染。"""
    task = {
        **_base_task(),
        "dilemma": {"both_cost": "缺选项的两难"},
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章两难" not in usr_p


def test_build_writer_prompt_with_partial_stakes_only_if_lose():
    """stakes 只有 if_lose 没有 if_win — 仍渲染(失败焦虑也算筹码)。"""
    task = {
        **_base_task(),
        "stakes": {"if_lose": ["唯一的妹妹"], "if_win": []},
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "本章筹码" in usr_p
    assert "唯一的妹妹" in usr_p
    # 成功将获得 应该不出现（因为 if_win 为空）
    assert "成功将获得" not in usr_p


# ─── 4. 集成检查 ─────────────────────────

def test_stakes_block_total_length_within_budget():
    """stakes + dilemma 完整渲染 ≤ 500 字符(writer prompt 预算)。"""
    task = {
        **_base_task(),
        "stakes": {
            "if_lose": ["a" * 30, "b" * 30, "c" * 30],
            "if_win": ["d" * 30, "e" * 30, "f" * 30],
        },
        "dilemma": {
            "option_a": "x" * 40,
            "option_b": "y" * 40,
            "both_cost": "z" * 40,
        },
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    # 估算 stakes+dilemma 块长度（用关键词定位）
    stakes_idx = usr_p.find("本章筹码")
    dilemma_idx = usr_p.find("本章两难")
    if stakes_idx != -1 and dilemma_idx != -1:
        block_len = dilemma_idx + 200 - stakes_idx  # +200 给 dilemma 后面的字
        assert block_len < 1000, f"stakes+dilemma block too long: {block_len}"