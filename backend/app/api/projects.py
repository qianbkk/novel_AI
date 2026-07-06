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
