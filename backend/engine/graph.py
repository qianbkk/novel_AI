"""Graph entry point — compiles novel_AI's StateGraph with SqliteSaver.
Replaces the subprocess-based bridge/invoke.py entirely.
ponytail: single checkpoints.sqlite, each project gets its own thread_id."""
from __future__ import annotations
import io
import os
import sys
import json
from contextlib import redirect_stdout
from pathlib import Path
from queue import Queue
from typing import Any, Optional

NOVEL_AI_DIR = str(Path(__file__).resolve().parent.parent.parent / "novel_AI")


def _ensure_import_path():
    """novel_AI agents import from api_client, orchestrator, etc.
    We need novel_AI/ on sys.path so those imports resolve in-process."""
    if NOVEL_AI_DIR not in sys.path:
        sys.path.insert(0, NOVEL_AI_DIR)


def build_project_graph(project_id: str) -> Any:
    """Build and compile the LangGraph StateGraph with SqliteSaver checkpointing.
    ponytail: monkey-patches orchestrator's build_graph to pass checkpointer,
    keeping novel_AI/ untouched (gitignored reference)."""
    from langgraph.checkpoint.sqlite import SqliteSaver
    from backend.engine.llm_router import LLMRouter, set_active_router

    _ensure_import_path()

    # 1. Install DB-backed LLM routing
    router = LLMRouter(project_id)
    router.install()
    set_active_router(router)

    # 2. Monkey-patch orchestrator.build_graph with SqliteSaver
    checkpointer = SqliteSaver.from_conn_string("checkpoints.sqlite")
    import orchestrator as _orch
    from langgraph.graph import StateGraph, END

    def _build_with_checkpoint():
        g = StateGraph(_orch.OrchestratorState)
        for name in ("load_arc_tasks","get_next_task","write_pipeline",
                      "rewrite","save_and_track","human_escalation"):
            g.add_node(name, getattr(_orch, f"node_{name}"))
        g.set_entry_point("load_arc_tasks")
        g.add_edge("load_arc_tasks","get_next_task")
        g.add_edge("get_next_task","write_pipeline")
        g.add_conditional_edges("write_pipeline", _orch.route_after_pipeline,
            {"save":"save_and_track","rewrite":"rewrite",
             "escalate":"human_escalation","budget_stop":END})
        g.add_conditional_edges("rewrite", _orch.route_after_rewrite,
            {"save":"save_and_track","rewrite":"rewrite","escalate":"human_escalation"})
        g.add_conditional_edges("save_and_track", _orch.route_after_save,
            {"next_task":"load_arc_tasks","done":END})
        g.add_edge("human_escalation", END)
        return g.compile(checkpointer=checkpointer)

    _orch.build_graph = _build_with_checkpoint
    return _build_with_checkpoint()
def load_state_for_project(project_id: str) -> dict:
    """Load state from SqliteSaver checkpoint, or create initial state.
    Falls back to JSON file for backward compat."""

    checkpointer = SqliteSaver.from_conn_string("checkpoints.sqlite")
    config = {"configurable": {"thread_id": project_id}}

    # Try loading from SqliteSaver first
    state = checkpointer.get_state(config)
    if state and state.values:
        return state.values

    # Fallback: load from JSON file
    json_path = Path(NOVEL_AI_DIR) / "output" / "orchestrator_state.json"
    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))

    # Create initial state
    from orchestrator_state import create_initial_state
    novel_config_path = Path(NOVEL_AI_DIR) / "config" / "novel_config.json"
    if novel_config_path.exists():
        nc = json.loads(novel_config_path.read_text(encoding="utf-8"))
    else:
        nc = {"novel_id": project_id, "title": "", "platform": "fanqie",
              "genre": "", "setting_concept": "", "budget_limit_usd": 500.0}

    return create_initial_state(
        novel_id=project_id,
        title=nc.get("title", ""),
        platform=nc.get("platform", "fanqie"),
        genre=nc.get("genre", ""),
        setting_concept=nc.get("setting_concept", ""),
        budget_limit_usd=nc.get("budget_limit_usd", 500.0),
    )


class SSECapture(io.StringIO):
    """Captures print() output and forwards each line to an SSE queue.
    ponytail: minimal — wraps existing print-based logging without
    changing orchestrator.py."""

    def __init__(self, queue: Queue):
        super().__init__()
        self.queue = queue
        self._buffer: list[str] = []

    def write(self, s: str):
        self._buffer.append(s)
        if s.endswith("\n"):
            line = "".join(self._buffer).rstrip("\n")
            self._buffer.clear()
            if line.strip():
                self.queue.put({"event": "log", "line": line})
        super().write(s)

    def flush(self):
        if self._buffer:
            line = "".join(self._buffer).rstrip()
            self._buffer.clear()
            if line.strip():
                self.queue.put({"event": "log", "line": line})
        super().flush()


