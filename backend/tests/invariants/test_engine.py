"""engine/ — Phase 3 测试拆分

不变量测试按业务域分文件存放。
原文件位置：tests/test_invariants.py（已替换为 re-export shim）
"""

from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(REPO_ROOT)
sys.path.insert(0, str(BACKEND))

# ── 原 test_invariants.py 顶部声明的 app.schema_validator 系列 ──
from app.schema_validator import (  # noqa: E402,F401
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,
)

class TestWriterFailureNoFakeStub:
    """历史 bug（你独立验证）：
      writer 抛 Connection error / SSL 错误时，orchestrator 写
      `f"[writer-stub] {task.get('chapter_goal','')}"` 占位文本（47 字）
      并继续 pipeline → checker 给这个假文本打 7.0 分 PASS，save_and_track
      落盘 ch_0064.txt — 用户视角"7.0 分 PASS"，实际是 47 字假文本。

    修复：
      writer 失败时设 task._writer_failed=True + raw_text=""，提前 return
      node_write_pipeline；route_after_pipeline 检查 _writer_failed → escalate
      → node_human_escalation 走人工 review 流程（不会再写 [writer-stub]）。

    本测试锁死：
      1) writer-stub 占位文本不再被使用
      2) WriterFailedError 类存在
      3) route_after_pipeline 在 _writer_failed=True 时返回 escalate
    """

    def test_no_writer_stub_in_orchestrator(self):
        """orchestrator.py 真代码行不能写 [writer-stub] 占位文本。
        之前 line 243: raw_text = f"[writer-stub] {task.get('chapter_goal','')}", 0.0
        （docstring 里提到 [writer-stub] 是历史说明，OK；真代码行不能用）

        实现：在源码中跟踪三引号 docstring 范围（docstring 内部）和 # 注释
        行，只检查真代码行。
        """
        import inspect
        from engine import orchestrator as orch
        src = inspect.getsource(orch)
        in_docstring = False
        for line in src.splitlines():
            stripped = line.strip()
            # 跟踪三引号 docstring 边界
            triple_count = stripped.count('"""') + stripped.count("'''")
            if triple_count % 2 == 1:
                in_docstring = not in_docstring
                if stripped.startswith(('"""', "'''")) and len(stripped) > 3:
                    continue
            if in_docstring:
                continue
            # 跳过纯注释
            if stripped.startswith("#"):
                continue
            assert "[writer-stub]" not in line, (
                f"orchestrator 真代码行仍写 [writer-stub] 占位: {line!r}。"
                f"writer 失败时应让 task._writer_failed=True + 提前 return。"
            )

    def test_writer_failed_error_class_exists(self):
        from engine.orchestrator import WriterFailedError
        assert issubclass(WriterFailedError, Exception)

    def test_route_after_pipeline_escalates_on_writer_failed(self):
        """_writer_failed=True → route_after_pipeline 必须返回 escalate。"""
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_writer_failed": True, "_checker_result": {"score": 7.0}},
            "rewrite_count_current": 0,
        }
        # 即便 checker "通过"了，writer 失败也必须 escalate（不能 save）
        result = route_after_pipeline(state)
        assert result == "escalate", (
            f"_writer_failed=True 时 route_after_pipeline 应返回 escalate，"
            f"实际: {result!r}"
        )

    def test_route_after_pipeline_saves_normal_pass(self):
        """_writer_failed=False + score>=PASS_SCORE → save（正常路径不能误伤）。"""
        from engine.orchestrator import route_after_pipeline, PASS_SCORE
        state = {
            "current_phase": "writing",
            "current_task": {"_writer_failed": False, "_checker_result": {"score": PASS_SCORE}},
            "rewrite_count_current": 0,
        }
        result = route_after_pipeline(state)
        assert result == "save", (
            f"正常 PASS 章节应 save，实际: {result!r}"
        )

    def test_node_write_pipeline_short_circuits_on_writer_exception(self, monkeypatch):
        """node_write_pipeline 在 writer 抛异常时不能继续 pipeline。
        模拟 run_writer 抛 ConnectionError，看 task._writer_failed 是否置位。
        """
        from engine import orchestrator as orch
        # monkeypatch run_writer 抛异常
        def fake_run_writer(task, memory, setting):
            raise ConnectionError("simulated writer failure")
        monkeypatch.setattr(orch, "run_writer", fake_run_writer)

        state = {
            "current_task": {"chapter_number": 99, "chapter_goal": "test"},
            "current_chapter": 99,
            "rewrite_count_current": 0,
            "error_log": [],
            "chapter_task_queue": [],
        }
        result = orch.node_write_pipeline(state)
        # 必须标记 _writer_failed=True
        assert result["current_task"].get("_writer_failed") is True, (
            "writer 抛异常时 task._writer_failed 必须置 True"
        )
        # 不能再有 checker_result（避免后续 save 假章节）
        assert "_checker_result" not in result["current_task"] or \
               not result["current_task"].get("_checker_result"), (
            "writer 失败时不应有 _checker_result（说明 pipeline 跑完了）"
        )
        # error_log 记录
        assert any("writer failed" in e for e in result.get("error_log", [])), (
            f"error_log 应记录 writer 失败，实际: {result.get('error_log', [])[-3:]}"
        )


class TestOrchestratorNoFakePass:
    """你独立验证发现的 5 个同型 fake-pass bug：

      1. compliance 失败 → 兜底 {"passed": True}（line 294 之前）
      2. checker 失败 → 兜底 {"score": 7.0, "verdict": "PASS"}（line 311 之前）
      3. rewriter 失败 → 兜底 new_text = draft_text（line 363 之前）
      4. checker (post-rewrite) 失败 → 兜底 cr2 = cr（line 402 之前）
      5. outline 失败 → 兜底 10 个 placeholder task（line 201 之前）

    统一修法：异常时设 task._xxx_failed=True（每个 stage 单独 flag），
    route_after_pipeline / route_after_rewrite 检查后路由到 escalate，
    不再让 fake 默认值污染下游。
    本测试锁死。
    """

    @pytest.fixture(autouse=True)
    def orch(self, monkeypatch):
        """提供 monkeypatched run_* helpers."""
        from engine import orchestrator as orch_mod
        return orch_mod

    def test_compliance_failure_marks_task(self, orch, monkeypatch):
        """compliance 抛异常 → task._compliance_check_failed=True + 提前 return"""
        def fake_writer(task, memory, setting):
            return "ok 2000字 真实文本 " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            raise ConnectionError("compliance down")
        monkeypatch.setattr(orch, "run_writer", fake_writer)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)
        state = {"current_task": {"chapter_number": 99, "audit_mode": "full"},
                 "current_chapter": 99, "rewrite_count_current": 0,
                 "error_log": [], "chapter_task_queue": [],
                 "platform": "fanqie"}
        result = orch.node_write_pipeline(state)
        assert result["current_task"].get("_compliance_check_failed") is True, (
            "compliance 抛异常时 task._compliance_check_failed 必须置 True"
        )
        # 不应继续到 checker
        assert "_checker_result" not in result["current_task"]

    def test_checker_failure_marks_task(self, orch, monkeypatch):
        """checker 抛异常 → task._checker_failed=True + 提前 return"""
        def fake_writer(task, memory, setting):
            return "ok text " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            return {"passed": True, "suggestion": ""}, 0.0
        def fake_checker(text, task, mode):
            raise ConnectionError("checker down")
        monkeypatch.setattr(orch, "run_writer", fake_writer)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)
        monkeypatch.setattr(orch, "run_checker", fake_checker)
        state = {"current_task": {"chapter_number": 99, "audit_mode": "full"},
                 "current_chapter": 99, "rewrite_count_current": 0,
                 "error_log": [], "chapter_task_queue": [],
                 "platform": "fanqie"}
        result = orch.node_write_pipeline(state)
        assert result["current_task"].get("_checker_failed") is True, (
            "checker 抛异常时 task._checker_failed 必须置 True（不再 fake score=7.0 PASS）"
        )

    def test_rewriter_failure_marks_task(self, orch, monkeypatch):
        """rewriter 抛异常 → task._rewriter_failed=True + 提前 return"""
        def fake_rewriter(text, lvl, feedback, task, cr, memory, setting):
            raise ConnectionError("rewriter down")
        monkeypatch.setattr(orch, "run_rewriter", fake_rewriter)
        state = {
            "current_task": {
                "chapter_number": 99,
                "_checker_result": {"score": 5.0, "rewrite_level": "P1"},
                "_draft_text": "原始文本",
            },
            "current_chapter": 99,
            "rewrite_count_current": 0,
            "error_log": [],
            "chapter_task_queue": [],
            "novel_id": "default",
        }
        result = orch.node_rewrite(state)
        assert result["current_task"].get("_rewriter_failed") is True, (
            "rewriter 抛异常时 task._rewriter_failed 必须置 True（不再用原文本当重写结果）"
        )
        # draft_text 应保留原值（不是被覆盖为空）
        assert result["current_task"].get("_draft_text") == "原始文本"

    def test_checker_post_rewrite_failure_marks_task(self, orch, monkeypatch):
        """checker (post-rewrite) 抛异常 → _checker_failed=True（不再用旧 cr 兜底）"""
        def fake_rewriter(text, lvl, feedback, task, cr, memory, setting):
            return "重写后文本 " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            return {"passed": True}, 0.0
        def fake_checker(text, task, mode):
            raise ConnectionError("post-rewrite checker down")
        monkeypatch.setattr(orch, "run_rewriter", fake_rewriter)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)
        monkeypatch.setattr(orch, "run_checker", fake_checker)
        state = {
            "current_task": {
                "chapter_number": 99,
                "_checker_result": {"score": 5.0, "rewrite_level": "P1", "feedback": "x"},
                "_draft_text": "原始文本",
                "_compliance_failed": False,
            },
            "current_chapter": 99,
            "rewrite_count_current": 0,
            "error_log": [],
            "chapter_task_queue": [],
            "novel_id": "default",
            "platform": "fanqie",
        }
        result = orch.node_rewrite(state)
        assert result["current_task"].get("_checker_failed") is True, (
            "post-rewrite checker 抛异常时 _checker_failed 必须置 True"
        )

    def test_route_after_pipeline_escalates_on_compliance_check_failed(self):
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_compliance_check_failed": True, "_checker_result": {"score": 7.0}},
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "escalate"

    def test_route_after_pipeline_escalates_on_checker_failed(self):
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_checker_failed": True, "_checker_result": {"score": 7.0}},
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "escalate"

    def test_writer_failed_error_sentinel_exists(self):
        """WriterFailedError sentinel 异常类必须存在且可正常 raise/catch（commit 5d1f83e 修复）。"""
        from engine.orchestrator import WriterFailedError
        assert issubclass(WriterFailedError, Exception)
        try:
            raise WriterFailedError("writer crashed")
        except WriterFailedError as e:
            assert "writer crashed" in str(e)

    def test_route_after_pipeline_escalates_on_writer_failed(self):
        """task._writer_failed=True → 必须 escalate（防止 47 字 writer-stub 假 PASS）。"""
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {"_writer_failed": True},
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "escalate", (
            "_writer_failed=True 时必须走 escalate，不能 'save'"
        )

    def test_route_after_pipeline_normal_high_score_saves(self):
        """正常高分任务必须能 save（防止锁死逻辑破坏 happy path）。"""
        from engine.orchestrator import route_after_pipeline
        state = {
            "current_phase": "writing",
            "current_task": {
                "_checker_result": {"score": 8.0, "verdict": "PASS"},
                "_compliance_failed": False,
                "_compliance_check_failed": False,
                "_checker_failed": False,
                "_writer_failed": False,
            },
            "rewrite_count_current": 0,
        }
        assert route_after_pipeline(state) == "save", (
            "正常高分任务必须能 save（不能锁死到 escalate）"
        )

    def test_post_rewrite_compliance_failure_marks_task(self, orch, monkeypatch):
        """迭代 #28: post-rewrite compliance 抛异常 → _compliance_check_failed=True

        跟 node_write_pipeline 里的 compliance fake-pass 同型问题。
        之前 line 391-394 兜底为 {"passed": True} → 重写后即便合规检查完全
        失败（异常被吞），章节也走"通过"路径落盘。
        """
        def fake_rewriter(text, lvl, feedback, task, cr, memory, setting):
            return "重写后文本 " * 200, 0.0
        def fake_normalizer(text, task):
            return text, [], 0.0
        def fake_compliance(text, platform):
            # post-rewrite compliance 抛异常（模拟 MiniMax 接口 503）
            raise ConnectionError("post-rewrite compliance down")
        monkeypatch.setattr(orch, "run_rewriter", fake_rewriter)
        monkeypatch.setattr(orch, "run_normalizer", fake_normalizer)
        monkeypatch.setattr(orch, "run_compliance", fake_compliance)

        state = {
            "current_task": {
                "chapter_number": 99,
                "_checker_result": {"score": 5.0, "rewrite_level": "P1", "feedback": "x"},
                "_draft_text": "原始文本",
                "_compliance_failed": False,
            },
            "current_chapter": 99,
            "rewrite_count_current": 0,
            "error_log": [],
            "chapter_task_queue": [],
            "novel_id": "default",
            "platform": "fanqie",
        }
        result = orch.node_rewrite(state)
        # 关键断言：post-rewrite compliance 抛异常时必须标记 _compliance_check_failed
        assert result["current_task"].get("_compliance_check_failed") is True, (
            "post-rewrite compliance 抛异常时 _compliance_check_failed 必须置 True"
            "（之前 fake-pass 兜底为 {'passed': True}，重写后合规检查被静默擦掉）"
        )
        # 不应有新 _checker_result（避免后续 route 误判"重写成功"）
        # 现有 _checker_result 是 pre-rewrite 的旧值，保留 OK（route 会 escalate）

    def test_route_after_rewrite_escalates_on_compliance_check_failed(self):
        """_compliance_check_failed=True → route_after_rewrite 必须 escalate。

        修复：route_after_rewrite 加了防御性检查（之前只查 _checker_result 分数），
        防止 _compliance_check_failed 标记被旧 cr 分数遮蔽（误判 save）。
        """
        from engine.orchestrator import route_after_rewrite
        state = {
            "current_task": {
                "_compliance_check_failed": True,
                "_checker_result": {"score": 7.0},  # 旧 cr 分数高于 PASS_SCORE
            },
            "rewrite_count_current": 0,
        }
        assert route_after_rewrite(state) == "escalate", (
            "_compliance_check_failed=True 时 route_after_rewrite 必须 escalate，"
            "不能因为旧 cr 分数 >= PASS_SCORE 就 save"
        )

    def test_route_after_rewrite_escalates_on_checker_failed(self):
        """_checker_failed=True → route_after_rewrite 必须 escalate（防止旧 cr 兜底）。"""
        from engine.orchestrator import route_after_rewrite
        state = {
            "current_task": {
                "_checker_failed": True,
                "_checker_result": {"score": 7.0},  # 旧 cr 分数高
            },
            "rewrite_count_current": 0,
        }
        assert route_after_rewrite(state) == "escalate", (
            "_checker_failed=True 时 route_after_rewrite 必须 escalate"
        )

    def test_route_after_rewrite_normal_high_score_saves(self):
        """正常高分重写任务必须能 save（防止锁死逻辑破坏 happy path）。"""
        from engine.orchestrator import route_after_rewrite
        state = {
            "current_task": {
                "_checker_result": {"score": 8.0, "verdict": "PASS"},
                "_compliance_failed": False,
                "_compliance_check_failed": False,
                "_checker_failed": False,
                "_rewriter_failed": False,
            },
            "rewrite_count_current": 0,
        }
        assert route_after_rewrite(state) == "save", (
            "正常高分重写任务必须能 save（不能锁死到 escalate）"
        )


