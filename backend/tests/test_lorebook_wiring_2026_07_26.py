"""test_lorebook_wiring_2026_07_26.py

架构审视 — 把关键词世界书接进写作回路（检索层补齐）。

背景：
`engine/memory/lorebook.py` 写好了、带 21 个离线测试，但文件头明确标注
"OFFLINE ONLY / NOT wired into the writer prompt" —— 从未接进写作回路。
引擎的写作上下文只有 L2 摘要 + 人物状态的**子串匹配**，没有任何按需检索。
同类长篇写作项目的共识是「摘要 + 检索 + 图谱」多层记忆，检索层缺失正是
几十万字后设定漂移的主因。

本次接线：
- `build_lorebook_from_setting()` 从 setting_package 派生词条
  （主角 / 关键配角 / 表里世界 / 力量体系与各等级 / 货币），零新字段零新依赖
- `writer._build_lorebook_block()` 用「本章要写什么 + 最近发生了什么」做触发
  查询，命中的才注入，总量受 LOREBOOK_BUDGET_CHARS 限制

与既有【世界观速览】块的分工：速览给总貌（固定内容），世界书给本章真正会
用到的设定原文（相关性驱动、认别名、预算受控）。
"""
from __future__ import annotations

import pytest

from engine.agents.writer import (
    LOREBOOK_BUDGET_CHARS,
    _build_lorebook_block,
    build_writer_prompt,
)
from engine.memory.lorebook import build_lorebook_from_setting, match


def _setting() -> dict:
    return {
        "genre": "西幻",
        "protagonist": {
            "name": "艾德里安", "background": "落魄贵族之子，家族因债务失势",
            "personality": "隐忍", "initial_power_level": "学徒",
            "speech_quirks": ["以剑起誓"],
        },
        "world_setting": {
            "surface_world_name": "阿斯特兰王国", "hidden_world_name": "深渊回廊",
            "hidden_world_history": "千年前龙裔与人类缔约，回廊自此封闭",
        },
        "power_system": {
            "name": "魔纹", "currency": "魔石", "description": "以血脉刻纹驱动",
            "levels": [
                {"name": "学徒", "description": "仅能点燃一枚魔纹"},
                {"name": "法师", "description": "可同时驱动三纹"},
                {"name": "大魔导", "description": "纹路自行流转"},
            ],
        },
        "key_characters": [
            {"name": "艾德里安", "role": "主角"},
            {"name": "莉拉", "role": "女主", "background": "精灵游侠",
             "speech_quirks": ["风会指引"]},
            {"name": "凯恩", "role": "反派", "background": "王国财政官"},
        ],
    }


def _keys(setting):
    return {e["key"] for e in build_lorebook_from_setting(setting)}


# ─── 1. 从 setting 派生词条 ─────────────────────────

def test_derives_all_entity_kinds():
    keys = _keys(_setting())
    for expected in ("艾德里安", "莉拉", "凯恩", "阿斯特兰王国", "深渊回廊",
                     "魔纹", "学徒", "法师", "大魔导", "魔石"):
        assert expected in keys, f"缺少词条 {expected}"


def test_protagonist_outranks_side_characters():
    """预算不够时主角必须先占位。"""
    book = {e["key"]: e for e in build_lorebook_from_setting(_setting())}
    assert book["艾德里安"]["priority"] > book["莉拉"]["priority"]


def test_protagonist_entry_wins_over_duplicate_in_key_characters():
    """主角同时出现在 protagonist 和 key_characters 里时只留一条（取高优先级）。"""
    entries = build_lorebook_from_setting(_setting())
    mine = [e for e in entries if e["key"] == "艾德里安"]
    assert len(mine) == 1
    assert "落魄贵族之子" in mine[0]["content"]  # 来自 protagonist 而非 key_characters


def test_power_system_aliases_are_level_names():
    """提到某个等级也应能触发整个体系词条。"""
    book = {e["key"]: e for e in build_lorebook_from_setting(_setting())}
    assert set(book["魔纹"]["aliases"]) >= {"学徒", "法师", "大魔导"}


def test_level_entry_records_position_in_ladder():
    book = {e["key"]: e for e in build_lorebook_from_setting(_setting())}
    assert "第 2 级/共 3 级" in book["法师"]["content"]


def test_unique_elements_are_not_turned_into_entries():
    """整句描述当关键词匹配不到东西，只会白占预算 —— 刻意不做成词条。"""
    s = _setting()
    s["world_setting"]["unique_elements"] = ["债务可以具象化为实体"]
    assert "债务可以具象化为实体" not in _keys(s)


