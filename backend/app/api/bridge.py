import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..bridge.chapter_import import import_chapters_from_novel_ai
# ponytail: runtime provider config via LLMRouter, no .env file needed
from engine.graph import run_graph_task
from ..bridge.reports import apply_review, read_budget_log, read_pending, read_status
from ..bridge.setting_sync import pull_setting_package, push_setting_concept
from ..database import SessionLocal, get_db
from ..logging_setup import get_logger
from ..models import BridgeRun, GenerationJob, NovelAIBinding, Project, Provider, RoleAssignment
from ..schemas import BridgeRunOut, BridgeRunRequest, NovelAIBindingOut, NovelAIBindingUpsert, ReviewRequest
from ..auth import get_current_user_optional
from ..auth_scope import is_production_mode, require_owned_project


def _current_user_or_401(request: Request):
    """生产模式下未登录直接 401；dev 模式返回 None。"""
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        from fastapi import HTTPException, status as _s
        raise HTTPException(_s.HTTP_401_UNAUTHORIZED, "authentication required")
    return user

log = get_logger("novel_ai.bridge")

router = APIRouter(prefix="/projects/{project_id}/bridge", tags=["bridge"])

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _terminate_process_tree(pid: int) -> None:
    """跨平台礼貌终止整个进程树 (security-2026-07-13 #3)。

    POSIX: os.killpg + SIGTERM；Windows: taskkill /T /PID (no /F)。
    给子进程一个清理机会；后续需要时再 SIGKILL/_kill_process_tree。
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.killpg(pid, 15)  # SIGTERM
    except (ProcessLookupError, PermissionError, OSError):
        # 子进程已死 / 跨用户 / 不存在；都不影响主流程
        pass


def _kill_process_tree(pid: int) -> None:
    """跨平台强杀整个进程树 (security-2026-07-13 #3)。

    _terminate_process_tree 之后宽限期仍不退出 → 直接 SIGKILL / taskkill /F。
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, timeout=10,
            )
        else:
            os.killpg(pid, 9)  # SIGKILL
    except (ProcessLookupError, PermissionError, OSError):
        pass

_run_queues: dict[str, Queue] = {}
# _project_locks 已删除（迭代 #30）：
#   之前用 asyncio.Lock 做"同 project 重复 run"并发保护，但锁从未被 acquire
#   （grep 证实无 `async with _get_project_lock`），检查永远 False
#   → 给 false sense of security。
#   真实保护是 DB 层 BridgeRun.status='running' 检查 + lifespan 启动时
#   _recover_orphan_bridge_runs（清理崩溃遗留的 running 行）。
WRITE_COMMANDS = {"planner", "bootstrap", "run", "resume", "init_arc"}


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


