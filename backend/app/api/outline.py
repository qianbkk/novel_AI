"""Outline API — 弧级大纲 CRUD + LLM 生成。

修订 2026-07-16：解决用户反馈「写小说没有大纲入口」。
- DB 持久化大纲（之前 chapter_task_queue 在 state 里，断电丢）
- 独立 API 给前端展示 / 编辑 / 删除 / 重新生成
- /generate 端点调 run_outline() 拿 LLM 真实 chapter_goal
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Outline

log = logging.getLogger("novel_ai.api.outline")

router = APIRouter()


# ──────────────────── Pydantic schemas ────────────────────


class OutlineCreate(BaseModel):
    arc_id: int
    arc_name: str
    arc_goal: str | None = None
    arc_estimated_chapters: int = 30
    arc_climax_description: str | None = None
    arc_climax_chapter_offset: int = 15
    arc_ending_state: str | None = None
    emotion_curve: str = "上升"
    outline_json: list[dict[str, Any]] | None = None


class OutlineUpdate(BaseModel):
    arc_name: str | None = None
    arc_goal: str | None = None
    arc_estimated_chapters: int | None = None
    arc_climax_description: str | None = None
    arc_climax_chapter_offset: int | None = None
    arc_ending_state: str | None = None
    emotion_curve: str | None = None
    status: str | None = None
    outline_json: list[dict[str, Any]] | None = None


class OutlineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    arc_id: int
    arc_name: str
    arc_goal: str | None
    arc_estimated_chapters: int
    arc_climax_description: str | None
    arc_climax_chapter_offset: int
    arc_ending_state: str | None
    emotion_curve: str
    status: str
    outline_json: list[dict[str, Any]] | None
    created_at: str
    updated_at: str

class ArcGenerateRequest(BaseModel):
    arc_id: int
    arc_name: str
    arc_goal: str
    arc_estimated_chapters: int = Field(default=30, ge=1, le=300)
    arc_climax_description: str | None = None
    arc_climax_chapter_offset: int = 15
    arc_ending_state: str | None = None
    emotion_curve: str = "上升"
    genre: str | None = None  # 可选：从项目拿不到 genre 时手动传


# ──────────────────── Endpoints ────────────────────


@router.get("/projects/{project_id}/outlines", response_model=list[OutlineOut])
def list_outlines(project_id: str, db: Session = Depends(get_db)) -> list[OutlineOut]:
    rows = (
        db.query(Outline)
        .filter_by(project_id=project_id)
        .order_by(Outline.arc_id.asc(), Outline.created_at.asc())
        .all()
    )
    return [_to_out(r) for r in rows]


@router.get("/projects/{project_id}/outlines/{outline_id}", response_model=OutlineOut)
def get_outline(project_id: str, outline_id: str, db: Session = Depends(get_db)) -> OutlineOut:
    row = (
        db.query(Outline)
        .filter_by(project_id=project_id, id=outline_id)
        .first()
    )
    if not row:
        raise HTTPException(404, f"Outline {outline_id} not found")
    return _to_out(row)


@router.post("/projects/{project_id}/outlines", response_model=OutlineOut)
def create_outline(
    project_id: str,
    payload: OutlineCreate,
    db: Session = Depends(get_db),
) -> OutlineOut:
    """手动创建大纲（不调 LLM）。"""
    row = Outline(
        project_id=project_id,
        arc_id=payload.arc_id,
        arc_name=payload.arc_name,
        arc_goal=payload.arc_goal,
        arc_estimated_chapters=payload.arc_estimated_chapters,
        arc_climax_description=payload.arc_climax_description,
        arc_climax_chapter_offset=payload.arc_climax_chapter_offset,
        arc_ending_state=payload.arc_ending_state,
        emotion_curve=payload.emotion_curve,
        status="draft",
        outline_json=payload.outline_json,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log.info("outline created: project=%s arc=%s id=%s", project_id, payload.arc_id, row.id)
    return _to_out(row)


@router.patch("/projects/{project_id}/outlines/{outline_id}", response_model=OutlineOut)
def update_outline(
    project_id: str,
    outline_id: str,
    payload: OutlineUpdate,
    db: Session = Depends(get_db),
) -> OutlineOut:
    row = (
        db.query(Outline)
        .filter_by(project_id=project_id, id=outline_id)
        .first()
    )
    if not row:
        raise HTTPException(404, f"Outline {outline_id} not found")
    updates = payload.model_dump(exclude_unset=True)
    for k, v in updates.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    log.info("outline updated: %s fields=%s", outline_id, list(updates.keys()))
    return _to_out(row)


@router.delete("/projects/{project_id}/outlines/{outline_id}")
def delete_outline(project_id: str, outline_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    row = (
        db.query(Outline)
        .filter_by(project_id=project_id, id=outline_id)
        .first()
    )
    if not row:
        raise HTTPException(404, f"Outline {outline_id} not found")
    db.delete(row)
    db.commit()
    log.info("outline deleted: %s", outline_id)
    return {"ok": True, "deleted_id": outline_id}


@router.post("/projects/{project_id}/outlines/generate", response_model=OutlineOut)
async def generate_outline(
    project_id: str,
    payload: ArcGenerateRequest,
    db: Session = Depends(get_db),
) -> OutlineOut:
    """调 LLM 生成大纲。复用 engine.agents.outline.run_outline()。

    若同 arc_id 已有 outline，覆盖其 outline_json（用户显式触发重新生成）。
    """
    from ..models import Project, WorldSetting, Character, PowerSystem
    from engine.agents.outline import run_outline

    # 取 project + world setting 作为 LLM 上下文
    project = db.query(Project).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(404, f"Project {project_id} not found")
    ws = db.query(WorldSetting).filter_by(project_id=project_id).first()

    # 一期修复：之前读 ws.config_json —— WorldSetting 根本没有这个列
    # （config_json 在 Project 表上），导致大纲页触发的生成永远拿空上下文
    # （protagonist={} / key_characters=[] / levels=[]），产出质量断崖。
    # 改为从真实表构造：Character 表出人物、PowerSystem 表出力量体系、
    # novel_ai_raw_setting_json（引擎回灌的设定包）兜底补 protagonist。
    raw_setting = (ws.novel_ai_raw_setting_json if ws else None) or {}
    characters = db.query(Character).filter_by(project_id=project_id).all()
    power = db.query(PowerSystem).filter_by(project_id=project_id).first()

    protagonist = raw_setting.get("protagonist") or {}
    if not protagonist:
        mc_row = next((c for c in characters if c.role and "主角" in c.role), None)
        if mc_row:
            protagonist = {"name": mc_row.name}

    key_characters = [
        {"name": c.name, "role": c.role or "配角",
         "speech_quirks": ((c.card_catchphrase_json or {}).get("lines") or [])[:2],
         "background": ((c.card_background_json or {}).get("origin") or "")}
        for c in characters if not (c.role and "主角" in c.role)
    ][:6] or raw_setting.get("key_characters", [])

    levels = (power.tiers_json if power and power.tiers_json else None) \
        or (raw_setting.get("power_system") or {}).get("levels") or []

    setting: dict[str, Any] = {
        "novel_id": project_id,
        "title": project.title or "未命名",
        "genre": payload.genre or project.genre or "都市",
        "protagonist": protagonist,
        "key_characters": key_characters,
        "power_system": {"levels": levels},
    }

    arc = {
        "arc_id": payload.arc_id,
        "arc_name": payload.arc_name,
        "arc_goal": payload.arc_goal,
        "estimated_chapters": payload.arc_estimated_chapters,
        "arc_climax_description": payload.arc_climax_description or "",
        "arc_climax_chapter_offset": payload.arc_climax_chapter_offset,
        "arc_ending_state": payload.arc_ending_state or "",
        "emotion_curve": payload.emotion_curve,
        "new_characters_introduced": [],
        "is_final_arc": False,
    }

    memory: dict[str, Any] = {"hot": {}, "cold_summary": ""}
    try:
        from engine.memory.manager import get_l2
        memory = get_l2(project_id) or memory
    except Exception:
        log.warning("get_l2 failed for project=%s, using empty memory", project_id)

    # 调 LLM
    log.info("generate_outline: project=%s arc=%s", project_id, payload.arc_id)
    try:
        tasks, cost = run_outline(arc, 1, setting, memory)
    except Exception as e:
        log.exception("run_outline failed: %s", e)
        raise HTTPException(500, f"LLM 生成失败：{e}")

    # 落库：覆盖同 arc_id 的记录
    existing = (
        db.query(Outline)
        .filter_by(project_id=project_id, arc_id=payload.arc_id)
        .first()
    )
    if existing:
        existing.outline_json = tasks
        existing.arc_name = payload.arc_name
        existing.arc_goal = payload.arc_goal
        existing.arc_estimated_chapters = payload.arc_estimated_chapters
        existing.status = "draft"
        row = existing
    else:
        row = Outline(
            project_id=project_id,
            arc_id=payload.arc_id,
            arc_name=payload.arc_name,
            arc_goal=payload.arc_goal,
            arc_estimated_chapters=payload.arc_estimated_chapters,
            arc_climax_description=payload.arc_climax_description,
            arc_climax_chapter_offset=payload.arc_climax_chapter_offset,
            arc_ending_state=payload.arc_ending_state,
            emotion_curve=payload.emotion_curve,
            status="draft",
            outline_json=tasks,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    log.info("outline generated: id=%s tasks=%d cost=$%.4f", row.id, len(tasks), cost)
    return _to_out(row)


# ──────────────────── helpers ────────────────────


def _to_out(row: Outline) -> OutlineOut:
    return OutlineOut(
        id=row.id,
        project_id=row.project_id,
        arc_id=row.arc_id,
        arc_name=row.arc_name,
        arc_goal=row.arc_goal,
        arc_estimated_chapters=row.arc_estimated_chapters,
        arc_climax_description=row.arc_climax_description,
        arc_climax_chapter_offset=row.arc_climax_chapter_offset,
        arc_ending_state=row.arc_ending_state,
        emotion_curve=row.emotion_curve,
        status=row.status,
        outline_json=row.outline_json,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
    )
