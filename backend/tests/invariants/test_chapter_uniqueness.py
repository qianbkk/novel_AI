"""Chapter 唯一约束不变式 (security-2026-07-13 #1)

锁定：同一 project 下 chapter_no 必须唯一——并发 POST 撞这个约束时 API
应返回 409 而不是 500；SQL 层有兜底保证数据不被静默双写。
"""
import asyncio

import pytest
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import Project, Chapter


class TestChapterUniqueConstraint:
    """security-2026-07-13 #1：DB 层兜底 (project_id, chapter_no) 唯一。"""

    def test_unique_index_created_by_migration(self, db_bootstrap):
        """migrations 应为 (project_id, chapter_no) 创建一个唯一索引。"""
        from sqlalchemy import text as _sa_text
        from app.database import engine
        with engine.connect() as conn:
            row = conn.execute(_sa_text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND name='uq_chapters_project_chapter_no'"
            )).fetchone()
            assert row is not None, "uq_chapters_project_chapter_no 索引不存在"

    def test_duplicate_insert_raises_integrity_error(
        self, db_bootstrap, tracked_project_id,
    ):
        """同 project 同 chapter_no 第二次 INSERT 必须被 DB 拒绝。"""
        project_id = tracked_project_id
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="uq-test", genre="test", status="ready",
                           config_json={}))
            db.add(Chapter(project_id=project_id, chapter_no=1, title="ch1", content="a"))
            db.commit()

            db.add(Chapter(project_id=project_id, chapter_no=1, title="dup", content="b"))
            with pytest.raises(IntegrityError) as ei:
                db.commit()
            assert "UNIQUE constraint failed" in str(ei.value) or \
                   "uq_chapters_project_chapter_no" in str(ei.value), \
                f"unexpected IntegrityError: {ei.value}"
            db.rollback()
        finally:
            db.close()

    def test_different_projects_same_chapter_no_allowed(
        self, db_bootstrap, tracked_project_ids,
    ):
        """不同 project 下 chapter_no 可以相同（约束是 (project_id, chapter_no) 联合）。"""
        p1, p2 = tracked_project_ids
        db = SessionLocal()
        try:
            for pid in (p1, p2):
                db.add(Project(id=pid, title="uq-test", genre="test", status="ready",
                               config_json={}))
                db.add(Chapter(project_id=pid, chapter_no=1, title="ch1", content="x"))
            db.commit()  # 不应该抛 IntegrityError
        finally:
            db.close()


class TestDuplicateChapterErrorAPI:
    """add_chapter() 抛 DuplicateChapterError；POST /chapters 路由转 409。"""

    def test_add_chapter_raises_duplicate_chapter_error(
        self, db_bootstrap, tracked_project_id,
    ):
        from app.rag.retrieval import add_chapter, DuplicateChapterError

        project_id = tracked_project_id
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="dup-err-test", genre="test",
                           status="ready", config_json={}))
            db.commit()
            # 第一次 add_chapter 用独立 session 避免同 session 内的 unsaved
            # Chapter 跟第二次的 INSERT 撞时 session 缓存混淆。
            asyncio.run(add_chapter(project_id, 7, "first", "content-A", db))
            db.close()
            db = SessionLocal()
            with pytest.raises(DuplicateChapterError) as ei:
                asyncio.run(add_chapter(project_id, 7, "second", "content-B", db))
            assert ei.value.chapter_no == 7
            assert ei.value.existing_chapter_id  # 非空
        finally:
            db.close()

    def test_post_chapter_returns_409_on_duplicate(
        self, db_bootstrap, tracked_project_id,
    ):
        """POST /projects/{id}/chapters 撞唯一约束 → 409，不是 500。"""
        from fastapi.testclient import TestClient
        from app.main import app

        project_id = tracked_project_id
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="409-test", genre="test",
                           status="ready", config_json={}))
            db.commit()
        finally:
            db.close()

        client = TestClient(app)
        try:
            r1 = client.post(
                f"/projects/{project_id}/chapters",
                json={"chapter_no": 3, "title": "first", "content": "hello"},
            )
            # 第一次必须成功才能测第二次撞约束
            assert r1.status_code == 200, \
                f"首次 POST 应成功，实际 {r1.status_code}: {r1.text}"
            r2 = client.post(
                f"/projects/{project_id}/chapters",
                json={"chapter_no": 3, "title": "second", "content": "world"},
            )
            assert r2.status_code == 409, \
                f"重复 POST 应 409，实际 {r2.status_code}: {r2.text}"
            body = r2.json()
            assert body["detail"]["code"] == "duplicate_chapter_no"
            assert body["detail"]["existing_chapter_id"]
        finally:
            pass  # cleanup 由 tracked_project_id fixture 自动处理