from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..auth import User
from ..auth_scope import is_production_mode, owner_filter_clause, require_owned_project
from ..database import get_db
from ..models import Character, Project
from ..schemas import ProjectCreate, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


def _get_current_user(request: Request) -> User | None:
    """可选鉴权：解析 Authorization Bearer，失败返回 None。"""
    from ..auth import get_current_user_optional
    return get_current_user_optional(request)


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """创建项目。

    ─── Phase 4: stamp owner_id ───
    如果当前请求带 token（已登录 user），把 owner_id 写为 user.id；
    否则 owner_id 留 NULL（表示"未认领"，dev 模式可访问）。
    """
    current_user = _get_current_user(request)
    project = Project(
        title=payload.title,
        genre=payload.genre,
        audience=payload.audience,
        config_json=payload.config_json,
        owner_id=current_user.id if current_user else None,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


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
    """
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
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
    return query.order_by(Project.created_at.desc()).all()