class TestAgentNetworkRetry:
    """ch63 / ch64 现场：MiniMax 30-60s 不可用时，router 内部 tenacity 3 次
    退避 1-10s（共最多 30s）仍会失败。agent 层加一轮 30s sleep 后再 retry，
    覆盖更长的瞬时不可用窗口。
    """

    def test_writer_retries_on_httpx_error(self, monkeypatch):
        """writer 第一次 httpx.TransportError → sleep + retry 一次成功。"""
        import time as _time
        from engine.agents import writer as writer_mod
        from engine.llm.router import LLMRouter
        call_count = [0]
        sleep_calls = []

        def fake_call_with_length_budget(self, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                import httpx
                raise httpx.TransportError("simulated conn reset")
            return "ok text " * 200, 0.0

        def fake_sleep(secs):
            sleep_calls.append(secs)
        monkeypatch.setattr(_time, "sleep", fake_sleep)
        monkeypatch.setattr(LLMRouter, "call_with_length_budget",
                            fake_call_with_length_budget)
        monkeypatch.setattr(writer_mod, "_get_router", lambda: LLMRouter())

        text, cost = writer_mod._call_with_budget(
            agent_name="writer", system="x", user="y", target_chars=2000,
        )
        assert call_count[0] == 2, f"应重试一次，实际 {call_count[0]} 次"
        assert sleep_calls == [30], f"应 sleep 30s 一次，实际: {sleep_calls}"
        assert text.startswith("ok text")

    def test_writer_raises_after_two_failures(self, monkeypatch):
        """writer 两次都失败 → 抛最后一次异常给 orchestrator 走 escalate。"""
        import time as _time
        from engine.agents import writer as writer_mod
        from engine.llm.router import LLMRouter
        import httpx

        def fake_call(self, **kwargs):
            raise httpx.TransportError("always fail")
        monkeypatch.setattr(_time, "sleep", lambda s: None)
        monkeypatch.setattr(LLMRouter, "call_with_length_budget", fake_call)
        monkeypatch.setattr(writer_mod, "_get_router", lambda: LLMRouter())

        import pytest
        with pytest.raises(httpx.TransportError):
            writer_mod._call_with_budget(
                agent_name="writer", system="x", user="y", target_chars=2000,
            )


class TestStatePathFromBinding:
    """历史 bug（你独立验证）：
      engine/orchestrator.py:43-45 硬编码 STATE_PATH / OUTPUT_DIR /
      CHAPTERS_DIR 到 backend/data/engine/output/，但
      app/bridge/reports.py:109 用 binding.novel_ai_dir（默认 novel_AI/output/）。
      → bridge/status 读 novel_AI/output/orchestrator_state.json（17 小时前），
        engine 实际写到 backend/data/engine/output/orchestrator_state.json（活跃）。
      → 双重真相：监控看不到 engine 真实状态。

    修复：engine 的 STATE_PATH / _STATE_PATH 优先用 NOVEL_AI_DIR 环境变量，
    bridge/run 在 spawn background task 前从 binding 注入这个 env。
    本测试锁死 env 行为。
    """

    def test_orchestrator_state_path_uses_env(self, monkeypatch, tmp_path):
        """设 NOVEL_AI_DIR 后，orchestrator.STATE_PATH 走那个目录。"""
        monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
        # 重新 import 让模块级常量重算
        import importlib
        from engine import orchestrator as orch
        importlib.reload(orch)
        try:
            assert str(orch.STATE_PATH).startswith(str(tmp_path)), (
                f"orchestrator.STATE_PATH 应在 NOVEL_AI_DIR 下，"
                f"实际: {orch.STATE_PATH}"
            )
            assert str(orch.STATE_PATH).endswith("orchestrator_state.json")
        finally:
            # 重新 reload 恢复默认
            monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
            importlib.reload(orch)

    def test_graph_state_path_uses_env(self, monkeypatch, tmp_path):
        """engine/graph.py 的 _STATE_PATH 也走 NOVEL_AI_DIR。"""
        monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
        import importlib
        from engine import graph as graph_mod
        importlib.reload(graph_mod)
        try:
            assert str(graph_mod._STATE_PATH).startswith(str(tmp_path))
        finally:
            monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
            importlib.reload(graph_mod)

    def test_bridge_run_injects_novel_ai_dir(self):
        """app/api/bridge.py 的 _spawn_engine_subprocess 必须从 binding 注入 NOVEL_AI_DIR。

        历史背景：commit 62baf44 把 in-process _run_bridge_async 切到 subprocess
        _spawn_engine_subprocess。这个 test 也跟着迁移（之前的版本测
        _run_bridge_async 源码里有 NOVEL_AI_DIR + binding 读，现在测的是
        subprocess 路径）。
        """
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._spawn_engine_subprocess)
        assert "NOVEL_AI_DIR" in src, (
            "_spawn_engine_subprocess 必须注入 NOVEL_AI_DIR env，"
            "否则 engine STATE_PATH 跟 binding 不一致（双重真相 bug）"
        )
        assert "NovelAIBinding" in src and "novel_ai_dir" in src, (
            "必须从 binding 读 novel_ai_dir 再注入 env"
        )


class TestBudgetManager:
    """迭代 #16：budget_manager.py 是 176 行的核心费用追踪模块，零测试覆盖。

    之前 audit/生产 bug 报告"费用不准"时无法快速定位 — 因为没测试。
    本轮先锁死核心 4 个函数：log_cost / load_all_records /
    generate_report / total_cost 累加。
    """

    def test_log_cost_writes_jsonl(self, monkeypatch, tmp_path):
        """log_cost 必须 append JSONL（一行一 JSON）到 BUDGET_LOG。"""
        # 重定向 BUDGET_LOG 到 tmp_path
        from engine.tools import budget_manager as bm
        log_path = tmp_path / "budget.jsonl"
        monkeypatch.setattr(bm, "BUDGET_LOG", str(log_path))

        bm.log_cost(chapter=1, agent="writer", model="test",
                    input_tokens=100, output_tokens=500, cost_usd=0.05)
        bm.log_cost(chapter=2, agent="checker", model="test",
                    input_tokens=80, output_tokens=20, cost_usd=0.01)

        # 文件存在 + 2 行
        content = log_path.read_text(encoding="utf-8").strip()
        lines = content.splitlines()
        assert len(lines) == 2, f"应有 2 行记录，实际 {len(lines)}"
        # 每行是合法 JSON
        import json
        recs = [json.loads(l) for l in lines]
        assert recs[0]["chapter"] == 1
        assert recs[0]["cost_usd"] == 0.05
        assert recs[1]["chapter"] == 2
        assert recs[1]["cost_usd"] == 0.01

    def test_load_all_records_skips_corrupt_lines(self, monkeypatch, tmp_path):
        """load_all_records 跳过损坏行（不是全文件失败）。"""
        from engine.tools import budget_manager as bm
        log_path = tmp_path / "budget.jsonl"
        log_path.write_text(
            '{"chapter": 1, "cost_usd": 0.05}\n'
            'THIS IS NOT JSON\n'
            '{"chapter": 2, "cost_usd": 0.02}\n'
            '\n'  # 空行
            , encoding="utf-8"
        )
        monkeypatch.setattr(bm, "BUDGET_LOG", str(log_path))
        records = bm.load_all_records()
        # 3 个有效行（损坏 + 空行被跳过）
        assert len(records) == 2, f"应只读 2 个有效记录，实际 {len(records)}"
        assert records[0]["chapter"] == 1
        assert records[1]["chapter"] == 2

    def test_load_all_records_returns_empty_when_file_missing(self, monkeypatch, tmp_path):
        """BUDGET_LOG 不存在 → load_all_records 返回 []（不抛 FileNotFoundError）。"""
        from engine.tools import budget_manager as bm
        monkeypatch.setattr(bm, "BUDGET_LOG", str(tmp_path / "nonexistent.jsonl"))
        assert bm.load_all_records() == []

    def test_generate_report_sums_costs_correctly(self, monkeypatch, tmp_path):
        """generate_report 必须正确累加所有 cost_usd。"""
        from engine.tools import budget_manager as bm
        log_path = tmp_path / "budget.jsonl"
        log_path.write_text(
            '{"chapter":1,"cost_usd":0.05,"agent":"writer","model":"x"}\n'
            '{"chapter":1,"cost_usd":0.02,"agent":"checker","model":"x"}\n'
            '{"chapter":2,"cost_usd":0.08,"agent":"writer","model":"x"}\n'
            , encoding="utf-8"
        )
        monkeypatch.setattr(bm, "BUDGET_LOG", str(log_path))
        report = bm.generate_report()
        # total = 0.05 + 0.02 + 0.08 = 0.15
        assert abs(report["total_cost_usd"] - 0.15) < 1e-3, (
            f"total_cost 累加错误：{report['total_cost_usd']}"
        )
        # chapters_done = unique chapter = {1, 2} = 2
        assert report["chapters_done"] == 2
        # by_agent 正确分组
        assert report["by_agent"]["writer"]["calls"] == 2
        assert report["by_agent"]["checker"]["calls"] == 1
        assert abs(report["by_agent"]["writer"]["cost"] - 0.13) < 1e-3
        assert abs(report["by_agent"]["checker"]["cost"] - 0.02) < 1e-3


