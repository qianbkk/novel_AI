"""test_pov_lock_2026_07_25.py

战略审视 Commit 6 — POV 视角锁定约束 + 切换密度检测。

覆盖:
- prompt_templates.POV_LOCK_INSTRUCTION 内容(单视角默认 + 多视角标注 + 反模式)
- writer prompt 注入 POV_LOCK_INSTRUCTION 到 user_prompt
- normalizer.detect_pov_switching: 0 / 1 / 2 / 3+ 切换
- run_normalizer 集成 POV 切换密度检测到 issues

详见 docs/wiki/03-Writing-Engine.md §1 M6。
"""
from __future__ import annotations

from engine.agents.normalizer import (
    POV_SWITCH_MARKER,
    POV_SWITCH_MULTI_THRESHOLD,
    POV_SWITCH_WARNING_THRESHOLD,
    detect_pov_switching,
    run_normalizer,
)
from engine.agents.writer import build_writer_prompt
from engine.config.prompt_templates import POV_LOCK_INSTRUCTION


# ─── 1. POV_LOCK_INSTRUCTION 内容 ─────────────────────────

def test_pov_lock_instruction_has_three_rules():
    """POV 锁定指令必须含 3 条规则:单视角默认 + 多视角标注 + 反模式。"""
    assert "默认第一人称 POV 锁定主角" in POV_LOCK_INSTRUCTION
    assert "POV 切换 → {角色名}" in POV_LOCK_INSTRUCTION or "【POV 切换" in POV_LOCK_INSTRUCTION
    assert "上帝视角严禁" in POV_LOCK_INSTRUCTION


def test_pov_lock_instruction_warns_against_excessive_switching():
    """必须显式警告 ≤ 2 次切换的反模式。"""
    assert "≤ 2 次" in POV_LOCK_INSTRUCTION
    assert "反模式" in POV_LOCK_INSTRUCTION


# ─── 2. writer 注入 ─────────────────────────

def _base_setup():
    return {
        "task": {
            "chapter_number": 5,
            "chapter_role": "发展",
            "chapter_goal": "测试",
            "main_characters": ["林渊"],
            "ending_hook_type": "悬念钩",
            "target_length": "2000-2200",
            "audit_mode": "full",
            "is_final_chapter": False,
        },
        "ctx": {
            "protagonist_level": "感债者", "protagonist_points": 0,
            "inventory": [], "scene_location": "债街", "time_context": "黄昏",
            "last_chapter_ending": "", "recent_events": "",
            "character_states": {}, "active_threads": [],
            "relevant_forbidden": [], "foreshadowing_due_soon": [],
            "cold_summary": "", "style_samples": [],
        },
        "setting": {
            "protagonist": {"name": "林渊"},
            "genre": "玄幻",
            "key_characters": [],
            "world_setting": None,
            "power_system": {},
        },
    }


def test_writer_prompt_includes_pov_lock():
    """writer user_prompt 必须含 POV_LOCK_INSTRUCTION。"""
    s = _base_setup()
    sys_d, usr_p = build_writer_prompt(s["task"], s["ctx"], s["setting"])
    assert "POV 视角锁定约束" in usr_p
    assert "第一人称 POV 锁定主角" in usr_p


def test_writer_prompt_pov_lock_after_methodology():
    """POV_LOCK_INSTRUCTION 必须在 methodology_block 之后(写作前最后约束)。"""
    s = _base_setup()
    sys_d, usr_p = build_writer_prompt(s["task"], s["ctx"], s["setting"])
    pov_idx = usr_p.find("POV 视角锁定约束")
    methodology_idx = usr_p.find("方法论执行清单")
    assert methodology_idx != -1 and pov_idx != -1
    assert methodology_idx < pov_idx


# ─── 3. detect_pov_switching ─────────────────────────

def test_detect_zero_switches():
    """无切换标记 → count=0。"""
    text = "林渊走进房间,看见桌上放着一封信。"
    count, switches = detect_pov_switching(text)
    assert count == 0
    assert switches == []