@pytest.mark.parametrize("bad", [None, {}, [], "字符串", 42])
def test_malformed_setting_yields_no_crash(bad):
    assert build_lorebook_from_setting(bad) == [] or isinstance(
        build_lorebook_from_setting(bad), list)


def test_entries_without_content_are_dropped():
    """只有名字没有任何内容的角色不该占预算。"""
    s = {"key_characters": [{"name": "无名氏"}]}
    assert "无名氏" not in _keys(s)


def test_missing_sections_are_tolerated():
    keys = _keys({"protagonist": {"name": "甲", "background": "背景"}})
    assert keys == {"甲"}


# ─── 2. 触发查询：命中什么、不命中什么 ─────────────────────────

def _block(task, ctx):
    return _build_lorebook_block(task, ctx, _setting())


def test_triggers_on_main_characters():
    blk = _block({"main_characters": ["莉拉"]}, {})
    assert "莉拉" in blk


def test_triggers_on_chapter_goal():
    blk = _block({"chapter_goal": "在深渊回廊入口汇合"}, {})
    assert "深渊回廊" in blk


def test_triggers_on_core_conflict():
    blk = _block({"core_conflict": "凯恩派人封锁回廊"}, {})
    assert "凯恩" in blk


def test_triggers_on_recent_events_from_context():
    blk = _block({}, {"recent_events": "艾德里安晋升法师"})
    assert "法师" in blk


def test_alias_hit_pulls_in_power_system():
    """只提到「法师」也该带出「魔纹」体系（别名触发）。"""
    blk = _block({}, {"recent_events": "他成了法师"})
    assert "魔纹" in blk


def test_unmentioned_entities_are_not_injected():
    """核心价值：没提到的设定不进 prompt，否则等于全量灌。"""
    blk = _block({"main_characters": ["莉拉"]}, {})
    assert "阿斯特兰王国" not in blk
    assert "大魔导" not in blk


def test_empty_query_injects_nothing():
    assert _build_lorebook_block({}, {}, _setting()) == ""


def test_empty_setting_injects_nothing():
    assert _build_lorebook_block({"main_characters": ["莉拉"]}, {}, {}) == ""


def test_block_failure_degrades_without_breaking_writer(monkeypatch):
    """世界书是增强项，坏了也不能阻断写作。"""
    import engine.memory.lorebook as lb
    monkeypatch.setattr(lb, "build_lorebook_from_setting",
                        lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _build_lorebook_block({"main_characters": ["莉拉"]}, {}, _setting()) == ""


# ─── 3. 预算 ─────────────────────────

def test_injection_respects_budget():
    big = _setting()
    big["key_characters"] += [
        {"name": f"配角{i}", "role": "路人", "background": "背" * 200}
        for i in range(20)
    ]
    query = " ".join(f"配角{i}" for i in range(20)) + " 莉拉 凯恩"
    hits = match(build_lorebook_from_setting(big), query, budget=LOREBOOK_BUDGET_CHARS)
    total = sum(len(h["content"]) + len(h["key"]) for h in hits)
    assert total <= LOREBOOK_BUDGET_CHARS


# ─── 4. 接进 writer prompt ─────────────────────────

def _prompt():
    task = {
        "chapter_number": 7, "chapter_goal": "在深渊回廊夺回家族徽记",
        "core_conflict": "凯恩派人封锁回廊",
        "main_characters": ["艾德里安", "莉拉"], "target_length": "2000-2200",
    }
    ctx = {"recent_events": "艾德里安晋升法师", "last_chapter_ending": "魔石耗尽"}
    return build_writer_prompt(task, ctx, _setting())[1]


def test_writer_prompt_contains_lorebook_section():
    assert "【本章相关设定" in _prompt()


def test_writer_prompt_lorebook_lists_triggered_entities():
    usr = _prompt()
    for name in ("艾德里安", "莉拉", "凯恩", "深渊回廊", "魔石"):
        assert name in usr


def test_writer_prompt_still_builds_without_lorebook_data():
    """setting 里没有可派生词条时，prompt 照常生成、不留空标题。"""
    usr = build_writer_prompt({"chapter_number": 1}, {}, {})[1]
    assert "【本章相关设定" not in usr
    assert "【主角状态】" in usr


def test_lorebook_block_precedes_protagonist_state():
    """设定原文要排在主角状态之前（先给约束，再给当前值）。"""
    usr = _prompt()
    assert usr.index("【本章相关设定") < usr.index("【主角状态】")


def test_lorebook_does_not_reintroduce_cross_project_leak():
    """回归护栏：接世界书不得把别的项目专名带回 prompt。"""
    usr = _prompt()
    for token in ("林渊", "苏晚栀", "孟浩", "顾青锋", "云州"):
        assert token not in usr