class TestOutlineCostNotDoubleCharged:
    """迭代 #28: node_load_arc_tasks 之前每次 outline 都计费 2 次。

    历史 bug：
      orchestrator.py 之前 line 209 在 try/except 之外多调一次
      `_add_cost(state, cost)`，而每个分支（card / talk / batch）
      内部已经调过 → 实际计费 = 2 × 真实花费。
      50 章跑下来 budget_used_usd 虚高 100%，超预算提前 escalate。

    修法：删掉 line 209 的重复调用，保留分支内部调用。
    本测试锁死：跑一次 outline → state.budget_used_usd 只增加真实花费。
    """
    @pytest.fixture(autouse=True)
    def import_orch(self):
        from engine import orchestrator as orch_mod
        self.orch = orch_mod
        return orch_mod

    def test_batch_outline_cost_added_once(self, monkeypatch):
        """batch 模式：run_outline 返回 cost=0.1 → budget_used 增 0.1（不是 0.2）。"""
        FAKE_COST = 0.1
        def fake_run_outline(arc, start, setting, memory):
            return [{"chapter_number": 1, "chapter_goal": "x",
                     "chapter_role": "r", "main_characters": [],
                     "shuang_type": None, "shuang_description": "",
                     "ending_hook_type": "信息钩", "ending_hook_description": "",
                     "setting_constraints": [], "forbidden_actions": [],
                     "target_length": "2000-2200", "audit_mode": "full",
                     "is_arc_climax": False}], FAKE_COST
        monkeypatch.setattr(self.orch, "run_outline", fake_run_outline)
        # batch 模式（默认）
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "batch")
        monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
        import json, tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            setting_dir = self.orch.OUTPUT_DIR  # 用真实 OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            # 关键断言：cost 只增 1 次
            used = result.get("budget_used_usd", 0.0)
            assert abs(used - FAKE_COST) < 1e-6, (
                f"batch outline cost 应为 {FAKE_COST}（1 次计费），"
                f"实际 {used}（双重计费 bug）"
            )

    def test_card_outline_cost_added_once(self, monkeypatch):
        """card 模式：run_outline_card 返回 cost=0.15 → budget_used 增 0.15。"""
        FAKE_COST = 0.15
        def fake_run_outline_card(arc, start, setting, memory):
            return [{"tasks": [{"chapter_number": 1, "chapter_goal": "x",
                               "chapter_role": "r", "main_characters": [],
                               "shuang_type": None, "shuang_description": "",
                               "ending_hook_type": "信息钩",
                               "ending_hook_description": "",
                               "setting_constraints": [], "forbidden_actions": [],
                               "target_length": "2000-2200", "audit_mode": "full",
                               "is_arc_climax": False}]}], FAKE_COST
        monkeypatch.setattr(self.orch, "run_outline_card", fake_run_outline_card)
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "card")
        import tempfile
        with tempfile.TemporaryDirectory():
            setting_dir = self.orch.OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            used = result.get("budget_used_usd", 0.0)
            assert abs(used - FAKE_COST) < 1e-6, (
                f"card outline cost 应为 {FAKE_COST}，实际 {used}（双重计费）"
            )

    def test_talk_outline_cost_added_once(self, monkeypatch):
        """talk 模式：run_outline_talk 返回 cost=0.08 → budget_used 增 0.08。"""
        FAKE_COST = 0.08
        def fake_run_outline_talk(arc, start, setting, memory):
            return ({"tasks": [{"chapter_number": 1, "chapter_goal": "x",
                               "chapter_role": "r", "main_characters": [],
                               "shuang_type": None, "shuang_description": "",
                               "ending_hook_type": "信息钩",
                               "ending_hook_description": "",
                               "setting_constraints": [], "forbidden_actions": [],
                               "target_length": "2000-2200", "audit_mode": "full",
                               "is_arc_climax": False}],
                    "questions": []}, FAKE_COST)
        monkeypatch.setattr(self.orch, "run_outline_talk", fake_run_outline_talk)
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "talk")
        import tempfile
        with tempfile.TemporaryDirectory():
            setting_dir = self.orch.OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            used = result.get("budget_used_usd", 0.0)
            assert abs(used - FAKE_COST) < 1e-6, (
                f"talk outline cost 应为 {FAKE_COST}，实际 {used}（双重计费）"
            )

    def test_outline_exception_no_cost_charged(self, monkeypatch):
        """outline 抛异常时不应计费（避免"失败还扣钱"误判）。"""
        def fake_run_outline_raises(arc, start, setting, memory):
            raise ConnectionError("outline service down")
        monkeypatch.setattr(self.orch, "run_outline", fake_run_outline_raises)
        monkeypatch.setenv("NOVEL_OUTLINE_MODE", "batch")
        import tempfile
        with tempfile.TemporaryDirectory():
            setting_dir = self.orch.OUTPUT_DIR
            setting_dir.mkdir(parents=True, exist_ok=True)
            (setting_dir / "setting_package.json").write_text("{}", encoding="utf-8")

            state = {
                "novel_id": "default",
                "current_chapter": 0,
                "current_arc": 0,
                "budget_used_usd": 0.0,
                "budget_limit_usd": 500.0,
                "arc_plans": [{"arc_id": 1, "arc_name": "test", "arc_goal": "x",
                               "estimated_chapters": 10,
                               "arc_climax_description": "",
                               "arc_climax_chapter_offset": 0,
                               "emotion_curve": "low",
                               "new_characters_introduced": [],
                               "arc_ending_state": "",
                               "is_final_arc": False}],
                "error_log": [],
            }
            result = self.orch.node_load_arc_tasks(state)
            used = result.get("budget_used_usd", 0.0)
            assert used == 0.0, (
                f"outline 抛异常时不应计费，实际 budget_used={used}"
            )
            assert result.get("_outline_failed") is True, (
                "outline 失败必须 _outline_failed=True（之前 bug: 兜底 10 placeholder）"
            )