def test_detect_one_switch():
    """1 个切换标记。"""
    text = "林渊听到声响。【POV 切换 → 苏晚栀】她正在窗边看信。"
    count, switches = detect_pov_switching(text)
    assert count == 1
    assert switches == ["苏晚栀"]


def test_detect_multiple_switches():
    """3 个切换。"""
    text = (
        "【POV 切换 → 林渊】他进门。\n"
        "【POV 切换 → 苏晚栀】她抬头。\n"
        "【POV 切换 → 孟浩】他冷笑。\n"
    )
    count, switches = detect_pov_switching(text)
    assert count == 3
    assert set(switches) == {"林渊", "苏晚栀", "孟浩"}


def test_detect_switch_arrow_variants():
    """支持 → 和 -> 两种箭头。"""
    for sep in ["→", "->", "→", "-"]:
        text = f"测试。【POV 切换 {sep} 林渊】继续。"
        count, _ = detect_pov_switching(text)
        assert count >= 1, f"arrow {sep!r} not recognized"


def test_detect_ignores_unmarked_perspective_change():
    """无标记的视角切换不算(如突然写另一角色所见,不该计 POV 切换)。"""
    text = (
        "林渊走进屋,看见桌上的信。他不知道,\n"
        "此时苏晚栀正在隔壁听着动静。"
    )
    count, _ = detect_pov_switching(text)
    assert count == 0  # 没有显式标注


# ─── 4. 阈值常量 ─────────────────────────

def test_warning_threshold_is_2():
    """默认章节 ≤ 2 次 POV 切换。"""
    assert POV_SWITCH_WARNING_THRESHOLD == 2


def test_multi_threshold_is_3():
    """多视角章节(pov_multi=True) ≤ 3 次。"""
    assert POV_SWITCH_MULTI_THRESHOLD == 3


def test_warning_less_than_multi():
    assert POV_SWITCH_WARNING_THRESHOLD < POV_SWITCH_MULTI_THRESHOLD


# ─── 5. run_normalizer 集成 POV 检测 ─────────────────────────

def test_run_normalizer_pov_excessive_default():
    """默认任务(非多视角)3 次切换 → 应触发 POV 超限 issue。"""
    text = (
        "【POV 切换 → 林渊】他走进屋。\n"
        "【POV 切换 → 苏晚栀】她抬头。\n"
        "【POV 切换 → 孟浩】他冷笑。\n"
    )
    task = {"target_length": "2000"}
    clean_text, issues, cost = run_normalizer(text, task)
    pov_issues = [i for i in issues if "POV 视角切换超限" in i]
    assert len(pov_issues) == 1
    assert "pov_switches=3" in pov_issues[0]


def test_run_normalizer_pov_multi_relaxes_threshold():
    """pov_multi=True 时 3 次切换不触发警告。"""
    text = (
        "【POV 切换 → 林渊】他走进屋。\n"
        "【POV 切换 → 苏晚栀】她抬头。\n"
        "【POV 切换 → 孟浩】他冷笑。\n"
    )
    task = {"target_length": "2000", "pov_multi": True}
    clean_text, issues, cost = run_normalizer(text, task)
    pov_issues = [i for i in issues if "POV 视角切换超限" in i]
    assert len(pov_issues) == 0


def test_run_normalizer_pov_within_threshold_no_issue():
    """≤ 2 次切换 → 不触发 POV 警告。"""
    text = (
        "【POV 切换 → 林渊】他走进屋。\n"
        "【POV 切换 → 苏晚栀】她抬头。\n"
    )
    task = {"target_length": "2000"}
    clean_text, issues, cost = run_normalizer(text, task)
    pov_issues = [i for i in issues if "POV" in i]
    assert len(pov_issues) == 0


# ─── 6. regex 格式 ─────────────────────────

def test_pov_marker_regex_pattern():
    """POV_SWITCH_MARKER 应严格匹配标准格式。"""
    valid = [
        "【POV 切换 → 林渊】",
        "【 POV 切换 → 苏晚栀 】",
        "【POV切换→林渊】",
        "【 POV 切换 -> 林渊 】",
    ]
    for s in valid:
        m = POV_SWITCH_MARKER.findall(s)
        assert len(m) >= 1, f"should match: {s!r}"