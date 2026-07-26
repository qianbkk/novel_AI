"""test_writer_prompt_no_project_leak_2026_07_26.py

架构审视 — writer prompt 跨项目专名污染修复。

背景:
`build_writer_prompt` 的【世界观设定一致性硬约束】块曾把 2026-07 那次 30 章实测
项目（「云州」宇宙）的角色名写死在 f-string 里:

    - 本章必须严格使用上面【关键人物】列出的角色名（林渊 / 苏晚栀 / 孟浩 / 顾青锋 等）

`prompt_templates.POV_LOCK_INSTRUCTION` 同样写死了「林渊看到/听见/想到」,
`_surface_world_name` 的 fallback 是「云州」。这三处都会随每章 prompt 发给真实
LLM —— 一本西幻/科幻小说会同时收到「关键人物是艾德里安/莉拉」和「必须严格使用
林渊/苏晚栀」两条互相矛盾的硬约束,直接诱导 LLM 串味写出别的书的专名。

修复: 三处全部按 setting 动态渲染,缺失时降级为不提任何专名的中性约束。

覆盖:
- 与测试项目完全无关的题材(西幻/科幻)不得出现「云州」宇宙任何专名
- 硬约束块渲染的是本项目真实角色名
- POV 指令渲染的是本项目真实主角名
- setting 为空 / 字段缺失时不得凭空发明世界名或角色名
"""
from __future__ import annotations

import pytest

from engine.agents.writer import build_writer_prompt
from engine.config.prompt_templates import (
    POV_LOCK_INSTRUCTION,
    get_pov_lock_instruction,
)

# 2026-07 30 章实测项目的专名。任何其它项目的 prompt 里都不该出现这些字符串。
LEAK_TOKENS = ["林渊", "苏晚栀", "孟浩", "顾青锋", "云州"]


def _fantasy_setting() -> dict:
    """一本跟「云州」宇宙毫无关系的西幻小说。"""
    return {
        "genre": "西幻",
        "protagonist": {"name": "艾德里安"},
        "world_setting": {
            "surface_world_name": "阿斯特兰王国",
            "hidden_world_name": "深渊回廊",
            "unique_elements": ["龙裔血脉"],
        },
        "power_system": {
            "name": "魔纹",
            "levels": [{"name": "学徒"}, {"name": "法师"}],
            "currency": "魔石",
        },
        "key_characters": [
            {"name": "艾德里安", "role": "主角", "background": "落魄贵族"},
            {"name": "莉拉", "role": "女主", "background": "精灵游侠"},
        ],
    }


def _fantasy_task() -> dict:
    return {
        "chapter_number": 7,
        "chapter_role": "发展",
        "chapter_goal": "夺回家族徽记",
        "main_characters": ["艾德里安", "莉拉"],
        "ending_hook_type": "悬念钩",
        "target_length": "2000-2200",
    }


# ─── 1. 跨项目专名不得泄漏 ─────────────────────────

@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_fantasy_prompt_has_no_test_project_proper_noun(token):
    """西幻项目的 prompt(system + user)不得含「云州」宇宙任何专名。"""
    sys_d, usr_p = build_writer_prompt(_fantasy_task(), {}, _fantasy_setting())
    assert token not in usr_p, f"user_prompt 泄漏了测试项目专名: {token}"
    assert token not in sys_d, f"system_prompt 泄漏了测试项目专名: {token}"


@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_empty_setting_prompt_has_no_test_project_proper_noun(token):
    """setting 全空时也不得凭空发明「云州」宇宙的专名(旧 fallback 是 '云州')。"""
    sys_d, usr_p = build_writer_prompt({"chapter_number": 1}, {}, {})
    assert token not in usr_p
    assert token not in sys_d


def test_pov_lock_constant_itself_has_no_hardcoded_name():
    """常量本身也不能残留写死的角色名(它会被别处直接引用)。"""
    for token in LEAK_TOKENS:
        assert token not in POV_LOCK_INSTRUCTION


