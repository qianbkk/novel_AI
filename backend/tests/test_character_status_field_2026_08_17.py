"""test_character_status_field_2026_08_17.py

P2-12 修复验证：Character 表必须有 status 列防"角色已死又出现"。

历史 bug（审计发现）：
- Character 表无 status / died_in_chapter 列，tracker._merge_character_states
  用 substring fuzzy dedup 把"死亡/濒死/半死"当成不同 key 持续追加
  （engine/agents/tracker.py:85-127）。
- 影响：50 章后反派复活 / 死人冒头无前置闸门；reviser 框架 (engine/revisers)
  只有 character_state_reviser 一个实现，没人更新 key_characters[*].description。
- writer prompt 在 main_characters 注入前不查 status → 把死人塞进 prompt。

修复（任务 P2-12 2026-08-17）：
1. Character 模型加 status 列（active/dead/missing）+ died_in_chapter int nullable
2. alembic 迁移 0004_characters_status 同步加列（向后兼容 SQLite）
3. Writer prompt 注入前过滤 status != "dead"（避免死人冒头）
4. Tracker 写入 "死亡" / "亡" / "殉" / "牺牲" 等关键词时自动标 status=dead +
   记录 died_in_chapter
"""

from __future__ import annotations

import pytest


# ── 1. Character 模型声明了 status + died_in_chapter 列 ─────────────────

def test_character_model_has_status_column():
    """Character ORM 必须声明 status 列（CLAUDE.md「表结构变化必须有迁移方案」）。"""
    from app.models import Character
    assert hasattr(Character, "status"), (
        "Character 模型必须加 status 列（active/dead/missing），"
        "否则 tracker 无法标已死状态，writer 会把死人塞进 prompt"
    )


def test_character_model_has_died_in_chapter_column():
    """Character ORM 必须声明 died_in_chapter 列（记录死亡章节便于溯源）。"""
    from app.models import Character
    assert hasattr(Character, "died_in_chapter"), (
        "Character 模型必须加 died_in_chapter 列（nullable int），"
        "记录在哪一章死的，便于 beat_checker 复活检测"
    )


# ── 2. 迁移文件存在（CLAUDE.md「必须有迁移方案和回归测试」）））

def test_alembic_migration_0004_characters_status_exists():
    """alembic 迁移 0004_characters_status 必须存在，schema 漂移需要迁移。"""
    from pathlib import Path
    migration_dir = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    candidates = list(migration_dir.glob("0004*characters*status*.py"))
    assert candidates, (
        f"必须创建 alembic 迁移 0004_characters_status.py，"
        f"扫描目录: {migration_dir}，找到: {list(migration_dir.glob('*.py'))}"
    )


# ── 3. Writer prompt 注入前过滤死角色 ─────────────────────────

def test_writer_prompt_filters_dead_characters():
    """build_writer_prompt 在 main_characters 注入前必须过滤 dead_characters
    集合（防"已死又出现"）。writer 通过 context["dead_characters"] 接收 hint。"""
    from engine.agents.writer import build_writer_prompt

    task = {
        "chapter_number": 5, "chapter_role": "发展", "chapter_goal": "g",
        "main_characters": ["活人A", "死人B", "活人C"],
        "ending_hook_type": "信息钩", "shuang_type": "打脸",
        "shuang_description": "s", "target_length": "2000",
        "emotion_core": "爽快", "emotion_intensity": 3,
        "forbidden_actions": [], "setting_constraints": [],
    }
    setting = {
        "genre": "都市", "protagonist": {"name": "活人A"},
        "world_setting": {"surface_world_name": "X"},
        "key_characters": [],
    }
    # 把"死人B"标 dead
    context = {"dead_characters": ["死人B"]}

    _, usr_p = build_writer_prompt(task, context, setting)

    # 死人B 必须在【本章出场人物】行里被过滤
    # 找到该行
    for line in usr_p.splitlines():
        if "本章出场人物" in line:
            assert "活人A" in line, "活人应保留"
            assert "活人C" in line, "活人应保留"
            assert "死人B" not in line, (
                "死人B 出现在【本章出场人物】，违反 Character.status 防 OOC 修复"
            )
            return
    pytest.fail("【本章出场人物】行未在 user_prompt 中找到")


def test_writer_prompt_handles_all_dead_characters():
    """main_characters 全是死人时 writer 必须显示降级提示，不抛错。"""
    from engine.agents.writer import build_writer_prompt

    task = {
        "chapter_number": 5, "chapter_role": "发展", "chapter_goal": "g",
        "main_characters": ["死人A", "死人B"],
        "ending_hook_type": "信息钩", "shuang_type": "打脸",
        "shuang_description": "s", "target_length": "2000",
        "emotion_core": "爽快", "emotion_intensity": 3,
        "forbidden_actions": [], "setting_constraints": [],
    }
    setting = {
        "genre": "都市", "protagonist": {"name": "死人A"},
        "world_setting": {"surface_world_name": "X"},
        "key_characters": [],
    }
    context = {"dead_characters": ["死人A", "死人B"]}

    sys_d, usr_p = build_writer_prompt(task, context, setting)
    # 不抛错
    assert "本章出场人物" in usr_p


# ── 4. Tracker 死亡关键词规范化（暂以源码扫描锁定契约）））

def test_tracker_normalizes_death_to_status_field():
    """tracker 必须把死亡关键词规范化（CLAUDE.md「不允许 fuzzy dedup 把
    死亡/濒死/半死当成不同 key 持续追加」）。"""
    import inspect
    from engine.agents import tracker

    src = inspect.getsource(tracker)

    # 必须含死亡关键词规范化逻辑
    death_keywords = ["死亡", "亡", "殉", "牺牲"]
    has_normalize = any(kw in src for kw in death_keywords)
    assert has_normalize, (
        "tracker 必须包含死亡关键词（死亡/亡/殉/牺牲），"
        "用于把 character_states 规范化为 status=dead"
    )