import asyncio
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

# ponytail: runtime provider config via LLMRouter, no .env file needed
from ..auth import get_current_user_optional
from ..auth_scope import is_production_mode, require_owned_project
from ..bridge.chapter_import import import_chapters_from_novel_ai
from ..bridge.reports import (
    apply_review, read_budget_log, read_memory, read_pending, read_status,
)
from ..bridge.setting_sync import pull_setting_package, push_setting_concept
from ..database import SessionLocal, get_db
from ..logging_setup import get_logger
from ..models import BridgeRun, GenerationJob, NovelAIBinding, Outline, Project
from ..schemas import BridgeRunOut, BridgeRunRequest, NovelAIBindingOut, NovelAIBindingUpsert, ReviewRequest


def _current_user_or_401(request: Request):
    """生产模式下未登录直接 401；dev 模式返回 None。"""
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        from fastapi import HTTPException
        from fastapi import status as _s
        raise HTTPException(_s.HTTP_401_UNAUTHORIZED, "authentication required")
    return user

log = get_logger("novel_ai.bridge")

router = APIRouter(prefix="/projects/{project_id}/bridge", tags=["bridge"])

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _kill_process_tree(pid: int, force: bool = False) -> None:
    """跨平台终止整个进程树 (security-2026-07-13 #3)。

    POSIX: os.killpg + SIGTERM (force=False) / SIGKILL (force=True)。
    Windows: taskkill /T /PID [force=True 时加 /F]。
    子进程已死 / 跨用户 / 不存在均静默忽略（不影响主流程）。
    """
    try:
        if sys.platform == "win32":
            args = ["taskkill", "/T", "/PID", str(pid)]
            if force:
                args.insert(1, "/F")
            subprocess.run(args, capture_output=True, timeout=10)
        else:
            os.killpg(pid, 9 if force else 15)
    except (ProcessLookupError, PermissionError, OSError):
        pass


_run_queues: dict[str, Queue] = {}

# 审计 #12 (2026-07-20)：stdout_text 长度上限。超过则保留尾部最近 N 字节，
# 避免长任务导致 DB 写入放大 + 内存膨胀。SSE 实时流不受影响。
_STDOUT_TEXT_MAX = 1_000_000
# _project_locks 已删除（迭代 #30）：
#   之前用 asyncio.Lock 做"同 project 重复 run"并发保护，但锁从未被 acquire
#   （grep 证实无 `async with _get_project_lock`），检查永远 False
#   → 给 false sense of security。
#   真实保护是 DB 层 BridgeRun.status='running' 检查 + lifespan 启动时
#   _recover_orphan_bridge_runs（清理崩溃遗留的 running 行）。
WRITE_COMMANDS = {"planner", "bootstrap", "run", "run_draft", "resume", "init_arc"}
AUTO_IMPORT_COMMANDS = {"bootstrap", "run", "run_draft", "resume"}
_EXPECTED_BINDING_DIRS = ("config", "output", "memory", "logs")


def _validate_binding_dir(raw_path: str) -> str:
    """Validate and initialize a project-scoped engine data directory."""
    value = (raw_path or "").strip()
    if not value or "\x00" in value:
        raise HTTPException(400, "novel_ai_dir must be a non-empty filesystem path")

    path = Path(value).expanduser()
    try:
        path = path.resolve(strict=False)
    except OSError as exc:
        raise HTTPException(400, f"invalid novel_ai_dir: {exc}") from exc

    if path.exists() and not path.is_dir():
        raise HTTPException(400, "novel_ai_dir must be a directory, not a file")

    existing_parent = path
    while not existing_parent.exists() and existing_parent != existing_parent.parent:
        existing_parent = existing_parent.parent
    if not existing_parent.exists() or not existing_parent.is_dir():
        raise HTTPException(400, "novel_ai_dir has no usable parent directory")
    if not os.access(existing_parent, os.R_OK | os.W_OK):
        raise HTTPException(400, "novel_ai_dir parent directory is not readable and writable")

    try:
        path.mkdir(parents=True, exist_ok=True)
        for dirname in _EXPECTED_BINDING_DIRS:
            (path / dirname).mkdir(exist_ok=True)
        probe = path / f".bridge-write-probe-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(400, f"novel_ai_dir is not usable: {exc}") from exc

    return str(path)


