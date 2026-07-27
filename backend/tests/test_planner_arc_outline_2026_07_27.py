"""test_planner_arc_outline_2026_07_27.py

真实 LLM 测试暴露的缺陷 —— planner 把已经是 arc 形态的 plot_skeleton
当成"卷"形态去读，字段名对不上导致 arc_goal 全空。

复现（Phase B 3 章冒烟，真 MiniMax-M3）：
    ❌ setting_package schema 校验失败 (5 处):
      - arc_outline/0/arc_goal: '' should be non-empty
      - arc_outline/1/arc_goal: '' should be non-empty
      ...（5 弧全空）

根因：
- worldbuild 落库的 `world_settings.plot_skeleton_json` 存的**已经是 arc 形态**，
  字段是 arc_id / arc_name / arc_goal / estimated_chapters /
  arc_climax_description / emotion_curve / new_characters_introduced /
  arc_ending_state / is_final_arc。
- 但 `engine/agents/planner.py:392-405` 的 `_merge_snapshot_into_setting` 按"卷"形态读：
  `vol.get("title")` / `vol.get("summary")`。这两个 key 在 arc 形态里
  **根本不存在** → `arc_goal` 落成 ""。
- schema（backend/schema/setting_package.schema.json）要求 arc_goal 非空
  → planner 每次都 exit_code=1，整条写作链路卡在第一步。

这不是"schema 太严"，是 planner 读错了字段：真实数据里 arc_goal 有 400+ 字
完整内容，被丢弃了。修法是兼容两种形态（arc 形态优先按原名取，
卷形态回退到 title/summary），不是放宽 schema。
"""
from __future__ import annotations

import pytest

from engine.agents.planner import _merge_snapshot_into_setting as _merge_snapshot


def _arc_shaped_skeleton():
    """worldbuild 实际落库的形态（已是 arc 形态）。"""
    return [
        {
            "arc_id": 1,
            "arc_name": "第一卷 野草破土",
            "arc_goal": "主角在绝境之夜撞破一桩密室交易，被卷入资本暗战。",
            "estimated_chapters": 30,
            "arc_climax_description": "亲眼目睹庇护者被清剿灭口。",
            "emotion_curve": "低开→持续上升→高潮→收尾",
            "new_characters_introduced": ["钟爷"],
            "arc_ending_state": "明白自己不过是一枚弃子。",
            "is_final_arc": False,
        },
        {
            "arc_id": 2,
            "arc_name": "第二卷 借势而上",
            "arc_goal": "主角被引路人纳入羽翼，学会用规则反制规则。",
            "estimated_chapters": 30,
            "arc_climax_description": "第一次在牌桌上正面击败对手。",
            "emotion_curve": "平开→压抑→爆发",
            "new_characters_introduced": [],
            "arc_ending_state": "获得第一块真正的地盘。",
            "is_final_arc": False,
        },
    ]


def _volume_shaped_skeleton():
    """历史/其它来源可能是"卷"形态（title/summary）。兼容性不能丢。"""
    return [
        {"title": "第一卷 起势", "summary": "主角从底层起步，第一次接触暗面规则。"},
        {"title": "第二卷 破局", "summary": "主角反制围剿，站稳脚跟。"},
    ]


def _base_setting():
    return {"arc_outline": []}


# ─── 1. arc 形态：字段必须被原样保留 ─────────────────────────

def test_arc_shaped_skeleton_preserves_arc_goal():
    """核心复现：arc 形态的 arc_goal 不能被丢成空字符串。"""
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    arcs = setting["arc_outline"]
    assert arcs[0]["arc_goal"], "arc_goal 被丢空 —— 这正是 Phase B 卡住的缺陷"
    assert "密室交易" in arcs[0]["arc_goal"]
    assert "规则反制规则" in arcs[1]["arc_goal"]


def test_arc_shaped_skeleton_preserves_arc_name():
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    arcs = setting["arc_outline"]
    assert arcs[0]["arc_name"] == "第一卷 野草破土"
    assert arcs[1]["arc_name"] == "第二卷 借势而上"


