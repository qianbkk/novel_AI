"""test_lorebook_aliases_expansion_2026_08_17.py

P2-12b 修复验证：lorebook 必须把 character 的 speech_quirks / catchphrase
当 aliases，触发命中时召回角色卡。

历史 bug（审计发现）：
- lorebook 词条 key 只有 Character.name；speech_quirks（口癖）只塞进 content
  （"口癖：这局我来开局/..."），不参与 match 检索。
- 影响：林渊的口头禅"这局我来开局"出现在正文里 → lorebook.match 找不到
  「林渊」这条 → writer 拿不到角色卡 → 同一角色 100 章后设定漂移。
- 网文角色多有别称/绰号/口癖（师尊/老头/前辈/老不死等），aliases 是
  NovelAI / Novelcrafter 等同类项目的标配。

修复（任务 P2-12b 2026-08-17）：
- build_lorebook_from_setting 把 character.speech_quirks + 任何 character.aliases
  字段提到 aliases 列表（不只塞 content）
- match 已经支持 aliases 检索（lorebook.py:196-202），无需改 match
"""

from __future__ import annotations

import pytest


# ── 1. build_lorebook_from_setting 必须把 speech_quirks 当 aliases ─────────────────

def test_lorebook_includes_speech_quirks_as_aliases():
    """角色 speech_quirks（口癖）必须作为 aliases 出现在 lorebook 词条，
    让正文里的口癖触发能召回角色卡。"""
    from engine.memory.lorebook import build_lorebook_from_setting

    setting = {
        "protagonist": {"name": "林渊"},
        "key_characters": [
            {
                "name": "林渊",
                "role": "主角",
                "background": "落魄贵族",
                "speech_quirks": ["这局我来开局", "老办法"],
            },
        ],
        "world_setting": {
            "surface_world_name": "云州",
            "hidden_world_name": "深渊回廊",
        },
    }

    book = build_lorebook_from_setting(setting)

    # 找林渊的词条（PRIORITY_PROTAGONIST=5）
    lin_yuan = [e for e in book if e["key"] == "林渊"]
    assert len(lin_yuan) >= 1, f"林渊词条缺失，book: {book}"
    aliases = lin_yuan[0].get("aliases", []) or []
    assert "这局我来开局" in aliases, (
        f"林渊的 speech_quirks 必须作为 aliases，正文出现口癖时才能召回角色卡；"
        f"实际 aliases: {aliases}"
    )
    assert "老办法" in aliases


def test_lorebook_aliases_excludes_self_key():
    """aliases 列表里不能含 key 自己（match 时已包含，避免重复匹配）。"""
    from engine.memory.lorebook import build_lorebook_from_setting

    setting = {
        "protagonist": {"name": "主角"},
        "key_characters": [
            {
                "name": "配角",
                "speech_quirks": ["配角", "哎"],  # "配角" === name，应被过滤
            },
        ],
    }
    book = build_lorebook_from_setting(setting)
    pei = [e for e in book if e["key"] == "配角"][0]
    aliases = pei.get("aliases", []) or []
    assert "配角" not in aliases, (
        f"aliases 不应包含 key 自己（match 已包含 key），实际: {aliases}"
    )
    assert "哎" in aliases, "非自身的别名应保留"


# ── 2. match 用 aliases 触发 ─────────────────────────

def test_lorebook_match_triggers_via_speech_quirk_alias():
    """lorebook.match 必须让口癖作为别名触发，召回角色卡。"""
    from engine.memory.lorebook import build_lorebook_from_setting, match

    setting = {
        "protagonist": {"name": "林渊"},
        "key_characters": [
            {
                "name": "林渊",
                "role": "主角",
                "background": "落魄贵族后裔",
                "speech_quirks": ["这局我来开局"],
            },
        ],
    }
    book = build_lorebook_from_setting(setting)

    # 正文含口癖（不含角色名）
    text = "他说：「这局我来开局。」 众人后后沉默。"
    hits = match(book, text, budget=2000)

    # 林渊应被召回（哪怕正文不含"林渊"三个字）
    assert any(h["key"] == "林渊" for h in hits), (
        f"口癖 '这局我来开局' 在正文里必须触发林渊词条召回，"
        f"实际 hits: {[h['key'] for h in hits]}"
    )


# ── 3. character.aliases 字段支持（如果 setting 提供）））

def test_lorebook_includes_character_aliases_field():
    """如果 character.aliases 字段存在，必须作为 aliases（双兜底）。"""
    from engine.memory.lorebook import build_lorebook_from_setting

    setting = {
        "key_characters": [
            {
                "name": "逍遥兄",
                "aliases": ["林渊", "林兄"],  # 角色别名表
                "background": "...",
            },
        ],
    }
    book = build_lorebook_from_setting(setting)
    e = [b for b in book if b["key"] == "逍遥兄"][0]
    aliases = e.get("aliases", []) or []
    assert "林渊" in aliases
    assert "林兄" in aliases