def _chapter_snapshot(novel_ai_dir: str) -> dict[str, tuple[int, int]]:
    """Capture formal chapter files and their size/mtime for result validation."""
    chapters_dir = Path(novel_ai_dir) / "output" / "chapters"
    if not chapters_dir.is_dir():
        return {}
    snapshot: dict[str, tuple[int, int]] = {}
    for path in chapters_dir.glob("ch_*.txt"):
        if not re.fullmatch(r"ch_\d+\.txt", path.name):
            continue
        try:
            stat = path.stat()
            snapshot[path.name] = (stat.st_size, stat.st_mtime_ns)
        except OSError:
            continue
    return snapshot


def _sync_approved_outlines(project_id: str, novel_ai_dir: str, db: Session) -> dict:
    """Copy approved DB outlines into engine state for deterministic later adoption."""
    approved = (
        db.query(Outline)
        .filter(
            Outline.project_id == project_id,
            Outline.status.in_(["approved", "in_progress"]),
        )
        .order_by(Outline.arc_id.asc())
        .all()
    )
    mapping: dict[str, list[dict]] = {}
    for row in approved:
        tasks = row.outline_json or []
        if not tasks or len(tasks) != row.arc_estimated_chapters:
            raise RuntimeError(
                f"approved outline arc {row.arc_id} has {len(tasks)} tasks; "
                f"expected {row.arc_estimated_chapters}"
            )
        mapping[str(row.arc_id)] = [dict(task) for task in tasks]

    # 2026-08-08 修复（e2e 真实 LLM 暴露）：state 在 output/ 而不是 config/。
    # engine.agents.init_arc.build_state_from_setting 调 save_state(STATE_PATH_STR)，
    # paths.py STATE_PATH_STR = ENGINE_DATA_DIR / "output" / "orchestrator_state.json"。
    # 之前路径硬编码 config/，init_arc 实际写到 output/ → 这里永远报
    # 'init_arc completed but orchestrator_state.json is missing'，
    # run 后续命令前就被后处理拦死。
    state_path = Path(novel_ai_dir) / "output" / "orchestrator_state.json"
    if not state_path.is_file():
        raise RuntimeError("init_arc completed but orchestrator_state.json is missing")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["approved_outline_tasks"] = mapping
    from engine.utils import atomic_write_json
    atomic_write_json(str(state_path), state)
    return {"approved_arcs": sorted(int(key) for key in mapping), "task_count": sum(map(len, mapping.values()))}


async def _run_success_postprocessing(
    project_id: str,
    command: str,
    novel_ai_dir: str,
    queue: Queue,
    chapter_snapshot_before: dict[str, tuple[int, int]],
    db: Session,
) -> dict:
    """Complete the business transaction after an engine subprocess succeeds."""
    if command == "planner":
        queue.put({"event": "auto_pull_setting_start", "data": {"project_id": project_id}})
        result = await pull_setting_package(project_id, novel_ai_dir, db)
        queue.put({"event": "auto_pull_setting_done", "data": result})
        return {"setting_pulled": True}

    if command == "init_arc":
        result = _sync_approved_outlines(project_id, novel_ai_dir, db)
        queue.put({"event": "approved_outlines_synced", "data": result})
        return result

    if command in AUTO_IMPORT_COMMANDS:
        chapter_snapshot_after = _chapter_snapshot(novel_ai_dir)
        changed_files = sorted(
            name for name, fingerprint in chapter_snapshot_after.items()
            if chapter_snapshot_before.get(name) != fingerprint
        )
        if not changed_files:
            raise RuntimeError(
                f"{command} exited successfully but produced no new or updated formal chapter files; "
                "run init_arc first and verify pending arc tasks"
            )
        queue.put({"event": "auto_import_chapters_start", "data": {"files": changed_files}})
        imported = await import_chapters_from_novel_ai(
            project_id,
            novel_ai_dir,
            db,
            chapter_numbers={int(name[3:-4]) for name in changed_files},
        )
        imported_numbers = {int(item["chapter_no"]) for item in imported}
        expected_numbers = {int(name[3:-4]) for name in changed_files}
        if imported_numbers != expected_numbers:
            missing = sorted(expected_numbers - imported_numbers)
            raise RuntimeError(f"chapter auto-import incomplete; missing chapters: {missing}")
        queue.put({"event": "auto_import_chapters_done", "imported": imported})
        return {"imported": len(imported), "chapter_numbers": sorted(imported_numbers)}

    return {}


def get_run_queue(run_id: str) -> Queue:
    if run_id not in _run_queues:
        _run_queues[run_id] = Queue()
    return _run_queues[run_id]