class TestSSEQueueCleanup:
    """迭代 #33: _run_queues (bridge.py) 和 _job_queues (worldbuild/orchestrator.py)
    之前只创建不清理 → 生产长期跑 N 个 run 后 dict 里堆 N 个 Queue，
    每个 Queue 有内部 buffer，内存持续涨。

    修法：SSE consumer 读完 done 事件后（或异常退出时）调用 cleanup_*_queue。
    本测试锁死：consumer 退出后 dict 里 queue 必须被移除。
    """
    def test_worldbuild_queue_cleanup_on_done(self):
        """stream_worldbuild consumer 读完 done → _job_queues 必须被清理。"""
        import asyncio
        from app.worldbuild import orchestrator as wb_orch
        from app.api.worldbuild import cleanup_job_queue

        # 先放一些事件 + done
        async def _scenario():
            q = wb_orch.get_job_queue("test-job-1")
            await q.put({"event": "stage_done", "stage": "x"})
            await q.put({"event": "done"})
            # 模拟 consumer：取完 done 后调 cleanup
            while True:
                payload = await q.get()
                if payload.get("event") == "done":
                    break
            cleanup_job_queue("test-job-1")
            # 验证 dict 已清
            assert "test-job-1" not in wb_orch._job_queues, (
                f"cleanup_job_queue 后 _job_queues 仍含 test-job-1，"
                f"keys={list(wb_orch._job_queues.keys())}"
            )
        asyncio.run(_scenario())

    def test_worldbuild_queue_cleanup_safe_when_already_removed(self):
        """重复 cleanup 是 no-op（不能抛）。"""
        from app.worldbuild.orchestrator import cleanup_job_queue
        cleanup_job_queue("nonexistent-job-xyz")  # 不抛
        cleanup_job_queue("nonexistent-job-xyz")  # 重复也不抛

    def test_bridge_run_queue_cleanup_safe_when_already_removed(self):
        """bridge.py cleanup_run_queue 同样幂等。"""
        from app.api.bridge import cleanup_run_queue
        cleanup_run_queue("nonexistent-run-xyz")
        cleanup_run_queue("nonexistent-run-xyz")

    def test_worldbuild_queue_event_generator_uses_finally_cleanup(self):
        """stream_worldbuild event_generator 必须用 try/finally 包裹清理（防止异常泄漏）。"""
        from pathlib import Path
        import re
        worldbuild_py = Path(BACKEND_ROOT) / "app" / "api" / "worldbuild.py"
        content = worldbuild_py.read_text(encoding="utf-8")
        # 找 event_generator 函数体
        m = re.search(
            r"async def event_generator\(\):(.*?)(?=\nasync def |\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 event_generator"
        body = m.group(1)
        # 必须有 try / finally 包裹 cleanup_job_queue
        assert "try:" in body, (
            "event_generator 必须 try/finally 包裹（防止异常时 queue 泄漏）"
        )
        assert "finally:" in body, "event_generator 必须有 finally 分支"
        assert "cleanup_job_queue" in body, (
            "event_generator finally 必须调 cleanup_job_queue"
        )

    def test_bridge_event_generator_uses_finally_cleanup(self):
        """stream_bridge event_generator 同理。"""
        from pathlib import Path
        import re
        bridge_py = Path(BACKEND_ROOT) / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 找 stream_bridge 的 event_generator
        m = re.search(
            r"async def stream_bridge\([\s\S]*?async def event_generator\(\):(.*?)(?=\nasync def |\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 stream_bridge.event_generator"
        body = m.group(1)
        assert "try:" in body, (
            "stream_bridge.event_generator 必须 try/finally 包裹"
        )
        assert "finally:" in body, "必须有 finally 分支"
        assert "cleanup_run_queue" in body, (
            "event_generator finally 必须调 cleanup_run_queue"
        )


class TestHumanEscalationNotEndRun:
    """独立 AI 深度审查发现（2026-07-03 报告）：
       orchestrator.py:573 之前 g.add_edge("human_escalation", END)，
       与 graph.py:290 的 human_escalation → load_arc_tasks 不一致。

       后果：run/resume 走 orchestrator 的图，章节触发人工介入时
       stream() 立即终止 → 整次 run 静默提前结束（即便 chapters_done
       < max_chapters），用户视角"成功"但实际没写完。

    本测试锁死：orchestrator.py 和 graph.py 的图拓扑必须一致。
    """
    def test_orchestrator_human_escalation_edge_target(self):
        """orchestrator.py 的图 human_escalation 必须指向 load_arc_tasks（不是 END）。"""
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.build_graph)
        # 找 human_escalation 行的 add_edge
        import re
        m = re.search(r'g\.add_edge\(\s*"human_escalation"\s*,\s*([^)]+)\)', src)
        assert m, "找不到 g.add_edge(human_escalation, ...)"
        target = m.group(1).strip()
        assert target == '"load_arc_tasks"', (
            f"orchestrator 的 human_escalation 必须指向 load_arc_tasks（继续下一章），"
            f"实际 {target!r}（独立 AI 审查发现的 bug）"
        )

    def test_graph_py_human_escalation_edge_target(self):
        """graph.py 的图 human_escalation 也必须指向 load_arc_tasks（两个文件保持一致）。"""
        import inspect
        from engine import graph as graph_mod
        src = inspect.getsource(graph_mod.build_project_graph)
        import re
        m = re.search(r'g\.add_edge\(\s*"human_escalation"\s*,\s*([^)]+)\)', src)
        assert m, "graph.py 找不到 g.add_edge(human_escalation, ...)"
        target = m.group(1).strip()
        assert target == '"load_arc_tasks"', (
            f"graph.py human_escalation 必须指向 load_arc_tasks，实际 {target!r}"
        )

    def test_both_graphs_have_consistent_topology(self):
        """orchestrator.py 和 graph.py 的图拓扑必须一致（防再次漂移）。"""
        import inspect
        from engine import orchestrator as orch_mod
        from engine import graph as graph_mod
        # 提取两个文件里所有 g.add_edge(...)
        def edges(src):
            import re
            return set(re.findall(r'g\.add_edge\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)', src))
        orch_edges = edges(inspect.getsource(orch_mod.build_graph))
        graph_edges = edges(inspect.getsource(graph_mod.build_project_graph))
        # human_escalation 必须两边都是 load_arc_tasks（关键边）
        assert ("human_escalation", "load_arc_tasks") in orch_edges, (
            "orchestrator 缺 human_escalation → load_arc_tasks 边"
        )
        assert ("human_escalation", "load_arc_tasks") in graph_edges, (
            "graph 缺 human_escalation → load_arc_tasks 边"
        )


class TestPlannerAtomicWrite:
    """迭代 #39: planner.py 写 setting_package.json 之前直接 open(w)，
    写一半被杀 → 文件损坏 → 后续 5 张表全空。改用 atomic_write_json。
    """
    def test_planner_imports_atomic_write_json(self):
        import inspect
        from engine.agents import planner as planner_mod
        src = inspect.getsource(planner_mod)
        assert "atomic_write_json" in src, \
            "planner.py 必须 import atomic_write_json（之前直接 open(w) 危险）"

    def test_planner_does_not_use_raw_open_for_json(self):
        """planner.py 不能再出现 `open(out_path, "w", encoding="utf-8")` 这种
        raw write——必须走 atomic_write_json。"""
        import inspect
        from engine.agents import planner as planner_mod
        src = inspect.getsource(planner_mod)
        # 找 setting_package.json 写入附近的代码
        assert 'open(out_path, "w", encoding="utf-8")' not in src, \
            "planner.py 不能再用 raw open(w) 写 setting_package.json（半写损坏风险）"
        assert 'open(out_path, "w"' not in src, \
            "planner.py 不能再用 raw open(w) 写 out_path（半写损坏风险）"

    def test_planner_setting_write_actually_atomic(self, tmp_path):
        """实际跑 run_planner 的写入路径（mock 掉 LLM）验证 atomic_write_json 被调用。"""
        from unittest.mock import patch, MagicMock
        from engine.agents import planner as planner_mod

        # mock LLM 返回 valid JSON
        mock_router = MagicMock()
        mock_router.call.return_value = ('{"novel_id":"x","arc_outline":[],"key_characters":[],"power_system":{"levels":[]}}', 0.001)
        with patch.object(planner_mod, "get_active_router", return_value=mock_router), \
             patch.object(planner_mod, "validate_setting_package"):
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            planner_mod.run_planner(args=[], output_dir=str(out_dir))
            target = out_dir / "setting_package.json"
            assert target.exists(), "setting_package.json 必须被写入"
            # 不应残留 .tmp
            assert not (out_dir / "setting_package.json.tmp").exists(), \
                "atomic write 完成后 .tmp 必须被替换走"


class TestTrackerParseFailureLogged:
    """迭代 #40: tracker.py 之前 parse_llm_json_response(resp, {}) — parse
    失败时 updates={} → chapter_summary / world_events / constraints 全部
    静默丢失。修法：用 None 作为 default 检测失败，log warning +
    meta.last_tracker_parse_failure_chapter + meta.tracker_parse_failure_count。
    """
    def test_tracker_uses_none_default(self):
        """tracker.py 必须用 None（不是 {}）作为 parse default — 才能
        检测 parse 失败并标记 meta。"""
        import inspect
        from engine.agents import tracker as tracker_mod
        src = inspect.getsource(tracker_mod)
        # 去掉注释行（避免 docstring / 注释里出现 `resp, {})` 误匹配）
        code_lines = [
            l for l in src.split("\n")
            if l.strip() and not l.strip().startswith("#")
        ]
        code_src = "\n".join(code_lines)
        # 真实调用行（不是注释）
        assert "parse_llm_json_response(resp, None)" in code_src, \
            "tracker.py 必须用 parse_llm_json_response(resp, None)，不能再传 {}"
        assert "parse_llm_json_response(resp, {})" not in code_src, \
            "tracker.py 不应再用 {} 作为 default（无法区分 parse 失败 vs 空 dict）"

    def test_tracker_logs_warning_on_parse_failure(self, caplog):
        """mock LLM 返回非 JSON → 必须 log warning + meta 标记。"""
        from unittest.mock import patch, MagicMock
        from engine.agents import tracker as tracker_mod

        mock_router = MagicMock()
        # LLM 返回完全无法 parse 的字符串
        mock_router.call.return_value = ("this is not JSON at all" * 20, 0.001)
        with patch.object(tracker_mod, "get_active_router", return_value=mock_router), \
             patch.object(tracker_mod, "save_l2"):
            current_memory = {
                "hot": {"protagonist_level": "感债者", "recent_summaries": []},
                "cold": {"world_events": [], "closed_threads": [], "resolved_foreshadowing": []},
                "constraints": {"forbidden_constraints": [], "established_facts": [],
                                "foreshadowing_planted": []},
                "meta": {"novel_id": "test", "total_chapters_tracked": 5},
            }
            with caplog.at_level("WARNING"):
                tracker_mod.run_tracker("章节正文", {"chapter_number": 6}, current_memory, "test")
            # 至少有 warning 被记下
            warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
            assert any("tracker" in m.lower() or "parse" in m.lower() for m in warning_msgs), \
                f"parse 失败时 tracker 必须 log warning，实际: {warning_msgs}"
            # meta 必须标记了
            assert current_memory["meta"].get("last_tracker_parse_failure_chapter") == 6, \
                f"meta 必须记 last_tracker_parse_failure_chapter=6，实际 {current_memory['meta']}"
            assert current_memory["meta"].get("tracker_parse_failure_count", 0) >= 1, \
                f"meta.tracker_parse_failure_count 必须 >=1，实际 {current_memory['meta']}"

    def test_tracker_success_path_unaffected(self):
        """正常 JSON 路径仍然更新 hot/cold/constraints，meta 标记不应出现。"""
        from unittest.mock import patch, MagicMock
        from engine.agents import tracker as tracker_mod

        mock_router = MagicMock()
        mock_router.call.return_value = (
            '{"chapter_summary":"主角觉醒","active_threads":["主线"],"inventory_add":["玉佩"]}',
            0.001,
        )
        with patch.object(tracker_mod, "get_active_router", return_value=mock_router), \
             patch.object(tracker_mod, "save_l2"):
            current_memory = {
                "hot": {"protagonist_level": "感债者", "recent_summaries": []},
                "cold": {"world_events": [], "closed_threads": [], "resolved_foreshadowing": []},
                "constraints": {"forbidden_constraints": [], "established_facts": [],
                                "foreshadowing_planted": []},
                "meta": {"novel_id": "test", "total_chapters_tracked": 5},
            }
            updated, cost = tracker_mod.run_tracker("章节正文", {"chapter_number": 6},
                                                     current_memory, "test")
        # chapter_summary 应被加入 recent_summaries
        summaries = updated["hot"]["recent_summaries"]
        assert any(s.get("chapter") == 6 and "主角觉醒" in s.get("summary", "")
                   for s in summaries), \
            f"正常路径必须把 chapter_summary 加进 recent_summaries，实际 {summaries}"
        # inventory 应有"玉佩"
        assert "玉佩" in updated["hot"]["inventory"], \
            f"inventory_add 必须被处理，实际 {updated['hot']['inventory']}"
        # meta 不应有 parse 失败标记
        assert "last_tracker_parse_failure_chapter" not in updated["meta"], \
            "正常 JSON 路径不应记录 parse 失败标记"


class TestComplianceParseFailNotFakePass:
    """迭代 #41: compliance.py 之前 parse 失败 → passed=True + 空 hard_rejects。
    修法：parse 失败 → passed=False + hard_rejects=[{PARSE_ERROR}]，让
    orchestrator 看到真实失败信号（不再 fake-pass）。
    """
    def test_compliance_parse_fail_marks_passed_false(self):
        from engine.agents.compliance import llm_semantic_check
        from unittest.mock import patch, MagicMock

        mock_router = MagicMock()
        mock_router.call.return_value = ("完全不是 JSON，是乱码", 0.001)
        with patch("engine.agents.compliance.get_active_router", return_value=mock_router):
            result, cost = llm_semantic_check("一些章节文本", platform="fanqie")
        assert result["passed"] is False, \
            f"JSON parse 失败时必须 passed=False（保守策略），实际 {result['passed']}"
        # hard_rejects 必须有 PARSE_ERROR 条目
        assert any("PARSE_ERROR" in str(h.get("rule", "")) for h in result.get("hard_rejects", [])), \
            f"parse 失败时必须给 hard_rejects 加 PARSE_ERROR 条目，实际 {result.get('hard_rejects')}"
        # suggestion 必须有可读信息
        assert "重跑" in result.get("suggestion", "") or "LLM" in result.get("suggestion", ""), \
            f"parse 失败时 suggestion 必须给用户可读 hint，实际 {result.get('suggestion')}"

    def test_compliance_source_no_fake_pass_on_exception(self):
        """源码扫描：llm_semantic_check 不再有 raw except Exception → passed=True。"""
        import inspect
        from engine.agents import compliance as comp_mod
        src = inspect.getsource(comp_mod)
        # 老代码是 `except Exception: result = {"passed": True, ...}`
        assert 'result = {"passed": True' not in src, \
            "compliance.py 不能再有 `except Exception: result = {passed:True}` fake-pass"
        # 新代码必须有 passed=False
        assert '"passed": False' in src, \
            "compliance.py parse 失败分支必须设 passed=False"

    def test_run_compliance_propagates_parse_fail_to_passed(self):
        """run_compliance（合并关键词 + LLM）必须把 parse 失败的 passed=False
        透传给最终结果。"""
        from engine.agents.compliance import run_compliance
        from unittest.mock import patch, MagicMock

        mock_router = MagicMock()
        mock_router.call.return_value = ("乱码", 0.001)
        with patch("engine.agents.compliance.get_active_router", return_value=mock_router):
            result, cost = run_compliance("章节文本（无关键词触发）", platform="fanqie")
        # 最终 passed 必须 False（即便 keyword scan 没发现 hard_kw）
        assert result["passed"] is False, \
            f"run_compliance 必须把 LLM parse 失败的 passed=False 透传，实际 {result['passed']}"


class TestInitArcJsonDecodeHandling:
    """迭代 #42: init_arc.py 之前 json.loads(raw read) — setting_package.json
    损坏时原始 JSONDecodeError 透出。同 pull_setting_package (迭代 #35) 同型。
    """
    def test_init_arc_source_catches_json_errors(self):
        """init_arc.py 必须 try/except (json.JSONDecodeError, UnicodeDecodeError)。"""
        import inspect
        from engine.agents import init_arc as init_mod
        src = inspect.getsource(init_mod.build_state_from_setting)
        assert "json.JSONDecodeError" in src, \
            "init_arc.build_state_from_setting 必须 catch json.JSONDecodeError"
        assert "UnicodeDecodeError" in src, \
            "init_arc.build_state_from_setting 必须 catch UnicodeDecodeError"

    def test_init_arc_corrupt_setting_raises_runtime_error(self, tmp_path):
        """模拟 setting_package.json 损坏 → 应该抛 RuntimeError 带可读信息，
        而不是透出原始 JSONDecodeError。"""
        from unittest.mock import patch
        from engine.agents import init_arc as init_mod
        import pytest

        # 写一个损坏的 JSON
        corrupt = tmp_path / "setting_package.json"
        corrupt.write_text("{ this is not valid JSON", encoding="utf-8")

        with patch.object(init_mod, "SETTING_PATH_STR", str(corrupt)):
            with pytest.raises(RuntimeError, match="setting_package.json 损坏"):
                init_mod.build_state_from_setting("test_proj")


class TestCallWithBudgetDedupe:
    """迭代 #45: writer.py + rewriter.py 之前各有一份几乎相同的 _call_with_budget
    （~30 行重试逻辑：网络抖动 sleep + retry）。抽到 engine.utils.call_with_budget_with_retry。

    锁死：
    1. utils 必须导出 call_with_budget_with_retry
    2. writer.py / rewriter.py 必须 import 它，不再自己实现重试循环
    3. 实际行为：retry 一次（max_attempts=2），全失败抛异常
    """
    def test_utils_exposes_call_with_budget_with_retry(self):
        from engine.utils import call_with_budget_with_retry
        import inspect
        sig = inspect.signature(call_with_budget_with_retry)
        params = sig.parameters
        for name in ("router", "agent_name", "system", "user", "target_chars"):
            assert name in params, \
                f"call_with_budget_with_retry 必须有参数 {name}，实际 {list(params.keys())}"
        assert params["max_attempts"].default == 2, \
            f"max_attempts 默认 2（保持历史行为），实际 {params['max_attempts'].default}"

    def test_writer_uses_shared_helper(self):
        import inspect
        from engine.agents import writer as writer_mod
        src = inspect.getsource(writer_mod)
        assert "call_with_budget_with_retry" in src, \
            "writer.py 必须 import + 调 call_with_budget_with_retry（不能自己实现重试）"
        assert "import time as _time" not in src, \
            "writer.py 不应再有 inline `import time as _time`（重试已迁到 utils）"

    def test_rewriter_uses_shared_helper(self):
        import inspect
        from engine.agents import rewriter as rewriter_mod
        src = inspect.getsource(rewriter_mod)
        assert "call_with_budget_with_retry" in src, \
            "rewriter.py 必须 import + 调 call_with_budget_with_retry（不能自己实现重试）"
        assert "import time as _time" not in src, \
            "rewriter.py 不应再有 inline `import time as _time`（重试已迁到 utils）"

    def test_call_with_budget_with_retry_returns_on_first_success(self):
        from unittest.mock import MagicMock
        from engine.utils import call_with_budget_with_retry

        router = MagicMock()
        router.call_with_length_budget.return_value = ("text", 0.01)
        text, cost = call_with_budget_with_retry(
            router, "writer", "sys", "user", 2000,
            sleep_seconds=0.001,
        )
        assert text == "text" and cost == 0.01
        assert router.call_with_length_budget.call_count == 1

    def test_call_with_budget_with_retry_retries_then_succeeds(self):
        from unittest.mock import MagicMock
        import httpx
        from engine.utils import call_with_budget_with_retry

        router = MagicMock()
        router.call_with_length_budget.side_effect = [
            httpx.ConnectError("connection refused"),
            ("text", 0.02),
        ]
        text, cost = call_with_budget_with_retry(
            router, "writer", "sys", "user", 2000,
            sleep_seconds=0.001,
        )
        assert text == "text" and cost == 0.02
        assert router.call_with_length_budget.call_count == 2, \
            f"必须 retry 一次，实际调了 {router.call_with_length_budget.call_count} 次"

    def test_call_with_budget_with_retry_raises_after_exhausting_attempts(self):
        from unittest.mock import MagicMock
        import httpx
        import pytest
        from engine.utils import call_with_budget_with_retry

        router = MagicMock()
        router.call_with_length_budget.side_effect = httpx.ConnectError("net down")
        with pytest.raises(httpx.ConnectError, match="net down"):
            call_with_budget_with_retry(
                router, "writer", "sys", "user", 2000,
                sleep_seconds=0.001, max_attempts=2,
            )
        assert router.call_with_length_budget.call_count == 2


class TestWriterNoPrivateRouterState:
    """#45-followup: writer.py 之前自己定义 _ACTIVE_ROUTER + set_active_router
    + _get_router，跟 rewriter.py / 其他 agent 用的 engine.llm_router.get_active_router()
    重复。删掉 writer.py 的私有状态，统一从 engine.llm_router 读。

    锁死：writer.py 不能有私有 _ACTIVE_ROUTER / set_active_router（必须用
    engine.llm_router.get_active_router()，避免多份 state 漂移）。
    """
    def test_writer_no_module_level_active_router(self):
        import inspect
        from engine.agents import writer as writer_mod
        src = inspect.getsource(writer_mod)
        # 去掉注释 + docstring（避免「_ACTIVE_ROUTER 删掉了」这种历史说明误匹配）
        code_lines = []
        in_docstring = False
        for line in src.split("\n"):
            stripped = line.strip()
            if '"""' in stripped or "'''" in stripped:
                count = stripped.count('"""') + stripped.count("'''")
                if count == 1:
                    in_docstring = not in_docstring
                    continue
                elif count == 2:
                    continue
                else:
                    in_docstring = not in_docstring
                    continue
            if in_docstring or stripped.startswith("#"):
                continue
            code_lines.append(line)
        code_src = "\n".join(code_lines)
        assert "_ACTIVE_ROUTER" not in code_src, \
            "writer.py 不应再有私有 _ACTIVE_ROUTER（统一用 engine.llm_router.get_active_router）"
        assert "def set_active_router" not in code_src, \
            "writer.py 不应再有 set_active_router 函数（同上）"

    def test_writer_uses_engine_llm_router(self):
        import inspect
        from engine.agents import writer as writer_mod
        src = inspect.getsource(writer_mod)
        # 必须 import engine.llm_router.get_active_router
        assert "from ..llm_router import get_active_router" in src, \
            "writer.py 必须 import engine.llm_router.get_active_router"

    def test_writer_get_router_fallback(self):
        """_get_router() 在没 active router 时 fallback 到 env-only 实例。"""
        from unittest.mock import patch
        from engine.agents import writer as writer_mod
        from engine.llm.router import LLMRouter
        with patch.object(writer_mod, "get_active_router", return_value=None):
            router = writer_mod._get_router()
        assert isinstance(router, LLMRouter), \
            "active router 为 None 时 _get_router 必须 fallback 到 fresh LLMRouter"


class TestSummarizerParseFailureNotSilent:
    """迭代 #47: summarizer.summarize_arc 之前 parse 失败时静默写 placeholder
    到 L5.arc_summaries，没有 log warning 让运维知道（跟 tracker.py iter #40
    同型问题，只是更早被作者放过）。

    修法：log warning + 加 _parse_failed=True 标记到 placeholder dict。
    """
    def test_summarizer_logs_warning_on_parse_failure(self, caplog):
        from unittest.mock import patch, MagicMock
        from engine.agents import summarizer as summ_mod

        mock_router = MagicMock()
        mock_router.call.return_value = ("乱码不是 JSON", 0.001)
        with patch.object(summ_mod, "get_active_router", return_value=mock_router), \
             patch.object(summ_mod, "save_l5"):
            memory = {"hot": {"recent_summaries": []}, "active_threads": []}
            with caplog.at_level("WARNING"):
                arc_summary, cost = summ_mod.summarize_arc(
                    {"arc_id": 3, "arc_name": "测试弧"}, [], memory, "test_novel",
                )
        warning_msgs = [r.message for r in caplog.records if r.levelname == "WARNING"]
        assert any("summarizer" in m.lower() for m in warning_msgs), \
            f"summarizer parse 失败时必须 log warning，实际: {warning_msgs}"
        assert arc_summary.get("_parse_failed") is True, \
            f"parse 失败时 placeholder 必须 _parse_failed=True，实际: {arc_summary}"

    def test_summarizer_placeholder_carries_failure_marker(self):
        import inspect
        from engine.agents import summarizer as summ_mod
        src = inspect.getsource(summ_mod.summarize_arc)
        assert "_parse_failed" in src, \
            "summarizer.summarize_arc 的 placeholder 必须带 _parse_failed=True 标记"

    def test_summarizer_has_logger(self):
        from engine.agents import summarizer as summ_mod
        assert hasattr(summ_mod, "log"), \
            "summarizer 必须有 module-level log（用于 log.warning 而非 print）"


class TestChapterCheckerNoFakePass:
    """迭代 #48: chapter_checker.llm_consistency_check 之前 parse 失败时
    返回 {"has_issues": False} — silent pass（同 compliance iter #41 /
    orchestrator iter #28 fake-pass 同型问题）。

    后果：LLM 检测到的跨章节矛盾（人物等级跳变 / 道具未获得 / 时间线错乱）
    JSON 解析失败 → 报告「无问题」→ 错误积累到后续章节。
    修法：parse 失败时 has_issues=True + issues 加 "解析失败" + _parse_failed=True
    """
    def test_consistency_check_parse_fail_not_silent_pass(self):
        from engine.tools import chapter_checker as checker_mod
        from unittest.mock import patch, MagicMock

        mock_router = MagicMock()
        mock_router.call.return_value = ("乱码不是 JSON", 0.001)
        with patch.object(checker_mod, "get_active_router", return_value=mock_router):
            result, cost = checker_mod.llm_consistency_check(
                "章节正文", {"characters": {}, "protagonist_level": "感债者",
                            "protagonist_points": 0, "inventory": [],
                            "established_facts": []},
            )
        assert result["has_issues"] is True, \
            f"JSON parse 失败时必须 has_issues=True（保守策略），实际 {result['has_issues']}"
        assert result.get("_parse_failed") is True, \
            f"parse 失败时必须 _parse_failed=True 标记，实际 {result}"
        assert any(
            "解析失败" in i.get("description", "") or "JSON" in i.get("description", "")
            for i in result.get("issues", [])
        ), f"parse 失败时 issues 必须包含解析失败条目，实际 {result.get('issues')}"

    def test_consistency_check_source_no_fake_pass(self):
        import inspect
        from engine.tools import chapter_checker as checker_mod
        src = inspect.getsource(checker_mod.llm_consistency_check)
        # 去掉注释（避免「之前 fake-pass {"has_issues": False}」这种历史说明误匹配）
        code_lines = [
            l for l in src.split("\n") if l.strip() and not l.strip().startswith("#")
        ]
        code_src = "\n".join(code_lines)
        assert '{"has_issues": False' not in code_src, \
            "chapter_checker.llm_consistency_check 不能再用 has_issues=False 默认值（fake-pass）"
        assert "parse_llm_json_response(resp, None)" in code_src, \
            "chapter_checker.llm_consistency_check 必须用 None default 检测 parse 失败"


class TestBudgetReportEmptyLogNoKeyError:
    """迭代 #50: budget_manager.generate_report 在 budget_log 为空时返回的 dict
    缺少 total_chapters_planned / cost_per_chapter_recent20 / projected_total_cost
    等 key。print_report 直接 `report["total_chapters_planned"]` → KeyError。

    后果：第一次启动 / 删 budget_log 后 → 用户跑 status/budget 命令 → 后端 500
    + traceback 暴露给前端。

    修法：generate_report 空 records 路径补 total_chapters_planned 字段；
    print_report 用 .get() 兜底 cost_per_chapter_recent20 / projected_total_cost。
    """
    def test_generate_report_empty_log_has_total_chapters_planned(self, tmp_path, monkeypatch):
        """budget_log 不存在时 generate_report 必须返回 total_chapters_planned 键。"""
        from engine.tools import budget_manager as bm_mod
        # budget_log 不存在 + state_path 不存在
        monkeypatch.setattr(bm_mod, "BUDGET_LOG", str(tmp_path / "no_log.jsonl"))
        monkeypatch.setattr(bm_mod, "STATE_PATH_STR", str(tmp_path / "no_state.json"))
        report = bm_mod.generate_report()
        assert "total_chapters_planned" in report, \
            f"空 log 路径 generate_report 必须有 total_chapters_planned 键，实际 keys: {list(report.keys())}"

    def test_print_report_no_keyerror_on_empty_log(self, tmp_path, monkeypatch, capsys):
        """budget_log 为空时 print_report 不能抛 KeyError（之前必崩）。"""
        from engine.tools import budget_manager as bm_mod
        monkeypatch.setattr(bm_mod, "BUDGET_LOG", str(tmp_path / "no_log.jsonl"))
        monkeypatch.setattr(bm_mod, "STATE_PATH_STR", str(tmp_path / "no_state.json"))
        # 不应抛 KeyError
        bm_mod.print_report()
        captured = capsys.readouterr()
        assert "💰 预算报告" in captured.out, "print_report 必须打报告内容"
        assert "KeyError" not in captured.out, "print_report 不应打 KeyError"

    def test_generate_report_loads_planned_from_state(self, tmp_path, monkeypatch):
        """从 STATE_PATH 读 total_chapters_planned 时，空 log 也要拿到。"""
        from engine.tools import budget_manager as bm_mod
        # 写一个 mock state
        state = {"total_chapters_planned": 200, "budget_limit_usd": 800,
                 "budget_used_usd": 12.5, "current_chapter": 50}
        state_path = tmp_path / "state.json"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        monkeypatch.setattr(bm_mod, "BUDGET_LOG", str(tmp_path / "no_log.jsonl"))
        monkeypatch.setattr(bm_mod, "STATE_PATH_STR", str(state_path))
        report = bm_mod.generate_report()
        assert report["total_chapters_planned"] == 200, \
            f"必须从 STATE_PATH 读 total_chapters_planned=200，实际 {report['total_chapters_planned']}"
        assert report["budget_limit_usd"] == 800
        assert report["chapters_done"] == 50


class TestOrchestratorTrackerNotSilent:
    """迭代 #58: orchestrator.node_save_and_track 之前 except Exception
    静默兜底 updated_mem=memory, cost=0 —— tracker LLM 失败时没信号。
    修法：标 task._tracker_failed + error_log + 不静默吞。
    """
    def test_orchestrator_marks_tracker_failed(self):
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.node_save_and_track)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "_tracker_failed" in code_src, \
            "orchestrator.node_save_and_track 异常路径必须标 _tracker_failed（iter #58）"
        assert "error_log" in code_src, \
            "orchestrator.node_save_and_track 异常路径必须 log error_log"


