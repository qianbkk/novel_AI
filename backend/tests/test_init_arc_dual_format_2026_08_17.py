"""test_init_arc_dual_format_2026_08_17.py

P1-8 修复验证：init_arc 必须支持 plot_skeleton 双形态（arc-form / volume-form）。

历史 bug（254c724 fix 残留）：
- planner._merge_snapshot_into_setting 已支持 arc_form + volume_form 双形态兜底（_pick）。
- 但 init_arc.build_state_from_paths:46-60 仍只读 setting.arc_outline，
  历史项目（plot_skeleton_json 是卷形态）直接拿不到任何弧。
- 影响：Bootstrap 流程第一个节点卡死，BridgeRun 一直 running。

修复（任务 P1-8 2026-08-17）：
- init_arc 读 arc_outline 之前先检查；为空时回退读 plot_skeleton，
  复用 planner 的 _pick 兜底（arc_name|title, arc_goal|summary 等）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── 1. 正常 arc-form arc_outline 路径（既有功能回归）））

def test_init_arc_arc_form_arc_outline_works(tmp_path):
    """arc_form arc_outline（既有数据）正常生成 arc_plans。"""
    from engine.agents.init_arc import build_state_from_paths

    setting = {
        "title_candidates": ["测试书"],
        "platform": "fanqie",
        "genre": "玄幻",
        "tagline": "测试",
        "arc_outline": [
            {"arc_id": 1, "arc_name": "弧一", "arc_goal": "主角获得金手指",
             "estimated_chapters": 10, "arc_climax_description": "小试身手",
             "arc_ending_state": "小有名气"},
        ],
    }
    setting_path = tmp_path / "setting.json"
    state_path = tmp_path / "state.json"
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    setting_path.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")

    state = build_state_from_paths(
        project_id="test",
        setting_path=setting_path,
        state_path=state_path,
        chapters_dir=chapters_dir,
    )

    arcs = state.get("arc_plans", [])
    assert len(arcs) >= 1
    assert arcs[0]["arc_name"] == "弧一"
    assert arcs[0]["arc_goal"] == "主角获得金手指"


# ── 2. 关键修复：plot_skeleton volume-form 必须能转弧 ─────────────────

def test_init_arc_volume_form_plot_skeleton_falls_back(tmp_path):
    """plot_skeleton 是卷形态（title/summary）时 init_arc 必须 _pick 兜底，
    不能让 arc_plans 全空。"""
    from engine.agents.init_arc import build_state_from_paths

    setting = {
        "title_candidates": ["卷形态测试"],
        "genre": "玄幻",
        "plot_skeleton": [
            {"title": "第1卷 债起云州", "summary": "主角重生觉醒"},
            {"title": "第2卷 入局云州", "summary": "建立根据地"},
            {"title": "第3卷 临海风波", "summary": "港口扩展"},
            {"title": "第4卷 苍莽之约", "summary": "深入妖族祖地"},
        ],
        # 注意：arc_outline 故意为空（模拟历史项目）
        "arc_outline": [],
    }
    setting_path = tmp_path / "setting.json"
    state_path = tmp_path / "state.json"
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    setting_path.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")

    state = build_state_from_paths(
        project_id="test",
        setting_path=setting_path,
        state_path=state_path,
        chapters_dir=chapters_dir,
    )

    arcs = state.get("arc_plans", [])
    assert len(arcs) >= 1, (
        f"volume-form plot_skeleton 必须 _pick 兜底生成 arc_plans，实际 {len(arcs)} 弧"
    )
    # 兜底：arc_name 取 title，arc_goal 取 summary
    assert arcs[0]["arc_name"] == "第1卷 债起云州"
    assert arcs[0]["arc_goal"] == "主角重生觉醒"
    # 关键字段不能全空（避免下游 schema 拒）
    assert arcs[0]["arc_climax_description"] != "", (
        "arc_climax_description 不能为空（schema 必填）"
    )
    assert arcs[0]["arc_ending_state"] != "", (
        "arc_ending_state 不能为空（schema 必填）"
    )


# ── 3. arc_form plot_skeleton（既有数据，254c724 修复后）））

def test_init_arc_arc_form_plot_skeleton_works(tmp_path):
    """plot_skeleton 是 arc_form 时直接读 arc_name/arc_goal。"""
    from engine.agents.init_arc import build_state_from_paths

    setting = {
        "title_candidates": ["arc-form 测试"],
        "genre": "玄幻",
        "plot_skeleton": [
            {"arc_id": 1, "arc_name": "弧甲", "arc_goal": "arc-form 弧目标",
             "estimated_chapters": 30, "arc_climax_description": "高潮甲",
             "arc_ending_state": "收束甲"},
        ],
        "arc_outline": [],
    }
    setting_path = tmp_path / "setting.json"
    state_path = tmp_path / "state.json"
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    setting_path.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")

    state = build_state_from_paths(
        project_id="test",
        setting_path=setting_path,
        state_path=state_path,
        chapters_dir=chapters_dir,
    )

    arcs = state.get("arc_plans", [])
    assert len(arcs) >= 1
    assert arcs[0]["arc_name"] == "弧甲"
    assert arcs[0]["arc_goal"] == "arc-form 弧目标"


# ── 4. 兜底优先级：arc_outline 优先于 plot_skeleton ─────────────────

def test_init_arc_prefers_arc_outline_over_plot_skeleton(tmp_path):
    """既有 arc_outline 时不应从 plot_skeleton 再读（避免双重计数）。"""
    from engine.agents.init_arc import build_state_from_paths

    setting = {
        "title_candidates": ["优先级测试"],
        "genre": "玄幻",
        "arc_outline": [
            {"arc_id": 1, "arc_name": "来自 arc_outline", "arc_goal": "来自 arc_outline",
             "estimated_chapters": 5},
        ],
        "plot_skeleton": [
            {"title": "来自 plot_skeleton", "summary": "来自 plot_skeleton"},
        ],
    }
    setting_path = tmp_path / "setting.json"
    state_path = tmp_path / "state.json"
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    setting_path.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")

    state = build_state_from_paths(
        project_id="test",
        setting_path=setting_path,
        state_path=state_path,
        chapters_dir=chapters_dir,
    )

    arcs = state.get("arc_plans", [])
    assert len(arcs) == 1, f"应只取 arc_outline（1 弧），实际 {len(arcs)}"
    assert arcs[0]["arc_name"] == "来自 arc_outline"


# ── 5. 既无 arc_outline 也无 plot_skeleton 时保持原行为 ─────────────────

def test_init_arc_no_arc_data_produces_empty_arcs(tmp_path):
    """空 setting 必须仍能 init（生成 0 弧），不让流程崩。"""
    from engine.agents.init_arc import build_state_from_paths

    setting = {
        "title_candidates": ["空测试"],
        "genre": "玄幻",
        # 既无 arc_outline 也无 plot_skeleton
    }
    setting_path = tmp_path / "setting.json"
    state_path = tmp_path / "state.json"
    chapters_dir = tmp_path / "chapters"
    chapters_dir.mkdir()
    setting_path.write_text(json.dumps(setting, ensure_ascii=False), encoding="utf-8")

    state = build_state_from_paths(
        project_id="test",
        setting_path=setting_path,
        state_path=state_path,
        chapters_dir=chapters_dir,
    )

    assert state.get("arc_plans") == []
    assert state.get("total_arcs_planned") == 0