def cleanup_run_queue(run_id: str) -> None:
    """SSE consumer 读完 done 事件后必须调用，否则 dict 无限增长（迭代 #33）。

    同 worldbuild._job_queues 的修复——之前 get_run_queue 只创建不清理，
    生产长期跑 100 个 bridge run 后 dict 里堆 100 个 Queue，内存持续涨。
    """
    _run_queues.pop(run_id, None)


def _append_stdout(current: str | None, new_chunks: list[str]) -> str:
    """把 stdout 增量写入 BridgeRun.stdout_text，超过 _STDOUT_TEXT_MAX 时环形截断。

    审计 #12 (2026-07-20)：之前直接 (current or "") + "".join(new_chunks)，
    长任务会导致 DB TEXT 字段和内存 str 持续增长（多 worker/长跑时放大到 MB+）。
    现在每次 append 前若总长度超上限则只保留尾部 N 字符（最近日志最有价值）。

    /simplify-2026-07-20：current 已经在上限时先 slice 一次，避免
    (1MB + 小块) 又 slice 一次的中间分配。
    """
    tail = "".join(new_chunks)
    combined = (current or "") + tail
    if len(combined) > _STDOUT_TEXT_MAX:
        combined = combined[-_STDOUT_TEXT_MAX:]
    return combined


@router.get("/binding", response_model=NovelAIBindingOut)
def get_binding(project_id: str, request: Request, db: Session = Depends(get_db)):
    _current_user_or_401(request)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    require_owned_project(db, project_id, get_current_user_optional(request))
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    if not binding:
        raise HTTPException(404, "NovelAIBinding not found for project")
    return {
        "project_id": project_id,
        "novel_ai_dir": binding.novel_ai_dir,
        "novel_id": binding.novel_id,
    }


@router.get("/orchestrator-state/talk-questions")
def get_talk_questions(project_id: str, request: Request, db: Session = Depends(get_db)):
    """P2-15（2026-08-17）：暴露 talk 模式的引导性问题给前端。

    之前 orchestrator 在 talk 模式下写 state.talk_questions 但全仓无消费端，
    talk 模式事实无意义。本路由让前端 WorldviewTab / OutlineView 能拿到
    待讨论问题，作者可在 UI 上回应 + 推进 outline。

    Owner 校验：与既有路由一致（_current_user_or_401 + require_owned_project）。
    """
    _current_user_or_401(request)
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    require_owned_project(db, project_id, get_current_user_optional(request))
    from .worldbuild import _load_talk_questions_from_engine
    return {"talk_questions": _load_talk_questions_from_engine(project_id)}


@router.put("/binding", response_model=NovelAIBindingOut)
def upsert_binding(project_id: str, payload: NovelAIBindingUpsert, request: Request, db: Session = Depends(get_db)):
    current_user = _current_user_or_401(request)
    project = require_owned_project(db, project_id, current_user)
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    novel_id = (payload.novel_id or project.id).strip()
    novel_ai_dir = _validate_binding_dir(payload.novel_ai_dir)
    if binding:
        binding.novel_ai_dir = novel_ai_dir
        binding.novel_id = novel_id
    else:
        binding = NovelAIBinding(project_id=project_id, novel_ai_dir=novel_ai_dir, novel_id=novel_id)
        db.add(binding)
    db.commit()
    return {
        "project_id": project_id,
        "novel_ai_dir": binding.novel_ai_dir,
        "novel_id": binding.novel_id,
    }