@router.put("/binding", response_model=NovelAIBindingOut)
def upsert_binding(project_id: str, payload: NovelAIBindingUpsert, request: Request, db: Session = Depends(get_db)):
    current_user = _current_user_or_401(request)
    project = require_owned_project(db, project_id, current_user)
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    novel_id = payload.novel_id or project.id
    if binding:
        binding.novel_ai_dir = payload.novel_ai_dir
        binding.novel_id = novel_id
    else:
        binding = NovelAIBinding(project_id=project_id, novel_ai_dir=payload.novel_ai_dir, novel_id=novel_id)
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
    #   1) DB 层：下面的 BridgeRun status in ('pending','running') 检查 — 同
    #      project 不能有两个 active 行
    #   2) lifespan 启动时 _recover_orphan_bridge_runs — 进程崩溃遗留的 running 行
    #      启动时被标 failed，避免永久卡住
    # 之前 _get_project_lock(project_id).locked() 是 dead code：
    #   asyncio.Lock 永不被 acquire（grep 证实无 `async with _get_project_lock`），
    #   检查永远 False，给 false sense of security。已删。
    # 迭代 #74: 之前只查 status='running' 有 TOCTOU 窗口 —— 新行插入后 status='pending'，
    # 翻 'running' 在 background thread 里才开始。两个并发请求都查 'running' 都查不到，
    # 都放行 + 都创建 pending → 同一 project_id 跑两个 engine 子进程写同一份 checkpoint。
    # 修法：active 检查包含 pending + running —— 一旦第一个 insert pending 成功并 commit，
    # 第二个请求的同检查能看到，不放行。
    running = db.query(BridgeRun).filter(
        BridgeRun.project_id == project_id,
        BridgeRun.status.in_(["pending", "running"]),
    ).first()
    if running:
        raise HTTPException(
            409,
            f"bridge run already active for this project (status={running.status})"
        )

    bridge_run = BridgeRun(
        project_id=project_id,
        command=command,
        args_json=payload.args,
        status="pending",
    )
    db.add(bridge_run)
    db.commit()
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
        # 从 Project 表读 per-project audit_mode（去全局化迭代 — 取代 os.environ）
        # 多项目共用一个 backend 时，A 设 draft 不会污染 B 的 run。
        project = db.get(Project, project_id)
        project_audit_mode = (project.audit_mode if project and project.audit_mode else "full")
        env = os.environ.copy()
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
        env["NOVEL_AI_DIR"] = (
            binding.novel_ai_dir if binding else os.environ.get("NOVEL_AI_DIR", "")
        )
        env["NOVEL_ENGINE_MOCK"] = os.environ.get("NOVEL_ENGINE_MOCK", "")
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

    cmd = [sys.executable, str(worker_script), run_id, project_id, command,
           *[str(a) for a in args], outline_mode]

    log.info("spawning engine subprocess: %s", " ".join(cmd[:3]))
    try:
        # security-2026-07-13 #2: start_new_session 让 subprocess 独立进程组，
        # 后续 killpg 能干净终止整个子进程树（避免孙进程泄漏）。
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(BACKEND_ROOT),
            text=True,
            bufsize=1,  # line buffered
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
        _activity = {"last_stdout_ts": _time.time(), "killed_by_watchdog": False}
        def _watchdog():
            """周期检查 stdout 空闲时间；超时 SIGTERM + 宽限期 SIGKILL。"""
            timeout_sec = _settings.engine_timeout_min * 60
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
                    _terminate_process_tree(proc.pid)
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
                    _kill_process_tree(proc.pid)
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
                    for line in iter(proc.stdout.readline, ""):
                        stdout_chunks.append(line)
                        # security-2026-07-13 #3: 每次 readline 视为子进程活跃
                        _activity["last_stdout_ts"] = _time.time()
                        # 把 stdout 当作 log 事件转发给 SSE
                        queue.put({"event": "log", "line": line.rstrip()})
                        # 每 50 行 flush 到 DB（避免频繁 commit）
                        if len(stdout_chunks) >= 50:
                            bridge_run.stdout_text = (bridge_run.stdout_text or "") + "".join(stdout_chunks)
                            db.commit()
                            stdout_chunks = []
                    # 进程结束
                    exit_code = proc.wait()
                    if stdout_chunks:
                        bridge_run.stdout_text = (bridge_run.stdout_text or "") + "".join(stdout_chunks)
                    bridge_run.exit_code = exit_code
                    bridge_run.finished_at = datetime.now(timezone.utc)
                    # security-2026-07-13 #3: 看门狗 SIGTERM/-KILL 终止 → 标 failed(timeout)
                    if _activity["killed_by_watchdog"]:
                        bridge_run.status = "failed"
                        timeout_msg = f"engine subprocess killed by watchdog after {_settings.engine_timeout_min}min idle"
                        bridge_run.stdout_text = (bridge_run.stdout_text or "") + f"\n[error] {timeout_msg}\n"
                    else:
                        bridge_run.status = "done" if exit_code == 0 else "failed"
                    db.commit()
                    queue.put({"event": "complete", "status": bridge_run.status,
                               "exit_code": exit_code})
                except Exception as loop_exc:
                    # 迭代 #54: 之前 try/finally 但没有 except — 循环里 DB 错误
                    # / KeyError 会让 daemon 线程静默死掉，bridge_run.status
                    # 卡在 "running"，下次 /bridge/run 触发 409 Conflict。
                    # 修法：把 bridge_run 标 failed + 记录异常 + 通过 queue
                    # 推送 error 事件，让 SSE consumer 看到真实原因。
                    log.exception("_drain_stdout loop failed")
                    try:
                        bridge_run.exit_code = -1
                        bridge_run.finished_at = datetime.now(timezone.utc)
                        bridge_run.status = "failed"
                        db.commit()
                    except Exception:
                        pass
                    queue.put({"event": "error", "message": str(loop_exc),
                               "traceback": traceback.format_exc()})
            finally:
                queue.put({"event": "done", "exit_code": proc.returncode})
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
    # 迭代 #79: pull_setting 之前没有 worldbuild 检查——root_cause_analysis.md
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



