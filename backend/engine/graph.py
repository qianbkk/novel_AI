"""Graph entry point — assembles the engine and returns a runnable.

P1-E rewrite: no more sys.path injection. All imports are local
relative imports from backend.engine.*. The graph builder is the
single entry point the bridge.py (and the smoke test, and run_mvp.py)
call to obtain a runnable for a given project.

Lifecycle:
  1. build_project_graph(project_id) loads DB config, builds a
     backend.engine.llm_router.LLMRouter, wires it into the agents and
     the orchestrator, and returns a LangGraph CompiledStateFluent
     ready to stream.
  2. The runner (bridge.py or test) opens an SSE EventSource and the
     graph pushes node_start/node_end/log/done events into a queue
     passed in by the caller.
"""
from __future__ import annotations
import json
import logging
import os
import sys
from pathlib import Path
from queue import Queue
from typing import Any, Optional

# ── Constants ──
BACKEND_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BACKEND_DIR / "data"
_CHECKPOINTS_PATH = str(DATA_DIR / "checkpoints.sqlite")
_STATE_PATH = str(DATA_DIR / "engine" / "output" / "orchestrator_state.json")

log = logging.getLogger("novel_ai.engine")


# ══════════════════════════════════════════
# SqliteSaver lifecycle (P3)
# ══════════════════════════════════════════
import sqlite3
import threading as _threading

_checkpointers: dict[str, "_CheckpointHandle"] = {}
_checkpointers_lock = _threading.Lock()


class _CheckpointHandle:
    """Holds a SqliteSaver + the sqlite3.Connection it was built on.

    Both must stay alive for the lifetime of the compiled graph; the
    handle keeps them paired so we can close them deterministically.
    """
    def __init__(self, saver, conn: sqlite3.Connection):
        self.saver = saver
        self.conn = conn


def _get_or_open_checkpointer(path: str):
    """Open or reuse a SqliteSaver-backed checkpointer at `path`.

    Returns the saver object (compatible with StateGraph.compile(checkpointer=...))
    or None if both SQLite and MemorySaver fail.
    """
    with _checkpointers_lock:
        handle = _checkpointers.get(path)
        if handle is not None:
            return handle.saver
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False because LangGraph may call put/get from
        # a background thread (e.g. when running via FastAPI BackgroundTasks).
        conn = sqlite3.connect(path, check_same_thread=False)
        from langgraph.checkpoint.sqlite import SqliteSaver
        saver = SqliteSaver(conn)
        # setup() is idempotent — creates tables if absent, returns immediately
        # if is_setup is already True.
        saver.setup()
        handle = _CheckpointHandle(saver=saver, conn=conn)
        with _checkpointers_lock:
            _checkpointers[path] = handle
        log.info("SqliteSaver wired: %s", path)
        return saver
    except Exception as e:
        log.warning("SqliteSaver unavailable (%s); falling back to MemorySaver", e)
        try:
            from langgraph.checkpoint.memory import MemorySaver
            return MemorySaver()
        except Exception as e2:
            log.warning("MemorySaver also unavailable: %s", e2)
            return None


def close_all_checkpointers() -> None:
    """Close every SqliteSaver + its underlying connection. Call at process
    shutdown (or between tests)."""
    with _checkpointers_lock:
        for path, handle in list(_checkpointers.items()):
            try:
                handle.conn.close()
            except Exception:
                pass
            _checkpointers.pop(path, None)


# ══════════════════════════════════════════
# Node event wrapper (Spec C)
# ══════════════════════════════════════════
class _NodeWrapper:
    """Wrap a LangGraph node fn so each entry/exit pushes a node_start/
    node_end event onto the SSE queue. Idempotent across queue re-use;
    safe to wrap any stateful callable."""
    def __init__(self, name: str, fn, queue: Queue):
        self.name = name
        self.fn = fn
        self.queue = queue

    def __call__(self, state):
        self.queue.put({"event": "node_start", "node": self.name})
        try:
            return self.fn(state)
        finally:
            self.queue.put({"event": "node_end", "node": self.name})


# ══════════════════════════════════════════
# SSECapture (print() → queue)
# ══════════════════════════════════════════
import io
from contextlib import redirect_stdout


