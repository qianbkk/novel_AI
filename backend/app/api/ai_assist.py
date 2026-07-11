"""api/ai_assist.py — 项目级 AI 参与度声明

端点：
  GET  /projects/{project_id}/ai-assist-level        读取
  PUT  /projects/{project_id}/ai-assist-level        更新 (ai_assisted | human_primary | unset)

对应 2025-09-01《人工智能生成合成内容标识办法》合规字段。

Phase 4：项目级 ai_assist_level 一样 owner 隔离——尽管字段小，
跨用户改别人的 ai_assist_level 等于篡改合规元数据。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth_scope import require_owned_project
from ..database import get_db
from ..models import Project
from ..schemas import AiAssistLevelUpdate

router = APIRouter(prefix="/projects/{project_id}/ai-assist-level", tags=["ai-assist"])

VALID_LEVELS = {"ai_assisted", "human_primary", "unset"}


def _owner_check(request: Request, project_id: str, db: Session = Depends(get_db)):
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        raise HTTPException(401, "authentication required")
    require_owned_project(db, project_id, user)
    return user


@router.get("")
def get_ai_assist_level(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return {"project_id": p.id, "ai_assist_level": p.ai_assist_level or "unset"}


@router.put("")
def put_ai_assist_level(
    project_id: str,
    payload: AiAssistLevelUpdate,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    level = payload.ai_assist_level
    if level not in VALID_LEVELS:
        raise HTTPException(400, f"ai_assist_level must be one of {sorted(VALID_LEVELS)}")
    p.ai_assist_level = level
    db.commit()
    db.refresh(p)
    return {"project_id": p.id, "ai_assist_level": p.ai_assist_level}
