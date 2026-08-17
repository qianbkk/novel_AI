"""test_outline_empty_candidates_hard_fail_2026_08_17.py

P1-6 修复验证：outline card/talk 模式返回空候选时不得静默落到 placeholder 模板。

历史 bug（审计发现，CLAUDE.md「失败要响亮」违反）：
- orchestrator.py:364-365 card 模式：candidates 为空时直接用
  _placeholder_task 生成 10 个模板任务入队，writer 拿到"推进剧情：推进主线，
  回收旧线索"等空泛文本。
- orchestrator.py:376 talk 模式：result.get("tasks") 为空时同样兜底。
- 影响：30 章长跑前几章看起来"在写"，实际内容空泛，读者/审稿视角才发现；
  chapter 数字在动但全是 placeholder 模板（比 fake PASS 章节更隐蔽）。

修复：
- 空候选时复用现有 _outline_failed 路径：state["_outline_failed"]=True +
  error_log + 提前 return state，不再让 placeholder 模板入队污染下游。
- 与 orchestrator.py:383-392 异常分支同等待遇（该分支已正确处理）。
"""

from __future__ import annotations

import pytest


@pytest.fixture
def orch(monkeypatch):
    """monkeypatch 友好的 orchestrator 引用。"""
    from engine import orchestrator as orch_mod
    return orch_mod


def _base_state(orch, *, outline_mode="card", arc_plans=None):
    """构造 node_load_arc_tasks 期望的最小 state。

    注意：orchestrator.py:343 实际从 os.environ.NOVEL_OUTLINE_MODE 读，
    不是 state["outline_mode"]。fixture 里同时设两者（state 兼容 + env 真实）。
    """
    if arc_plans is None:
        arc_plans = [{
            "arc_id": 1,
            "arc_name": "第一弧",
            "arc_goal": "主角获得金手指",
            "estimated_chapters": 5,
            "arc_climax_description": "首次大场面",
            "arc_climax_chapter_offset": 3,
            "emotion_curve": "压抑→爽快",
            "new_characters_introduced": [],
            "arc_ending_state": "主角小有名气",
            "is_final_arc": False,
        }]
    return {
        "novel_id": "default",
        "arc_plans": arc_plans,
        "chapter_task_queue": [],
        "current_arc": 0,
        "current_chapter": 0,
        "outline_mode": outline_mode,
        "error_log": [],
        "_outline_failed": False,
        "outline_candidates": [],
        "talk_questions": [],
        "platform": "fanqie",
        "approved_outline_tasks": {},
    }


# ── 1. card 模式返回空候选时必须标 _outline_failed ─────────────────

def test_card_mode_empty_candidates_marks_outline_failed(orch, monkeypatch):
    """run_outline_card 返回 [] 时 state._outline_failed=True，
    不让 _placeholder_task 模板入队污染下游。"""
    def fake_outline_card(arc, start, setting, memory):
        return [], 0.0  # LLM 失败 / 模型没返回任何候选
    monkeypatch.setattr(orch, "run_outline_card", fake_outline_card)
    monkeypatch.setattr(orch, "_setting", lambda: {"novel_id": "default"})
    monkeypatch.setenv("NOVEL_OUTLINE_MODE", "card")

    state = _base_state(orch, outline_mode="card")
    result = orch.node_load_arc_tasks(state)

    assert result.get("_outline_failed") is True, (
        "card 模式返回空候选时必须标 _outline_failed，"
        "现状：静默落到 _placeholder_task 模板污染下游"
    )


def test_card_mode_empty_candidates_does_not_enqueue_placeholders(orch, monkeypatch):
    """card 模式空候选时 chapter_task_queue 不应被 placeholder 填充。"""
    def fake_outline_card(arc, start, setting, memory):
        return [], 0.0
    monkeypatch.setattr(orch, "run_outline_card", fake_outline_card)
    monkeypatch.setattr(orch, "_setting", lambda: {"novel_id": "default"})
    monkeypatch.setenv("NOVEL_OUTLINE_MODE", "card")

    state = _base_state(orch, outline_mode="card")
    result = orch.node_load_arc_tasks(state)

    queue = result.get("chapter_task_queue", [])
    # 占位任务的 chapter_goal 含「推进剧情」「埋下关键伏笔」等模板词
    for task in queue:
        goal = task.get("chapter_goal", "")
        assert "埋下关键伏笔" not in goal, (
            f"placeholder 模板入队污染下游，task={task}"
        )
        assert "推进主线，回收旧线索" not in goal, (
            f"placeholder 模板入队污染下游，task={task}"
        )