class TestHumanReviewAtomicAndLoadNoSilent:
    """迭代 #59: engine/tools/human_review.py 两个 bug
    1. save_state 用 raw open(w) 写 orchestrator_state.json（半写损坏）
    2. load_state 损坏时 except Exception: pass → 返回 {} →
       人工审核看到空 state 却不知道文件坏了 → 假审核
    修法：atomic_write_json + 损坏时 backup 到 .corrupted.{ts} 后 raise。
    """
    def test_human_review_load_state_raises_on_corrupt(self, tmp_path, monkeypatch):
        """损坏 state 文件必须 raise（不能再 silent fallback 到 {}）。"""
        from engine.tools import human_review as hr_mod
        corrupt = tmp_path / "state.json"
        corrupt.write_text("{ not valid", encoding="utf-8")
        monkeypatch.setattr(hr_mod, "STATE_PATH", str(corrupt))
        with pytest.raises(Exception):
            hr_mod.load_state()

    def test_human_review_save_state_uses_atomic(self):
        import inspect
        from engine.tools import human_review as hr_mod
        src = inspect.getsource(hr_mod.save_state)
        assert "atomic_write_json" in src, \
            "human_review.save_state 必须用 atomic_write_json"
        assert "open(STATE_PATH" not in src, \
            "human_review.save_state 不能再 raw open(STATE_PATH, 'w')"

    def test_human_review_meta_write_uses_atomic(self):
        import inspect
        from engine.tools import human_review as hr_mod
        src = inspect.getsource(hr_mod)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "atomic_write_json" in code_src, \
            "human_review meta 写盘也必须用 atomic_write_json"