# ─── 2. 渲染的是本项目真实专名 ─────────────────────────

def test_consistency_block_lists_actual_roster():
    """硬约束块必须列出本项目真实角色名。"""
    _, usr_p = build_writer_prompt(_fantasy_task(), {}, _fantasy_setting())
    assert "【世界观设定一致性硬约束】" in usr_p
    assert "艾德里安" in usr_p
    assert "莉拉" in usr_p


def test_consistency_block_names_actual_surface_world():
    """世界名约束必须引用本项目的表世界名。"""
    _, usr_p = build_writer_prompt(_fantasy_task(), {}, _fantasy_setting())
    assert "阿斯特兰王国" in usr_p


def test_pov_instruction_uses_actual_protagonist():
    """POV 指令必须渲染本项目主角名。"""
    _, usr_p = build_writer_prompt(_fantasy_task(), {}, _fantasy_setting())
    assert "艾德里安看到/听见/想到" in usr_p


def test_protagonist_is_first_in_roster():
    """主角必须排在角色名单首位(writer 据此判断谁是视角人物)。"""
    setting = _fantasy_setting()
    task = _fantasy_task()
    task["main_characters"] = ["莉拉"]  # 主角不在 main_characters 里
    _, usr_p = build_writer_prompt(task, {}, setting)
    line = next(ln for ln in usr_p.splitlines() if "必须严格使用" in ln)
    assert line.index("艾德里安") < line.index("莉拉")


def test_roster_deduplicates_across_sources():
    """main_characters 与 key_characters 重叠时不得重复列名。"""
    _, usr_p = build_writer_prompt(_fantasy_task(), {}, _fantasy_setting())
    line = next(ln for ln in usr_p.splitlines() if "必须严格使用" in ln)
    assert line.count("艾德里安") == 1
    assert line.count("莉拉") == 1


# ─── 3. 字段缺失时的降级 ─────────────────────────

def test_missing_surface_world_degrades_to_neutral_constraint():
    """没有表世界名时用中性措辞,不得发明一个世界名。"""
    setting = _fantasy_setting()
    setting["world_setting"] = {}
    _, usr_p = build_writer_prompt(_fantasy_task(), {}, setting)
    assert "表世界「" not in usr_p
    assert "必须原样复用上文给出的名称" in usr_p


def test_missing_protagonist_degrades_to_generic_word():
    """拿不到主角名时 POV 指令退回中性的「主角」。"""
    setting = _fantasy_setting()
    setting["protagonist"] = {}
    _, usr_p = build_writer_prompt({"chapter_number": 3}, {}, setting)
    assert "主角看到/听见/想到" in usr_p


def test_world_setting_none_still_renders_constraint_block():
    """world_setting=None(既有回归场景)仍要渲染硬约束块,不得崩。"""
    setting = _fantasy_setting()
    setting["world_setting"] = None
    _, usr_p = build_writer_prompt(_fantasy_task(), {}, setting)
    assert "【世界观设定一致性硬约束】" in usr_p
    assert "严禁「吞设定」" in usr_p


# ─── 4. get_pov_lock_instruction 单元 ─────────────────────────

@pytest.mark.parametrize("name,expected", [
    ("艾德里安", "艾德里安看到/听见/想到"),
    ("", "主角看到/听见/想到"),
    ("   ", "主角看到/听见/想到"),
])
def test_get_pov_lock_instruction_renders(name, expected):
    assert expected in get_pov_lock_instruction(name)


def test_get_pov_lock_instruction_leaves_no_placeholder():
    """渲染后不得残留 {主角} 占位符。"""
    assert "{主角}" not in get_pov_lock_instruction("艾德里安")
    assert "{主角}" not in get_pov_lock_instruction("")


def test_get_pov_lock_instruction_keeps_all_rules():
    """替换占位符不能破坏原有 3 条规则。"""
    out = get_pov_lock_instruction("艾德里安")
    assert "默认第一人称 POV 锁定主角" in out
    assert "上帝视角严禁" in out
    assert "反模式" in out
