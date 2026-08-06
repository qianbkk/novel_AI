"""章节人工编辑的一致性写服务。

编辑与候选采纳都必须经过这里：用 revision hash 做乐观锁，更新数据库正文、
人物图谱边、RAG 场景块，并在项目绑定了引擎目录时同步正式章节文件。
"""
from __future__ import annotations

import hashlib
import logging
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

log = logging.getLogger("novel_ai.chapter_edit")


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


def _run_tracker_resync(
    *,
    project_id: str,
    chapter_no: int,
    title: str | None,
    content: str,
    source: str,
) -> None:
    """人工编辑/采纳候选后重跑 tracker，让 L2 记忆反映新正文。

    2026-08-06 修复（核查清单 #4）：
    之前人工编辑/采纳候选后只更新 DB + 章节文件 + RAG + 人物边，**完全
    不调 run_tracker**。L2 memory 的 character_states / inventory /
    active_threads / last_chapter_ending 等字段继续指向编辑前的事实——
    100 章长篇里越往后漂移越严重，下一章 writer 按"跳过了这一章"写。

    run_tracker 是同步 + 阻塞 + 调 LLM 的（5-30 秒）。人工编辑是低频操作
    但每个请求等这么久不友好，所以由 BackgroundTasks 在响应后跑：
      - LLM 失败要响亮（CLAUDE.md "失败要响亮"）：log error + 不抛
      - 不传 unverified=True：人工编辑 = 已确认事实，不是草稿
      - 不传 task._checker_result / emotion_intensity 等 orchestrator 字段：
        tracker 只读 task["chapter_number"]

    novel_id 在引擎层就是 project_id（graph.py:248 等多处复用），沿用。
    """
    try:
        # 延迟 import：避免在 import 阶段把 engine 全树拉起来。
        from engine.agents.tracker import run_tracker
        from engine.memory.manager import get_l2

        memory = get_l2(project_id)
        task = {
            "chapter_number": chapter_no,
            # 人工编辑时不存在 orchestrator 的 goal/role，给个中性的占位
            # 让 tracker prompt 能拼出有意义的 task 描述。
            "chapter_goal": (title or "").strip() or f"人工编辑第{chapter_no}章",
            "chapter_role": "manual_edit" if source == "manual_edit" else f"candidate_accept:{source}",
        }
        updated_mem, cost = run_tracker(
            content, task, memory, project_id,
        )
        log.info(
            "tracker resync after manual edit: project=%s chapter=%s source=%s cost=%.4f",
            project_id, chapter_no, source, cost,
        )
    except Exception as e:
        # 失败要响亮：log error，但不抛 —— manual edit 已经成功落盘，
        # tracker 失败不能影响编辑本身的成功返回。运维能 grep tracker 失败日志。
        log.error(
            "tracker resync FAILED after manual edit: project=%s chapter=%s source=%s err=%s",
            project_id, chapter_no, source, e,
        )


def schedule_tracker_resync(
    background_tasks,
    *,
    project_id: str,
    chapter_no: int,
    title: str | None,
    content: str,
    source: str,
) -> None:
    """把 tracker 重跑挂到 BackgroundTasks。

    background_tasks 可能是 None（测试 / CLI 直调），None 时降级为同步跑
    一次 —— 同步跑依然能更新 L2，只是调用方要多等几秒。这样保持测试
    可以直接 await update_chapter_content(...) 拿状态、观察 L2 已被更新。
    """
    if background_tasks is None:
        _run_tracker_resync(
            project_id=project_id,
            chapter_no=chapter_no,
            title=title,
            content=content,
            source=source,
        )
        return
    background_tasks.add_task(
        _run_tracker_resync,
        project_id=project_id,
        chapter_no=chapter_no,
        title=title,
        content=content,
        source=source,
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
    background_tasks=None,
) -> dict:
    """以单事务更新章节派生数据，并同步绑定引擎的正式稿文件。

    文件在数据库 commit 前原子替换；若 commit 失败，则尽力恢复旧文件。
    绑定目录中原文件存在时先复制到 output/backups/manual_edits/。

    background_tasks：传入时，commit 成功后挂一个 tracker 重跑任务；
    传入 None 时降级为同步跑（测试 / CLI 场景）。
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
    old_disk_content: str | None = None
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
        backup_path = _backup_path(chapters_dir, chapter.chapter_no, current_hash)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if target_existed:
            old_disk_content = target_path.read_text(encoding="utf-8")
            shutil.copy2(target_path, backup_path)
        else:
            # 引擎正式稿尚不存在时，仍把数据库中的原章留作可恢复快照。
            atomic_write_text(str(backup_path), old_content)
        atomic_write_text(str(target_path), normalized_content)

    try:
        db.commit()
    except Exception:
        db.rollback()
        if target_path is not None:
            try:
                if target_existed and old_disk_content is not None:
                    atomic_write_text(str(target_path), old_disk_content)
                elif target_path.exists():
                    target_path.unlink()
            except OSError:
                pass
        chapter.title = old_title
        chapter.content = old_content
        raise

    # 2026-08-06 修复（核查清单 #4）：commit 成功后挂一次 tracker resync，
    # 让 L2 memory 的 character_states / inventory / last_chapter_ending
    # 等字段反映人工编辑/采纳后的真实事实，而不是编辑前的事实。
    # BackgroundTasks 在响应后跑（同步 LLM 调用 5-30 秒不阻塞 API 返回）。
    schedule_tracker_resync(
        background_tasks,
        project_id=project_id,
        chapter_no=chapter.chapter_no,
        title=normalized_title,
        content=normalized_content,
        source=source,
    )

    return {
        **chapter_payload(chapter),
        "source": source,
        "indexed_chunk_count": len(chunks),
        "engine_file_synced": target_path is not None,
        "backup_path": (
            str(backup_path.relative_to(Path(dirs["novel_ai_dir"])))
            if backup_path is not None else None
        ),
        "tracker_resync_scheduled": background_tasks is not None,
    }
