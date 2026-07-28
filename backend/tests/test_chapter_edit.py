from __future__ import annotations

import uuid

import pytest

from tests._test_db import isolated_test_db  # noqa: F401


@pytest.fixture
def editable_chapter(api_client, tmp_path):
    from app.database import SessionLocal
    from app.models import Character, NovelAIBinding, Project

    project_id = f"test-edit-{uuid.uuid4().hex[:8]}"
    engine_dir = tmp_path / "engine"
    db = SessionLocal()
    try:
        db.add(Project(id=project_id, title="编辑测试", genre="都市", config_json={}))
        db.add(Character(id=f"char-{uuid.uuid4().hex[:8]}", project_id=project_id,
                         name="林渊", role="主角"))
        db.commit()
        db.add(NovelAIBinding(project_id=project_id, novel_ai_dir=str(engine_dir),
                              novel_id=project_id))
        db.commit()
    finally:
        db.close()

    created = api_client.post(
        f"/projects/{project_id}/chapters",
        json={"chapter_no": 1, "title": "旧标题", "content": "林渊走进云州。旧内容。"},
    )
    assert created.status_code == 200, created.text
    chapter_id = created.json()["chapter_id"]

    chapters_dir = engine_dir / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    (chapters_dir / "ch_0001.txt").write_text("磁盘旧正文", encoding="utf-8")
    return project_id, chapter_id, engine_dir


def test_get_chapter_exposes_revision_hash(api_client, editable_chapter):
    project_id, chapter_id, _ = editable_chapter
    response = api_client.get(f"/projects/{project_id}/chapters/{chapter_id}")
    assert response.status_code == 200
    revision = response.json()["revision_hash"]
    assert len(revision) == 64
    assert all(ch in "0123456789abcdef" for ch in revision)


def test_update_chapter_syncs_db_file_graph_and_chunks(api_client, editable_chapter):
    from app.database import SessionLocal
    from app.models import Chapter, ChapterCharacter, EmbeddingChunk

    project_id, chapter_id, engine_dir = editable_chapter
    current = api_client.get(f"/projects/{project_id}/chapters/{chapter_id}").json()
    response = api_client.patch(
        f"/projects/{project_id}/chapters/{chapter_id}",
        json={
            "title": "新标题",
            "content": "苏晚栀来到新地点。" * 80,
            "expected_revision_hash": current["revision_hash"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision_hash"] != current["revision_hash"]
    assert body["engine_file_synced"] is True
    assert body["indexed_chunk_count"] >= 1
    assert body["backup_path"]

    final_path = engine_dir / "output" / "chapters" / "ch_0001.txt"
    assert final_path.read_text(encoding="utf-8") == "苏晚栀来到新地点。" * 80
    backup_path = engine_dir / body["backup_path"]
    assert backup_path.read_text(encoding="utf-8") == "磁盘旧正文"

    db = SessionLocal()
    try:
        chapter = db.get(Chapter, chapter_id)
        assert chapter.title == "新标题"
        assert chapter.content == "苏晚栀来到新地点。" * 80
        assert db.query(ChapterCharacter).filter_by(chapter_id=chapter_id).count() == 0
        chunks = db.query(EmbeddingChunk).filter_by(
            project_id=project_id, source_id=chapter_id, source_type="chapter",
        ).all()
        assert len(chunks) == body["indexed_chunk_count"]
        assert all("旧内容" not in row.text_snippet for row in chunks)
    finally:
        db.close()


def test_update_chapter_rejects_stale_revision(api_client, editable_chapter):
    project_id, chapter_id, _ = editable_chapter
    response = api_client.patch(
        f"/projects/{project_id}/chapters/{chapter_id}",
        json={
            "title": "不应写入",
            "content": "并发覆盖",
            "expected_revision_hash": "0" * 64,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "chapter_revision_conflict"

    current = api_client.get(f"/projects/{project_id}/chapters/{chapter_id}").json()
    assert current["title"] == "旧标题"
    assert current["content"] == "林渊走进云州。旧内容。"


def test_update_chapter_rejects_empty_content(api_client, editable_chapter):
    project_id, chapter_id, _ = editable_chapter
    current = api_client.get(f"/projects/{project_id}/chapters/{chapter_id}").json()
    response = api_client.patch(
        f"/projects/{project_id}/chapters/{chapter_id}",
        json={
            "title": "空正文",
            "content": " ",
            "expected_revision_hash": current["revision_hash"],
        },
    )
    assert response.status_code in (400, 422)
