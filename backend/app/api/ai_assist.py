"""api/ai_assist.py — 项目级 AI 参与度声明

端点：
  GET  /projects/{project_id}/ai-assist-level        读取
  PUT  /projects/{project_id}/ai-assist-level        更新 (ai_assisted | human_primary | unset)

对应 2025-09-01《人工智能生成合成内容标识办法》合规字段。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Project
from ..schemas import AiAssistLevelUpdate

router = APIRouter(prefix="/projects/{project_id}/ai-assist-level", tags=["ai-assist"])

VALID_LEVELS = {"ai_assisted", "human_primary", "unset"}


@router.get("")
def get_ai_assist_level(project_id: str, db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if not p:
        raise HTTPException(404, "project not found")
    return {"project_id": p.id, "ai_assist_level": p.ai_assist_level or "unset"}


@router.put("")
def put_ai_assist_level(project_id: str, payload: AiAssistLevelUpdate,
                        db: Session = Depends(get_db)):
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