def test_arc_shaped_skeleton_preserves_climax_and_ending():
    """arc_climax_description / arc_ending_state 也不能被 summary 覆盖掉。

    旧实现把这三个字段全塞成同一个 vol.get("summary")，即使 summary 存在，
    高潮描述和收尾状态也会退化成同一句话 —— 信息量塌缩。
    """
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    a0 = setting["arc_outline"][0]
    assert "清剿灭口" in a0["arc_climax_description"]
    assert "弃子" in a0["arc_ending_state"]
    # 三者必须互不相同（旧实现会让它们全等于 summary）
    assert a0["arc_goal"] != a0["arc_climax_description"]
    assert a0["arc_goal"] != a0["arc_ending_state"]


def test_arc_shaped_skeleton_preserves_emotion_and_characters():
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    a0 = setting["arc_outline"][0]
    assert a0["emotion_curve"] == "低开→持续上升→高潮→收尾"
    assert a0["new_characters_introduced"] == ["钟爷"]


def test_arc_shaped_skeleton_preserves_estimated_chapters():
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    assert setting["arc_outline"][0]["estimated_chapters"] == 30


# ─── 2. 卷形态：向后兼容不能破 ─────────────────────────

def test_volume_shaped_skeleton_still_works():
    """历史"卷"形态（title/summary）必须继续可用。"""
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _volume_shaped_skeleton()})
    arcs = setting["arc_outline"]
    assert arcs[0]["arc_name"] == "第一卷 起势"
    assert "底层起步" in arcs[0]["arc_goal"]


def test_mixed_shapes_are_tolerated():
    """两种形态混在一个列表里也不能崩。"""
    setting = _base_setting()
    mixed = [_arc_shaped_skeleton()[0], _volume_shaped_skeleton()[0]]
    _merge_snapshot(setting, {"plot_skeleton": mixed})
    arcs = setting["arc_outline"]
    assert arcs[0]["arc_goal"]
    assert arcs[1]["arc_goal"]


# ─── 3. schema 契约：arc_goal 必须非空 ─────────────────────────

def test_no_arc_goal_is_left_empty():
    """补齐到 4 弧时，占位弧的 arc_goal 也不能是空字符串。

    schema（setting_package.schema.json）要求 arc_goal 非空；补位弧写空串
    会让整个 planner 失败，等于"补齐"反而把链路搞挂。
    """
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    arcs = setting["arc_outline"]
    assert len(arcs) >= 4, "planner 有 ≥4 弧硬约束"
    for i, a in enumerate(arcs):
        assert str(a.get("arc_goal", "")).strip(), \
            f"第 {i} 弧 arc_goal 为空，schema 会拒绝整个 setting_package"


def test_arc_ids_are_sequential():
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    ids = [a["arc_id"] for a in setting["arc_outline"]]
    assert ids == list(range(1, len(ids) + 1)), f"arc_id 不连续：{ids}"


def test_exactly_one_final_arc():
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": _arc_shaped_skeleton()})
    finals = [a for a in setting["arc_outline"] if a.get("is_final_arc")]
    assert len(finals) == 1, f"必须恰好一个终弧，实际 {len(finals)} 个"
    assert finals[0] is setting["arc_outline"][-1], "终弧必须是最后一弧"


# ─── 4. 畸形输入不崩 ─────────────────────────

@pytest.mark.parametrize("bad", [None, [], "字符串", 42, [None], ["串"], [42]])
def test_malformed_plot_skeleton_does_not_crash(bad):
    setting = _base_setting()
    _merge_snapshot(setting, {"plot_skeleton": bad})
    assert isinstance(setting.get("arc_outline"), list)


def test_missing_plot_skeleton_leaves_existing_arc_outline():
    """snapshot 没给 plot_skeleton 时，不能把已有的 arc_outline 抹掉。"""
    setting = {"arc_outline": [{"arc_id": 1, "arc_name": "既有弧",
                                "arc_goal": "既有目标"}]}
    _merge_snapshot(setting, {})
    assert setting["arc_outline"][0]["arc_goal"] == "既有目标"