class SSECapture(io.StringIO):
    """Captures print() output and forwards each line to an SSE queue.
    Used by run_graph_task so the orchestrator's print statements become
    `log` events for the browser."""

    def __init__(self, queue: Queue):
        super().__init__()
        self.queue = queue
        self._buffer: list[str] = []

    def write(self, s: str) -> int:
        self._buffer.append(s)
        if s.endswith("\n"):
            line = "".join(self._buffer).rstrip("\n")
            self._buffer.clear()
            if line.strip():
                self.queue.put({"event": "log", "line": line})
        return super().write(s)

    def flush(self) -> None:
        if self._buffer:
            line = "".join(self._buffer).rstrip()
            self._buffer.clear()
            if line.strip():
                self.queue.put({"event": "log", "line": line})
        super().flush()


# ══════════════════════════════════════════
# DB / state loaders
# ══════════════════════════════════════════
def _ensure_data_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "engine" / "output").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "engine" / "output" / "chapters").mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "engine" / "config").mkdir(parents=True, exist_ok=True)


def _load_state_for_project(project_id: str) -> dict:
    """Load OrchestratorState from disk if present; otherwise build an
    initial state from the project's saved config.

    Order:
      1. JSON state file on disk
      2. Project row → config_json → initial state
      3. Hardcoded defaults
    """
    from .state import load_state, create_initial_state

    if os.path.exists(_STATE_PATH):
        try:
            return load_state(_STATE_PATH)
        except Exception:
            pass

    from app.database import SessionLocal
    from app.models import Project
    db = SessionLocal()
    try:
        p = db.get(Project, project_id)
        cfg = p.config_json if p else None
        return create_initial_state(
            novel_id=project_id,
            title=p.title if p else "",
            platform=(cfg or {}).get("platform", "fanqie"),
            genre=p.genre if p else "都市",
            setting_concept=(cfg or {}).get("setting_concept", ""),
            budget_limit_usd=(cfg or {}).get("budget_limit_usd", 500.0) if p else 500.0,
        )
    finally:
        db.close()


# ══════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════
def build_project_graph(project_id: str, queue: Queue | None = None) -> Any:
    """Build a runnable LangGraph state machine for one project.

    Steps:
      1. Ensure the data dirs exist.
      2. Read DB config into backend.engine.llm_router.LLMRouter.
      3. Install the router as the active one for the agents.
      4. Build a SqliteSaver with the project_id as thread_id.
      5. Wire queue-based _NodeWrapper around each node if a queue is given.
      6. Return a compiled graph.

    After this call, `LLMRouter(project_id).engine` is the active
    router and the orchestrator's nodes will use it.
    """
    _ensure_data_dirs()

    # 1. Router — reads DB and configures the engine router.
    from .llm_router import LLMRouter as _BridgeRouter
    bridge_router = _BridgeRouter(project_id)
    bridge_router.install()
    engine_router = bridge_router.engine

    # 2. Checkpoint saver (P3: real SqliteSaver).
    #    langgraph-checkpoint-sqlite 3.1+ takes a sqlite3.Connection, not a
    #    string path. We open the connection ourselves (check_same_thread=
    #    False because LangGraph dispatches from multiple threads), then
    #    construct SqliteSaver directly and call setup() to create tables.
    #    The connection lives as long as the compiled graph does; we cache
    #    it module-level so multiple builds of the same project re-use the
    #    same DB handle. Falls back to MemorySaver if sqlite fails for any
    #    reason (e.g. read-only FS, sqlite3 unavailable).
    checkpointer = _get_or_open_checkpointer(_CHECKPOINTS_PATH)

    # 3. Build the LangGraph state machine from the orchestrator module.
    from .orchestrator import (
        OrchestratorState, node_load_arc_tasks, node_get_next_task,
        node_write_pipeline, node_rewrite, node_save_and_track,
        node_human_escalation, build_graph,
    )

    # 4. If a queue was provided, wrap each node to emit node_start/node_end.
    if queue is None:
        return build_graph(checkpointer=checkpointer)

    # Re-build with wrappers. The original build_graph() does this internally
    # by reading node functions from the module, so we instead re-instantiate
    # the StateGraph here with wrapped nodes.
    from langgraph.graph import StateGraph, END
    g = StateGraph(OrchestratorState)  # type: ignore
    g.add_node("load_arc_tasks",   _NodeWrapper("load_arc_tasks",   node_load_arc_tasks,   queue))
    g.add_node("get_next_task",    _NodeWrapper("get_next_task",    node_get_next_task,    queue))
    g.add_node("write_pipeline",   _NodeWrapper("write_pipeline",   node_write_pipeline,   queue))
    g.add_node("rewrite",          _NodeWrapper("rewrite",          node_rewrite,          queue))
    g.add_node("save_and_track",   _NodeWrapper("save_and_track",   node_save_and_track,   queue))
    g.add_node("human_escalation", _NodeWrapper("human_escalation", node_human_escalation, queue))
    g.set_entry_point("load_arc_tasks")
    g.add_edge("load_arc_tasks", "get_next_task")
    g.add_edge("get_next_task",  "write_pipeline")

    from .orchestrator import route_after_pipeline, route_after_rewrite, route_after_save
    g.add_conditional_edges("write_pipeline", route_after_pipeline,
        {"save": "save_and_track", "rewrite": "rewrite",
         "escalate": "human_escalation", "budget_stop": END})
    g.add_conditional_edges("rewrite", route_after_rewrite,
        {"save": "save_and_track", "rewrite": "rewrite", "escalate": "human_escalation"})
    g.add_conditional_edges("save_and_track", route_after_save,
        {"next_task": "load_arc_tasks", "done": END})
    # P3 fix: human_escalation 后回到 load_arc_tasks 继续下一章（不再 END）
    g.add_edge("human_escalation", "load_arc_tasks")
    return g.compile(checkpointer=checkpointer)


