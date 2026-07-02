import asyncio
import json
from datetime import datetime
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

_run_queues: dict[str, Queue] = {}
_project_locks: dict[str, asyncio.Lock] = {}
WRITE_COMMANDS = {"planner", "bootstrap", "run", "resume", "init_arc"}


def get_run_queue(run_id: str) -> Queue:
    if run_id not in _run_queues:
        _run_queues[run_id] = Queue()
    return _run_queues[run_id]


def _get_project_lock(project_id: str) -> asyncio.Lock:
    if project_id not in _project_locks:
        _project_locks[project_id] = asyncio.Lock()
    return _project_locks[project_id]


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
    if _get_project_lock(project_id).locked():
        raise HTTPException(409, "该项目正在生成中，请勿重复触发")
    running = db.query(BridgeRun).filter_by(project_id=project_id, status="running").first()
    if running:
        raise HTTPException(409, "bridge run already running for this project")

    bridge_run = BridgeRun(
        project_id=project_id,
        command=command,
        args_json=payload.args,
        status="pending",
    )
    db.add(bridge_run)
    db.commit()
    db.refresh(bridge_run)

    # ponytail: run in-process via BackgroundTasks (uvicorn + TestClient 都能正确跟踪)
    queue = get_run_queue(bridge_run.id)
    # outline_mode (batch/card/talk) 透传给 orchestrator
    outline_mode = (payload.outline_mode or "batch").strip().lower()
    background_tasks.add_task(
        _run_bridge_async,
        bridge_run.id, project_id, command, payload.args or [],
        queue, outline_mode,
    )
    return bridge_run


@router.get("/stream")
async def stream_bridge(project_id: str, run_id: str, db: Session = Depends(get_db)):
    bridge_run = db.get(BridgeRun, run_id)
    if not bridge_run or bridge_run.project_id != project_id:
        raise HTTPException(404, "bridge run not found")
    queue = get_run_queue(run_id)

    async def event_generator():
        while True:
            payload = await asyncio.to_thread(queue.get)
            if payload.get("event") == "done":
                yield {"event": "done", "data": json.dumps(payload, ensure_ascii=False, default=str)}
                break
            yield {
                "event": payload.get("event", "log"),
                "data": json.dumps(payload, ensure_ascii=False, default=str),
            }

    return EventSourceResponse(event_generator())


@router.post("/push-concept")
async def push_concept(project_id: str, db: Session = Depends(get_db)):
    project, binding = _get_project_and_binding(project_id, db)
    if not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before pushing concept")
    return await push_setting_concept(project_id, binding.novel_ai_dir, db)


@router.post("/pull-setting")
async def pull_setting(project_id: str, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(project_id, db)
    return await pull_setting_package(project_id, binding.novel_ai_dir, db)


@router.post("/import-chapters")
async def import_chapters(project_id: str, db: Session = Depends(get_db)):
    _, binding = _get_project_and_binding(project_id, db)
    return await import_chapters_from_novel_ai(project_id, binding.novel_ai_dir, db)


@router.post("/reimport-chapters")
async def reimport_chapters(project_id: str, db: Session = Depends(get_db)):
    """强制重新导入章节：用最新的 txt + meta 覆盖 DB 已有行（修复章节管理显示问题）。
    普通 /import-chapters 是幂等的，会跳过已存在行；
    这个端点专用于修复标题 / 内容 / 摘要。"""
    _, binding = _get_project_and_binding(project_id, db)
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
    """Run graph command in-process. Called via asyncio.create_task from the endpoint."""
    from engine.graph import run_graph_task
    from datetime import datetime
    log.info("bridge run start: run_id=%s project=%s cmd=%s args=%s",
             run_id, project_id, command, args)
    lock = _get_project_lock(project_id)
    async with lock:
        db = SessionLocal()
        try:
            bridge_run = db.get(BridgeRun, run_id)
            if not bridge_run:
                log.warning("bridge run %s 不存在，跳过", run_id)
                return
            bridge_run.status = "running"
            db.commit()
            queue.put({"event": "start", "run_id": run_id, "command": command,
                       "outline_mode": outline_mode})

            # 把 binding.novel_ai_dir 注入 NOVEL_AI_DIR env，让 engine 的
            # STATE_PATH / OUTPUT_DIR / CHAPTERS_DIR 跟 binding 一致
            # （之前 state 在 novel_AI/，chapters 在 backend/，双重路径混乱）
            import os
            _prev_mode = os.environ.get("NOVEL_OUTLINE_MODE")
            os.environ["NOVEL_OUTLINE_MODE"] = outline_mode
            _prev_novel_ai_dir = os.environ.get("NOVEL_AI_DIR")
            binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
            if binding:
                os.environ["NOVEL_AI_DIR"] = binding.novel_ai_dir
            try:
                exit_code, stdout_text = await asyncio.to_thread(
                    run_graph_task, project_id, command, args, run_id, queue
                )
            finally:
                if _prev_mode is None:
                    os.environ.pop("NOVEL_OUTLINE_MODE", None)
                else:
                    os.environ["NOVEL_OUTLINE_MODE"] = _prev_mode
                if _prev_novel_ai_dir is None:
                    os.environ.pop("NOVEL_AI_DIR", None)
                else:
                    os.environ["NOVEL_AI_DIR"] = _prev_novel_ai_dir

            bridge_run.exit_code = exit_code
            bridge_run.stdout_text = stdout_text
            bridge_run.finished_at = datetime.utcnow()
            bridge_run.status = "done" if exit_code == 0 else "failed"
            db.commit()
            log.info("bridge run done: run_id=%s exit=%s stdout_len=%d",
                     run_id, exit_code, len(stdout_text or ""))

            if exit_code == 0 and command == "planner":
                queue.put({"event": "auto_pull_setting_start"})
                binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
                if binding:
                    await pull_setting_package(project_id, binding.novel_ai_dir, db)
                    queue.put({"event": "auto_pull_setting_done"})
            if exit_code == 0 and command in {"run", "resume"}:
                queue.put({"event": "auto_import_chapters_start"})
                binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
                if binding:
                    imported = await import_chapters_from_novel_ai(project_id, binding.novel_ai_dir, db)
                    queue.put({"event": "auto_import_chapters_done", "imported": imported})

            queue.put({"event": "complete", "status": bridge_run.status, "exit_code": exit_code})
        except Exception as exc:
            import traceback
            tb = traceback.format_exc()
            queue.put({"event": "error", "message": str(exc), "traceback": tb[-1000:]})
            bridge_run = db.get(BridgeRun, run_id)
            if bridge_run:
                bridge_run.status = "failed"
                bridge_run.finished_at = datetime.utcnow()
                db.commit()
        finally:
            done_payload = {"event": "done"}
            if "exit_code" in locals():
                done_payload["exit_code"] = exit_code
            queue.put(done_payload)
            db.close()


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



