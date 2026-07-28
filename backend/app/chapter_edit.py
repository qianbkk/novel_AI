"""章节人工编辑的一致性写服务。

编辑与候选采纳都必须经过这里：用 revision hash 做乐观锁，更新数据库正文、
人物图谱边、RAG 场景块，并在项目绑定了引擎目录时同步正式章节文件。
"""
from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from shared.atomic_io import atomic_write_text

from .chapter_rewrite import _resolve_engine_paths
from .models import (
    Chapter, ChapterCharacter, Character, EmbeddingChunk, NovelAIBinding,
)
from .rag.retrieval import persist_chapter_chunks


class ChapterEditConflictError(Exception):
    """客户端基于旧正文保存，拒绝 last-write-wins。"""


class ChapterEditNotFoundError(Exception):
    """项目下不存在目标章节。"""


def chapter_revision_hash(title: str | None, content: str) -> str:
    """返回稳定的章节版本指纹；标题变化同样会触发冲突。"""
    payload = f"{title or ''}\0{content}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def chapter_payload(chapter: Chapter) -> dict:
    return {
        "id": chapter.id,
        "chapter_no": chapter.chapter_no,
        "title": chapter.title,
        "content": chapter.content,
        "revision_hash": chapter_revision_hash(chapter.title, chapter.content or ""),
    }


def _backup_path(chapters_dir: Path, chapter_no: int, old_hash: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return chapters_dir.parent / "backups" / "manual_edits" / (
        f"ch_{chapter_no:04d}_{stamp}_{old_hash[:10]}.txt"
    )


async def update_chapter_content(
    *,
    project_id: str,
    chapter_id: str,
    title: str | None,
    content: str,
    expected_revision_hash: str,
    db: Session,
    source: str = "manual_edit",
) -> dict:
    """以单事务更新章节派生数据，并同步绑定引擎的正式稿文件。

    文件在数据库 commit 前原子替换；若 commit 失败，则尽力恢复旧文件。
    绑定目录中原文件存在时先复制到 output/backups/manual_edits/。
    """
    chapter = db.get(Chapter, chapter_id)
    if chapter is None or chapter.project_id != project_id:
        raise ChapterEditNotFoundError("chapter not found")

    current_hash = chapter_revision_hash(chapter.title, chapter.content or "")
    if expected_revision_hash != current_hash:
        raise ChapterEditConflictError(current_hash)

    normalized_title = title.strip() if isinstance(title, str) else None
    normalized_title = normalized_title or None
    normalized_content = content.replace("\r\n", "\n").strip()
    if not normalized_content:
        raise ValueError("content 不能为空")

    old_title = chapter.title
    old_content = chapter.content or ""
    target_path: Path | None = None
    target_existed = False
    backup_path: Path | None = None

    # 先在数据库事务中重建所有派生数据；此时尚未 commit。
    chapter.title = normalized_title
    chapter.content = normalized_content
    db.query(ChapterCharacter).filter_by(chapter_id=chapter.id).delete(
        synchronize_session=False
    )
    db.query(EmbeddingChunk).filter_by(
        project_id=project_id, source_type="chapter", source_id=chapter.id,
    ).delete(synchronize_session=False)

    characters = db.query(Character).filter_by(project_id=project_id).all()
    for character in characters:
        if character.name and character.name in normalized_content:
            db.add(ChapterCharacter(chapter_id=chapter.id, character_id=character.id))

    chunks = await persist_chapter_chunks(
        project_id=project_id,
        chapter_id=chapter.id,
        chapter_no=chapter.chapter_no,
        content=normalized_content,
        db=db,
    )

    dirs = _resolve_engine_paths(project_id, db)
    chapters_dir = dirs["chapters_dir"]
    # 仅对已绑定或已存在的引擎输出目录做同步；纯手工项目不额外制造 engine 目录。
    binding_exists = (
        db.query(NovelAIBinding).filter_by(project_id=project_id).first() is not None
    )
    if binding_exists or chapters_dir.exists():
        chapters_dir.mkdir(parents=True, exist_ok=True)
        target_path = chapters_dir / f"ch_{chapter.chapter_no:04d}.txt"
        target_existed = target_path.exists()
        if target_existed:
            backup_path = _backup_path(chapters_dir, chapter.chapter_no, current_hash)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_path, backup_path)
        atomic_write_text(str(target_path), normalized_content)

    try:
        db.commit()
    except Exception:
        db.rollback()
        if target_path is not None:
            try:
                if target_existed:
                    atomic_write_text(str(target_path), old_content)
                elif target_path.exists():
                    target_path.unlink()
            except OSError:
                pass
        chapter.title = old_title
        chapter.content = old_content
        raise

    return {
        **chapter_payload(chapter),
        "source": source,
        "indexed_chunk_count": len(chunks),
        "engine_file_synced": target_path is not None,
        "backup_path": (
            str(backup_path.relative_to(Path(dirs["novel_ai_dir"])))
            if backup_path is not None else None
        ),
    }
