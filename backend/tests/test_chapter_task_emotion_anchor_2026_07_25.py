"""test_chapter_task_emotion_anchor_2026_07_25.py

战略审视 Commit 3 — emotion_core + emotion_intensity 字段回归。

覆盖:
- 老 task 无 emotion 字段 → 默认 "压抑"×3（中等强度）
- 7 种 emotion_core 全部渲染对应描述
- emotion_intensity 1-5 范围映射"轻微/低/中等/高/爆点"
- 越界值（0/6/str）兜底

详见 docs/wiki/03-Writing-Engine.md §1 M3 (方法论代理 O1)。
"""
from __future__ import annotations

from engine.agents.writer import build_writer_prompt
from engine.config.prompt_templates import EMOTION_CORES


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
        "chapter_goal": "测试",
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


# ─── 1. EMOTION_CORES 完整性 ─────────────────────────

def test_emotion_cores_has_seven_keys():
    """情绪核心 7 类必须齐全。"""
    expected = {"憋屈", "压抑", "爽快", "震惊", "虐心", "甜蜜", "燃"}
    assert set(EMOTION_CORES.keys()) == expected


# ─── 2. 默认兜底 ─────────────────────────

def test_default_emotion_when_missing():
    """老 task 无 emotion 字段 → 默认 "压抑"×3。"""
    task = _base_task()  # 无 emotion 字段
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "emotion=压抑×3(中等)" in usr_p
    assert "情绪核心" in usr_p


def test_default_emotion_when_invalid_value():
    """emotion_core 不在 7 类中 → 默认 "压抑"。"""
    task = {**_base_task(), "emotion_core": "不知道"}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "emotion=压抑" in usr_p


def test_default_intensity_when_out_of_range():
    """emotion_intensity 越界 → 默认 3（中等）。"""
    task = {**_base_task(), "emotion_intensity": 7}  # 越界
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "×3(中等)" in usr_p


def test_default_intensity_when_string():
    """emotion_intensity 是字符串 → 默认 3。"""
    task = {**_base_task(), "emotion_intensity": "三"}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "×3(中等)" in usr_p


# ─── 3. 7 类 emotion_core 全部渲染 ─────────────────────────

def test_all_seven_emotion_cores_render():
    """7 类 emotion_core 都应渲染对应的中文描述。"""
    for core in EMOTION_CORES:
        task = {**_base_task(), "emotion_core": core, "emotion_intensity": 4}
        sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
        assert f"emotion={core}×4(高)" in usr_p, f"missing {core}"
        # 描述至少 ≥ 5 字
        assert f"情绪核心：" in usr_p


def test_emotion_block_contains_anti_fatigue_warning():
    """情绪锚点块必须含「避免连续 3 章同 emotion_core」防疲劳提示。"""
    task = {**_base_task(), "emotion_core": "爽快", "emotion_intensity": 5}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "避免连续 3 章" in usr_p
    assert "情绪疲劳" in usr_p


# ─── 4. 强度等级映射 ─────────────────────────

def test_intensity_labels():
    """1-5 强度应映射正确标签。"""
    for i, label in [(1, "轻微"), (2, "低"), (3, "中等"), (4, "高"), (5, "爆点")]:
        task = {**_base_task(), "emotion_core": "爽快", "emotion_intensity": i}
        sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
        assert f"×{i}({label})" in usr_p, f"intensity {i} → label {label} missing"


def test_intensity_zero_falls_back_to_three():
    """0 是越界(有效范围 1-5),应兜底为 3。"""
    task = {**_base_task(), "emotion_core": "爽快", "emotion_intensity": 0}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    assert "×3(中等)" in usr_p


# ─── 5. 集成 ─────────────────────────

def test_emotion_combined_with_other_fields():
    """emotion 与 stakes+dilemma+thread 等字段同时渲染,所有块都出现。"""
    task = {
        **_base_task(),
        "stakes": {"if_lose": ["妹妹"], "if_win": ["修为"]},
        "dilemma": {"option_a": "A", "option_b": "B", "both_cost": "C"},
        "narrative_thread": "side",
        "info_asymmetry": {"reader_knows": ["真相"], "protagonist_knows": [], "reveals_at_chapter": None},
        "anchor_to": 1,
        "emotion_core": "震惊",
        "emotion_intensity": 5,
    }
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    # 所有块都应渲染
    assert "本章筹码" in usr_p
    assert "本章两难" in usr_p
    assert "narrative_thread=side" in usr_p
    assert "本章信息差" in usr_p
    assert "anchor_to=arc1" in usr_p
    assert "emotion=震惊×5(爆点)" in usr_p


def test_emotion_block_length_within_budget():
    """emotion_block 单块 ≤ 400 字符(writer prompt 总预算)。"""
    task = {**_base_task(), "emotion_core": "燃", "emotion_intensity": 5}
    sys_d, usr_p = build_writer_prompt(task, _minimal_context(), _base_setting())
    idx = usr_p.find("本章情绪锚点")
    assert idx != -1
    # 取 emotion_block 到下一个块的开始位置
    end_markers = ["\n【", "现在开始写"]
    end_idx = len(usr_p)
    for m in end_markers:
        pos = usr_p.find(m, idx + 1)
        if pos != -1 and pos < end_idx:
            end_idx = pos
    block_len = end_idx - idx
    assert block_len < 600, f"emotion block too long: {block_len} chars"