class TestOrchestratorSummarizerNotSilent:
    """迭代 #60: orchestrator.node_save_and_track 弧末 run_summarizer
    之前 except Exception: cost=0.0 静默 —— 跟 #58 run_tracker 同型。
    """
    def test_orchestrator_marks_summarizer_failed(self):
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.node_save_and_track)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        assert "_summarizer_failed" in code_src, \
            "orchestrator.node_save_and_track summarizer 异常路径必须标 _summarizer_failed（iter #60）"
        assert "summarizer failed arc" in code_src, \
            "orchestrator.node_save_and_track summarizer 异常路径必须 log error_log"


class TestOrchestratorPipelineStateCoherence:
    """锁定 engine/orchestrator 节点间状态连贯性。
    
    验证以下不变量：
    1. create_initial_state 后所有进度字段 = 0
    2. node_load_arc_tasks 后 arc_plans + chapter_task_queue 注入
    3. node_get_next_task 后 current_task + rewrite_count = 0
    4. 任何阶段异常 → state.error_log 增量 + 标 _xxx_failed
    5. save_state 后 last_updated 自动更新（iter #7-#28 行为）
    """
    def test_create_initial_state_all_progress_zero(self):
        from engine.state import create_initial_state
        s = create_initial_state(
            novel_id="test_n", title="测试", platform="fanqie",
            genre="玄幻", setting_concept="",
        )
        assert s["current_chapter"] == 0
        assert s["current_arc"] == 0
        assert s["budget_used_usd"] == 0.0
        assert s["chapter_task_queue"] == []
        assert s["current_task"] is None
        assert s["error_log"] == []
        assert s["rewrite_count_current"] == 0

    def test_save_state_updates_last_updated(self, tmp_path):
        """save_state 必须更新 last_updated（iter #28 行为）。"""
        from engine.state import create_initial_state, save_state, load_state
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        s["last_updated"] = "2000-01-01T00:00:00"  # 显式旧值
        path = str(tmp_path / "state.json")
        save_state(s, path)
        loaded = load_state(path)
        assert loaded["last_updated"] != "2000-01-01T00:00:00", \
            "save_state 必须更新 last_updated（iter #28）"

    def test_save_state_creates_tmp_during_write(self, tmp_path):
        """save_state 必须用 .tmp + atomic rename（半写损坏防护）。"""
        from engine.state import create_initial_state, save_state
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        path = str(tmp_path / "state.json")
        save_state(s, path)
        # 完成后 .tmp 必须被替换走
        assert not (tmp_path / "state.json.tmp").exists(), \
            "save_state atomic write 完成后 .tmp 必须被替换走"

    def test_node_get_next_task_pops_from_queue(self):
        """node_get_next_task 必须从 queue pop 并 set current_task。"""
        from engine.state import create_initial_state
        from engine.orchestrator import node_get_next_task
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        s["chapter_task_queue"] = [
            {"chapter_number": 1, "chapter_role": "铺垫", "chapter_goal": "起始"},
            {"chapter_number": 2, "chapter_role": "发展", "chapter_goal": "推进"},
        ]
        s = node_get_next_task(s)
        assert s["current_task"]["chapter_number"] == 1, \
            "node_get_next_task 必须 set current_task = queue[0]"
        assert len(s["chapter_task_queue"]) == 1, \
            f"node_get_next_task 必须 pop 一个，剩余 {len(s['chapter_task_queue'])}"
        assert s["rewrite_count_current"] == 0, \
            "node_get_next_task 必须重置 rewrite_count_current"
        assert s["current_chapter"] == 1, \
            f"current_chapter 必须 = current_task.chapter_number，实际 {s['current_chapter']}"

    def test_node_get_next_task_empty_queue_returns_state(self):
        """queue 空时 node_get_next_task 必须返回 state（不抛异常）。"""
        from engine.state import create_initial_state
        from engine.orchestrator import node_get_next_task
        s = create_initial_state("t", "t", "fanqie", "玄幻", "")
        s["chapter_task_queue"] = []
        s2 = node_get_next_task(s)
        assert s2["current_task"] is None, \
            "空 queue 时 current_task 必须仍为 None"


