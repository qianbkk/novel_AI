from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Chapter
from ..schemas import ChapterCreate
from ..rag.retrieval import add_chapter, semantic_search_chapters

router = APIRouter(prefix="/projects/{project_id}/chapters", tags=["chapters"])


@router.get("")
def list_chapters(project_id: str, db: Session = Depends(get_db)):
    """章节列表，按章节号排序，正文只给前 80 字预览——列表页不需要整章内容。"""
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
    query: str,
    character_id: str | None = None,
    top_k: int = 5,
    db: Session = Depends(get_db),
):
    """
    语义检索：传 character_id 时，先用图谱把候选范围收窄到"这个角色出现过的章节"，
    再用向量相似度排序——图谱负责精确范围，向量负责模糊相关性，分工不是二选一。

    注意：这个路由必须注册在 "/{chapter_id}" 之前——否则 FastAPI 会把
    "search" 当成 chapter_id 的值匹配到 get_chapter 上去。
    """
    return await semantic_search_chapters(project_id, query, character_id, top_k, db)


@router.get("/{chapter_id}")
def get_chapter(project_id: str, chapter_id: str, db: Session = Depends(get_db)):
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "chapter not found")
    return {
        "id": chapter.id,
        "chapter_no": chapter.chapter_no,
        "title": chapter.title,
        "content": chapter.content,
        "created_at": chapter.created_at,
    }


@router.post("")
async def create_chapter(project_id: str, payload: ChapterCreate, db: Session = Depends(get_db)):
    """
    新增一章正文：落库 + 自动标记出场人物（图谱边）+ embed 全文（向量）+
    跑一次重复度检测。返回结果里的 repetition_warnings 是"建议复核"，
    不会拦截保存——拦不拦是作者的判断，不是系统的判断。
    """
    return await add_chapter(project_id, payload.chapter_no, payload.title, payload.content, db)
