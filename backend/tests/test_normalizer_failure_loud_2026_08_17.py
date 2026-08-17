"""test_normalizer_failure_loud_2026_08_17.py

P1-4 修复验证：normalizer 异常必须响亮（CLAUDE.md 红线）。

历史 bug（审计发现）：
- node_write_pipeline:691 与 node_rewrite:885 两处 normalizer try/except 都
  静默 fallback（clean_text = raw_text / new_text），无 error_log 无
  _normalizer_failed 标记。
- 影响：normalizer 偶发超时/LLM 5xx → 本章含满篇对话癌/AI 腔落盘，
  下游 checker 仅扣文笔分不会 reject，污染不可观测。

修复（最小 + 与现有 _writer_failed/_checker_failed 同模式）：
- normalizer 异常 → task._normalizer_failed=True + state.error_log 追加条目
- route_after_pipeline / route_after_rewrite 检查 _normalizer_failed → escalate
  （与 _writer_failed 同等待遇，不让受污染章节静默落盘）
"""

from __future__ import annotations

import pytest


# ── 1. normalizer 异常时被标记 ─────────────────────────

@pytest.fixture
def orch(monkeypatch):
    """monkeypatch 友好的 orchestrator 引用 + 常用 fake。"""
    from engine import orchestrator as orch_mod
    return orch_mod


def _baseline_state(orch, task_extras=None):
    """构造 node_write_pipeline / node_rewrite 期望的最小 state。"""
    task = {"chapter_number": 99, "audit_mode": "full"}
    if task_extras:
        task.update(task_extras)
    return {
        "current_task": task,
        "current_chapter": 99,
        "rewrite_count_current": 0,
        "error_log": [],
        "chapter_task_queue": [],
        "platform": "fanqie",
    }


def test_write_pipeline_normalizer_failure_marks_task(orch, monkeypatch):
    """node_write_pipeline 中 normalizer 抛异常 → task._normalizer_failed=True。"""
    def fake_writer(task, memory, setting):
        return "ok 2000字 真实文本 " * 200, "fake_title", 0.0
    def fake_normalizer(text, task):
        raise ConnectionError("normalizer LLM 5xx")
    def fake_compliance(text, platform):
        return {"passed": True, "suggestion": ""}, 0.0
    def fake_checker(text, task, mode):
        # checker 不会被调用（normalizer 失败后应当 escalate，不再跑 checker）
        raise AssertionError("checker 不应被调用：normalizer 失败应短路")
    monkeypatch.setattr(orch, "run_writer", fake_writer)
    monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
    monkeypatch.setattr(orch, "run_compliance", fake_compliance)
    monkeypatch.setattr(orch, "run_checker", fake_checker)

    state = _baseline_state(orch)
    result = orch.node_write_pipeline(state)

    assert result["current_task"].get("_normalizer_failed") is True, (
        "normalizer 抛异常时 task._normalizer_failed 必须置 True（"
        "现状：静默 fallback 到 raw_text，污染下游不可观测）"
    )


def test_write_pipeline_normalizer_failure_appends_error_log(orch, monkeypatch):
    """normalizer 异常时 state.error_log 必须追加条目（响亮信号）。"""
    def fake_writer(task, memory, setting):
        return "ok text " * 200, "title", 0.0
    def fake_normalizer(text, task):
        raise TimeoutError("normalizer LLM timeout")
    def fake_compliance(text, platform):
        return {"passed": True, "suggestion": ""}, 0.0
    def fake_checker(text, task, mode):
        raise AssertionError("不应到 checker")
    monkeypatch.setattr(orch, "run_writer", fake_writer)
    monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
    monkeypatch.setattr(orch, "run_compliance", fake_compliance)
    monkeypatch.setattr(orch, "run_checker", fake_checker)

    state = _baseline_state(orch)
    result = orch.node_write_pipeline(state)

    assert any("normalizer" in e.lower() and "fail" in e.lower()
               for e in result.get("error_log", [])), (
        f"normalizer 异常必须写 error_log（响亮），实际: "
        f"{result.get('error_log', [])[-3:]}"
    )


# ── 2. _normalizer_failed 触发 escalate（与 _writer_failed 同等待遇） ─────────

def test_route_after_pipeline_escalates_on_normalizer_failed():
    """_normalizer_failed=True 时 route_after_pipeline 必须返回 escalate，
    即便 score 达到 PASS_SCORE（不能让受污染章节落盘）。"""
    from engine.orchestrator import route_after_pipeline, PASS_SCORE
    state = {
        "current_phase": "writing",
        "current_task": {
            "_normalizer_failed": True,
            "_checker_result": {"score": PASS_SCORE + 0.5},
        },
        "rewrite_count_current": 0,
    }
    assert route_after_pipeline(state) == "escalate", (
        "_normalizer_failed=True 必须 escalate（不能 save 静默污染章节）"
    )


def test_route_after_pipeline_does_not_escalate_without_normalizer_flag():
    """对照组：_normalizer_failed 缺省或 False 时，正常 PASS 仍走 save。"""
    from engine.orchestrator import route_after_pipeline, PASS_SCORE
    state = {
        "current_phase": "writing",
        "current_task": {
            "_checker_result": {"score": PASS_SCORE},
        },
        "rewrite_count_current": 0,
    }
    assert route_after_pipeline(state) == "save"


# ── 3. 既有 _writer_failed / _checker_failed 仍按原路径 escalate ─────────

def test_route_after_pipeline_still_escalates_on_writer_failed():
    """回归测试：修复不能误伤 _writer_failed 的既有 escalate 路径。"""
    from engine.orchestrator import route_after_pipeline
    state = {
        "current_phase": "writing",
        "current_task": {"_writer_failed": True, "_checker_result": {"score": 7.0}},
        "rewrite_count_current": 0,
    }
    assert route_after_pipeline(state) == "escalate"


# ── 4. normalizer 异常时不应让 raw_text 静默流入下游 ─────────

def test_write_pipeline_normalizer_failure_does_not_set_clean_text(orch, monkeypatch):
    """normalizer 异常时不应把 raw_text 当 clean_text 写回（保留 _draft_text=""）。
    现状：clean_text = raw_text，下游 checker 会基于污染文本打分。
    修复后：应 escalate，不应继续把污染文本传给 compliance/checker。"""
    def fake_writer(task, memory, setting):
        return "污染文本（满篇对话癌）" * 100, "title", 0.0
    def fake_normalizer(text, task):
        raise RuntimeError("normalizer broken")
    monkeypatch.setattr(orch, "run_writer", fake_writer)
    monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)

    state = _baseline_state(orch)
    result = orch.node_write_pipeline(state)

    # _normalizer_failed=True 时 _draft_text 不应被设为污染的 raw_text
    # （checker 不会被调用，污染文本不应继续流动）
    assert "_draft_text" not in result["current_task"] or \
           not result["current_task"].get("_draft_text"), (
        "_normalizer_failed 时 _draft_text 应为空/缺省，"
        "不能让污染 raw_text 进入下游 pipeline"
    )