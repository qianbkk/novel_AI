"""pre_production.py — v1.0 Stage B API: theme_spine + genre_profile + opening_design

设计：所有 Stage 1 输出（genre_profile / theme_spine / opening_design / research_notes）
都通过同一组 REST 端点暴露，统一前缀 `/projects/{project_id}/pre-production/`。

UI 编辑约定（用户确认）：
- GET 读已落盘 JSON（用户编辑后或 AI 生成）
- PUT 接受完整 JSON 保存（用户编辑版，source='user'）
- POST /generate 调用 LLM 在模板基础上改写（source='llm'）
- 任何缺字段的 PUT → 400 InvalidThemeError（不让损坏数据落盘）

CLAUDE.md 红线：
- owner_id 校验（multi-user 模式下）
- production 模式下要求登录
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..auth import User
from ..auth_scope import is_production_mode, require_owned_project
from ..database import get_db
from ..models import Project

router = APIRouter(prefix="/projects/{project_id}/pre-production", tags=["pre-production"])

_log = logging.getLogger("novel_ai.api.pre_production")


# ── owner_id 校验 ─────────────────────────

def _get_current_user(request: Request) -> User | None:
    from ..auth import get_current_user_optional
    return get_current_user_optional(request)


def _require_owned_or_dev(project_id: str, request: Request, db: Session) -> Project:
    """dev 模式免登录直接通过；production 模式强制 owner 校验。"""
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, f"project {project_id} not found")

    if is_production_mode():
        user = _get_current_user(request)
        if user is None:
            raise HTTPException(401, "auth required (production mode)")
        require_owned_project(project, user)
    return project


def _resolve_novel_ai_dir(project_id: str, db: Session) -> Path:
    """从 NovelAIBinding 表读 novel_ai_dir（与 push-concept / pull-setting 一致）。

    若项目没绑定（dev 模式常见），返回 env NOVEL_AI_DIR（用于测试和单租户模式）。
    """
    from ..models import NovelAIBinding
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    if binding and binding.novel_ai_dir:
        return Path(binding.novel_ai_dir)
    # Fallback: NOVEL_AI_DIR env（dev 模式 + 测试路径）
    env_dir = os.environ.get("NOVEL_AI_DIR")
    if env_dir:
        return Path(env_dir)
    # 终极 fallback：engine 默认目录（确保不抛，调用方拿默认）
    from ..config.paths import novel_ai_dir
    return Path(novel_ai_dir()) if hasattr(novel_ai_dir, "__call__") else Path(str(novel_ai_dir))


# ── Request/Response schemas ─────────────────────────

class ThemeSpineIn(BaseModel):
    theme_statement: str
    expectation_arc: dict[str, Any]
    resonance_anchors: list[str]
    source: str = "user"


class GenerateThemeIn(BaseModel):
    concept: str = ""
    use_llm: bool = True


class GenreProfileIn(BaseModel):
    genre_key: str
    use_llm: bool = False


# ── Genre Profile endpoints ─────────────────────────

@router.get("/genre-profile")
def get_genre_profile(project_id: str, request: Request, db: Session = Depends(get_db)):
    """读 genre profile（落盘后返回，未生成返回 404）。"""
    _require_owned_or_dev(project_id, request, db)
    novel_ai_dir = _resolve_novel_ai_dir(project_id, db)
    from engine.agents.genre_profiler import load_profile
    profile = load_profile(str(novel_ai_dir))
    if profile is None:
        raise HTTPException(404, "genre_profile not yet generated")
    return profile


@router.post("/genre-profile/generate")
def generate_genre_profile(
    project_id: str,
    payload: GenreProfileIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """(重新)生成 genre profile，落盘。"""
    _require_owned_or_dev(project_id, request, db)
    novel_ai_dir = _resolve_novel_ai_dir(project_id, db)
    from engine.agents.genre_profiler import profile_genre, UnknownGenreError
    try:
        profile = profile_genre(
            payload.genre_key,
            use_llm=payload.use_llm,
            novel_id=str(novel_ai_dir),
        )
    except UnknownGenreError as exc:
        raise HTTPException(400, str(exc)) from exc
    return profile


# ── Theme Spine endpoints ─────────────────────────

@router.get("/theme")
def get_theme(project_id: str, request: Request, db: Session = Depends(get_db)):
    """读 theme_spine（落盘后返回，未生成返回 404）。"""
    _require_owned_or_dev(project_id, request, db)
    novel_ai_dir = _resolve_novel_ai_dir(project_id, db)
    from engine.agents.theme_designer import load_theme
    theme = load_theme(str(novel_ai_dir))
    if theme is None:
        raise HTTPException(404, "theme_spine not yet generated")
    return theme


@router.put("/theme")
def put_theme(
    project_id: str,
    payload: ThemeSpineIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """用户编辑后保存（source='user'）。缺字段 → 400。"""
    _require_owned_or_dev(project_id, request, db)
    novel_ai_dir = _resolve_novel_ai_dir(project_id, db)
    from engine.agents.theme_designer import save_theme, InvalidThemeError

    theme_dict = payload.model_dump()
    # 强制 source='user'（PUT 是 UI 编辑路径）
    theme_dict["source"] = "user"
    try:
        save_theme(str(novel_ai_dir), theme_dict)
    except InvalidThemeError as exc:
        raise HTTPException(400, f"invalid theme_spine: {exc}") from exc
    return {"status": "saved", "source": "user"}


@router.post("/theme/generate")
def generate_theme(
    project_id: str,
    payload: GenerateThemeIn,
    request: Request,
    db: Session = Depends(get_db),
):
    """(重新)生成 theme_spine（LLM 改写模板），落盘。

    用 project key_characters 作为输入。
    """
    _require_owned_or_dev(project_id, request, db)
    novel_ai_dir = _resolve_novel_ai_dir(project_id, db)
    from engine.agents.theme_designer import design_theme

    # 取 genre profile（若存在）+ key_characters（若存在）
    from engine.agents.genre_profiler import load_profile
    profile = load_profile(str(novel_ai_dir)) or {}

    project = db.get(Project, project_id)
    key_chars = []
    if project and getattr(project, "world_setting", None):
        ws = project.world_setting
        raw = getattr(ws, "novel_ai_raw_setting_json", None) or {}
        if isinstance(raw, dict):
            key_chars = raw.get("key_characters") or []

    theme = design_theme(
        concept=payload.concept,
        genre_profile=profile,
        key_characters=key_chars,
        use_llm=payload.use_llm,
        novel_id=str(novel_ai_dir),
    )
    return theme