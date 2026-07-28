from __future__ import annotations

import uuid

from tests._test_db import isolated_test_db  # noqa: F401


def _project(api_client):
    from app.database import SessionLocal
    from app.models import Project

    project_id = f"test-context-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        db.add(Project(id=project_id, title="上下文测试", genre="玄幻", config_json={}))
        db.commit()
    finally:
        db.close()
    return project_id


def test_rag_status_reports_coverage_and_dimensions(api_client):
    project_id = _project(api_client)
    for no, content in ((1, "家族徽记被夺走。" * 70), (2, "雨夜集市购买魔石。" * 70)):
        response = api_client.post(
            f"/projects/{project_id}/chapters",
            json={"chapter_no": no, "title": f"第{no}章", "content": content},
        )
        assert response.status_code == 200

    response = api_client.get(f"/projects/{project_id}/context/rag/status")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["chapter_count"] == 2
    assert body["indexed_chapter_count"] == 2
    assert body["coverage_percent"] == 100.0
    assert body["chunk_count"] >= 2
    assert body["unindexed_chapter_nos"] == []
    assert body["dimensions"] == [{"dimension": 256, "count": body["chunk_count"]}]
    assert body["mixed_dimensions"] is False


def test_rag_status_exposes_missing_and_orphaned_indexes(api_client):
    from app.database import SessionLocal
    from app.models import Chapter, EmbeddingChunk

    project_id = _project(api_client)
    db = SessionLocal()
    try:
        chapter = Chapter(project_id=project_id, chapter_no=7, title="未索引", content="正文")
        db.add(chapter)
        db.add(EmbeddingChunk(
            project_id=project_id, source_type="chapter", source_id="missing",
            text_snippet="", embedding_json=[1.0, 0.0], model="legacy",
        ))
        db.commit()
    finally:
        db.close()

    body = api_client.get(f"/projects/{project_id}/context/rag/status").json()
    assert body["coverage_percent"] == 0.0
    assert body["unindexed_chapter_nos"] == [7]
    assert body["orphaned_chunk_count"] == 1
    assert body["empty_chunk_count"] == 1


def test_rag_preview_reports_budget_and_sources(api_client):
    project_id = _project(api_client)
    api_client.post(
        f"/projects/{project_id}/chapters",
        json={"chapter_no": 1, "title": "徽记", "content": "夺回家族徽记。" * 90},
    )
    response = api_client.post(
        f"/projects/{project_id}/context/rag/preview",
        json={"query": "家族徽记", "top_k": 3, "budget_chars": 300},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["used_chars"] <= 300
    assert body["budget_chars"] == 300
    assert body["budget_percent"] <= 100.0
    assert body["chunks"]
    assert body["chunks"][0]["chapter_no"] == 1
    assert body["degraded"] is False


def test_rag_preview_degrades_explicitly_without_index(api_client):
    project_id = _project(api_client)
    response = api_client.post(
        f"/projects/{project_id}/context/rag/preview",
        json={"query": "不存在", "top_k": 3, "budget_chars": 300},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["chunks"] == []
    assert body["degraded"] is True
    assert body["message"]
