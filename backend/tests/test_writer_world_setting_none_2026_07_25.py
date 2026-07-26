"""test_writer_world_setting_none_2026_07_25.py

回归测试：修 bug 后，setting.world_setting 为 None / 空 dict 时
build_writer_prompt 不再抛 TypeError（之前 f-string 中 `{{}}` 触发的 unhashable type: 'dict'）。

详见 docs/wiki/03-Writing-Engine.md §已知方法论 gap 与补全计划 §3 R-Prompt 护栏。
"""
from __future__ import annotations

from engine.agents.writer import build_writer_prompt


def _make_minimal_context():
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


def _make_minimal_task(is_final: bool = False):
    return {
        "chapter_number": 5,
        "chapter_role": "发展",
        "chapter_goal": "测试",
        "main_characters": ["林渊"],
        "ending_hook_type": "悬念钩",
        "target_length": "2000-2200",
        "audit_mode": "full",
        "is_final_chapter": is_final,
    }


def test_build_writer_prompt_with_world_setting_none():
    """setting.world_setting = None 时不应抛 TypeError（原 bug）。"""
    task = _make_minimal_task()
    setting = {
        "protagonist": {"name": "林渊"},
        "genre": "玄幻",
        "key_characters": [],
        "world_setting": None,  # 关键：原 bug 在这触发
        "power_system": {},
    }
    sys_d, usr_p = build_writer_prompt(task, _make_minimal_context(), setting)
    assert isinstance(sys_d, str)
    assert isinstance(usr_p, str)
    # 2026-07-26：原断言是 `"表世界「云州」" in usr_p`，锁定的是一个缺陷 ——
    # 缺表世界名时 fallback 到测试项目的「云州」，会把别的书的专名塞进任意项目的
    # prompt。现在改为降级成不提任何专名的中性约束。
    # 见 test_writer_prompt_no_project_leak_2026_07_26.py。
    assert "表世界「" not in usr_p
    assert "必须原样复用上文给出的名称" in usr_p


def test_build_writer_prompt_with_world_setting_empty_dict():
    """setting.world_setting = {} 时也不应抛 TypeError。"""
    task = _make_minimal_task()
    setting = {
        "protagonist": {"name": "林渊"},
        "genre": "玄幻",
        "key_characters": [],
        "world_setting": {},
        "power_system": {},
    }
    sys_d, usr_p = build_writer_prompt(task, _make_minimal_context(), setting)
    # 同上：空 dict 也不得发明「云州」，降级为中性约束
    assert "表世界「" not in usr_p
    assert "必须原样复用上文给出的名称" in usr_p


def test_build_writer_prompt_with_world_setting_populated():
    """setting.world_setting.surface_world_name 有值时,渲染该值。"""
    task = _make_minimal_task()
    setting = {
        "protagonist": {"name": "林渊"},
        "genre": "玄幻",
        "key_characters": [],
        "world_setting": {"surface_world_name": "九州", "hidden_world_name": "九幽"},
        "power_system": {},
    }
    sys_d, usr_p = build_writer_prompt(task, _make_minimal_context(), setting)
    assert "表世界「九州」" in usr_p
    # 隐藏世界也应该在 system_dynamic 里
    assert "九幽" in sys_d or "九幽" in usr_p


def test_build_writer_prompt_final_chapter_no_methodology():
    """终章场景应只保留三层期待感(不包含但是法则/信息差),回归 Commit 0 的终章降级。"""
    task = _make_minimal_task(is_final=True)
    setting = {
        "protagonist": {"name": "林渊"},
        "genre": "玄幻",
        "key_characters": [],
        "world_setting": None,  # 也测 None
        "power_system": {},
    }
    sys_d, usr_p = build_writer_prompt(task, _make_minimal_context(), setting)
    assert "终章要求" in usr_p
    assert "三层期待感" in usr_p
    # 终章降级:不包含但是法则/信息差/模块化叙事
    assert "但是法则" not in usr_p
    assert "信息差三模式" not in usr_p
    assert "模块化叙事" not in usr_p


def test_build_writer_prompt_non_final_chapter_has_all_four_methodology():
    """非终章应注入全套 4 招方法论。"""
    task = _make_minimal_task(is_final=False)
    setting = {
        "protagonist": {"name": "林渊"},
        "genre": "玄幻",
        "key_characters": [],
        "world_setting": None,
        "power_system": {},
    }
    sys_d, usr_p = build_writer_prompt(task, _make_minimal_context(), setting)
    assert "但是法则" in usr_p
    assert "信息差三模式" in usr_p
    assert "三层期待感" in usr_p
    assert "模块化叙事" in usr_p
    # 必须有方法论 header
    assert "方法论执行清单" in usr_p