@router.post("/run", response_model=BridgeRunOut)
async def run_bridge(
    project_id: str,
    payload: BridgeRunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    project, binding = _get_project_and_binding(request, project_id, db)
    command = payload.command.lower().strip()
    if command in WRITE_COMMANDS and not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before running write commands")
    # 并发保护是双重的：
    #   1) DB 层：partial unique index `uq_bridge_runs_active_per_project`
    #      WHERE status IN ('pending','running')（见 app/migrations.py）—— 数据库
    #      层面硬保证"一个 project 至多一条 active run"，第二次 INSERT 抛 IntegrityError
    #      被下面 except 接住并翻译成 409。
    #   2) lifespan 启动时 _recover_orphan_bridge_runs — 进程崩溃遗留的 running 行
    #      启动时被标 failed，避免永久卡住。
    # 之前 2 个并发 "先查 active→再 insert" 有 TOCTOU 窗口（_74 + 旧 _running only check），
    # 都查不到对方（都还在 pending 状态）→ 都 insert 成功 → 同一 project 跑 2 个 engine
    # 子进程写同一份 checkpoint。partial unique index 在 INSERT 时由 SQLite 强制检查，
    # 不再依赖 Python 层"先查后插"模式。
    bridge_run = BridgeRun(
        project_id=project_id,
        command=command,
        args_json=payload.args,
        status="pending",
    )
    db.add(bridge_run)
    try:
        db.commit()
    except IntegrityError as exc:
        # 唯一约束违反说明已有 active run。rollback 后查那条行的 status 给用户清晰提示。
        db.rollback()
        existing = db.query(BridgeRun).filter(
            BridgeRun.project_id == project_id,
            BridgeRun.status.in_(["pending", "running"]),
        ).first()
        raise HTTPException(
            409,
            f"bridge run already active for this project (status={existing.status if existing else 'unknown'})",
        ) from exc
    db.refresh(bridge_run)

    # spawn subprocess 跑 engine（不再是 in-process via BackgroundTasks）
    # 原因：uvicorn 重启（手动 / --reload）会杀掉 in-process engine；
    # subprocess 独立于 uvicorn 进程，重启时 in-flight run 不会被打断。
    queue = get_run_queue(bridge_run.id)
    outline_mode = (payload.outline_mode or "batch").strip().lower()
    background_tasks.add_task(
        _spawn_engine_subprocess,
        bridge_run.id, project_id, command, payload.args or [],
        queue, outline_mode,
    )
    return bridge_run


@router.post("/set-audit-mode")
def set_audit_mode(project_id: str, payload: dict, request: Request, db: Session = Depends(get_db)):
    """运行时切换单个项目的 audit_mode（持久到 Project 行 + 推 env 到下次 subprocess run）。

    草稿模式 = audit_mode='draft'：node_load_arc_tasks 把所有任务的
    audit_mode 覆盖为 'draft'，node_write_pipeline 跳过 compliance+checker。
    完整模式 = 'full'（默认）：全质检链路。

    ─── Phase 3 ───
    之前直接写 os.environ["NOVEL_AUDIT_MODE"]，是进程全局状态——多项目共用
    一个 backend 时，A 设 draft 会污染 B 的下次 run。去全局化：写入 Project.audit_mode，
    run 时由 bridge 从 DB 读出注入 subprocess env，单项目隔离。
    """
    mode = (payload or {}).get("mode", "full").lower()
    if mode not in ("full", "lite", "draft"):
        raise HTTPException(400, f"audit_mode must be one of full|lite|draft (got {mode!r})")
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    require_owned_project(db, project_id, get_current_user_optional(request))
    project.audit_mode = mode
    db.commit()
    log.info("set_audit_mode project=%s mode=%s (persisted; will be propagated to subprocess on next run)",
             project_id, mode)
    return {"mode": mode}


def _spawn_engine_subprocess(run_id: str, project_id: str, command: str,
                              args: list[str], queue, outline_mode: str = "batch"):
    """在 subprocess 里跑 engine.run_graph_task。

    之前（in-process）：uvicorn 重启杀掉 engine，in-flight run 中断。
    现在（subprocess）：engine 在独立 Python 进程里跑，uvicorn 重启不影响。

    stdout pipe → 主进程读 → 转 put 到 SSE queue；同时把 stdout 追加写到
    BridgeRun.stdout_text 字段（兜底，SSE 断了也能查）。
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    # 从 binding 读 novel_ai_dir 注入 env（跟 in-process 版本一致）
    db = SessionLocal()
    try:
        binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
        novel_ai_dir = binding.novel_ai_dir if binding else os.environ.get("NOVEL_AI_DIR", "")
        chapter_snapshot_before = _chapter_snapshot(novel_ai_dir)
        # 从 Project 表读 per-project audit_mode（去全局化迭代 — 取代 os.environ）
        # 多项目共用一个 backend 时，A 设 draft 不会污染 B 的 run。
        project = db.get(Project, project_id)
        project_audit_mode = (project.audit_mode if project and project.audit_mode else "full")
        env = os.environ.copy()
        # 2026-07-23 修复（问题 #10）：显式 unbuffered 防止子进程 stdout
        # 在 Windows + text=True + PIPE 模式下死锁。
        # 之前 bufsize=1 + text=True + start_new_session 在 Windows 上
        # 子进程 print() 输出被 line-buffered 缓冲，主进程 readline 立即
        # 返回空 → 状态显示 0 字节 stdout → exit_code=3221225794 (ACCESS_VIOLATION)
        # 实际是子进程 hang 在 print buffer flush，spawn 后几秒被 watchdog 杀。
        # 修法：设 PYTHONUNBUFFERED=1 + PYTHONIOENCODING=utf-8 + 用 -u flag。
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["NOVEL_OUTLINE_MODE"] = outline_mode
        # 草稿模式开关：POST /bridge/set-audit-mode 写入 Project.audit_mode，
        # subprocess 必须继承，否则 engine 的 outline 仍走完整 audit_mode='full' 链路。
        # 兼容兜底：如果 Project.audit_mode 为空（极老数据列尚未应用），沿用父进程 env。
        env["NOVEL_AUDIT_MODE"] = project_audit_mode or os.environ.get("NOVEL_AUDIT_MODE", "")
        # P0 修复 (iter #84)：subprocess 必须继承 NOVEL_AI_DIR + NOVEL_ENGINE_MOCK。
        # 否则：
        #   - NOVEL_AI_DIR 缺失 → engine 写到 backend/data/engine/output/，
        #     bridge.reports 读不到 orchestrator_state.json / setting_package.json，
        #     size 只有 ~2 字节（空文件）
        #   - NOVEL_ENGINE_MOCK 缺失 → LLMRouter 不走 mock，真去调 API 报
        #     "MINIMAX_API_KEY 未设置" ValueError
        # binding.novel_ai_dir 优先；父进程 env 兜底（兼容 binding 缺失 / 测试场景）
        env["NOVEL_AI_DIR"] = novel_ai_dir
        env["NOVEL_ENGINE_MOCK"] = os.environ.get("NOVEL_ENGINE_MOCK", "0")
    finally:
        db.close()

    # 调用 engine.graph.run_graph_task 的等价入口
    # worker 脚本：engine/workers/run_bridge_subprocess.py
    worker_script = Path(__file__).resolve().parent.parent.parent / "engine" / "workers" / "run_bridge_subprocess.py"
    if not worker_script.exists():
        # worker 脚本是必需依赖，不存在就立刻报错（不要再降级到 -c + 调用
        # 已删除的 in-process fallback 函数路径，参考 commit 62baf44）。
        log.error("engine worker script missing: %s", worker_script)
        raise RuntimeError(
            f"engine/workers/run_bridge_subprocess.py 不存在：{worker_script}。"
            f"该脚本是 run 进程的必需依赖，缺失会导致 run 完全不可用。"
        )

    # 2026-07-23 修复（问题 #10）：加 -u flag 显式 unbuffered（与 env 配合双保险）。
    cmd = [sys.executable, "-u", str(worker_script), run_id, project_id, command,
           *[str(a) for a in args], outline_mode]

    log.info("spawning engine subprocess: %s", " ".join(cmd[:3]))
    try:
        # security-2026-07-13 #2: start_new_session 让 subprocess 独立进程组，
        # 后续 killpg 能干净终止整个子进程树（避免孙进程泄漏）。
        # Windows 上 start_new_session 在 Python 3.10+ 等价于 CREATE_NEW_PROCESS_GROUP，
        # subprocess 缓冲行为仍可能问题；改用 bytes 模式（不用 text=True）+ 自己 decode。
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(BACKEND_ROOT),
            text=False,  # 2026-07-23 改：bytes 模式避免 Windows text 模式缓冲死锁
            bufsize=0,  # 2026-07-23 改：unbuffered
            start_new_session=True,
        )
        # 在独立线程读 stdout → put to queue
        import threading

        # security-2026-07-13 #3: stdout 空闲看门狗。
        # 子进程 LLM 卡死 / 网络重试死循环会卡在 stdout 不动；
        # 没有看门狗 → BridgeRun 永久 running，SSE consumer 线程泄漏。
        # 共享 last_stdout_ts dict：_drain_stdout 每次 readline 更新 ts，
        # watchdog 线程每 30s 轮询检查 ts 超时则 killpg 终止整个进程组。
        import time as _time

        from app.config import settings as _settings
        # /simplify-2026-07-13: 把 timeout 一次性 bind 到局部变量，避免 watchdog
        # 闭包长期 pin Settings 单例（Pydantic 对象 + validators + alias map）。
        timeout_sec = _settings.engine_timeout_min * 60
        timeout_min_for_msg = _settings.engine_timeout_min
        _activity = {"last_stdout_ts": _time.time(), "killed_by_watchdog": False}
        def _watchdog():
            """周期检查 stdout 空闲时间；超时 SIGTERM + 宽限期 SIGKILL。"""
            grace_sec = 30  # SIGTERM 后等 30s 再 SIGKILL
            term_sent_at = None
            while True:
                _time.sleep(30)
                if proc.poll() is not None:
                    return  # 子进程已退出
                idle = _time.time() - _activity["last_stdout_ts"]
                if term_sent_at is None and idle > timeout_sec:
                    # 第一次超时：礼貌终止整个进程组（跨平台 helper）
                    log.warning(
                        "engine watchdog: idle %.0fs > %ds, terminating pid=%s run_id=%s",
                        idle, timeout_sec, proc.pid, run_id,
                    )
                    _kill_process_tree(proc.pid, force=False)
                    term_sent_at = _time.time()
                    _activity["killed_by_watchdog"] = True
                    queue.put({
                        "event": "log",
                        "line": f"[watchdog] idle {int(idle)}s, sent termination signal to engine subprocess",
                    })
                    continue
                if term_sent_at is not None and (_time.time() - term_sent_at) > grace_sec:
                    # 宽限期结束：强杀
                    log.warning(
                        "engine watchdog: grace period expired, force-killing pid=%s run_id=%s",
                        proc.pid, run_id,
                    )
                    _kill_process_tree(proc.pid, force=True)
                    return
        def _drain_stdout():
            db = SessionLocal()
            stdout_chunks: list[str] = []
            try:
                bridge_run = db.get(BridgeRun, run_id)
                if not bridge_run:
                    return
                bridge_run.status = "running"
                # security-2026-07-13 #2: 把子进程 pid 记下来，
                # lifespan 回收时用 pid 探测活体——还活着就**不动**这条行。
                bridge_run.pid = proc.pid
                db.commit()
                queue.put({"event": "start", "run_id": run_id, "command": command,
                           "outline_mode": outline_mode})
                try:
                    # 2026-07-23 修复（问题 #10）：text=False 后 line 是 bytes，
                    # 手动 decode 成 str。ignore 让 decode 错误不阻塞 stdout 读取。
                    for raw_line in iter(proc.stdout.readline, b""):
                        if isinstance(raw_line, bytes):
                            try:
                                line = raw_line.decode("utf-8", errors="replace")
                            except Exception:
                                line = repr(raw_line)
                        else:
                            line = raw_line
                        stdout_chunks.append(line)
                        # security-2026-07-13 #3: 每次 readline 视为子进程活跃
                        _activity["last_stdout_ts"] = _time.time()
                        # 把 stdout 当作 log 事件转发给 SSE
                        queue.put({"event": "log", "line": line.rstrip()})
                        # 每 50 行 flush 到 DB（避免频繁 commit）
                        if len(stdout_chunks) >= 50:
                            bridge_run.stdout_text = _append_stdout(
                                bridge_run.stdout_text, stdout_chunks
                            )
                            db.commit()
                            stdout_chunks = []
                    # 进程结束。exit_code=0 只是引擎层成功；Bridge 还必须完成
                    # setting 回流 / 章节导入和产物验收，才算业务成功。
                    exit_code = proc.wait()
                    if stdout_chunks:
                        bridge_run.stdout_text = _append_stdout(
                            bridge_run.stdout_text, stdout_chunks
                        )
                    if _activity["killed_by_watchdog"]:
                        exit_code = exit_code or -1
                        bridge_run.status = "failed"
                        timeout_msg = f"engine subprocess killed by watchdog after {timeout_min_for_msg}min idle"
                        bridge_run.stdout_text = _append_stdout(
                            bridge_run.stdout_text, [f"\n[error] {timeout_msg}\n"]
                        )
                    elif exit_code != 0:
                        bridge_run.status = "failed"
                    else:
                        try:
                            post_result = asyncio.run(_run_success_postprocessing(
                                project_id,
                                command,
                                novel_ai_dir,
                                queue,
                                chapter_snapshot_before,
                                db,
                            ))
                            bridge_run.status = "done"
                            if post_result:
                                queue.put({"event": "business_result", "data": post_result})
                        except Exception as post_exc:
                            log.exception("bridge postprocessing failed run_id=%s command=%s", run_id, command)
                            db.rollback()
                            bridge_run = db.get(BridgeRun, run_id)
                            exit_code = 1
                            bridge_run.status = "failed"
                            safe_msg = (str(post_exc) or "postprocessing failed")[:300]
                            bridge_run.stdout_text = _append_stdout(
                                bridge_run.stdout_text,
                                [f"\n[error] bridge business postprocessing failed: {safe_msg}\n"],
                            )
                            queue.put({"event": "auto_chain_error", "message": safe_msg})
                    bridge_run.exit_code = exit_code
                    bridge_run.finished_at = datetime.now(timezone.utc)
                    db.commit()
                    queue.put({"event": "complete", "status": bridge_run.status,
                               "exit_code": exit_code})
                except Exception as loop_exc:
                    # 迭代 #54: 之前 try/finally 但没有 except — 循环里 DB 错误
                    # / KeyError 会让 daemon 线程静默死掉，bridge_run.status
                    # 卡在 "running"，下次 /bridge/run 触发 409 Conflict。
                    # 修法：把 bridge_run 标 failed + 记录异常 + 通过 queue
                    # 推送 error 事件，让 SSE consumer 看到真实原因。
                    # 审计 #1 (2026-07-20)：不再把 traceback 文本塞进 queue payload，
                    # 避免 SSE 把堆栈（含绝对路径/SQL/内部模块名）透传给前端。
                    # 完整堆栈仍记在 log（运维看），SSE 只拿"内部错误"前缀 + 短消息。
                    log.exception("_drain_stdout loop failed")
                    try:
                        bridge_run.exit_code = -1
                        bridge_run.finished_at = datetime.now(timezone.utc)
                        bridge_run.status = "failed"
                        db.commit()
                    except Exception:
                        # 2026-08-18 修复（CLAUDE.md「失败要响亮」+ silent-exception agent）：
                        # 之前 pass 吞掉 — exit_code 更新失败意味着 BridgeRun 行
                        # 永远卡在 "running" 状态，前端 dashboard 显示 ⟳ 一直转。
                        # log.exception 让运维能从日志看到根因（数据库锁 / session 失效等）。
                        log.exception(
                            "_drain_stdout: BridgeRun 状态更新失败（行可能卡在 running）"
                        )
                    safe_msg = (str(loop_exc) or "loop error")[:200]
                    queue.put({"event": "error", "message": f"内部错误：{safe_msg}"})
            finally:
                final_run = db.get(BridgeRun, run_id)
                final_exit_code = final_run.exit_code if final_run and final_run.exit_code is not None else proc.returncode
                queue.put({"event": "done", "exit_code": final_exit_code})
                db.close()
        threading.Thread(target=_drain_stdout, daemon=True).start()
        threading.Thread(target=_watchdog, daemon=True).start()
    except Exception as e:
        log.exception("spawn engine subprocess failed")
        queue.put({"event": "error", "message": str(e)})
        queue.put({"event": "done", "exit_code": -1})


@router.get("/stream")
async def stream_bridge(project_id: str, run_id: str, request: Request, db: Session = Depends(get_db)):
    _current_user_or_401(request)
    require_owned_project(db, project_id, get_current_user_optional(request))
    bridge_run = db.get(BridgeRun, run_id)
    if not bridge_run or bridge_run.project_id != project_id:
        raise HTTPException(404, "bridge run not found")
    queue = get_run_queue(run_id)

    async def event_generator():
        try:
            while True:
                payload = await asyncio.to_thread(queue.get)
                if payload.get("event") == "done":
                    yield {"event": "done", "data": json.dumps(payload, ensure_ascii=False, default=str)}
                    break
                # /simplify-2026-07-20：error 事件脱敏放在 producer 侧
                # （_drain_stdout 异常分支只 put 干净 payload），consumer
                # 不再二次过滤；其他事件直接 json.dumps 透传。
                yield {
                    "event": payload.get("event", "log"),
                    "data": json.dumps(payload, ensure_ascii=False, default=str),
                }
        finally:
            # 迭代 #33：consumer 退出（break / 异常 / 客户端断开）时清理 queue，
            # 否则 _run_queues 无限增长导致内存泄漏。
            cleanup_run_queue(run_id)

    return EventSourceResponse(event_generator())


@router.post("/push-concept")
async def push_concept(project_id: str, request: Request, db: Session = Depends(get_db)):
    project, binding = _get_project_and_binding(request, project_id, db)
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before pushing concept")
    return await push_setting_concept(project_id, binding.novel_ai_dir, db)


@router.post("/pull-setting")
async def pull_setting(project_id: str, request: Request, db: Session = Depends(get_db)):
    project, binding = _get_project_and_binding(request, project_id, db)
    # pull_setting 之前必须完成 worldbuild，避免跨表依赖顺序被破坏。
    # 第 87 行明确指出 "50 章 0 个 ChapterCharacter 边" 就是因为 import_chapters 早于
    # pull 拉的代码路径。现在明确：pull 必须 worldbuild 完成。
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before pulling setting")
    return await pull_setting_package(project_id, binding.novel_ai_dir, db)


@router.post("/import-chapters")
async def import_chapters(project_id: str, request: Request, db: Session = Depends(get_db)):
    project, binding = _get_project_and_binding(request, project_id, db)
    # 迭代 #79: import_chapters 之前没有 worldbuild 检查——"50 章 0 character 边"
    # 根因之一就在这里。import 早于 pull → add_chapter 找不到任何 character 可建边。
    # 强制：必须 worldbuild 完成（status='ready' 或 worldbuild GenerationJob=done）。
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before importing chapters")
    return await import_chapters_from_novel_ai(project_id, binding.novel_ai_dir, db)


@router.post("/reimport-chapters")
async def reimport_chapters(project_id: str, request: Request, db: Session = Depends(get_db)):
    """强制重新导入章节：用最新的 txt + meta 覆盖 DB 已有行（修复章节管理显示问题）。
    普通 /import-chapters 是幂等的，会跳过已存在行；
    这个端点专用于修复标题 / 内容 / 摘要。"""
    project, binding = _get_project_and_binding(request, project_id, db)
    # 迭代 #79: reimport 跟 import-chapters 同样的根因——没有 worldbuild guard。
    # reimport 通常用于修复显示问题，但仍然依赖 character / setting 已写入。
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before reimporting chapters")
    from ..bridge.chapter_import import _force_reimport
    return await _force_reimport(project_id, binding.novel_ai_dir, db)


# security-2026-07-13 #5: 删除 POST /bridge/strip-junk-headers 端点
# 历史：这是个一次性清理脚本的 HTTP 包装，硬编码 `data/engine/output/chapters`
# 和 `../novel_AI/output/chapters` 路径，与传入的 project_id 完全无关——误
# 点此按钮会改写固定目录的文件，破坏另一个项目。
# 修法：删端点。需要清理章节假标题请直接跑
#   python -m scripts.strip_chapter_headers
# 或参考 docs/wiki/06-Dev-Setup.md "一次性修复脚本" 段。
# （scripts/strip_chapter_headers.py 本身保留——它有更严格的目标文件过滤
# 逻辑，不是问题。）


@router.get("/status")
def status(project_id: str, request: Request, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(request, project_id, db)
    return read_status(binding.novel_ai_dir)


@router.get("/pending")
def pending(project_id: str, request: Request, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(request, project_id, db)
    return read_pending(binding.novel_ai_dir)


@router.get("/budget")
def budget(project_id: str, request: Request, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(request, project_id, db)
    return read_budget_log(binding.novel_ai_dir)


@router.get("/memory")
def memory(project_id: str, request: Request, db: Session = Depends(get_db)):
    """分层记忆快照（L2 热/冷/约束 + L5 弧归档）。

    只读。在此之前记忆状态没有任何 API，伏笔逾期 / 质量债 / tracker 解析失败
    这些长篇致命信号只能去绑定目录手翻 JSON。
    """
    _, binding = _get_project_and_binding(request, project_id, db)
    return read_memory(binding.novel_ai_dir, binding.novel_id)


@router.post("/review")
def review(project_id: str, payload: ReviewRequest, request: Request, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(request, project_id, db)
    try:
        return apply_review(
            binding.novel_ai_dir,
            action=payload.action,
            task_id=payload.task_id,
            task_index=payload.task_index,
            chapter_number=payload.chapter_number,
            content=payload.content,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


async def _run_bridge_async(run_id: str, project_id: str, command: str,
                            args: list[str], queue,
                            outline_mode: str = "batch"):
    """DEPRECATED: in-process bridge runner, replaced by `_spawn_engine_subprocess`.

    删除原因：commit 62baf44 改成 subprocess 模式后，run endpoint 调的是
    _spawn_engine_subprocess。这个函数变成 dead code，保留会让人误以为
    还在用。新代码不要调用它；future endpoint 应该用 _spawn_engine_subprocess。
    """
    raise NotImplementedError(
        "_run_bridge_async 已废弃，请用 _spawn_engine_subprocess"
    )


def _get_project_and_binding(
    request: Request,
    project_id: str,
    db: Session,
) -> tuple[Project, NovelAIBinding]:
    """拿项目 + binding，且校验 ownership（Phase 4）。"""
    current_user = _current_user_or_401(request)
    project = require_owned_project(db, project_id, current_user)
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    if not binding:
        raise HTTPException(400, "NovelAIBinding not found for project")
    return project, binding


def _worldbuild_done(project_id: str, project: Project, db: Session) -> bool:
    if project.status == "ready":
        return True
    latest = (
        db.query(GenerationJob)
        .filter_by(project_id=project_id, job_type="worldbuild")
        .order_by(GenerationJob.created_at.desc())
        .first()
    )
    return bool(latest and latest.status == "done")



