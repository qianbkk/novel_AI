import asyncio
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from queue import Queue

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
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

log = get_logger("novel_ai.bridge")

router = APIRouter(prefix="/projects/{project_id}/bridge", tags=["bridge"])

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent

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
def get_binding(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    if not binding:
        raise HTTPException(404, "NovelAIBinding not found for project")
    return {
        "project_id": project_id,
        "novel_ai_dir": binding.novel_ai_dir,
        "novel_id": binding.novel_id,
    }


@router.put("/binding", response_model=NovelAIBindingOut)
def upsert_binding(project_id: str, payload: NovelAIBindingUpsert, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    project, binding = _get_project_and_binding(project_id, db)
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
        env = os.environ.copy()
        env["NOVEL_OUTLINE_MODE"] = outline_mode
        if binding:
            env["NOVEL_AI_DIR"] = binding.novel_ai_dir
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
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            cwd=str(BACKEND_ROOT),
            text=True,
            bufsize=1,  # line buffered
        )
        # 在独立线程读 stdout → put to queue
        import threading
        def _drain_stdout():
            db = SessionLocal()
            stdout_chunks: list[str] = []
            try:
                bridge_run = db.get(BridgeRun, run_id)
                if not bridge_run:
                    return
                bridge_run.status = "running"
                db.commit()
                queue.put({"event": "start", "run_id": run_id, "command": command,
                           "outline_mode": outline_mode})
                try:
                    for line in iter(proc.stdout.readline, ""):
                        stdout_chunks.append(line)
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
    except Exception as e:
        log.exception("spawn engine subprocess failed")
        queue.put({"event": "error", "message": str(e)})
        queue.put({"event": "done", "exit_code": -1})


@router.get("/stream")
async def stream_bridge(project_id: str, run_id: str, db: Session = Depends(get_db)):
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
async def push_concept(project_id: str, db: Session = Depends(get_db)):
    project, binding = _get_project_and_binding(project_id, db)
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before pushing concept")
    return await push_setting_concept(project_id, binding.novel_ai_dir, db)


@router.post("/pull-setting")
async def pull_setting(project_id: str, db: Session = Depends(get_db)):
    project, binding = _get_project_and_binding(project_id, db)
    # 迭代 #79: pull_setting 之前没有 worldbuild 检查——root_cause_analysis.md
    # 第 87 行明确指出 "50 章 0 个 ChapterCharacter 边" 就是因为 import_chapters 早于
    # pull 拉的代码路径。现在明确：pull 必须 worldbuild 完成。
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before pulling setting")
    return await pull_setting_package(project_id, binding.novel_ai_dir, db)


@router.post("/import-chapters")
async def import_chapters(project_id: str, db: Session = Depends(get_db)):
    project, binding = _get_project_and_binding(project_id, db)
    # 迭代 #79: import_chapters 之前没有 worldbuild 检查——"50 章 0 character 边"
    # 根因之一就在这里。import 早于 pull → add_chapter 找不到任何 character 可建边。
    # 强制：必须 worldbuild 完成（status='ready' 或 worldbuild GenerationJob=done）。
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before importing chapters")
    return await import_chapters_from_novel_ai(project_id, binding.novel_ai_dir, db)


@router.post("/reimport-chapters")
async def reimport_chapters(project_id: str, db: Session = Depends(get_db)):
    """强制重新导入章节：用最新的 txt + meta 覆盖 DB 已有行（修复章节管理显示问题）。
    普通 /import-chapters 是幂等的，会跳过已存在行；
    这个端点专用于修复标题 / 内容 / 摘要。"""
    project, binding = _get_project_and_binding(project_id, db)
    # 迭代 #79: reimport 跟 import-chapters 同样的根因——没有 worldbuild guard。
    # reimport 通常用于修复显示问题，但仍然依赖 character / setting 已写入。
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before reimporting chapters")
    from ..bridge.chapter_import import _force_reimport
    return await _force_reimport(project_id, binding.novel_ai_dir, db)


@router.post("/strip-junk-headers")
async def strip_junk_headers(project_id: str, db: Session = Depends(get_db)):
    """清理章节 txt 文件里的"假标题"残留头（【修改后正文】/【卷名】第N章 标题/重复第N章 行）。
    一次跑 3 个常见 case：ch1 占位 / ch42 卷首 / ch50 重复标题。
    修完 txt 后自动 reimport 把 DB 同步。"""
    import subprocess, sys
    from pathlib import Path
    # 调用 scripts.strip_chapter_headers
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    proc = subprocess.run(
        [sys.executable, "-m", "scripts.strip_chapter_headers"],
        cwd=scripts_dir.parent,  # backend dir
        capture_output=True, text=True,
    )
    log.info("strip-junk-headers: rc=%s, stdout=%s",
             proc.returncode, proc.stdout[:500])
    return {
        "return_code": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


@router.get("/status")
def status(project_id: str, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(project_id, db)
    return read_status(binding.novel_ai_dir)


@router.get("/pending")
def pending(project_id: str, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(project_id, db)
    return read_pending(binding.novel_ai_dir)


@router.get("/budget")
def budget(project_id: str, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(project_id, db)
    return read_budget_log(binding.novel_ai_dir)


@router.post("/review")
def review(project_id: str, payload: ReviewRequest, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(project_id, db)
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


def _get_project_and_binding(project_id: str, db: Session) -> tuple[Project, NovelAIBinding]:
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
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



