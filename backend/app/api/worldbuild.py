import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from ..auth_scope import require_owned_project
from ..database import get_db, SessionLocal
from ..models import (
    Project, GenerationJob, WorldSetting, Character, Faction,
    PowerSystem, MapNode, Foreshadowing, Currency, EntityRelation,
)
from ..schemas import JobOut, StageListOut
from ..worldbuild.stages import STAGES
from ..worldbuild.orchestrator import run_worldbuild_job, get_job_queue, cleanup_job_queue

router = APIRouter(prefix="/projects/{project_id}/worldbuild", tags=["worldbuild"])
# 不带 project_id 前缀 — STAGES 是全局常量，跟具体项目无关（meta 路由不挂 owner）
meta_router = APIRouter(prefix="/worldbuild", tags=["worldbuild"])


def _owner_check(request: Request, project_id: str, db: Session = Depends(get_db)):
    """Phase 4：所有 project-scoped 路由统一 owner 校验。

    worldbuild.* 路由（start / stream / result）连到 GenerationJob 表，
    跨用户读到任务进度 / 启动任务 = 项目元数据泄漏，同样要 403。
    """
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        raise HTTPException(401, "authentication required")
    require_owned_project(db, project_id, user)
    return user


@meta_router.get("/stages", response_model=StageListOut)
def list_worldbuild_stages():
    """暴露 10 阶段清单给前端 WorldBuild.tsx — 之前前端硬编码 STAGES 数组，
    改一端忘改另一端就会进度条错位。DB-free 端点，前端首次挂载 fetch 一次即可。

    注意：meta 路由（不带 project_id），NOT project-scoped，**不挂 owner 校验**——
    STAGES 是全应用一致的 10 阶段常量，任何 user 看都一样。

    Cache-Control：STAGES 是发布期常量，部署后内容永远不会变（除非新版本重启后端）。
    加 `public, max-age=3600` 让浏览器/CDN 缓存 1 小时，前端刷新页面
    不会重发请求；同时 versioning 不依赖 query string（前端硬编码 URL，
    后端重启天然 cache-bust —— 部署后 StageListOut 内容变了，命中失效是 OK 的，
    因为前端 useEffect 在组件 mount 时会拉一次）。
    """
    # Phase 7：从模块顶层 import（auditor 🟢-1 建议）。原本 defer 是误以为能避免
    # 循环导入，但 orchestrator.py 必然 import stages.py，启动时早已加载。
    return JSONResponse(
        content={"stages": [{"key": k, "label": lbl} for (k, lbl, _fn) in STAGES]},
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post("/start", response_model=JobOut)
def start_worldbuild(
    project_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
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
async def stream_worldbuild(
    project_id: str,
    job_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """
    前端订阅这个 SSE 端点，事件结构对应截图里"分析配置参数完成 / 设计主要人物完成..."
    这种逐步勾选的 UI：{event: stage_done, stage, label, progress_percent}

    Phase 4：跨项目读取 SSE 流也能泄漏进度，必加 owner 校验。
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
def get_worldbuild_result(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """构建完成后，前端用这个接口一次性拉取所有世界设定实体，渲染成截图(图4)那种 Tab 页面。

    Phase 4：这接口内含全量世界观+角色+关系，跨用户读到=创意外泄，必加 owner 校验。
    """
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
        # ─── Phase 3：新增结构化字段（前端 WorldBuild UI 用）───
        # 老项目所有字段都是 None，前端 fallback 到 legacy world_view / story_core
        "worldview_rich": _serialize_field("world_view_rich_json", db.query(WorldSetting).filter_by(project_id=project_id).first()),
        "story_core_struct": _serialize_field("story_core_struct_json", db.query(WorldSetting).filter_by(project_id=project_id).first()),
        "history_timeline": _serialize_field("history_timeline_json", db.query(WorldSetting).filter_by(project_id=project_id).first()),
    }


def _serialize(row):
    if row is None:
        return None
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def _serialize_field(attr_name: str, row):
    """从 ORM row 抽单个 JSON 字段的值。row=None 时返回 None。"""
    if row is None:
        return None
    return getattr(row, attr_name, None)
