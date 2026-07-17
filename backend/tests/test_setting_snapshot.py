from types import SimpleNamespace

from app.bridge.setting_sync import _build_worldbuild_snapshot
from app.models import Character, Faction, Foreshadowing, PowerSystem, WorldSetting
from engine.agents.planner import _snapshot_block


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter_by(self, **_kwargs):
        return self

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows_by_model):
        self._rows_by_model = rows_by_model

    def query(self, model):
        return _FakeQuery(self._rows_by_model.get(model, []))


def test_worldbuild_snapshot_preserves_structured_entities():
    world = SimpleNamespace(
        world_view_rich_json={"geography": "浮空群岛"},
        story_core_struct_json={"conflict": "城邦争夺能源"},
        history_timeline_json=[{"era": "旧纪元", "event": "天穹破碎"}],
        plot_skeleton_json=[{"arc": 1, "goal": "进入主城"}],
    )
    character = SimpleNamespace(
        name="林砚", role="主角", card_basic_json={"age": 19},
        card_personality_json={"tags": ["克制"]},
        card_background_json={"origin": "边岛"},
        card_abilities_json={"power_name": "刻印"},
        card_catchphrase_json={"lines": ["先算清代价。"]},
        card_arc_json={"start_state": "流亡"},
    )
    faction = SimpleNamespace(name="巡天司", detail_json={"goal": "维持航路"})
    power = SimpleNamespace(name="刻印体系", description="以记忆换取力量", tiers_json=[{"name": "初刻"}])
    seed = SimpleNamespace(
        content="破损罗盘会指向禁区", importance="高", status="未铺垫",
        planted_chapter_hint="1-3", payoff_chapter_hint="25-30",
    )
    db = _FakeSession({
        WorldSetting: [world], Character: [character], Faction: [faction],
        PowerSystem: [power], Foreshadowing: [seed],
    })

    snapshot = _build_worldbuild_snapshot("project-1", db)

    assert snapshot["world_view_rich"]["geography"] == "浮空群岛"
    assert snapshot["history_timeline"][0]["event"] == "天穹破碎"
    assert snapshot["characters"][0]["name"] == "林砚"
    assert snapshot["power_systems"][0]["tiers"][0]["name"] == "初刻"
    assert snapshot["foreshadowings"][0]["content"] == "破损罗盘会指向禁区"

    prompt_block = _snapshot_block({"worldbuild_snapshot": snapshot})
    assert "林砚" in prompt_block
    assert "巡天司" in prompt_block
    assert "天穹破碎" in prompt_block
    assert "破损罗盘会指向禁区" in prompt_block


def test_worldbuild_snapshot_is_empty_without_rows():
    assert _build_worldbuild_snapshot("project-1", _FakeSession({})) == {}
    assert _snapshot_block({}) == ""