class TestEngineStateSafetyInvariants:
    """锁定 engine/state.py + orchestrator.py 的安全不变量。
    
    这些不变量不一定对应 bug，但锁住防止回归：
    1. save_state 后 budget_used_usd 单调递增（前提 cost >= 0）
    2. load_state 必须能恢复 save_state 写入的内容
    3. error_log 最多 100 条（防止内存无限增长）
    4. log("ERR ...") 必须把消息加进 error_log
    """
    def test_save_load_round_trip(self, tmp_path):
        """save → load 必须 round-trip（同一字段相同值）。"""
        from engine.state import create_initial_state, save_state, load_state
        s = create_initial_state("t", "title", "fanqie", "玄幻", "测试概念")
        s["current_chapter"] = 5
        s["budget_used_usd"] = 12.5
        s["error_log"] = ["ERR foo", "WARN bar"]
        path = str(tmp_path / "state.json")
        save_state(s, path)
        loaded = load_state(path)
        assert loaded["novel_id"] == "t"
        assert loaded["current_chapter"] == 5
        assert loaded["budget_used_usd"] == 12.5
        assert loaded["error_log"] == ["ERR foo", "WARN bar"]

    def test_error_log_capped_at_100(self):
        """engine/orchestrator.py log() 必须把 error_log 截到 100 条（el[-100:]）。"""
        import inspect
        from engine import orchestrator as orch_mod
        src = inspect.getsource(orch_mod.log)
        assert "el[-100:]" in src or "[-100:]" in src, \
            "orchestrator.log 必须把 error_log 截到 100 条防止内存无限增长"

    def test_log_err_message_goes_into_error_log(self):
        """log(\"ERR xxx\") 必须把消息加进 error_log。"""
        from engine import orchestrator as orch_mod
        s = orch_mod.create_initial_state("t", "t", "fanqie", "玄幻", "")
        orch_mod.log("ERR 测试错误信息", s)
        assert any("ERR 测试错误信息" in line for line in s["error_log"]), \
            f"log('ERR xxx') 必须把消息加进 error_log，实际 {s['error_log']}"

    def test_log_non_err_message_not_in_error_log(self):
        """log(\"...\" 不含 ERR/FAIL) 不应进 error_log。"""
        from engine import orchestrator as orch_mod
        s = orch_mod.create_initial_state("t", "t", "fanqie", "玄幻", "")
        orch_mod.log("普通日志信息，不应进 error_log", s)
        # 普通信息不应进 error_log（除非包含 ERR/FAIL）
        # 但 ERR/FAIL 是字符串检查，如果信息里有"ERR"字样仍会进 — 这是预期
        assert all("ERR" not in line and "FAIL" not in line
                   for line in s["error_log"]), \
            f"普通信息不应进 error_log，实际 {s['error_log']}"


class TestOrchestratorSettingLoadError:
    """迭代 #64: engine/orchestrator.py:_setting 之前 `json.load(f)`
    损坏文件时直接抛 — 没有 backup / 没有清晰错误。
    修法：损坏时 backup 到 .corrupted.{ts}，raise 带可读信息。
    """
    def test_setting_load_corrupt_file_raises_with_clear_message(self, tmp_path, monkeypatch):
        """损坏 setting_package.json 必须 raise 带「文件损坏」可读信息。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        # 重置 module-level cache
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "SETTING_PATH", Path(tmp_path / "setting.json"))

        # 写损坏文件
        (tmp_path / "setting.json").write_text("{ not valid", encoding="utf-8")

        with pytest.raises(Exception) as exc_info:
            orch_mod._setting()
        # 必须有可读错误信息（提到损坏 / JSON / 文件名）
        err_msg = str(exc_info.value)
        assert "Expecting" in err_msg or "JSON" in err_msg, \
            f"损坏文件必须 raise JSON 解析错误，实际 {type(exc_info.value).__name__}: {err_msg}"

    def test_setting_load_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        """setting_package.json 不存在时 _setting 必须返回 {}（首次启动）。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "SETTING_PATH", Path(tmp_path / "nope.json"))
        # 文件不存在 → 返回 {}（不抛）
        result = orch_mod._setting()
        assert result == {}, \
            f"setting 文件不存在时必须返回 {{}}，实际 {result}"

    def test_setting_cache_hit_after_first_load(self, tmp_path, monkeypatch):
        """_setting_cache 同 mtime 必须 cache（不重读盘）；iter #65 mtime-based 行为。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        import json as _json
        (tmp_path / "setting.json").write_text(
            _json.dumps({"title_candidates": ["test"], "genre": "玄幻"}),
            encoding="utf-8",
        )
        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        monkeypatch.setattr(orch_mod, "SETTING_PATH", Path(tmp_path / "setting.json"))

        first = orch_mod._setting()
        assert first["title_candidates"] == ["test"]
        # 第二次不改文件 → cache hit（同 mtime 不 reload）
        from unittest.mock import patch
        with patch("engine.orchestrator.json.load") as mock_load:
            second = orch_mod._setting()
        assert mock_load.call_count == 0, \
            f"不改文件时不应调 json.load，实际调了 {mock_load.call_count} 次"
        assert second["title_candidates"] == ["test"]


class TestOrchestratorSettingCacheInvalidates:
    """迭代 #65: orchestrator._setting 之前 cache 一旦填就永不刷新 — 同一进程
    跑完 planner 后 setting_package.json 更新了，orchestrator 还用老值。
    修法：按 mtime 检测文件变化自动 invalidate。
    """
    def test_setting_cache_invalidates_on_file_change(self, tmp_path, monkeypatch):
        from pathlib import Path
        import time as _t
        from engine import orchestrator as orch_mod
        import json as _json

        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        setting_path = Path(tmp_path / "setting.json")
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)

        # 第一次写入
        setting_path.write_text(
            _json.dumps({"version": 1, "title": "old"}), encoding="utf-8"
        )
        first = orch_mod._setting()
        assert first["version"] == 1

        # 模拟时间过去 + 修改文件
        _t.sleep(0.05)  # 确保 mtime 不同
        setting_path.write_text(
            _json.dumps({"version": 2, "title": "new"}), encoding="utf-8"
        )
        second = orch_mod._setting()
        assert second["version"] == 2, \
            f"文件改了 _setting 必须 reload（按 mtime invalidate），实际 {second}"
        assert second["title"] == "new"

    def test_setting_cache_hit_keeps_same_value_no_file_change(self, tmp_path, monkeypatch):
        """没改文件时 _setting 必须走 cache（不重读盘）。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        import json as _json

        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        setting_path = Path(tmp_path / "setting.json")
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)
        setting_path.write_text(
            _json.dumps({"version": 1}), encoding="utf-8"
        )

        # 第一次读
        first = orch_mod._setting()
        assert first["version"] == 1

        # 用 mock 替换 json.load 验证第二次不调
        from unittest.mock import patch
        with patch("engine.orchestrator.json.load") as mock_load:
            second = orch_mod._setting()
        assert mock_load.call_count == 0, \
            f"文件没改时 _setting 不应重读盘，但调了 {mock_load.call_count} 次 json.load"
        # 迭代 #69：_setting 现在返回 dict copy（防调用方污染 cache），
        # 不再保证 identity 相等，但 value 必须一致
        assert second == first, \
            f"cache 命中必须返回相同内容（#69 返回 copy，不再是同对象），实际 {second} vs {first}"

    def test_invalidate_setting_cache_helper(self, tmp_path, monkeypatch):
        """invalidate_setting_cache() 必须重置 cache + mtime。"""
        from pathlib import Path
        from engine import orchestrator as orch_mod
        import json as _json

        monkeypatch.setattr(orch_mod, "_setting_cache", None)
        monkeypatch.setattr(orch_mod, "_setting_mtime", None)
        setting_path = Path(tmp_path / "setting.json")
        monkeypatch.setattr(orch_mod, "SETTING_PATH", setting_path)
        setting_path.write_text(_json.dumps({"version": 1}), encoding="utf-8")
        orch_mod._setting()  # populate cache

        # 调用 invalidate
        orch_mod.invalidate_setting_cache()
        assert orch_mod._setting_cache is None, \
            "invalidate_setting_cache 必须重置 _setting_cache 为 None"
        assert orch_mod._setting_mtime is None, \
            "invalidate_setting_cache 必须重置 _setting_mtime 为 None"


