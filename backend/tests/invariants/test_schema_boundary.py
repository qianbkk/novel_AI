"""Schema 边界语料与错误定位（任务 01）

每个 schema 至少 3 个合法 + 5 个非法边界用例，覆盖 LLM 常见输出偏差：
- null、数字/数字字符串、缺必填字段、额外字段、空列表、错误枚举、markdown fence、中英文键混淆
- 校验错误必须包含可定位的 JSON path

语料以内联 dict 为主；少数 markdown fence / 中文键混淆场景用 JSON fixture
放在 tests/fixtures/schema_samples/ 下，方便人工审阅。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.schema_validator import (
    SchemaError,
    validate_chapter_meta,
    validate_character_card,
    validate_entity_relation_rich,
    validate_setting_package,
    validate_world_view_rich,
)


_Fixtures = Path(__file__).resolve().parent.parent / "fixtures" / "schema_samples"


# ──────────────────────────────────────────────────────────────────────
# 工具：合法 / 非法最小可包
# ──────────────────────────────────────────────────────────────────────


def _setting_minimum(**overrides):
    """最小合法的 setting_package（除 key_characters/arc_outline 外字段都填全）。"""
    base = {
        "novel_id": "n-001",
        "platform": "fanqie",
        "genre": "玄幻",
        "title_candidates": ["书A", "书B", "书C"],
        "tagline": "一句话简介",
        "protagonist": {
            "name": "林尘",
            "background": "出身平凡",
            "personality": "冷静",
            "awakening_trigger": "师父点化",
        },
        "world_setting": {
            "hidden_world_name": "九霄",
            "hidden_world_history": "x" * 60,
            "surface_world_name": "苍玄",
        },
        "power_system": {
            "name": "灵力",
            "levels": [{"level": 1, "name": "锻体"}],
            "currency": "元晶",
        },
        "key_characters": [{"name": "配角A"}],
        "arc_outline": [
            {"arc_id": 1, "arc_name": "弧1", "arc_goal": "起步", "estimated_chapters": 30},
        ],
        "foreshadowing_seeds": [{"content": "伏笔种子：老鬼的真正身份"}],
    }
    base.update(overrides)
    return base


def _chapter_meta_min(**over):
    base = {
        "chapter_number": 1,
        "chapter_role": "铺垫",
        "chapter_goal": "展现绝境",
        "score": 6.0,
        "word_count": 2000,
    }
    base.update(over)
    return base


def _worldview_min(**over):
    base = {
        "cosmos":     "蓝星与九天之上并存，" * 4,
        "geography":  "云州、临海、苍莽山脉，" * 4,
        "history":    "1984 年灵气潮汐以来，" * 4,
        "society":    "修士与凡人共治，九品分等，" * 3,
        "technology": "灵气耦合电路的混合科技，" * 3,
        "races":      "人族 / 古妖族 / 幽冥三族鼎立，" * 2,
        "customs":    "祭剑节 / 走火大会 / 长辈赐字，" * 2,
    }
    base.update(over)
    return base


def _character_card_min(**over):
    base = {
        "basic":       {"gender": "男", "age": 32, "identity": "云州林氏长子"},
        "appearance":  {"height": "182cm", "hair": "短黑"},
        "personality": {"tags": ["克制"], "summary": "外表冷峻内心压着火，" * 3},
        "background":  {"origin": "云州林氏", "motivation": "改写家族命运"},
        "abilities":   {"power_name": "先知回响", "current_tier": "一级"},
        "catchphrase": {"lines": ["这局我来开局。"]},
        "props":       {"signature_item": "老旧铜怀表"},
        "arc":         {"start_state": "破产边缘", "catalyst": "重生", "end_state": "领袖"},
    }
    base.update(over)
    return base


def _entity_relation_min(**over):
    base = {
        "mutual":    True,
        "intensity": 8,
        "tags":      ["亲密"],
        "evolution": [{"phase": "开端", "state": "陌生"}],
        "key_events": [{"chapter_hint": "第3章", "event": "初遇"}],
    }
    base.update(over)
    return base


# ──────────────────────────────────────────────────────────────────────
# setting_package：3 合法 + 5 非法
# ──────────────────────────────────────────────────────────────────────


VALID_SETTING = [
    pytest.param(_setting_minimum(), id="minimum"),
    pytest.param(
        _setting_minimum(
            protagonist={**_setting_minimum()["protagonist"], "age": 22},
        ),
        id="protagonist_age_int",
    ),
    pytest.param(
        _setting_minimum(
            foreshadowing_seeds=[
                {"content": "老鬼的真正身份", "target_arc": 1, "importance": "high"},
                {"content": "玄铁剑的来历谜", "target_arc": 2, "importance": "low"},
            ],
        ),
        id="foreshadowing_enums",
    ),
]


INVALID_SETTING = [
    pytest.param(
        {"platform": "fanqie"},  # 缺全部必填
        ["novel_id", "title_candidates", "tagline", "protagonist",
         "world_setting", "power_system", "key_characters",
         "arc_outline", "foreshadowing_seeds"],
        id="missing_many_required",
    ),
    pytest.param(
        _setting_minimum(platform="weibo"),  # 错枚举
        ["platform"],
        id="wrong_enum_platform",
    ),
    pytest.param(
        _setting_minimum(title_candidates=[]),  # 空列表 + minItems=1
        ["title_candidates"],
        id="empty_title_candidates",
    ),
    pytest.param(
        _setting_minimum(arc_outline=[]),  # 空列表 + minItems=1
        ["arc_outline"],
        id="empty_arc_outline",
    ),
    pytest.param(
        _setting_minimum(
            world_setting={
                "hidden_world_name": "x",
                "surface_world_name": "x",
                "hidden_world_history": "短",  # < minLength=50
            },
        ),
        ["world_setting", "hidden_world_history"],
        id="history_under_min_length",
    ),
]


@pytest.mark.parametrize("data", VALID_SETTING)
def test_setting_valid(data):
    """合法语料必须通过 schema 校验且不抛异常。"""
    validate_setting_package(data)  # 不抛 = 通过


@pytest.mark.parametrize("data,must_appear", INVALID_SETTING)
def test_setting_invalid_paths(data, must_appear):
    """非法语料必须抛 SchemaError，且错误信息含 JSON path。"""
    with pytest.raises(SchemaError) as exc:
        validate_setting_package(data)
    msg = str(exc.value)
    for token in must_appear:
        assert token in msg, f"错误信息应包含路径 token {token!r}, 实际:\n{msg}"


# ──────────────────────────────────────────────────────────────────────
# chapter_meta：3 合法 + 5 非法
# ──────────────────────────────────────────────────────────────────────


VALID_CHAPTER_META = [
    pytest.param(_chapter_meta_min(), id="minimum"),
    pytest.param(_chapter_meta_min(status="pass"), id="status_pass"),
    pytest.param(_chapter_meta_min(score=9.5, word_count=3500), id="high_score_long"),
]


INVALID_CHAPTER_META = [
    pytest.param({"score": 5.0}, ["chapter_number"], id="missing_chapter_number"),
    pytest.param(
        _chapter_meta_min(status="unknown"),
        ["status"],
        id="wrong_enum_status",
    ),
    pytest.param(
        _chapter_meta_min(chapter_number=0),
        ["chapter_number"],
        id="chapter_number_zero",
    ),
    pytest.param(
        _chapter_meta_min(score=11.0),
        ["score"],
        id="score_over_max",
    ),
    pytest.param(
        _chapter_meta_min(chapter_number="1"),  # 字符串而非整数
        ["chapter_number"],
        id="chapter_number_string",
    ),
]


@pytest.mark.parametrize("data", VALID_CHAPTER_META)
def test_chapter_meta_valid(data):
    validate_chapter_meta(data)


@pytest.mark.parametrize("data,must_appear", INVALID_CHAPTER_META)
def test_chapter_meta_invalid_paths(data, must_appear):
    with pytest.raises(SchemaError) as exc:
        validate_chapter_meta(data)
    msg = str(exc.value)
    for token in must_appear:
        assert token in msg, f"错误信息应包含路径 token {token!r}, 实际:\n{msg}"


# ──────────────────────────────────────────────────────────────────────
# world_view_rich：3 合法 + 5 非法
# ──────────────────────────────────────────────────────────────────────


VALID_WORLDRICH = [
    pytest.param(_worldview_min(), id="minimum_30char_per_section"),
    pytest.param(
        _worldview_min(cosmos=("蓝星与九天之上并存，" * 4)),
        id="cosmos_long",
    ),
    pytest.param(
        _worldview_min(history=("1984 年灵气潮汐以来，" * 6)),
        id="history_long",
    ),
]


@pytest.mark.parametrize("data", VALID_WORLDRICH)
def test_worldview_valid(data):
    validate_world_view_rich(data)


INVALID_WORLDRICH = [
    pytest.param({"cosmos": "x" * 30}, ["geography"], id="missing_geography_etc"),
    pytest.param(
        {"cosmos": "x", "geography": "x", "history": "x",
         "society": "x", "technology": "x", "races": "x", "customs": "x"},
        ["cosmos"],
        id="all_sections_too_short",
    ),
    pytest.param(
        _worldview_min(history="<30字"),  # 短
        ["history"],
        id="history_too_short",
    ),
    pytest.param(
        _worldview_min(society=None),
        ["society"],
        id="society_null",
    ),
    pytest.param(
        _worldview_min(races=42),  # 不是字符串
        ["races"],
        id="races_not_string",
    ),
]


@pytest.mark.parametrize("data,must_appear", INVALID_WORLDRICH)
def test_worldview_invalid_paths(data, must_appear):
    with pytest.raises(SchemaError) as exc:
        validate_world_view_rich(data)
    msg = str(exc.value)
    for token in must_appear:
        assert token in msg, f"错误信息应包含路径 token {token!r}, 实际:\n{msg}"


# ──────────────────────────────────────────────────────────────────────
# character_card：3 合法 + 5 非法
# ──────────────────────────────────────────────────────────────────────


VALID_CARD = [
    pytest.param(_character_card_min(), id="minimum"),
    pytest.param(
        _character_card_min(basic={**_character_card_min()["basic"], "age": "三十二岁"}),
        id="age_as_string",
    ),
    pytest.param(
        _character_card_min(
            personality={"tags": ["精算", "克制"], "summary": ("外表冷峻内心压着火，" * 2)},
        ),
        id="multiple_tags_long_summary",
    ),
]


@pytest.mark.parametrize("data", VALID_CARD)
def test_character_card_valid(data):
    validate_character_card(data)


INVALID_CARD = [
    pytest.param({"catchphrase": {"lines": ["x"]}}, ["personality"], id="missing_personality"),
    pytest.param(
        {
            "personality": {"summary": "x" * 50},  # 缺 tags
            "catchphrase": {"lines": ["x"]},
            "arc": {"start_state": "x", "catalyst": "x", "end_state": "x"},
        },
        ["tags"],
        id="personality_missing_tags",
    ),
    pytest.param(
        _character_card_min(catchphrase={"lines": []}),  # minItems=1
        ["lines"],
        id="catchphrase_lines_empty",
    ),
    pytest.param(
        _character_card_min(
            arc={"start_state": "x", "catalyst": "x"},  # 缺 end_state
        ),
        ["arc"],
        id="arc_missing_end_state",
    ),
    pytest.param(
        _character_card_min(personality={"tags": ["x"]}),  # 缺 summary
        ["summary"],
        id="personality_missing_summary",
    ),
]


@pytest.mark.parametrize("data,must_appear", INVALID_CARD)
def test_character_card_invalid_paths(data, must_appear):
    with pytest.raises(SchemaError) as exc:
        validate_character_card(data)
    msg = str(exc.value)
    for token in must_appear:
        assert token in msg, f"错误信息应包含路径 token {token!r}, 实际:\n{msg}"


# ──────────────────────────────────────────────────────────────────────
# entity_relation_rich：3 合法 + 5 非法
# ──────────────────────────────────────────────────────────────────────


VALID_REL = [
    pytest.param(_entity_relation_min(), id="minimum"),
    pytest.param(
        _entity_relation_mut := _entity_relation_min(
            intensity=0, mutual=False,
        ),
        id="intensity_zero_mutual_false",
    ),
    pytest.param(
        _entity_relation_min(
            evolution=[
                {"phase": "开端", "state": "陌生"},
                {"phase": "中段", "state": "因破产疏远"},
                {"phase": "结尾", "state": "重逢"},
            ],
            key_events=[
                {"chapter_hint": "第3章", "event": "初遇"},
                {"chapter_hint": "第18章", "event": "守在身边"},
            ],
        ),
        id="long_evolution_and_events",
    ),
]


@pytest.mark.parametrize("data", VALID_REL)
def test_entity_relation_valid(data):
    validate_entity_relation_rich(data)


INVALID_REL = [
    pytest.param(
        _entity_relation_min(intensity=15),  # 越界
        ["intensity"],
        id="intensity_too_high",
    ),
    pytest.param(
        _entity_relation_min(intensity=-1),  # 越界
        ["intensity"],
        id="intensity_negative",
    ),
    pytest.param(
        _entity_relation_min(intensity="5"),  # 字符串而非整数
        ["intensity"],
        id="intensity_string",
    ),
    pytest.param(
        {"mutual": True, "intensity": "high"},
        ["intensity"],
        id="intensity_word",
    ),
    pytest.param(
        _entity_relation_min(
            evolution=[{"state": "陌生"}],  # 缺 phase
        ),
        ["phase"],
        id="evolution_missing_phase",
    ),
]


@pytest.mark.parametrize("data,must_appear", INVALID_REL)
def test_entity_relation_invalid_paths(data, must_appear):
    with pytest.raises(SchemaError) as exc:
        validate_entity_relation_rich(data)
    msg = str(exc.value)
    for token in must_appear:
        assert token in msg, f"错误信息应包含路径 token {token!r}, 实际:\n{msg}"


# ──────────────────────────────────────────────────────────────────────
# 跨 schema 通用：markdown fence 与中英文键混淆（fixture 文件）
# ──────────────────────────────────────────────────────────────────────


def test_markdown_fence_setting_package_strip_or_fail():
    """LLM 经常把整段 JSON 包在 ```json ... ``` 内。如果应用层直接喂 schema
    validator，应识别为非法 JSON（schema validator 是 dict-in 接口，
    不应假设自己剥 fence）。此处断言：原始字符串喂给 json.loads 失败，
    且提取 JSON 子串后必能通过 schema 校验。
    """
    raw = "```json\n" + json.dumps(_setting_minimum(), ensure_ascii=False) + "\n```"
    # 1) 不允许静默吞掉 → 喂到 schema validator 之前必须先解析
    with pytest.raises((ValueError, TypeError)):
        validate_setting_package(raw)  # type: ignore[arg-type]

    # 2) 抽出 JSON 子段必须可过 schema
    inner = raw.split("```json\n", 1)[1].rsplit("\n```", 1)[0]
    validate_setting_package(json.loads(inner))


def test_chinese_key_alias_is_not_accepted():
    """LLM 经常把 hidden_world_name 写成『隐藏世界名』。schema 严格按英文键
    名校验——若 schema 只声明英文键，中文键要么命中 additionalProperties
    失败，要么被忽略。entity_relation_rich 的 additionalProperties=true
    所以不会因额外键失败，但缺失的英文键仍是 required → 抛 SchemaError。
    """
    payload = {
        "隐藏世界名": "九霄",
        "surface_world_name": "苍玄",
        "hidden_world_history": "x" * 60,
        # 没写 hidden_world_name（英文键必填）
    }
    with pytest.raises(SchemaError) as exc:
        validate_setting_package(_setting_minimum() | {"world_setting": payload})
    msg = str(exc.value)
    assert "hidden_world_name" in msg


def test_schema_error_carries_json_path():
    """通用契约：SchemaError 抛出的错误字符串必含一条 /-join 的 JSON path。"""
    bad = _setting_minimum(title_candidates=[])
    with pytest.raises(SchemaError) as exc:
        validate_setting_package(bad)
    # 错误由 SchemaError.name / .errors 共同构造；str() 应含形如 "title_candidates"
    assert "title_candidates" in str(exc.value)
    # errors 列表应是结构化
    assert exc.value.errors and isinstance(exc.value.errors, list)
    for e in exc.value.errors:
        assert "path" in e and "message" in e
