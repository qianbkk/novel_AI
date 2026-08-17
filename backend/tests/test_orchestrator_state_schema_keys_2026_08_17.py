"""test_orchestrator_state_schema_keys_2026_08_17.py

P1-5 修复验证：OrchestratorState schema 必须显式声明所有运行时新增的 key，
否则 LangGraph 按声明合并，未声明的字段会被静默丢弃。

历史教训（state.py:142-146 注释）：
- node_load_arc_tasks 置的 _outline_failed 之前就因此丢失
- run_orchestrator 的 fail-fast 检测从未生效
- outline 失败后流程继续空转

修复（任务 P1-5）：
- OrchestratorState 显式声明 summarizer_metrics（orchestrator.py:1082 设置）
- OrchestratorState 显式声明 memory_gaps（orchestrator.py:1215 设置）

回归测试：检查两个 key 在 OrchestratorState.__annotations__ / __optional_keys__ /
TypedDict 字段中存在。
"""

from __future__ import annotations

import pytest


def test_summarizer_metrics_is_declared_in_state_schema():
    """summarizer_metrics 必须在 OrchestratorState schema 里声明，
    否则 LangGraph 会静默丢弃。"""
    from engine.state import OrchestratorState

    # TypedDict 把声明字段放进 __annotations__ / __optional_keys__
    declared = set(OrchestratorState.__annotations__.keys()) \
        | set(getattr(OrchestratorState, "__optional_keys__", set()) or set())

    assert "summarizer_metrics" in declared, (
        "summarizer_metrics 必须声明在 OrchestratorState 中，"
        "否则 orchestrator.node_save_and_track 写入的 arc 级 summary "
        "会被 LangGraph 按未声明字段静默丢弃，导致 frontend / 测试拿不到 "
        "arc_end 报告（与 _outline_failed 同型 bug）"
    )


def test_memory_gaps_is_declared_in_state_schema():
    """memory_gaps 必须在 OrchestratorState schema 里声明。"""
    from engine.state import OrchestratorState

    declared = set(OrchestratorState.__annotations__.keys()) \
        | set(getattr(OrchestratorState, "__optional_keys__", set()) or set())

    assert "memory_gaps" in declared, (
        "memory_gaps 必须声明在 OrchestratorState 中，"
        "否则 orchestrator.node_human_escalation 写入的章节级 gap 列表 "
        "会被 LangGraph 静默丢弃，导致 arc_end 报告 / 测试断言读不到"
    )


def test_audit_rule_layer_is_already_declared():
    """回归测试：audit_rule_layer 之前已声明（state.py:150），不能误删。"""
    from engine.state import OrchestratorState

    declared = set(OrchestratorState.__annotations__.keys()) \
        | set(getattr(OrchestratorState, "__optional_keys__", set()) or set())

    assert "audit_rule_layer" in declared, (
        "audit_rule_layer 之前已声明，禁止本次修复误删"
    )


def test_create_initial_state_initializes_new_keys():
    """create_initial_state 必须给新增 key 显式默认值（避免 KeyError）。"""
    from engine.state import create_initial_state

    state = create_initial_state(
        novel_id="test",
        title="测试",
        platform="fanqie",
        genre="都市",
        setting_concept="测试用",
    )

    # 缺省值必须是空 dict / 空 list，不能 raise KeyError
    assert state.get("summarizer_metrics") == {}, (
        f"summarizer_metrics 缺省值应为 {{}}，实际: {state.get('summarizer_metrics')}"
    )
    assert state.get("memory_gaps") == [], (
        f"memory_gaps 缺省值应为 []，实际: {state.get('memory_gaps')}"
    )


def test_save_state_preserves_new_keys_through_roundtrip(tmp_path):
    """save_state → load_state 往返必须保留 summarizer_metrics 与 memory_gaps。"""
    from engine.state import create_initial_state, save_state, load_state

    state = create_initial_state(
        novel_id="test", title="t", platform="fanqie",
        genre="都市", setting_concept="",
    )
    state["summarizer_metrics"] = {"1": {"plan_vs_actual": {"score": 7.5}}}
    state["memory_gaps"]         = [{"chapter": 5, "issue": "test"}]

    state_file = tmp_path / "state.json"
    save_state(state, str(state_file))
    loaded = load_state(str(state_file))

    assert loaded.get("summarizer_metrics") == {"1": {"plan_vs_actual": {"score": 7.5}}}
    assert loaded.get("memory_gaps") == [{"chapter": 5, "issue": "test"}]