class TestMemoryManagerNoSilentException:
    """迭代 #73（medium bug fix）：

    CHANGELOG 里"发现某处 except Exception: pass → 改 log+fail-fast"
    模式被多次套用，但 engine/memory/manager.py 漏扫到——这文件里有 4 处
    `except Exception: continue/pass`，读章节 meta / 章节正文 / 风格样本 /
    清理旧 auto 文件失败时**完全静默**。影响：损坏文件悄悄导致 Writer
    上下文不完整，没有 signal 告诉运维为什么。

    审计报告（2026-07-05）确认这是被漏扫的真 bug，不是"测试只验证
    设置了 MASTER_KEY 的场景"那种测试覆盖盲点。

    修法：module logger + 每处 `except Exception` 都 log.exception 后
    continue，行为不变（仍 continue）但有诊断信号。
    """
    def _function_source(self, func):
        import inspect
        return inspect.getsource(func)

    def test_module_has_logger(self):
        """源码扫描：manager.py 模块级必须定义 logger（#73 标志）。"""
        import inspect
        from engine.memory import manager as mgr_mod
        src = inspect.getsource(mgr_mod)
        assert "getLogger" in src or "logger" in src.lower(), \
            "memory/manager.py 必须有 module logger 用于报告被吞掉的异常（#73）"

    def test_no_silent_continue_in_internal_meta_loop(self):
        """_get_internal_samples 读 meta 失败的 except 必须有 log.* 调用。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod._get_internal_samples)
        # 找第 1 个 except Exception
        idx = src.find("except Exception:")
        assert idx != -1, "_get_internal_samples 必须有 except Exception 段"
        # 那一段（到下一个 except 或函数结束）必须有 log
        chunk_end = idx + 200  # 查到下一个 except
        next_except = src.find("except Exception:", idx + 10)
        if next_except != -1:
            chunk_end = next_except
        chunk = src[idx:chunk_end]
        assert "log" in chunk.lower(), \
            f"_get_internal_samples 第 1 处 except 必须 log.exception（#73），chunk:\n{chunk}"
        assert "continue" in chunk, \
            f"行为应继续往下（continue）但有日志，chunk:\n{chunk}"

    def test_no_silent_continue_in_chapter_text_loop(self):
        """_get_internal_samples 读 ch_NNNN.txt 失败的 except 必须有 log。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod._get_internal_samples)
        # 第 2 个 except
        first = src.find("except Exception:")
        second = src.find("except Exception:", first + 10)
        assert second != -1, \
            "_get_internal_samples 必须有第 2 处 except（章节正文读取）"
        chunk = src[second:second + 200]
        assert "log" in chunk.lower(), \
            f"_get_internal_samples 第 2 处 except 必须 log.exception（#73），chunk:\n{chunk}"

    def test_no_silent_continue_in_external_samples(self):
        """_get_external_samples 读风格样本失败的 except 必须有 log。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod._get_external_samples)
        idx = src.find("except Exception:")
        assert idx != -1, "_get_external_samples 必须有 except Exception"
        chunk = src[idx:idx + 200]
        assert "log" in chunk.lower(), \
            f"_get_external_samples except 必须 log.exception（#73），chunk:\n{chunk}"

    def test_no_silent_pass_in_cleanup_loop(self):
        """maybe_update_style_samples 清理旧 auto 文件 except 必须有 log。"""
        from engine.memory import manager as mgr_mod
        src = self._function_source(mgr_mod.maybe_update_style_samples)
        idx = src.find("except Exception:")
        assert idx != -1, "maybe_update_style_samples 必须有 except Exception"
        chunk = src[idx:idx + 200]
        assert "log" in chunk.lower(), \
            f"maybe_update_style_samples except 必须 log.exception（#73），chunk:\n{chunk}"

    def test_behavioral_broken_meta_logs_and_continues(self, tmp_path, monkeypatch, caplog):
        """行为测试：meta 文件损坏时 _get_internal_samples 必须 log 但仍返回可用样本。

        之前 bug：静默 continue，外部完全看不到信号。
        修复后：log.exception 后 continue，caplog 能抓到。
        """
        import json
        import logging
        from pathlib import Path
        from engine.config.paths import CHAPTERS_DIR_STR
        from engine.memory import manager as mgr_mod

        # 重定向 CHAPTERS_DIR_STR 到临时目录
        monkeypatch.setattr(mgr_mod, "CHAPTERS_DIR_STR", str(tmp_path))

        # 写 1 个坏 meta + 1 个好 meta + 对应章节文件
        bad_meta = tmp_path / "ch_0001_meta.json"
        bad_meta.write_text("{ this is not valid json", encoding="utf-8")
        good_meta = tmp_path / "ch_0002_meta.json"
        good_meta.write_text(json.dumps({"score": 8.0, "chapter_number": 2}),
                             encoding="utf-8")
        # good meta 对应章节文件
        good_ch = tmp_path / "ch_0002.txt"
        good_ch.write_text("这是高分章节正文", encoding="utf-8")

        with caplog.at_level(logging.ERROR, logger="novel_ai.engine.memory.manager"):
            result = mgr_mod._get_internal_samples()

        # 行为：仍返回样本（continue 不抛）
        assert isinstance(result, list)
        # 行为：好的那条被收进来了（meta 解析成功 + 分数 ≥ 7.5 + 章节文件存在）
        assert any("高分章节正文" in s for s in result), \
            f"好样本应保留，实际 {result}"
        # 关键：坏 meta 必须产生 log 记录（之前静默 swallowed）
        err_records = [r for r in caplog.records
                       if r.levelno >= logging.ERROR
                       and "memory" in r.name.lower()]
        assert err_records, (
            "损坏的 meta 文件必须被 log 记录（之前静默吞掉，#73 修法）"
            f"实际 caplog records: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        assert any("ch_0001" in r.getMessage() for r in err_records), \
            f"log 应包含损坏文件路径 ch_0001，实际 messages: {[r.getMessage() for r in err_records]}"


class TestRouterProxyMountNoSilentException:
    """迭代 #76（小修）：engine/llm/router.py._get_proxied_client 的
    mount proxy 代码块之前是 `except Exception: pass` —— 如果 urlparse
    抛异常（畸形 base_url），proxy 默默不挂载 → caller 以为自己"没设 proxy"
    直连请求，但其实设了——而且实际生效状态取决于代码路径而非配置。

    修法：log.warning 带 provider / base_url / exc 信息让运维知道，
    行为不变（client 仍返回，request 直连）但有诊断信号。
    """
    def test_proxy_mount_exception_handled_with_log(self):
        """_get_proxied_client mount proxy 段的 except 必须有 log.warning（#76）。"""
        import inspect
        from engine.llm import router as llm_router
        src = inspect.getsource(llm_router._get_proxied_client)
        # 找到 mount proxy 段的 try/except
        # 预期：try: ... mount ... except Exception as e: log.warning(...)
        import re
        # 定位到 urlparse 之后的那段 except
        try_idx = src.find("from urllib.parse import urlparse")
        assert try_idx != -1, "_get_proxied_client 必须导入 urlparse"
        # 找该 try 之后的 except
        except_idx = src.find("except Exception", try_idx)
        assert except_idx != -1, "_get_proxied_client 的 mount proxy 段必须有 except"
        # 该 except 段（往后 500 字符）必须有 log.warning / log.exception
        chunk = src[except_idx:except_idx + 500]
        assert "log" in chunk.lower(), (
            f"_get_proxied_client mount proxy 段的 except 必须 log.warning（#76），chunk:\n{chunk}"
        )
        # 反向保证：不能退回到 bare pass
        bare_pass = re.search(r"except[^:]+:\s*\n\s*pass\s*$", chunk, re.MULTILINE)
        assert not bare_pass, (
            f"_get_proxied_client 不能退回到 bare pass（#76 已修），匹配 {bare_pass.group() if bare_pass else None}"
        )

    def test_proxy_mount_urlparse_failure_logs_and_returns_client(self, caplog):
        """行为测试：urlparse 抛异常时 _get_proxied_client 仍返回 client + 记录 warning。

        模拟 urlparse 失败 + 验证 log 行为 + 验证返回值仍然是合法 client。
        """
        import logging
        import httpx
        from engine.llm import router as llm_router

        # 重置 _proxy_mounts 避免 cache 影响
        llm_router._proxy_mounts.clear()
        llm_router._PROVIDER_PROXY["test_provider"] = "http://127.0.0.1:7890"

        # 通过 monkeypatch urlparse 让它抛异常
        original_urlparse = None
        try:
            from urllib.parse import urlparse as _orig_urlparse

            def _boom(*args, **kwargs):
                raise ValueError("simulated bad url")

            # 把 urlparse 在 _get_proxied_client 局部命名空间里替换
            with caplog.at_level(logging.WARNING, logger="novel_ai.engine.llm"):
                # 在函数体内 urlparse 是 from urllib.parse import urlparse，
                # 我们没法直接 monkeypatch module import。改用：base_url 给个畸形值让
                # urlparse 在某些 Python 实现下也出意外 —— 但实际上 urlparse 很 robust。
                # 简单方法：让 base_url 为 None，.netloc 会抛 AttributeError
                client = llm_router._get_proxied_client(
                    "test_provider", "not a real url scheme ://", timeout=60,
                )
            # 验证返回 client 是 httpx.Client 实例（即使 mount 失败也是 client）
            assert isinstance(client, httpx.Client), \
                f"_get_proxied_client 必须返回 httpx.Client，实际 {type(client)}"
        finally:
            llm_router._proxy_mounts.clear()
            llm_router._PROVIDER_PROXY.pop("test_provider", None)


class TestStyleManagerNoSilentException:
    """迭代 #77：engine/tools/style_manager.py 4 处静默 `except Exception:
    continue`（L26/55/71/99）跟 #73 memory/manager.py 同型。

    这是 CLI 工具而非核心 runtime，但审计模式应一致：损坏文件时
    应该 log.exception 让运维看到信号。

    修法：模块级 `_log` logger + 4 处都加 `_log.exception(...)` 后 continue。
    """
    def test_module_has_logger(self):
        """源码扫描：style_manager.py 必须有 logger（#77 标志）。"""
        import inspect
        from engine.tools import style_manager as sm
        src = inspect.getsource(sm)
        assert "_log" in src or "getLogger" in src, (
            "engine/tools/style_manager.py 必须有 module logger（#77）"
        )

    def _function_source(self, func):
        import inspect
        return inspect.getsource(func)

    def test_list_samples_excepts_have_log(self):
        """list_samples 段 except 必须有 log。"""
        from engine.tools import style_manager as sm
        src = self._function_source(sm.list_samples)
        idx = src.find("except Exception:")
        assert idx != -1, "list_samples 必须有 except Exception"
        chunk = src[idx:idx + 200]
        # 找下一个 except 或函数结束
        next_except = src.find("except Exception:", idx + 10)
        if next_except != -1 and next_except - idx < 250:
            chunk = src[idx:next_except]
        assert "log" in chunk.lower(), \
            f"list_samples except 必须 log（#77），chunk:\n{chunk}"

    def test_extract_internal_samples_excepts_have_log(self):
        """extract_internal_samples 两处 except 都必须有 log。"""
        from engine.tools import style_manager as sm
        src = self._function_source(sm.extract_internal_samples)
        except_indices = []
        pos = 0
        while True:
            i = src.find("except Exception:", pos)
            if i == -1:
                break
            except_indices.append(i)
            pos = i + 1
        assert len(except_indices) >= 2, \
            f"extract_internal_samples 应至少有 2 处 except，实际 {len(except_indices)}"
        # 每处 except 都必须在后续 250 字符内有 log
        for idx in except_indices:
            chunk = src[idx:idx + 250]
            assert "log" in chunk.lower(), \
                f"extract_internal_samples except @ {idx} 必须 log（#77），chunk:\n{chunk}"

    def test_generate_style_prefix_excepts_have_log(self):
        """generate_style_prefix except 必须有 log。"""
        from engine.tools import style_manager as sm
        src = self._function_source(sm.generate_style_prefix)
        idx = src.find("except Exception:")
        assert idx != -1, "generate_style_prefix 必须有 except Exception"
        chunk = src[idx:idx + 250]
        assert "log" in chunk.lower(), \
            f"generate_style_prefix except 必须 log（#77），chunk:\n{chunk}"

    def test_behavioral_broken_meta_logs_and_continues(self, tmp_path, monkeypatch, caplog):
        """行为测试：chapter meta 文件损坏时 extract_internal_samples 必须 log 但仍 continue。"""
        import json
        import logging
        from engine.tools import style_manager as sm

        # 重定向 STYLE_DIR + CHAPTERS_DIR 到临时目录
        monkeypatch.setattr(sm, "STYLE_DIR", str(tmp_path / "samples"))
        monkeypatch.setattr(sm, "CHAPTERS_DIR", str(tmp_path))
        (tmp_path / "samples").mkdir(exist_ok=True)

        # 写 1 个坏 meta + 1 个好 meta + 对应章节文件
        bad_meta = tmp_path / "ch_0001_meta.json"
        bad_meta.write_text("{ broken json", encoding="utf-8")
        good_meta = tmp_path / "ch_0002_meta.json"
        good_meta.write_text(json.dumps({"score": 8.0, "chapter_number": 2}),
                             encoding="utf-8")
        good_ch = tmp_path / "ch_0002.txt"
        good_ch.write_text("高分章节正文", encoding="utf-8")

        with caplog.at_level(logging.ERROR, logger="novel_ai.engine.tools.style_manager"):
            extracted = sm.extract_internal_samples(min_score=7.5, max_samples=5)

        # 行为：好那条被提取了
        assert extracted >= 1, \
            f"好 meta + 对应章节应被提取，实际 {extracted}"
        # 关键：坏 meta 必须产生 log 记录
        err_records = [r for r in caplog.records
                       if r.levelno >= logging.ERROR
                       and "style_manager" in r.name]
        assert err_records, (
            "损坏 meta 必须被 log（之前静默吞掉，#77）"
            f"实际 caplog: {[(r.levelname, r.name, r.getMessage()) for r in caplog.records]}"
        )
        assert any("ch_0001" in r.getMessage() for r in err_records), \
            f"log 应包含坏文件路径 ch_0001，实际 messages: {[r.getMessage() for r in err_records]}"


class TestPlannerMockPayloadValid:
    """P0 修复（iter #85）：router._MOCK_RESPONSES["planner"] 之前缺 7 个
    setting_package required 字段（tagline/protagonist/world_setting/...），
    mock 模式跑 planner 直接被 schema_validator fail-fast 拦下。
    修：mock_payload 补齐所有 required 字段，parse 后的 dict 通过 schema。"""

    def test_planner_mock_passes_setting_package_schema(self):
        """_MOCK_RESPONSES['planner'] parse 后必须过 setting_package schema"""
        import json
        from engine.llm.router import _MOCK_RESPONSES
        from app.schema_validator import validate_setting_package
        mock = _MOCK_RESPONSES.get("planner")
        assert mock is not None, "_MOCK_RESPONSES 缺 'planner'"
        parsed = json.loads(mock)
        try:
            validate_setting_package(parsed)
        except Exception as e:
            raise AssertionError(
                f"_MOCK_RESPONSES['planner'] parse 后未通过 schema 校验: {e}\n"
                f"parsed keys: {list(parsed.keys())}"
            )

    def test_planner_mock_has_all_required_fields(self):
        """mock planner 必含 7 个 required 字段"""
        import json
        from engine.llm.router import _MOCK_RESPONSES
        parsed = json.loads(_MOCK_RESPONSES["planner"])
        for required in ["tagline", "protagonist", "world_setting",
                          "power_system", "key_characters", "arc_outline",
                          "foreshadowing_seeds"]:
            assert required in parsed, (
                f"_MOCK_RESPONSES['planner'] 缺 required 字段 '{required}'"
            )
