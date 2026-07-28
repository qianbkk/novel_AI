from __future__ import annotations

import asyncio
import uuid

from tests._test_db import isolated_test_db  # noqa: F401


def _create_project():
    from app.database import SessionLocal
    from app.models import Project

    project_id = f"test-rag-life-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.add(Project(id=project_id, title="RAG 生命周期", genre="玄幻", config_json={}))
        db.commit()
    finally:
        db.close()
    return project_id


def test_add_chapter_uses_scene_chunks_without_whole_chapter_duplicate(api_client):
    from app.database import SessionLocal
    from app.models import EmbeddingChunk

    project_id = _create_project()
    content = "主角夺回家族徽记。" * 180
    response = api_client.post(
        f"/projects/{project_id}/chapters",
        json={"chapter_no": 1, "title": "徽记", "content": content},
    )
    assert response.status_code == 200, response.text
    chapter_id = response.json()["chapter_id"]

    db = SessionLocal()
    try:
        rows = db.query(EmbeddingChunk).filter_by(
            project_id=project_id, source_type="chapter", source_id=chapter_id,
        ).all()
        assert len(rows) > 1
        assert all(len(row.text_snippet) < len(content) for row in rows)
        assert all(row.model == "mock-ngram" for row in rows)
    finally:
        db.close()


def test_persist_chapter_chunks_is_idempotent():
    from app.database import SessionLocal
    from app.models import Chapter, EmbeddingChunk
    from app.rag.retrieval import persist_chapter_chunks

    project_id = _create_project()
    db = SessionLocal()
    try:
        chapter = Chapter(project_id=project_id, chapter_no=1, title="旧", content="旧内容。" * 80)
        db.add(chapter)
        db.flush()
        asyncio.run(persist_chapter_chunks(
            project_id=project_id, chapter_id=chapter.id, chapter_no=1,
            content=chapter.content, db=db,
        ))
        first_count = db.query(EmbeddingChunk).filter_by(source_id=chapter.id).count()

        chapter.content = "新内容。" * 120
        asyncio.run(persist_chapter_chunks(
            project_id=project_id, chapter_id=chapter.id, chapter_no=1,
            content=chapter.content, db=db,
        ))
        rows = db.query(EmbeddingChunk).filter_by(source_id=chapter.id).all()
        assert first_count > 0
        assert rows
        assert all("旧内容" not in row.text_snippet for row in rows)
        assert all("新内容" in row.text_snippet for row in rows)
    finally:
        db.rollback()
        db.close()


def test_semantic_chapter_search_deduplicates_multi_chunk_chapter(api_client):
    project_id = _create_project()
    content = "家族徽记在深渊回廊。" * 180
    created = api_client.post(
        f"/projects/{project_id}/chapters",
        json={"chapter_no": 1, "title": "徽记", "content": content},
    )
    assert created.status_code == 200

    response = api_client.get(
        f"/projects/{project_id}/chapters/search",
        params={"query": "家族徽记", "top_k": 10},
    )
    assert response.status_code == 200, response.text
    results = response.json()
    assert len(results) == 1
    assert results[0]["chapter_id"] == created.json()["chapter_id"]


def test_repetition_warnings_are_unique_per_chapter(api_client):
    project_id = _create_project()
    content = "主角夺回家族徽记，与凯恩在深渊回廊对峙。" * 160
    first = api_client.post(
        f"/projects/{project_id}/chapters",
        json={"chapter_no": 1, "title": "第一章", "content": content},
    )
    assert first.status_code == 200
    second = api_client.post(
        f"/projects/{project_id}/chapters",
        json={"chapter_no": 2, "title": "第二章", "content": content},
    )
    assert second.status_code == 200, second.text
    warnings = second.json()["repetition_warnings"]
    assert len(warnings) == 1
    assert warnings[0]["compared_chapter_id"] == first.json()["chapter_id"]
