"""test_summarizer_wired_2026_08_05.py

2026-08-05 修复（清单衍生）：node_save_and_track 弧末调 run_summarizer 时解构
返回值，把 plan_vs_actual 等观测信号写进 state 而非丢弃。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_summarizer_result_persisted_into_state():
    """node_save_and_track 弧末必须接住 run_summarizer 的返回 dict，写到 state。"""
    from engine import orchestrator as orch
    from engine.agents import tracker as tr_mod  # noqa

    fake_summary = {
        "arc_summary": {"arc_id": 1, "arc_name": "归航", "summary_100": "x"},
        "plan_vs_actual": {
            "total_planned_chapters": 12,
            "matched_chapters": 3,
            "coverage_ratio": 0.25,    # <0.5 触发 warn + error_log 写入
            "missing_goals": ["找到舰队", "激活遗迹", "穿越时空"],
        },
    }

    def fake_run_summarizer(trigger, arc, memory, novel_id):
        return (fake_summary, 0.05)

    # 跑 tracker mock 让 _add_cost / _budget_ok 不报错，且 update memory 不挂
    def fake_run_tracker(text, task, memory, novel_id, **kw):
        return (memory, 0.0)

    # 给 state 装一个 current_task + arc_plans，让函数走到弧末分支
    state = {
        "novel_id": "test",
        "current_arc": 0,           # 弧 1 完成 → 触发 summarizer
        "current_chapter": 12,
        "current_task": {
            "chapter_number": 12, "chapter_role": "终章",
            "chapter_goal": "找到一个答案",
            "main_characters": ["林渊"],
            "shuang_type": None, "shuang_description": "",
            "ending_hook_type": "无", "ending_hook_description": "",
            "setting_constraints": [], "forbidden_actions": [],
            "target_length": "2000-2200", "audit_mode": "full",
            "is_arc_climax": False,
            "_draft_text": "本章正文内容" * 80,
            "_checker_result": {"score": 7.0, "verdict": "PASS", "dimensions": {},
                                "specific_feedback": "", "strongest_point": "",
                                "weakest_point": ""},
            "_draft_title": "Arc 1 终章",
            "_tracker_failed": False,
        },
        "arc_plans": [
            {"arc_id": 1, "arc_name": "归航", "arc_goal": "找到舰队",
             "estimated_chapters": 12, "arc_climax_description": "",
             "arc_climax_chapter_offset": 8, "emotion_curve": "up",
             "new_characters_introduced": [], "arc_ending_state": "",
             "is_final_arc": True},
        ],
        "chapter_task_queue": [],   # 空 → 触发 summarizer（save_and_track
                                       # 弧末分支用 not queue 作为触发条件）
        "quality_history": [7.0] * 12,
        "rewrite_count_current": 0,
        "consecutive_low_score": 0,
        "budget_used_usd": 5.0,
        "budget_limit_usd": 500.0,
        "error_log": [],
        "human_pending": [],
        "tracker_pending": [],
        "approved_outline_tasks": {},
        "memory_gaps": [],
    }

    # 拦住 save_state / save_chapter 防止真落盘
    with __import__("unittest.mock", fromlist=["patch"]).patch.multiple(
        "engine.orchestrator",
        save_state=lambda *a, **kw: None,
        save_chapter=lambda *a, **kw: None,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "run_summarizer", fake_run_summarizer,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "run_tracker", fake_run_tracker,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "save_state", lambda *a, **kw: None,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "save_chapter", lambda *a, **kw: None,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "_setting", lambda: {"title_candidates": ["x"], "tagline": ""},
    ):
        result = orch.node_save_and_track(state)

    # 弧末跑完后必须：current_arc 推进 + state.summarizer_metrics 写入
    assert result["current_arc"] == 1, f"current_arc 应推进至 1; got {result['current_arc']}"
    sm = result.get("summarizer_metrics") or {}
    assert "1" in sm, f"state 应写入 summarizer_metrics['1']; got {sm}"
    assert sm["1"].get("plan_vs_actual", {}).get("coverage_ratio") == 0.25

    # arc_plans[0] 也应有 _plan_vs_actual 镜像
    arc_record = result["arc_plans"][0]
    assert "_plan_vs_actual" in arc_record, (
        f"arc_plans[0] 应挂 _plan_vs_actual；got {arc_record}"
    )
    assert arc_record["_plan_vs_actual"]["matched_chapters"] == 3

    # coverage < 0.5 时应触发 warn + error_log
    assert any("plan coverage" in msg for msg in result.get("error_log", [])), (
        f"coverage < 0.5 必须写入 error_log；got {result.get('error_log')}"
    )


def test_summarizer_high_coverage_no_warning():
    """coverage ≥ 50% 时不应在 error_log 写告警（不应该误报）。"""
    from engine import orchestrator as orch

    fake_summary = {
        "plan_vs_actual": {
            "total_planned_chapters": 10,
            "matched_chapters": 7,
            "coverage_ratio": 0.70,
            "missing_goals": [],
        },
        "arc_summary": {"arc_id": 2, "arc_name": "x", "summary_100": "y"},
    }

    state = {
        "novel_id": "test",
        "current_arc": 1,
        "current_chapter": 10,
        "current_task": {
            "chapter_number": 10, "chapter_role": "终章",
            "chapter_goal": "收尾", "main_characters": ["X"],
            "shuang_type": None, "shuang_description": "",
            "ending_hook_type": "无", "ending_hook_description": "",
            "setting_constraints": [], "forbidden_actions": [],
            "target_length": "2000-2200", "audit_mode": "full",
            "is_arc_climax": False,
            "_draft_text": "正文" * 60,
            "_checker_result": {"score": 7.0, "verdict": "PASS", "dimensions": {},
                                "specific_feedback": "", "strongest_point": "",
                                "weakest_point": ""},
            "_draft_title": "t",
            "_tracker_failed": False,
        },
        "arc_plans": [
            {"arc_id": 1, "arc_name": "a1", "arc_goal": "g1",
             "estimated_chapters": 10, "arc_climax_description": "",
             "arc_climax_chapter_offset": 5, "emotion_curve": "u",
             "new_characters_introduced": [], "arc_ending_state": "",
             "is_final_arc": False},
            {"arc_id": 2, "arc_name": "a2", "arc_goal": "g2",
             "estimated_chapters": 8, "arc_climax_description": "",
             "arc_climax_chapter_offset": 4, "emotion_curve": "u",
             "new_characters_introduced": [], "arc_ending_state": "",
             "is_final_arc": True},
        ],
        "chapter_task_queue": [],
        "quality_history": [7.0] * 10,
        "rewrite_count_current": 0,
        "consecutive_low_score": 0,
        "budget_used_usd": 5.0,
        "budget_limit_usd": 500.0,
        "error_log": [],
    }

    def fake_run_summarizer(*a, **kw):
        return (fake_summary, 0.0)
    def fake_run_tracker(*a, **kw):
        return ({"hot": {}}, 0.0)

    with __import__("unittest.mock", fromlist=["patch"]).patch.multiple(
        "engine.orchestrator",
        save_state=lambda *a, **kw: None,
        save_chapter=lambda *a, **kw: None,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "run_summarizer", fake_run_summarizer,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "run_tracker", fake_run_tracker,
    ), __import__("unittest.mock", fromlist=["patch"]).patch.object(
        orch, "_setting", lambda: {"title_candidates": ["x"], "tagline": ""},
    ):
        result = orch.node_save_and_track(state)

    arc_record = result["arc_plans"][1]   # current_arc=1 → 弧 idx=1 即 arc_plans[1]（arc_id 2）
    assert "_plan_vs_actual" in arc_record, (
        f"current_arc=1 触发的弧末 summarizer 应挂在 arc_plans[1]；arc_plans={result['arc_plans']}"
    )
    assert not any("plan coverage" in msg for msg in result.get("error_log", [])), (
        f"coverage=0.7 不应写入覆盖率告警；got {result.get('error_log')}"
    )
