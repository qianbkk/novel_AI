from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Character, Project
from ..schemas import ProjectCreate, ProjectOut

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectOut)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)):
    project = Project(
        title=payload.title,
        genre=payload.genre,
        audience=payload.audience,
        config_json=payload.config_json,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, db: Session = Depends(get_db)):
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    return project


@router.put("/{project_id}/platform")
def set_project_platform(
    project_id: str,
    payload: dict,
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
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    cfg = dict(project.config_json or {})
    cfg["platform"] = platform
    project.config_json = cfg
    db.commit()
    db.refresh(project)
    return {"project_id": project_id, "platform": platform}


@router.get("", response_model=list[ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="模糊匹配 title 或主角名"),
    genre: str | None = Query(None, description="精确匹配 genre"),
):
    query = db.query(Project)
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
