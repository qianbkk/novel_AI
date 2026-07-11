"""api/foreshadowings.py — 伏笔状态流转

端点：
  PUT /projects/{project_id}/foreshadowings/{foreshadowing_id}/status
    body: { status: 未铺垫 | 已铺垫 | 已回收 }

Phase 4：跨用户读到伏笔=读作品剧情走向，必加 owner 校验。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth_scope import require_owned_project
from ..database import get_db
from ..models import Foreshadowing, Project

router = APIRouter(prefix="/projects/{project_id}/foreshadowings", tags=["foreshadowings"])

VALID_STATUSES = {"未铺垫", "已铺垫", "已回收"}


def _owner_check(request: Request, project_id: str, db: Session = Depends(get_db)):
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        raise HTTPException(401, "authentication required")
    require_owned_project(db, project_id, user)
    return user


@router.put("/{foreshadowing_id}/status")
def update_foreshadowing_status(
    project_id: str,
    foreshadowing_id: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    status = (payload or {}).get("status", "")
    if status not in VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(VALID_STATUSES)}")
    fs = db.get(Foreshadowing, foreshadowing_id)
    if not fs or fs.project_id != project_id:
        raise HTTPException(404, "foreshadowing not found")
    fs.status = status
    db.commit()
    db.refresh(fs)
    return {
        "id": fs.id,
        "content": fs.content,
        "importance": fs.importance,
        "status": fs.status,
        "linked_character_id": fs.linked_character_id,
    }


@router.get("")
def list_foreshadowings(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """伏笔列表（Phase 4：跨用户读到=剧情泄漏，必加 owner 校验）。"""
    project = db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "project not found")
    rows = db.query(Foreshadowing).filter_by(project_id=project_id).all()
    return [
        {
            "id": r.id,
            "content": r.content,
            "importance": r.importance,
            "status": r.status,
            "linked_character_id": r.linked_character_id,
            "planted_chapter_hint": r.planted_chapter_hint,
            "payoff_chapter_hint": r.payoff_chapter_hint,
        }
        for r in rows
    ]