def load_state_for_project(project_id: str) -> dict:
    """Public helper: load the OrchestratorState for a project, falling
    back to an initial state if no JSON is on disk."""
    return _load_state_for_project(project_id)


# ══════════════════════════════════════════
# run_graph_task — the original "run a command in-process" entry
# ══════════════════════════════════════════
def run_graph_task(
    project_id: str,
    command: str,
    args: list[str],
    run_id: str,
    queue: Queue,
) -> tuple[int, str]:
    """Run a graph command in-process. Returns (exit_code, stdout_text).

    Mirrors the old subprocess-based run_graph_task contract:
      - test               → run system_test
      - planner/bootstrap  → run that agent
      - run / resume       → run the orchestrator
      - status / dashboard / budget / scan / pending / fingerprint /
        export / stats / show / init_arc → auxiliary commands
    """
    capture = SSECapture(queue)
    capture.write(f"[engine] run_id={run_id} project={project_id} cmd={command}\n")

    try:
        # Build graph (also installs the router for the agents)
        graph = build_project_graph(project_id, queue)
        state = load_state_for_project(project_id)
        config = {
            "configurable": {
                "thread_id":  project_id,
                "project_id": project_id,
            }
        }

        if command == "test":
            try:
                from .tools.system_test import run_all_tests
                with redirect_stdout(capture):
                    result = run_all_tests()
                exit_code = 0 if result else 1
            except ImportError:
                capture.write("[engine] WARN: backend.engine.tools.system_test not yet ported (P3)\n")
                exit_code = 0
        elif command == "planner":
            try:
                from .agents.planner import run_planner as _run_planner
                with redirect_stdout(capture):
                    _run_planner(args, str(DATA_DIR / "engine" / "output"))
                exit_code = 0
            except ImportError:
                capture.write("[engine] WARN: planner agent not yet ported (P2)\n")
                exit_code = 0
        elif command == "bootstrap":
            try:
                from .tools.bootstrap import run_bootstrap
                with redirect_stdout(capture):
                    run_bootstrap(novel_id=project_id)
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] bootstrap failed: {e}\n")
                exit_code = 1
        elif command in ("run", "resume"):
            chapters = int(args[0]) if args else 10
            from .orchestrator import run_orchestrator
            with redirect_stdout(capture):
                state = run_orchestrator(state, max_chapters=chapters)
            exit_code = 0
        elif command == "status":
            with redirect_stdout(capture):
                if state.get("current_phase"):
                    capture.write(
                        f"📂 已加载状态：第{state.get('current_chapter',0)}章\n"
                        f"  弧{state.get('current_arc',0)+1} | 预算"
                        f"${state.get('budget_used_usd',0):.3f}/"
                        f"${state.get('budget_limit_usd',500):.0f}\n"
                    )
                    if state.get("human_pending"):
                        capture.write(
                            f"  ⚠️  {len(state['human_pending'])}个待处理\n"
                        )
                else:
                    capture.write("状态未初始化\n")
            exit_code = 0
        elif command == "dashboard":
            capture.write("[engine] WARN: dashboard command not yet ported (P3)\n")
            exit_code = 0
        elif command == "budget":
            try:
                from .tools.budget_manager import print_report
                with redirect_stdout(capture):
                    print_report()
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] budget failed: {e}\n")
                exit_code = 1
        elif command == "scan":
            try:
                from .tools.chapter_checker import scan_all_chapters
                with redirect_stdout(capture):
                    scan_all_chapters(novel_id=project_id)
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] scan failed: {e}\n")
                exit_code = 1
        elif command == "pending":
            pending = state.get("human_pending", [])
            with redirect_stdout(capture):
                if not pending:
                    capture.write("✅ 无待处理任务\n")
                else:
                    capture.write(f"🚨 {len(pending)}个待处理任务：\n")
                    for t in pending:
                        prio = "🔴" if t.get("priority") == "must" else "🟡"
                        capture.write(
                            f"  {prio} [{t.get('task_type','?')}] "
                            f"{t.get('description','')}\n"
                        )
            exit_code = 0
        elif command == "fingerprint":
            try:
                from .tools.fingerprint_checker import cmd_scan
                with redirect_stdout(capture):
                    cmd_scan()
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] fingerprint failed: {e}\n")
                exit_code = 1
        elif command == "export":
            try:
                from .tools.exporter import export_chapters, print_stats
                sub = args[0] if args else "full"
                with redirect_stdout(capture):
                    if sub == "stats":
                        print_stats()
                    else:
                        export_chapters()
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] export failed: {e}\n")
                exit_code = 1
        elif command == "stats":
            try:
                from .tools.exporter import print_stats
                with redirect_stdout(capture):
                    print_stats()
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] stats failed: {e}\n")
                exit_code = 1
        elif command == "init_arc":
            try:
                from .agents.init_arc import build_state_from_setting
                with redirect_stdout(capture):
                    build_state_from_setting(project_id)
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] init_arc failed: {e}\n")
                exit_code = 1
        elif command == "human_review":
            try:
                from .tools.human_review import run_review
                with redirect_stdout(capture):
                    run_review()
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] human_review failed: {e}\n")
                exit_code = 1
        elif command == "style":
            try:
                from .tools.style_manager import cmd_list, extract_internal_samples
                sub = args[0] if args else "list"
                with redirect_stdout(capture):
                    if sub == "extract":
                        extract_internal_samples()
                    else:
                        cmd_list()
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] style failed: {e}\n")
                exit_code = 1
        elif command == "calibrate":
            try:
                from .tools.calibrate_checker import run_calibration
                with redirect_stdout(capture):
                    run_calibration()
                exit_code = 0
            except Exception as e:
                capture.write(f"[engine] calibrate failed: {e}\n")
                exit_code = 1
        elif command == "acceptance":
            try:
                from .tools.acceptance_tests import run_all
                with redirect_stdout(capture):
                    ok = run_all()
                exit_code = 0 if ok else 1
            except Exception as e:
                capture.write(f"[engine] acceptance failed: {e}\n")
                exit_code = 1
        elif command == "show":
            n = int(args[0]) if args else 1
            txt = DATA_DIR / "engine" / "output" / "chapters" / f"ch_{n:04d}.txt"
            with redirect_stdout(capture):
                if txt.exists():
                    capture.write(txt.read_text(encoding="utf-8") + "\n")
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
