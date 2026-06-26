import asyncio
import json
from datetime import datetime
from queue import Queue

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..bridge.chapter_import import import_chapters_from_novel_ai
from ..bridge.env_writer import collect_assigned_providers, write_provider_env
from ..bridge.invoke import invoke_novel_ai
from ..bridge.reports import apply_review, read_budget_log, read_pending, read_status
from ..bridge.setting_sync import pull_setting_package, push_setting_concept
from ..database import SessionLocal, get_db
from ..models import BridgeRun, GenerationJob, NovelAIBinding, Project, Provider, RoleAssignment
from ..schemas import BridgeRunOut, BridgeRunRequest, NovelAIBindingOut, NovelAIBindingUpsert, ReviewRequest

router = APIRouter(prefix="/projects/{project_id}/bridge", tags=["bridge"])

_run_queues: dict[str, Queue] = {}
WRITE_COMMANDS = {"planner", "bootstrap", "run", "resume", "init_arc"}


def get_run_queue(run_id: str) -> Queue:
    if run_id not in _run_queues:
        _run_queues[run_id] = Queue()
    return _run_queues[run_id]


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
def run_bridge(
    project_id: str,
    payload: BridgeRunRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    project, binding = _get_project_and_binding(project_id, db)
    command = payload.command.lower().strip()
    if command in WRITE_COMMANDS and not _worldbuild_done(project_id, project, db):
        raise HTTPException(400, "worldbuild must be completed before running write commands")
    running = db.query(BridgeRun).filter_by(project_id=project_id, status="running").first()
    if running:
        raise HTTPException(409, "bridge run already running for this project")

    role_overrides, assigned_provider_ids = _build_role_overrides(db)
    providers = collect_assigned_providers(db, assigned_provider_ids)
    write_provider_env(binding.novel_ai_dir, providers)

    bridge_run = BridgeRun(
        project_id=project_id,
        command=command,
        args_json=payload.args,
        status="pending",
    )
    db.add(bridge_run)
    db.commit()
    db.refresh(bridge_run)

    background_tasks.add_task(_run_bridge_background, bridge_run.id, role_overrides)
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


def _run_bridge_background(run_id: str, role_overrides: dict[str, tuple[str, str]]) -> None:
    db = SessionLocal()
    queue = get_run_queue(run_id)
    try:
        bridge_run = db.get(BridgeRun, run_id)
        if not bridge_run:
            return
        binding = db.query(NovelAIBinding).filter_by(project_id=bridge_run.project_id).first()
        if not binding:
            bridge_run.status = "failed"
            bridge_run.stdout_text = "NovelAIBinding not found"
            bridge_run.finished_at = datetime.utcnow()
            db.commit()
            queue.put({"event": "error", "message": bridge_run.stdout_text})
            queue.put({"event": "done", "status": bridge_run.status})
            return

        bridge_run.status = "running"
        db.commit()
        queue.put({"event": "start", "run_id": run_id, "command": bridge_run.command})

        async def on_line(line: str) -> None:
            queue.put({"event": "log", "line": line})

        exit_code, stdout_text = asyncio.run(invoke_novel_ai(
            binding.novel_ai_dir,
            bridge_run.command,
            bridge_run.args_json or [],
            role_overrides,
            on_line,
        ))
        bridge_run.exit_code = exit_code
        bridge_run.stdout_text = stdout_text
        bridge_run.finished_at = datetime.utcnow()
        bridge_run.status = "done" if exit_code == 0 else "failed"
        db.commit()

        if exit_code == 0 and bridge_run.command == "planner":
            queue.put({"event": "auto_pull_setting_start"})
            asyncio.run(pull_setting_package(bridge_run.project_id, binding.novel_ai_dir, db))
            queue.put({"event": "auto_pull_setting_done"})
        if exit_code == 0 and bridge_run.command in {"run", "resume"}:
            queue.put({"event": "auto_import_chapters_start"})
            imported = asyncio.run(import_chapters_from_novel_ai(bridge_run.project_id, binding.novel_ai_dir, db))
            queue.put({"event": "auto_import_chapters_done", "imported": imported})

        queue.put({"event": "complete", "status": bridge_run.status, "exit_code": exit_code})
    except Exception as exc:  # noqa: BLE001
        bridge_run = db.get(BridgeRun, run_id)
        if bridge_run:
            bridge_run.status = "failed"
            bridge_run.stdout_text = ((bridge_run.stdout_text or "") + f"\n{exc}").strip()
            bridge_run.finished_at = datetime.utcnow()
            db.commit()
        queue.put({"event": "error", "message": str(exc)})
    finally:
        queue.put({"event": "done"})
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


def _build_role_overrides(db: Session) -> tuple[dict[str, tuple[str, str]], list[str]]:
    rows = db.query(RoleAssignment, Provider).join(Provider, RoleAssignment.provider_id == Provider.id).all()
    role_overrides = {}
    provider_ids = []
    for assignment, provider in rows:
        provider_ids.append(provider.id)
        role_overrides[assignment.role_key] = (
            provider.provider_type,
            assignment.model_override or provider.default_model,
        )
    return role_overrides, provider_ids
