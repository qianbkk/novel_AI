"""项目写作上下文的只读可见性接口。"""
from __future__ import annotations

from collections import Counter

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Chapter, EmbeddingChunk
from ..rag.retrieval import SEARCH_BUDGET_CHARS, retrieve_relevant_chunks
from .chapters import _owner_check

router = APIRouter(prefix="/projects/{project_id}/context", tags=["context"])


@router.get("/rag/status")
def get_rag_status(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """返回索引覆盖率和向量兼容性，不暴露提示词或 provider 凭据。"""
    chapters = db.query(Chapter.id, Chapter.chapter_no).filter_by(
        project_id=project_id,
    ).order_by(Chapter.chapter_no.asc()).all()
    chunks = db.query(EmbeddingChunk).filter_by(
        project_id=project_id, source_type="chapter",
    ).all()

    chapter_ids = {row.id for row in chapters}
    indexed_ids = {row.source_id for row in chunks if row.source_id in chapter_ids}
    unindexed = [row.chapter_no for row in chapters if row.id not in indexed_ids]
    dimensions = Counter(len(row.embedding_json or []) for row in chunks)
    models = Counter((row.model or "unknown") for row in chunks)
    empty_count = sum(1 for row in chunks if not (row.text_snippet or "").strip())
    orphaned_count = sum(1 for row in chunks if row.source_id not in chapter_ids)
    chapter_count = len(chapters)

    return {
        "available": bool(chunks),
        "chapter_count": chapter_count,
        "indexed_chapter_count": len(indexed_ids),
        "coverage_percent": round(
            len(indexed_ids) / chapter_count * 100, 1,
        ) if chapter_count else 100.0,
        "chunk_count": len(chunks),
        "unindexed_chapter_nos": unindexed,
        "orphaned_chunk_count": orphaned_count,
        "empty_chunk_count": empty_count,
        "dimensions": [
            {"dimension": dimension, "count": count}
            for dimension, count in sorted(dimensions.items())
        ],
        "models": [
            {"model": model, "count": count}
            for model, count in sorted(models.items())
        ],
        "mixed_dimensions": len(dimensions) > 1,
        "default_budget_chars": SEARCH_BUDGET_CHARS,
    }


class ContextPreviewRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)
    budget_chars: int = Field(default=SEARCH_BUDGET_CHARS, ge=100, le=5000)


@router.post("/rag/preview")
async def preview_rag_context(
    project_id: str,
    payload: ContextPreviewRequest,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """预览指定写作意图会注入哪些章节块及预算占用。"""
    chunks = await retrieve_relevant_chunks(
        project_id=project_id,
        query=payload.query,
        db=db,
        top_k=payload.top_k,
        budget_chars=payload.budget_chars,
    )
    used = sum(len(item.get("text") or "") for item in chunks)
    return {
        "query": payload.query,
        "chunks": chunks,
        "used_chars": used,
        "budget_chars": payload.budget_chars,
        "budget_percent": round(used / payload.budget_chars * 100, 1),
        "degraded": not chunks,
        "message": "没有兼容的相关索引块" if not chunks else None,
    }