async def run_graph_task(
    project_id: str,
    command: str,
    args: list[str],
    run_id: str,
    queue: Queue,
) -> tuple[int, str]:
    """Run a graph command in-process (no subprocess).

    Yields:
        (exit_code, stdout_text)

    Each printed line is pushed to queue as {"event": "log", "line": ...}.
    The caller (FastAPI endpoint) picks these up and forwards via SSE.
    """
    _ensure_import_path()

    capture = SSECapture(queue)
    capture.write(f"[engine] run_id={run_id} project={project_id} cmd={command}\n")

    try:
        # Build graph with SqliteSaver (idempotent on second call within same process)
        graph = build_project_graph(project_id)
        state = load_state_for_project(project_id)
        config = {
            "configurable": {
                "thread_id": project_id,
                "project_id": project_id,
            }
        }

        if command == "test":
            # Run system_test.py in-process
            from tools.system_test import run_all_tests
            with redirect_stdout(capture):
                result = run_all_tests()
            exit_code = 0 if result else 1

        elif command == "planner":
            from agents.planner_agent import run_planner
            novel_config_path = Path(NOVEL_AI_DIR) / "config" / "novel_config.json"
            nc = json.loads(novel_config_path.read_text(encoding="utf-8"))
            with redirect_stdout(capture):
                result = run_planner(nc, os.path.join(NOVEL_AI_DIR, "output"))
            exit_code = 0

        elif command in ("run", "resume"):
            chapters = int(args[0]) if args else 10
            from orchestrator import run_orchestrator
            with redirect_stdout(capture):
                state = run_orchestrator(state, max_chapters=chapters)
            exit_code = 0

        elif command == "status":
            from orchestrator import run_orchestrator
            from orchestrator_state import save_state
            state_path = os.path.join(NOVEL_AI_DIR, "output", "orchestrator_state.json")
            with redirect_stdout(capture):
                if state.get("current_phase"):
                    capture.write(f"📂 已加载状态：第{state.get('current_chapter',0)}章\n")
                    capture.write(f"  弧{state.get('current_arc',0)+1} | 预算${state.get('budget_used_usd',0):.3f}/${state.get('budget_limit_usd',500):.0f}\n")
                    if state.get("human_pending"):
                        capture.write(f"  ⚠️  {len(state['human_pending'])}个待处理\n")
                else:
                    capture.write("状态未初始化\n")
            exit_code = 0

        elif command == "bootstrap":
            from tools.bootstrap import run_bootstrap
            with redirect_stdout(capture):
                run_bootstrap()
            exit_code = 0

        elif command == "dashboard":
            from tools.dashboard import print_dashboard
            with redirect_stdout(capture):
                print_dashboard()
            exit_code = 0

        elif command == "budget":
            from tools.budget_manager import print_report
            with redirect_stdout(capture):
                print_report()
            exit_code = 0

        elif command == "scan":
            from tools.chapter_checker import scan_all_chapters
            with redirect_stdout(capture):
                scan_all_chapters(state.get("novel_id", project_id))
            exit_code = 0

        elif command == "pending":
            pending = state.get("human_pending", [])
            with redirect_stdout(capture):
                if not pending:
                    capture.write("✅ 无待处理任务\n")
                else:
                    capture.write(f"🚨 {len(pending)}个待处理任务：\n")
                    for t in pending:
                        prio = "🔴" if t.get("priority") == "must" else "🟡"
                        capture.write(f"  {prio} [{t.get('task_type','?')}] {t.get('description','')}\n")
            exit_code = 0

        elif command == "fingerprint":
            from tools.fingerprint_checker import cmd_scan
            with redirect_stdout(capture):
                cmd_scan()
            exit_code = 0

        elif command == "export":
            from tools.exporter import export_chapters
            with redirect_stdout(capture):
                export_chapters()
            exit_code = 0

        elif command == "stats":
            from tools.exporter import print_stats
            with redirect_stdout(capture):
                print_stats()
            exit_code = 0

        elif command == "init_arc":
            _ensure_import_path()
            from agents.outline_agent import run_outline
            from agents.tracker_agent import load_memory
            setting_path = os.path.join(NOVEL_AI_DIR, "output", "setting_package.json")
            setting = json.loads(open(setting_path, encoding="utf-8").read())
            arcs = state.get("arc_plans", [])
            idx = state.get("current_arc", 0)
            if idx >= len(arcs):
                with redirect_stdout(capture):
                    capture.write("❌ 所有弧已完成\n")
                return 0, capture.getvalue()
            arc = arcs[idx]
            mem = load_memory(state.get("novel_id", project_id))
            tasks, cost = run_outline(arc, state.get("current_chapter", 0) + 1, setting, mem)
            out_path = os.path.join(NOVEL_AI_DIR, "output", f"arc_{arc['arc_id']}_tasks.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
            with redirect_stdout(capture):
                capture.write(f"✅ 弧{arc['arc_id']}「{arc['arc_name']}」{len(tasks)}章 → {out_path}\n")
            exit_code = 0

        elif command == "show":
            n = int(args[0]) if args else 1
            ch_path = os.path.join(NOVEL_AI_DIR, "output", "chapters", f"ch_{n:04d}.txt")
            meta_path = ch_path.replace(".txt", "_meta.json")
            with redirect_stdout(capture):
                if os.path.exists(ch_path):
                    capture.write(open(ch_path, encoding="utf-8").read() + "\n")
                    if os.path.exists(meta_path):
                        m = json.loads(open(meta_path, encoding="utf-8").read())
                        capture.write(f"\n📊 {m.get('score',0):.1f}分 | 重写{m.get('rewrite_count',0)}次\n")
                else:
                    capture.write(f"❌ 第{n}章不存在\n")
            exit_code = 0

        else:
            with redirect_stdout(capture):
                capture.write(f"未知命令: {command}\n")
            exit_code = 1

    except Exception as exc:
        capture.write(f"[engine] ERROR: {exc}\n")
        exit_code = 1

    capture.flush()
    return exit_code, capture.getvalue()
