from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import User
from ..auth_scope import is_production_mode, owner_filter_clause, require_owned_project
from ..database import get_db
from ..models import Character, Project
from ..schemas import ProjectCreate, ProjectOut
import logging

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_current_user(request: Request) -> User | None:
    """可选鉴权：解析 Authorization Bearer，失败返回 None。"""
    from ..auth import get_current_user_optional
    return get_current_user_optional(request)


@router.post("", response_model=ProjectOut, status_code=201)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """创建项目。

    ─── Phase 4: stamp owner_id ───
    如果当前请求带 token（已登录 user），把 owner_id 写为 user.id；
    否则 owner_id 留 NULL（表示"未认领"，dev 模式可访问）。

    ─── 2026-08-05 清单 P05 ───
    payload.title 为空时调 LLM 基于 genre / main_conflict 自动取名，
    满足前端 NewProject.tsx "留空则 AI 自动取名" placeholder 的承诺。
    LLM 失败 / mock 模式 fallback 到 f"未命名项目-{uuid4().hex[:6]}" 不留空串。
    """
    current_user = _get_current_user(request)
    title = (payload.title or "").strip()
    if not title:
        try:
            title = await _ai_auto_name(payload)
            title = (title or "").strip()
        except Exception as _exc:
            log = logging.getLogger("novel_ai.api.projects")
            log.warning("project auto-name LLM call failed: %s", _exc)
            title = ""
        if not title:
            import uuid as _uuid
            title = f"未命名项目-{_uuid.uuid4().hex[:6]}"

    project = Project(
        title=title,
        genre=payload.genre,
        audience=payload.audience,
        config_json=payload.config_json,
        owner_id=current_user.id if current_user else None,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


async def _ai_auto_name(payload: ProjectCreate) -> str:
    """缺 title 时调 LLM 真取名；走 app 侧 llm_client 与 create_project 同步流程解耦。

    输入：genre + main_conflict + tropes（payload.config_json 子字段）。
    输出：4-12 字的小说名，不要「《》」号、不要"第N本"前缀、不要具体角色名。
    """
    from .llm_client import LLMError, call_llm_json
    cfg = payload.config_json or {}
    conflicts = (cfg.get("main_conflict") or "").strip()
    tropes = "、".join(cfg.get("tropes") or []) if isinstance(cfg.get("tropes"), list) else ""
    user_prompt = (
        f"【题材】{payload.genre or '未指定'}\n"
        f"【标签】{tropes or '无'}\n"
        f"【主要冲突】{conflicts or '无'}\n"
        f"受众：{payload.audience or '未指定'}\n"
    )
    return await call_llm_json(
        role="creative_detail",
        system_prompt=(
            "你是网文取名编辑。任务：基于下面的题材/标签/主要冲突，"
            "起一个 4-12 字的网文书名（不要「《》」书名号、不要「第N本」编号、"
            "不要具体角色名、不要文艺腔）。只返回 JSON："
            '{"title":"起好的名字"}。只返回这一项，不要任何额外文字。'
        ),
        user_prompt=user_prompt,
        mock_payload={"title": ""},
    ).get("title", "")


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """读取项目详情。

    ─── Phase 4: owner 校验 ───
    dev 模式允许 owner_id=NULL 的项目被任意 user 看（兼容旧数据）；
    prod 模式按 owner 过滤。
    """
    current_user = _get_current_user(request)
    project = require_owned_project(db, project_id, current_user)
    return project


@router.put("/{project_id}/platform")
def set_project_platform(
    project_id: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """设置项目平台。

    支持：
      fanqie | qidian | qimao —— 走对应平台合规
      personal | none | internal —— 跳过平台合规（个人原型 / 自存档用）

    写 project.config_json.platform，下次 push-concept → novel_config.json → planner →
    setting_package.json 都会带过去；engine run 时 compliance agent 读取 platform
    决定是否跳过。
    """
    platform = (payload or {}).get("platform", "").strip()
    valid = {"fanqie", "qidian", "qimao", "personal", "none", "internal"}
    if platform not in valid:
        raise HTTPException(400, f"platform must be one of {sorted(valid)} (got {platform!r})")
    current_user = _get_current_user(request)
    project = require_owned_project(db, project_id, current_user)
    cfg = dict(project.config_json or {})
    cfg["platform"] = platform
    project.config_json = cfg
    db.commit()
    db.refresh(project)
    return {"project_id": project_id, "platform": platform}


@router.get("", response_model=list[ProjectOut])
def list_projects(
    request: Request,
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="模糊匹配 title 或主角名"),
    genre: str | None = Query(None, description="精确匹配 genre"),
):
    """列出项目。

    ─── Phase 4: owner 过滤 ───
    已登录 user：仅看 owner_id == self.id 或 owner_id IS NULL；
    未登录 + dev 模式：看全部；
    未登录 + production 模式：401（authrouter 会拦截）。

    ─── 2026-07-24 运行态可见性 ───
    返回每条 ProjectOut 带 active_run_command/status 字段：当前
    pending/running 的最新一条 BridgeRun。Dashboard 用它显示
    "运行中" badge，否则用户看不到正在跑的小说（status 字段不会变）。
    """
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
    from ..models import BridgeRun
    current_user = get_current_user_optional(request)

    if current_user is None and is_production_mode():
        raise HTTPException(401, "authentication required")

    query = db.query(Project)
    query = query.filter(owner_filter_clause(current_user))
    if genre:
        query = query.filter(Project.genre == genre)
    if q:
        # 模糊匹配 title 或主角名（Character.role == '主角'）
        like = f"%{q}%"
        protagonist_ids = db.query(Character.project_id).filter(
            Character.role == "主角",
            Character.name.like(like),
        ).subquery()
        query = query.filter(or_(
            Project.title.like(like),
            Project.id.in_(select(protagonist_ids.c.project_id)),
        ))
    projects = query.order_by(Project.created_at.desc()).all()

    # 取所有相关项目的 active BridgeRun（一次性查避免 N+1）
    project_ids = [p.id for p in projects]
    active_by_pid: dict[str, BridgeRun] = {}
    if project_ids:
        for run in db.query(BridgeRun).filter(
            BridgeRun.project_id.in_(project_ids),
            BridgeRun.status.in_(["pending", "running"]),
        ).all():
            existing = active_by_pid.get(run.project_id)
            # 同 project 多条 → 取最新 started_at
            if existing is None or (run.started_at and run.started_at > existing.started_at):
                active_by_pid[run.project_id] = run

    out = []
    for p in projects:
        ar = active_by_pid.get(p.id)
        # Project model 当前没有 updated_at 列（schema 已预留），用 getattr 容错
        updated_at = getattr(p, "updated_at", None)
        out.append(ProjectOut(
            id=p.id, title=p.title, genre=p.genre, audience=p.audience,
            status=p.status, budget_limit_usd=p.budget_limit_usd,
            novel_ai_status=p.novel_ai_status,
            created_at=p.created_at, updated_at=updated_at,
            active_run_command=ar.command if ar else None,
            active_run_status=ar.status if ar else None,
            active_run_id=ar.id if ar else None,
            active_run_started_at=ar.started_at if ar else None,
        ))
    return out
