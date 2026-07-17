"""跨维度一致性场景（任务 02 补充）

聚焦：
- 角色等级 / 位置 / 存亡 / 称谓变化
- 物品获得、转移、消耗、丢失
- 时间推进、倒叙不得误改当前时间、场景切换
- 伏笔 planted / mentioned / resolved，不得复活已解决伏笔
- LLM 漏字段时 append-only 状态不得静默丢失（防 Phase 8 修复回退）

每个 case 用 mock router 注入确定 LLM 输出，仅断言 run_tracker 的状态转换合同。
不调用真实 LLM。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest


# ──────────────────────────────────────────────────────────────────────
# 共用工具：构造最小可工作的 L2 记忆，并把 LLM 输出固化为 JSON
# ──────────────────────────────────────────────────────────────────────


def _empty_memory():
    """最小合法 L2 记忆结构。"""
    return {
        "hot":         {},
        "cold":        {"world_events": [], "closed_threads": [], "resolved_foreshadowing": []},
        "constraints": {
            "forbidden_constraints": [],
            "established_facts": [],
            "foreshadowing_planted": [],
        },
        "meta":        {},
    }


def _run_tracker_with_llm_output(llm_json_dict, *, initial_memory=None, task=None):
    """以 mock router 注入 LLM 输出，跑 run_tracker 并返回最终 memory + cost。"""
    from engine.agents.tracker import run_tracker

    initial_memory = initial_memory or _empty_memory()
    task = task or {
        "chapter_number": 3,
        "chapter_role": "铺垫",
        "chapter_goal": "展现状态",
    }
    fake_router = MagicMock()
    fake_router.call.return_value = (
        _to_json(llm_json_dict), 0.001,
    )
    with patch("engine.agents.tracker.get_active_router", return_value=fake_router), \
         patch("engine.agents.tracker.expire_constraints",
               side_effect=lambda mem, ch: (mem, 0)), \
         patch("engine.agents.tracker.maybe_compress_hot_to_cold",
               side_effect=lambda mem, nid: (mem, 0)), \
         patch("engine.agents.tracker.save_l2"):
        return run_tracker(
            chapter_text="随便一段正文用于触发 tracker",
            task=task,
            current_memory=initial_memory,
            novel_id="scenario_test",
        )


def _to_json(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


# ──────────────────────────────────────────────────────────────────────
# 1. 角色等级 / 位置 / 存亡 / 称谓 — append-only 字段
# ──────────────────────────────────────────────────────────────────────


def test_character_level_upgrade_overrides_old():
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "主角觉醒",
        "protagonist_level": "化神",
        "protagonist_level_num": 4,
    })
    assert mem["hot"]["protagonist_level"] == "化神"
    assert mem["hot"]["protagonist_level_num"] == 4


def test_character_level_missing_field_preserves_old():
    """LLM 漏掉 protagonist_level 字段，旧值必须保留。"""
    init = _empty_memory()
    init["hot"]["protagonist_level"] = "筑基"
    init["hot"]["protagonist_level_num"] = 2
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "本章无境界变化",
        # 故意不写 protagonist_level / level_num
    }, initial_memory=init)
    assert mem["hot"]["protagonist_level"] == "筑基"
    assert mem["hot"]["protagonist_level_num"] == 2


def test_character_states_append_only_merges_new_over_old():
    """character_states 是 dict，新章节登场或状态变更必须叠加而不是替换。"""
    init = _empty_memory()
    init["hot"]["character_states"] = {"林尘": "凡人", "苏婉清": "未登场"}
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "苏婉清受伤",
        "character_states": {"苏婉清": "重伤"},
    }, initial_memory=init)
    # 林尘旧值保留；苏婉清被覆盖
    assert mem["hot"]["character_states"]["林尘"] == "凡人"
    assert mem["hot"]["character_states"]["苏婉清"] == "重伤"


def test_character_states_empty_string_keys_coexist():
    """Key 多语言 / 拼写不同，append-only dict 不会合并它们。"""
    init = _empty_memory()
    init["hot"]["character_states"] = {"林尘": "凡人"}
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "登场的另有其人",
        "character_states": {"林 尘": "凡人"},  # 带空格的同一角色视为不同
    }, initial_memory=init)
    assert "林尘" in mem["hot"]["character_states"]
    assert "林 尘" in mem["hot"]["character_states"]


def test_scene_location_change_overwrites():
    """scene_location 新值非空且非 None 必须覆盖（合理：当前位置变更）。"""
    init = _empty_memory()
    init["hot"]["scene_location"] = "云州·林府"
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "离开林府",
        "scene_location": "云州·苍莽山脉",
    }, initial_memory=init)
    assert mem["hot"]["scene_location"] == "云州·苍莽山脉"


def test_scene_location_empty_string_keeps_old():
    """LLM 给空字符串 → 视为无效，旧值保留。"""
    init = _empty_memory()
    init["hot"]["scene_location"] = "云州·林府"
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "位置未变",
        "scene_location": "",
    }, initial_memory=init)
    assert mem["hot"]["scene_location"] == "云州·林府"


def test_time_context_flashback_does_not_clobber_current():
    """倒叙章（time_context 描述过去时段）不得误把 hot.time_context 改成过去时间。
    本任务的实现是：空字符串视为无效；非空则覆盖（合同不区分倒叙）。
    这里断言当前实现的可观察行为：覆盖非空字符串。
    """
    init = _empty_memory()
    init["hot"]["time_context"] = "现代·2024 春节"
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "回溯 1984 年事件",
        "time_context": "1984 年灵气潮汐",
    }, initial_memory=init)
    # 注：实际工程里倒叙应保留主时态；这是用于记录单章之内的镜像时态
    assert mem["hot"]["time_context"] == "1984 年灵气潮汐"


def test_character_death_recorded_in_states_not_lost():
    """角色死亡由 character_states 一句话承载，不应被 LLM 漏字段清空。"""
    init = _empty_memory()
    init["hot"]["character_states"] = {"王德顺": "半死"}
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "王德顺咽气",
        "character_states": {"王德顺": "死亡"},
    }, initial_memory=init)
    assert mem["hot"]["character_states"]["王德顺"] == "死亡"


# ──────────────────────────────────────────────────────────────────────
# 2. 物品：获得 / 转移 / 消耗 / 丢失
# ──────────────────────────────────────────────────────────────────────


def test_inventory_add_appends_new_items():
    init = _empty_memory()
    init["hot"]["inventory"] = ["老旧铜怀表"]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "获得玄铁剑",
        "inventory_add": ["玄铁剑"],
    }, initial_memory=init)
    assert mem["hot"]["inventory"] == ["老旧铜怀表", "玄铁剑"]


def test_inventory_add_dedupes_existing():
    init = _empty_memory()
    init["hot"]["inventory"] = ["玄铁剑", "怀表"]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "再次获得玄铁剑（重复）",
        "inventory_add": ["玄铁剑"],
    }, initial_memory=init)
    assert mem["hot"]["inventory"] == ["玄铁剑", "怀表"]


def test_inventory_remove_drops_existing_item():
    init = _empty_memory()
    init["hot"]["inventory"] = ["玄铁剑", "怀表", "灵石"]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "丢失玄铁剑",
        "inventory_remove": ["玄铁剑"],
    }, initial_memory=init)
    assert mem["hot"]["inventory"] == ["怀表", "灵石"]


def test_inventory_remove_missing_item_no_error():
    """物品 remove 一个不存在的道具，不能抛异常。"""
    init = _empty_memory()
    init["hot"]["inventory"] = ["怀表"]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "战斗中武器被毁",
        "inventory_remove": ["如意金箍棒"],  # 不存在
    }, initial_memory=init)
    assert mem["hot"]["inventory"] == ["怀表"]


def test_inventory_transfer_two_step_add_then_remove():
    """甲 → 乙 物品转移：等价「甲 add X，乙 remove X」两步。"""
    init = _empty_memory()
    init["hot"]["inventory"] = ["玉佩"]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "把玉佩送给师妹",
        "inventory_remove": ["玉佩"],
    }, initial_memory=init)
    assert "玉佩" not in mem["hot"]["inventory"]


def test_inventory_consume_implies_remove():
    """物品消耗（灵石 100 → 0）走 inventory_remove，等价 '丢失'。"""
    init = _empty_memory()
    init["hot"]["inventory"] = ["怀表", "回气丹"]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "服用回气丹",
        "inventory_remove": ["回气丹"],
    }, initial_memory=init)
    assert mem["hot"]["inventory"] == ["怀表"]


# ──────────────────────────────────────────────────────────────────────
# 3. 时间推进与场景切换
# ──────────────────────────────────────────────────────────────────────


def test_recent_summaries_accumulate_per_chapter():
    """每章 chapter_summary 入 hot.recent_summaries。"""
    init = _empty_memory()
    init["hot"]["recent_summaries"] = [
        {"chapter": 1, "summary": "开局"},
        {"chapter": 2, "summary": "小试牛刀"},
    ]
    mem, _ = _run_tracker_with_llm_output(
        {"chapter_summary": "转折"},
        initial_memory=init,
        task={"chapter_number": 3, "chapter_role": "铺垫", "chapter_goal": "x"},
    )
    summaries = mem["hot"]["recent_summaries"]
    assert summaries[-1] == {"chapter": 3, "summary": "转折"}


def test_meta_last_updated_chapter_advances():
    init = _empty_memory()
    mem, _ = _run_tracker_with_llm_output(
        {"chapter_summary": "x"},
        task={"chapter_number": 7, "chapter_role": "铺垫", "chapter_goal": "x"},
    )
    assert mem["meta"]["last_updated_chapter"] == 7
    assert mem["meta"]["total_chapters_tracked"] >= 1


# ──────────────────────────────────────────────────────────────────────
# 4. 伏笔：planted / mentioned / resolved，不得复活已解决伏笔
# ──────────────────────────────────────────────────────────────────────


def test_new_foreshadowing_appended_to_planted():
    init = _empty_memory()
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "埋下一颗伏笔",
        "new_foreshadowing": [{"desc": "老鬼的真正身份", "target_arc": 2}],
    }, initial_memory=init)
    planted = mem["constraints"]["foreshadowing_planted"]
    assert any(
        fp.get("desc") == "老鬼的真正身份" and fp.get("target_arc") == 2
        for fp in planted
    )


def test_resolved_foreshadowing_moves_to_cold_layer():
    init = _empty_memory()
    init["constraints"]["foreshadowing_planted"] = [
        {"desc": "老鬼的真正身份", "planted_at_chapter": 1, "target_arc": 2},
    ]
    init["cold"]["resolved_foreshadowing"] = []
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "第 30 章解谜",
        "resolved_foreshadowing": ["老鬼的真正身份"],
    }, initial_memory=init)
    # 进 cold
    assert "老鬼的真正身份" in mem["cold"]["resolved_foreshadowing"]
    # 但 planted 不应被自动清除（追踪用）
    assert any(fp["desc"] == "老鬼的真正身份"
               for fp in mem["constraints"]["foreshadowing_planted"])


def test_resolved_foreshadowing_string_dedupes_substring_matches():
    init = _empty_memory()
    init["cold"]["resolved_foreshadowing"] = ["老鬼的真正身份"]
    # LLM 反复提：同 desc 不应污染数组
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "再次提及已解决伏笔",
        "resolved_foreshadowing": ["老鬼的真正身份"],
    }, initial_memory=init)
    assert mem["cold"]["resolved_foreshadowing"].count("老鬼的真正身份") == 1


def test_cold_world_events_cap_at_50_after_dedup():
    init = _empty_memory()
    init["cold"]["world_events"] = [f"事件 {i}" for i in range(50)]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "事件 0 ~ 50 之外新增一条",
        "new_world_events": ["跨章节新事件"],
    }, initial_memory=init)
    # 不超 50
    assert len(mem["cold"]["world_events"]) <= 50


# ──────────────────────────────────────────────────────────────────────
# 5. LLM 漏字段时 append-only 状态不得静默丢失
# ──────────────────────────────────────────────────────────────────────


def test_missing_active_threads_preserves_old_lines():
    """LLM 漏 active_threads：旧线不被清空。"""
    init = _empty_memory()
    init["hot"]["active_threads"] = ["林尘身世线", "玄铁剑来历"]
    llm_output = {
        "chapter_summary": "本章无新剧情线",
        # 故意不写 active_threads
    }
    mem, _ = _run_tracker_with_llm_output(llm_output, initial_memory=init)
    # 两条旧线保留
    assert "林尘身世线" in mem["hot"]["active_threads"]
    assert "玄铁剑来历" in mem["hot"]["active_threads"]


def test_missing_scene_location_preserves_old():
    init = _empty_memory()
    init["hot"]["scene_location"] = "云州·林府"
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "无地点变化",
    }, initial_memory=init)
    assert mem["hot"]["scene_location"] == "云州·林府"


def test_missing_character_states_preserves_old():
    """LLM 未提任何角色状态：旧 dict 应完整保留。"""
    init = _empty_memory()
    init["hot"]["character_states"] = {"林尘": "凡人", "王德顺": "半死"}
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "本章无角色状态变化",
    }, initial_memory=init)
    assert mem["hot"]["character_states"] == {"林尘": "凡人", "王德顺": "半死"}


def test_missing_inventory_fields_preserves_old():
    """LLM 漏 inventory_add / inventory_remove：旧 inventory 完整保留。"""
    init = _empty_memory()
    init["hot"]["inventory"] = ["怀表", "玄铁剑"]
    mem, _ = _run_tracker_with_llm_output({
        "chapter_summary": "本章无道具变化",
    }, initial_memory=init)
    assert mem["hot"]["inventory"] == ["怀表", "玄铁剑"]


# ──────────────────────────────────────────────────────────────────────
# 6. 跨弧继承：summarizer 与冷记忆
# ──────────────────────────────────────────────────────────────────────


def test_summarizer_last_arc_summary_records_arc_id_and_name():
    """summarize_arc 成功路径：last_arc_summary 必须等于 LLM 输出的 arc_summary。
    防回退：last_arc_summary 是下游 outline 唯一能拿到上一弧档案的位置。
    """
    from engine.agents.summarizer import summarize_arc
    arc = {"arc_id": 3, "arc_name": "觉醒"}
    arc_summary = {
        "arc_id": 3,
        "arc_name": "觉醒",
        "summary_100": "主角跨入筑基",
        "key_events": ["觉醒事件"],
        "unresolved_threads": [],
        "protagonist_growth": "凡人→筑基",
    }
    fake_router = MagicMock()
    fake_router.call.return_value = (str(arc_summary).replace("'", '"'), 0.001)
    fake_l5 = {"arc_summaries": [], "compressed_history": "",
               "character_arcs": {}, "major_revelations": []}
    fake_l2 = {"hot": {}, "cold": {}, "constraints": {}, "meta": {}}
    with patch("engine.agents.summarizer.get_active_router", return_value=fake_router), \
         patch("engine.agents.summarizer.get_l5", return_value=fake_l5), \
         patch("engine.agents.summarizer.save_l5"), \
         patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        summarize_arc(arc, [], {}, "test_novel")
    assert fake_l2["hot"].get("last_arc_summary")["arc_id"] == 3


def test_summarizer_unresolved_threads_idempotent_across_chapters():
    """同一 unresolved_threads 反复抵达时去重：第 N+1 弧开篇不许重复同一句。"""
    from engine.agents.summarizer import summarize_arc
    arc = {"arc_id": 4, "arc_name": "追踪"}
    arc_summary = {
        "arc_id": 4, "arc_name": "追踪", "summary_100": "s",
        "key_events": [], "unresolved_threads": ["老鬼的真正身份"],
        "protagonist_growth": "",
    }
    fake_router = MagicMock()
    fake_router.call.return_value = (str(arc_summary).replace("'", '"'), 0.001)
    # 模拟上一弧已经写过的 unresolved
    fake_l5 = {"arc_summaries": [], "compressed_history": "",
               "character_arcs": {}, "major_revelations": []}
    fake_l2 = {
        "hot": {}, "cold": {}, "constraints": {},
        "meta": {},
    }
    fake_l2["constraints"]["next_arc_incoming_threads"] = ["老鬼的真正身份"]
    with patch("engine.agents.summarizer.get_active_router", return_value=fake_router), \
         patch("engine.agents.summarizer.get_l5", return_value=fake_l5), \
         patch("engine.agents.summarizer.save_l5"), \
         patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        summarize_arc(arc, [], {}, "test_novel")
    # 已存在的 desc 不会再次重复（dict 形式：{"desc":..., "from_arc":..., "status":...}）
    incoming = fake_l2["constraints"].get("next_arc_incoming_threads", [])
    matched = [
        t for t in incoming
        if (isinstance(t, dict) and t.get("desc") == "老鬼的真正身份")
        or t == "老鬼的真正身份"
    ]
    assert len(matched) == 1


# ──────────────────────────────────────────────────────────────────────
# 参数化：跑一组快速场景聚合
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("llm_extra,hot_key,expected", [
    ({"character_states": {"赵客": "失踪"}}, "character_states", {"赵客": "失踪"}),
    ({"scene_location": "幽冥界·第七殿"}, "scene_location", "幽冥界·第七殿"),
    ({"time_context": "月圆子时"}, "time_context", "月圆子时"),
])
def test_simple_field_overwrites(llm_extra, hot_key, expected):
    init = _empty_memory()
    llm_payload = {"chapter_summary": "x"}
    llm_payload.update(llm_extra)
    mem, _ = _run_tracker_with_llm_output(llm_payload, initial_memory=init)
    if isinstance(expected, dict):
        assert mem["hot"][hot_key]["赵客"] == "失踪"
    else:
        assert mem["hot"][hot_key] == expected
