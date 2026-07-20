"""orchestrator 无任务路径回归测试。

e2e 实跑发现（2026-07-19）：新项目跳过 init_arc 直接 run 时，
state.arc_plans=[] → node_load_arc_tasks 标 current_phase="done"，
但图的边是无条件 load_arc_tasks → get_next_task → write_pipeline，
write_pipeline / save_and_track 都直接 `state["current_task"]` +
`task.get(...)` → task=None 时 AttributeError/TypeError 裸崩，
用户只看到 "ERR writer failed: 'NoneType' object has no attribute 'get'"
和 exit_code=1，完全没有可操作的提示。

约束：不改 LangGraph 拓扑（CLAUDE.md）——修法是给 write_pipeline /
save_and_track 加 no-task 短路守卫，沿既有 "done" 路由自然走到 END。
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest


@pytest.fixture()
def isolated_engine_paths(monkeypatch, tmp_path):
    """把 orchestrator / memory 的落盘常量指到 tmp——
    绝不能让测试覆盖 backend/data/engine/output/orchestrator_state.json
    （用户真实进度）。"""
    import engine.orchestrator as orch
    import engine.memory.manager as mgr

    setting_file = tmp_path / "setting_package.json"
    setting_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(orch, "STATE_PATH", tmp_path / "orchestrator_state.json")
    monkeypatch.setattr(orch, "SETTING_PATH", setting_file)
    monkeypatch.setattr(mgr, "L2_DIR_STR", str(tmp_path))
    return tmp_path


def test_run_orchestrator_empty_arc_plans_terminates_cleanly(isolated_engine_paths, capsys):
    """arc_plans=[]（新项目漏跑 init_arc）时 run 必须干净结束：
    不抛异常、不调 writer、终态 phase=done，并打印可操作的提示。"""
    import engine.orchestrator as orch
    from engine.state import create_initial_state

    state = create_initial_state(
        novel_id="empty-arc-regression",
        title="t",
        platform="fanqie",
        genre="都市",
        setting_concept="",
        budget_limit_usd=1.0,
    )
    out = orch.run_orchestrator(state, max_chapters=1)
    assert out.get("current_phase") == "done"
    printed = capsys.readouterr().out
    assert "init_arc" in printed, (
        "无任务结束时必须提示用户先跑 init_arc（否则用户不知道缺哪步）"
    )


def test_write_pipeline_none_task_does_not_call_writer(isolated_engine_paths, monkeypatch):
    """current_task=None 时 write_pipeline 不得调用 run_writer。"""
    import engine.orchestrator as orch

    called = {"writer": False}

    def _boom(*a, **k):
        called["writer"] = True
        raise AssertionError("run_writer 不应被调用")

    monkeypatch.setattr(orch, "run_writer", _boom)
    state = {"current_task": None, "current_phase": "writing", "novel_id": "x"}
    out = orch.node_write_pipeline(state)
    assert called["writer"] is False
    assert out.get("current_phase") == "done"


def test_outline_failure_stops_run_and_propagates_flag(isolated_engine_paths, monkeypatch, capsys):
    """outline 失败时 run 必须立即终止并对用户可见。

    历史缺陷：node_load_arc_tasks 置 state["_outline_failed"]=True，但该键
    未在 OrchestratorState TypedDict 声明 → LangGraph 按 schema 合并时静默
    丢弃 → run_orchestrator 的 '_outline_failed 检测后停' 从加入起就是死代码，
    outline 失败后流程继续空转（e2e 实跑 2026-07-19 发现）。"""
    import engine.orchestrator as orch
    from engine.state import create_initial_state

    def _raise(*a, **k):
        raise RuntimeError("mock outline boom")

    monkeypatch.setattr(orch, "run_outline", _raise)
    state = create_initial_state(
        novel_id="outline-fail-regression",
        title="t",
        platform="fanqie",
        genre="都市",
        setting_concept="",
        budget_limit_usd=1.0,
    )
    state["arc_plans"] = [{
        "arc_id": 1, "arc_name": "测试弧", "arc_goal": "g",
        "estimated_chapters": 3, "arc_climax_description": "",
        "arc_climax_chapter_offset": 2, "emotion_curve": "",
        "new_characters_introduced": [], "arc_ending_state": "",
        "is_final_arc": True,
    }]
    state["total_arcs_planned"] = 1
    out = orch.run_orchestrator(state, max_chapters=1)
    printed = capsys.readouterr().out
    assert out.get("_outline_failed") is True, (
        "_outline_failed 必须在图状态里存活（需在 OrchestratorState 声明）"
    )
    assert "outline 失败" in printed, "必须对用户打印 outline 失败终止提示"
    assert not list(Path(isolated_engine_paths).rglob("ch_*.txt"))


def test_noop_save_does_not_count_as_completed_chapter(isolated_engine_paths, capsys):
    """无任务短路结束时，不得打印 '✅ [1/N] Ch0 完成' 这类误导计数。"""
    import engine.orchestrator as orch
    from engine.state import create_initial_state

    state = create_initial_state(
        novel_id="noop-count-regression",
        title="t",
        platform="fanqie",
        genre="都市",
        setting_concept="",
        budget_limit_usd=1.0,
    )
    orch.run_orchestrator(state, max_chapters=2)
    printed = capsys.readouterr().out
    assert "完成" not in printed or "Ch0 " not in printed, (
        f"no-op save 不应计入完成章节，实际输出：\n{printed}"
    )


def test_run_outline_renumbers_tasks_from_start_chapter(monkeypatch):
    """run_outline 返回的任务章号必须从 start_chapter 连续编号。

    历史缺陷（e2e 实跑 2026-07-19）：LLM/mock 返回的 chapter_number 是
    绝对值（如固定 1..3），忽略提示词里的起始章 → get_next_task 把
    current_chapter 拉回旧值 → 队列耗尽后 load_arc_tasks 用
    current_chapter+1 重拆出同一批章节 → 无限循环直到 recursion_limit
    （真实模式下持续烧预算）。章号完全可由 start_chapter 推导，
    与 LLM 输出不符时必须确定性重编号。"""
    from unittest.mock import MagicMock
    import engine.agents.outline as outline_mod

    bad_tasks = [
        {"chapter_number": 1, "chapter_role": "铺垫", "chapter_goal": "g1",
         "ending_hook_type": "悬念钩", "foreshadowing_ops": []},
        {"chapter_number": 2, "chapter_role": "发展", "chapter_goal": "g2",
         "ending_hook_type": "危机钩", "foreshadowing_ops": []},
    ]
    import json as _json
    mock_router = MagicMock()
    mock_router.call.return_value = (_json.dumps(bad_tasks, ensure_ascii=False), 0.001)
    monkeypatch.setattr(outline_mod, "get_active_router", lambda: mock_router)

    arc = {"arc_id": 2, "arc_name": "第二弧", "estimated_chapters": 2}
    tasks, _cost = outline_mod.run_outline(arc, start_chapter=4, setting={}, memory={})
    got = [t["chapter_number"] for t in tasks]
    assert got == [4, 5], f"起始章 4 时任务章号应为 [4, 5]，实际 {got}"


def test_write_pipeline_rule_layer_actually_runs(isolated_engine_paths, monkeypatch):
    """规则层预检必须真正执行（audit_rule_layer 记录 + _rule_feedback 注入）。

    历史缺陷��e2e 实跑 2026-07-19）：orchestrator 里 `analyze_chapter(
    clean_text, prev_openings)` 引用了不存在的变量（实际叫 prev_opens）
    → NameError 被 '非阻塞' try/except 吞掉 → 零成本规则层自加入起
    从未生效，只在日志里留一行 warning。"""
    import engine.orchestrator as orch

    monkeypatch.setattr(orch, "run_writer",
                        lambda task, mem, setting: ("测试正文。" * 400, "测试标题", 0.0))
    monkeypatch.setattr(orch, "run_normalizer", lambda text, task: (text, [], 0.0))
    monkeypatch.setattr(orch, "run_compliance", lambda *a, **k: ({"passed": True}, 0.0))
    monkeypatch.setattr(
        orch, "run_checker",
        lambda text, task, mode: ({"score": 8.0, "verdict": "PASS",
                                   "dimensions": {}, "rewrite_level": "none",
                                   "feedback": "", "strongest_point": "",
                                   "weakest_point": ""}, 0.0))
    state = {
        "current_task": {"chapter_number": 1, "chapter_role": "铺垫",
                         "chapter_goal": "g", "shuang_description": ""},
        "novel_id": "rule-layer-regression",
        "current_phase": "writing",
        "audit_mode": "full",
        "platform": "fanqie",
    }
    out = orch.node_write_pipeline(state)
    assert out.get("audit_rule_layer"), (
        "规则层必须执行并把结果记入 state.audit_rule_layer"
        "（NameError 被静默吞掉时这里为空）"
    )
    assert out.get("_recent_chapter_openings"), "本章开场必须存入最近开场列表"


def test_state_declares_cross_node_keys():
    """所有跨节点读写的 state 键必须在 OrchestratorState TypedDict 声明，
    否则 LangGraph 按 schema 合并时静默丢弃（_outline_failed 同型缺陷）。"""
    from engine.state import OrchestratorState

    ann = OrchestratorState.__annotations__
    for key in ("_outline_failed", "_recent_chapter_openings", "audit_rule_layer"):
        assert key in ann, (
            f"OrchestratorState 缺 '{key}' 声明——该键会在节点间被 LangGraph 丢弃"
        )


def test_save_and_track_none_task_is_noop(isolated_engine_paths):
    """current_task=None 时 save_and_track 不得落盘、不得抛异常。"""
    import engine.orchestrator as orch

    state = {"current_task": None, "current_phase": "done", "novel_id": "x"}
    out = orch.node_save_and_track(state)
    assert out is state or isinstance(out, dict)
    chapters = list(Path(isolated_engine_paths).rglob("ch_*.txt"))
    assert chapters == [], "无任务时不应写出任何章节文件"
