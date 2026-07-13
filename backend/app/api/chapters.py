from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth_scope import require_owned_project
from ..database import get_db
from ..models import Chapter, ChapterCharacter, Character
from ..schemas import ChapterCreate, ChapterFull
from ..rag.retrieval import add_chapter, semantic_search_chapters, DuplicateChapterError

router = APIRouter(prefix="/projects/{project_id}/chapters", tags=["chapters"])


def _owner_check(request: Request, project_id: str, db: Session = Depends(get_db)):
    """Phase 4 跨用户隔离：所有 project-scoped 路由都强制 owner 校验。

    dev 模式默认放行（兼容旧客户端），prod 模式（NOVEL_PRODUCTION=1）严格 403。
    """
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        raise HTTPException(401, "authentication required")
    require_owned_project(db, project_id, user)
    return user  # 路由侧若需要 user 可读取（当前未用）


@router.get("")
def list_chapters(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """章节列表，按章节号排序，正文只给前 80 字预览——列表页不需要整章内容。

    Phase 4：依赖 _owner_check，已登录 user 仅看到自己的 project 的章节。
    """
    rows = (
        db.query(Chapter)
        .filter_by(project_id=project_id)
        .order_by(Chapter.chapter_no.asc())
        .all()
    )
    return [
        {
            "id": r.id,
            "chapter_no": r.chapter_no,
            "title": r.title,
            "content_preview": (r.content or "")[:80],
            "word_count": len(r.content or ""),
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/search")
async def search_chapters(
    project_id: str,
    request: Request,
    query: str,
    character_id: str | None = None,
    top_k: int = 5,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """
    语义检索：传 character_id 时，先用图谱把候选范围收窄到"这个角色出现过的章节"，
    再用向量相似度排序——图谱负责精确范围，向量负责模糊相关性，分工不是二选一。

    注意：这个路由必须注册在 "/{chapter_id}" 之前——否则 FastAPI 会把
    "search" 当成 chapter_id 的值匹配到 get_chapter 上去。

    Phase 4：跨项目越权读取向量检索结果也属于资源泄漏，必加 owner 校验。
    """
    return await semantic_search_chapters(project_id, query, character_id, top_k, db)


@router.get("/{chapter_id}", response_model=ChapterFull)
def get_chapter(
    project_id: str,
    chapter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """单章详情（含完整正文 + 出场人物列表）。

    Phase 4：跨用户读别人章节正文=严重信息泄漏，必加 owner 校验。
    """
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "chapter not found")
    # 关联出场人物
    edges = (
        db.query(ChapterCharacter, Character)
        .join(Character, ChapterCharacter.character_id == Character.id)
        .filter(ChapterCharacter.chapter_id == chapter_id)
        .all()
    )
    characters = [
        {
            "id": edge.ChapterCharacter.id,
            "character_id": edge.Character.id,
            "character_name": edge.Character.name,
            "character_role": edge.Character.role,
        }
        for edge in edges
    ]
    return ChapterFull(
        id=chapter.id,
        chapter_no=chapter.chapter_no,
        title=chapter.title,
        content=chapter.content,
        created_at=chapter.created_at,
        characters=characters,
    )


@router.get("/{chapter_id}/characters")
def get_chapter_characters(
    project_id: str,
    chapter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """某章出场的角色列表（来自 ChapterCharacter 图谱边）。"""
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "chapter not found")
    rows = (
        db.query(ChapterCharacter, Character)
        .join(Character, ChapterCharacter.character_id == Character.id)
        .filter(ChapterCharacter.chapter_id == chapter_id)
        .all()
    )
    return [
        {
            "id": edge.ChapterCharacter.id,
            "character_id": edge.Character.id,
            "character_name": edge.Character.name,
            "character_role": edge.Character.role,
        }
        for edge in rows
    ]


@router.post("")
async def create_chapter(
    project_id: str,
    payload: ChapterCreate,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """
    新增一章正文：落库 + 自动标记出场人物（图谱边）+ embed 全文（向量）+
    跑一次重复度检测。返回结果里的 repetition_warnings 是"建议复核"，
    不会拦截保存——拦不拦是作者的判断，不是系统的判断。

    Phase 4：写入类更要校验 — 否则用户能直接往别人 project 里塞内容。
    """
    try:
        return await add_chapter(project_id, payload.chapter_no, payload.title, payload.content, db)
    except DuplicateChapterError as e:
        # security-2026-07-13 #1: 同一 (project_id, chapter_no) 唯一约束触发，
        # 前端可以做"跳到已有章节 / 先删除再新建"二选一。
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_chapter_no",
                "message": f"chapter_no={e.chapter_no} already exists",
                "existing_chapter_id": e.existing_chapter_id,
            },
        )
