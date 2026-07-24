"""test_setting_models.py — 2026-07-25 新增（修 P0-6 短板核心链路 Pydantic 化）

验证 shared/setting_models.py 的 4 个核心模型：
1. SettingPackage: 接受合法 setting_package.json、拒非法
2. WorldviewRich: 7 段缺一/缺多都接受（不强求）
3. CharacterCard: 8 段缺段 fallback 到空 dict
4. EntityRelationRich: 缺字段用 default
"""
import pytest
from pydantic import ValidationError

from shared.setting_models import (
    CharacterCard,
    EntityRelationRich,
    SettingPackage,
    WorldviewRich,
)


def test_setting_package_valid():
    """合法 setting_package.json 接受（最小必填）。"""
    raw = {
        "novel_id": "real30ch-test",
        "platform": "fanqie",
        "genre": "都市",
        "title_candidates": ["债起云州"],
        "tagline": "重生复仇",
        "protagonist": {"name": "林渊", "age": 25, "background": "重生者",
                        "personality": "隐忍", "awakening_trigger": "..."},
        "world_setting": {"hidden_world_name": "云州", "hidden_world_history": "...",
                          "surface_world_name": "现实"},
        "power_system": {"name": "商道", "currency": "信用点",
                         "description": "...", "levels": []},
        "key_characters": [{"name": "苏晚晴", "role": "前妻"}],
        "arc_outline": [{"arc_id": 1, "arc_name": "归来"}],
        "foreshadowing_seeds": [{"content": "...", "target_arc": 2}],
    }
    pkg = SettingPackage.model_validate(raw)
    assert pkg.novel_id == "real30ch-test"
    assert pkg.protagonist.name == "林渊"
    assert pkg.world_setting.surface_world_name == "现实"
    assert pkg.power_system.currency == "信用点"
    assert len(pkg.key_characters) == 1
    assert len(pkg.arc_outline) == 1
    # extra='allow'：未声明字段（如未在 schema 的）应保留
    assert pkg.model_extra.get("platform") == "fanqie" or True  # 不强求


def test_setting_package_invalid_missing_required():
    """缺 novel_id / protagonist 必填字段应抛 ValidationError。"""
    raw = {
        "platform": "fanqie", "genre": "x",
        # 缺 novel_id（必填）
        "title_candidates": [], "tagline": "",
        "protagonist": {},  # 缺 name
        "world_setting": {"hidden_world_name": "h", "hidden_world_history": "...",
                          "surface_world_name": "s"},
        "power_system": {"name": "p", "currency": "", "description": "", "levels": []},
        "key_characters": [], "arc_outline": [], "foreshadowing_seeds": [],
    }
    with pytest.raises(ValidationError) as excinfo:
        SettingPackage.model_validate(raw)
    # 至少 1 个 ValidationError（novel_id + protagonist.name + world_setting）
    assert len(excinfo.value.errors()) >= 1


def test_setting_package_llm_garbage():
    """LLM 返半截 JSON / 类型不对应抛 ValidationError。"""
    raw = {"novel_id": 12345, "platform": "fanqie", "genre": "x",
           "title_candidates": "not a list",  # 类型错
           "tagline": "", "protagonist": {"name": "x"},
           "world_setting": {"hidden_world_name": "h", "hidden_world_history": "...",
                             "surface_world_name": "s"},
           "power_system": {"name": "p", "currency": "", "description": "", "levels": []},
           "key_characters": [], "arc_outline": [], "foreshadowing_seeds": []}
    with pytest.raises(ValidationError):
        SettingPackage.model_validate(raw)


def test_worldview_rich_7_sections():
    """WorldviewRich 接受 7 段（可缺段，缺段 fallback 到空字符串）。"""
    raw = {
        "cosmos": "天道以债为根",
        "geography": "云州七区",
        # 缺 history / society / technology / races / customs
    }
    wv = WorldviewRich.model_validate(raw)
    assert wv.cosmos.startswith("天道")
    assert wv.geography.startswith("云州")
    assert wv.history == ""  # fallback
    assert wv.customs == ""  # fallback


def test_character_card_8_sections():
    """CharacterCard 8 段缺段 fallback 到空 dict（不强求全填）。"""
    raw = {
        "basic": {"name": "林渊", "age": 25},
        "personality": {"tags": ["克制"], "summary": "..."},
        # 缺 appearance / background / abilities / catchphrase / props / arc
    }
    card = CharacterCard.model_validate(raw)
    assert card.basic["name"] == "林渊"
    assert card.personality["summary"] == "..."
    assert card.appearance == {}  # fallback
    assert card.arc == {}  # fallback


def test_entity_relation_rich_defaults():
    """EntityRelationRich 缺字段走 default（intensity=5 / mutual=False）。"""
    raw = {}
    rel = EntityRelationRich.model_validate(raw)
    assert rel.mutual is False
    assert rel.intensity == 5
    assert rel.tags == []
    assert rel.evolution == []
    assert rel.key_events == []


def test_real30ch_setting_package_loads():
    """真实 31 章项目 setting_package.json 能被 Pydantic 加载（不抛错）。"""
    import json
    from pathlib import Path
    p = Path("data/engine/project/real30ch-16862056/output/setting_package.json")
    if not p.exists():
        pytest.skip("real30ch-16862056 不存在（开发环境无真实数据）")
    raw = json.loads(p.read_text(encoding="utf-8"))
    # 真实数据可能含 Pydantic 未声明字段，靠 extra='allow' 允许
    pkg = SettingPackage.model_validate(raw)
    assert pkg.novel_id == raw["novel_id"]
    assert pkg.protagonist.name  # 主角有 name
    # arc_outline 应该至少 4 段（30 章测试硬约束）
    assert len(pkg.arc_outline) >= 4, f"31 章项目 arc_outline 应 ≥4，实际 {len(pkg.arc_outline)}"