def test_card_mode_empty_candidates_appends_error_log(orch, monkeypatch):
    """空候选时必须写 error_log（响亮信号）。"""
    def fake_outline_card(arc, start, setting, memory):
        return [], 0.0
    monkeypatch.setattr(orch, "run_outline_card", fake_outline_card)
    monkeypatch.setattr(orch, "_setting", lambda: {"novel_id": "default"})
    monkeypatch.setenv("NOVEL_OUTLINE_MODE", "card")

    state = _base_state(orch, outline_mode="card")
    result = orch.node_load_arc_tasks(state)

    assert any("outline" in e.lower() and (
        "empty" in e.lower() or "空" in e or "候选" in e or "candidate" in e.lower()
    ) for e in result.get("error_log", [])), (
        f"空候选时必须写 error_log（响亮），实际: "
        f"{result.get('error_log', [])[-3:]}"
    )


# ── 2. talk 模式返回空 tasks 时同样处理 ─────────────────────────

def test_talk_mode_empty_tasks_marks_outline_failed(orch, monkeypatch):
    """run_outline_talk 返回空 tasks 时同样标 _outline_failed。"""
    def fake_outline_talk(arc, start, setting, memory):
        return {"tasks": [], "questions": ["Q1"]}, 0.0
    monkeypatch.setattr(orch, "run_outline_talk", fake_outline_talk)
    monkeypatch.setattr(orch, "_setting", lambda: {"novel_id": "default"})
    monkeypatch.setenv("NOVEL_OUTLINE_MODE", "talk")

    state = _base_state(orch, outline_mode="talk")
    result = orch.node_load_arc_tasks(state)

    assert result.get("_outline_failed") is True, (
        "talk 模式返回空 tasks 时必须标 _outline_failed"
    )


# ── 3. 正常有候选时不应误伤 ─────────────────────────

def test_card_mode_with_candidates_does_not_mark_failed(orch, monkeypatch):
    """对照组：card 模式正常返回 1+ 候选时不应标 _outline_failed。"""
    def fake_outline_card(arc, start, setting, memory):
        # 1 个候选，含 3 个 task
        return [{"tasks": [
            {"chapter_number": 1, "chapter_goal": "g1"},
            {"chapter_number": 2, "chapter_goal": "g2"},
            {"chapter_number": 3, "chapter_goal": "g3"},
        ]}], 0.0
    monkeypatch.setattr(orch, "run_outline_card", fake_outline_card)
    monkeypatch.setattr(orch, "_setting", lambda: {"novel_id": "default"})
    monkeypatch.setenv("NOVEL_OUTLINE_MODE", "card")

    state = _base_state(orch, outline_mode="card")
    result = orch.node_load_arc_tasks(state)

    assert result.get("_outline_failed") is not True, (
        "正常候选时不应误标 _outline_failed（修复不能误伤正常路径）"
    )
    assert len(result.get("chapter_task_queue", [])) >= 1, (
        "正常候选时任务应入队"
    )


# ── 4. 回归：现有异常分支仍正常 fail-fast ─────────────────────────

def test_outline_exception_still_marks_failed(orch, monkeypatch):
    """run_outline_card 抛异常时仍走 _outline_failed 路径（既有功能，回归）。"""
    def fake_outline_card(arc, start, setting, memory):
        raise ConnectionError("outline LLM down")
    monkeypatch.setattr(orch, "run_outline_card", fake_outline_card)
    monkeypatch.setattr(orch, "_setting", lambda: {"novel_id": "default"})
    monkeypatch.setenv("NOVEL_OUTLINE_MODE", "card")

    state = _base_state(orch, outline_mode="card")
    result = orch.node_load_arc_tasks(state)

    assert result.get("_outline_failed") is True
    assert any("outline" in e.lower() for e in result.get("error_log", []))