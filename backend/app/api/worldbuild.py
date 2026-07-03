import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..database import get_db, SessionLocal
from ..models import (
    Project, GenerationJob, WorldSetting, Character, Faction,
    PowerSystem, MapNode, Foreshadowing, Currency, EntityRelation,
)
from ..schemas import JobOut
from ..worldbuild.orchestrator import run_worldbuild_job, get_job_queue, cleanup_job_queue

router = APIRouter(prefix="/projects/{project_id}/worldbuild", tags=["worldbuild"])


@router.post("/start", response_model=JobOut)
def start_worldbuild(project_id: str, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")

    job = GenerationJob(project_id=project_id, job_type="worldbuild", status="pending")
    db.add(job)
    db.commit()
    db.refresh(job)

    # 后台任务用独立的 db session，避免和当前请求的 session 生命周期冲突
    def _run():
        session = SessionLocal()
        try:
            asyncio.run(run_worldbuild_job(job.id, project_id, session))
        finally:
            session.close()

    background_tasks.add_task(_run)
    return job


@router.get("/stream")
async def stream_worldbuild(project_id: str, job_id: str):
    """
    前端订阅这个 SSE 端点，事件结构对应截图里"分析配置参数完成 / 设计主要人物完成..."
    这种逐步勾选的 UI：{event: stage_done, stage, label, progress_percent}
    """
    queue = get_job_queue(job_id)

    async def event_generator():
        try:
            while True:
                payload = await queue.get()
                if payload.get("event") == "done":
                    break
                # 显式 json.dumps——sse-starlette 拿到非字符串的 data 会用 Python repr
                # （单引号字典）输出，浏览器端 JSON.parse 碰到单引号会直接报错，
                # 这是接真实前端之前才会暴露的坑，提前在这里堵掉。
                yield {"event": payload["event"], "data": json.dumps(payload, default=str)}
        finally:
            # 迭代 #33：consumer 退出（break / 异常 / 客户端断开）时清理 queue，
            # 否则 _job_queues 无限增长导致内存泄漏。
            cleanup_job_queue(job_id)

    return EventSourceResponse(event_generator())


@router.get("/result")
def get_worldbuild_result(project_id: str, db: Session = Depends(get_db)):
    """构建完成后，前端用这个接口一次性拉取所有世界设定实体，渲染成截图(图4)那种 Tab 页面"""
    latest_job = (
        db.query(GenerationJob)
        .filter_by(project_id=project_id, job_type="worldbuild")
        .order_by(GenerationJob.created_at.desc())
        .first()
    )
    return {
        "world_setting": _serialize(db.query(WorldSetting).filter_by(project_id=project_id).first()),
        "characters": [_serialize(c) for c in db.query(Character).filter_by(project_id=project_id).all()],
        "relations": [_serialize(r) for r in db.query(EntityRelation).filter_by(project_id=project_id).all()],
        "factions": [_serialize(f) for f in db.query(Faction).filter_by(project_id=project_id).all()],
        "power_systems": [_serialize(p) for p in db.query(PowerSystem).filter_by(project_id=project_id).all()],
        "map_nodes": [_serialize(m) for m in db.query(MapNode).filter_by(project_id=project_id).all()],
        "foreshadowings": [_serialize(f) for f in db.query(Foreshadowing).filter_by(project_id=project_id).all()],
        "currencies": [_serialize(c) for c in db.query(Currency).filter_by(project_id=project_id).all()],
        # 一致性校验清单：交给作者自己判断，不自动拦截——见 stage_consistency_check 的设计说明
        "consistency_warnings": (latest_job.consistency_warnings_json if latest_job else []) or [],
    }


def _serialize(row):
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
