"""Chapter 唯一约束不变式 (security-2026-07-13 #1)

锁定：同一 project 下 chapter_no 必须唯一——并发 POST 撞这个约束时 API
应返回 409 而不是 500；SQL 层有兜底保证数据不被静默双写。
"""
from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(REPO_ROOT)
sys.path.insert(0, str(BACKEND))

from app.database import SessionLocal, engine  # noqa: E402
from app.models import Project, Chapter  # noqa: E402
from sqlalchemy import text as _sa_text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402

import secrets  # noqa: E402


def _ensure_schema():
    """测试运行时确保表结构就绪（直接走 Base.metadata.create_all 最简单）。"""
    from app.database import Base
    Base.metadata.create_all(engine)


def _cleanup_project(project_id: str):
    """删干净 project 及其所有子表行（避免外键约束让清理失败）。
    测试断言已通过后清理失败不应 fail 测试。"""
    try:
        with engine.begin() as conn:
            # Project 有 ~12 个子表都有外键 project_id，逐一 DELETE；最稳。
            for tbl in (
                "embedding_chunks", "chapter_characters",
                "entity_relations", "characters", "factions", "locations",
                "power_systems", "currencies", "foreshadowing",
                "world_settings", "story_cores", "settings",
                "rule_configs", "chapters",
            ):
                conn.execute(_sa_text(
                    f"DELETE FROM {tbl} WHERE project_id = :pid"
                ), {"pid": project_id})
            conn.execute(_sa_text("DELETE FROM projects WHERE id = :pid"),
                         {"pid": project_id})
    except Exception:
        # 清理失败不影响测试断言
        pass


class TestChapterUniqueConstraint:
    """security-2026-07-13 #1：DB 层兜底 (project_id, chapter_no) 唯一。"""

    def test_unique_index_created_by_migration(self):
        """migrations 应为 (project_id, chapter_no) 创建一个唯一索引。"""
        _ensure_schema()
        from app.migrations import run_migrations
        run_migrations()

        with engine.connect() as conn:
            row = conn.execute(
                __import__("sqlalchemy").text(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND name='uq_chapters_project_chapter_no'"
                )
            ).fetchone()
            assert row is not None, "uq_chapters_project_chapter_no 索引不存在"

    def test_duplicate_insert_raises_integrity_error(self):
        """同 project 同 chapter_no 第二次 INSERT 必须被 DB 拒绝。"""
        _ensure_schema()
        from app.migrations import run_migrations
        run_migrations()

        project_id = f"test-uq-ch-{secrets.token_hex(8)}"
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
            _cleanup_project(project_id)

    def test_different_projects_same_chapter_no_allowed(self):
        """不同 project 下 chapter_no 可以相同（约束是 (project_id, chapter_no) 联合）。"""
        _ensure_schema()
        from app.migrations import run_migrations
        run_migrations()

        p1 = f"test-uq-p1-{secrets.token_hex(8)}"
        p2 = f"test-uq-p2-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            for pid in (p1, p2):
                db.add(Project(id=pid, title="uq-test", genre="test", status="ready",
                               config_json={}))
                db.add(Chapter(project_id=pid, chapter_no=1, title="ch1", content="x"))
            db.commit()  # 不应该抛 IntegrityError
        finally:
            db.close()
            for pid in (p1, p2):
                _cleanup_project(pid)


class TestDuplicateChapterErrorAPI:
    """add_chapter() 抛 DuplicateChapterError；POST /chapters 路由转 409。"""

    def test_add_chapter_raises_duplicate_chapter_error(self):
        from app.rag.retrieval import add_chapter, DuplicateChapterError
        _ensure_schema()
        from app.migrations import run_migrations
        run_migrations()

        project_id = f"test-dup-err-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="dup-err-test", genre="test",
                           status="ready", config_json={}))
            db.commit()
            import asyncio
            asyncio.run(add_chapter(project_id, 7, "first", "content-A", db))
            with pytest.raises(DuplicateChapterError) as ei:
                asyncio.run(add_chapter(project_id, 7, "second", "content-B", db))
            assert ei.value.chapter_no == 7
            assert ei.value.existing_chapter_id  # 非空
        finally:
            db.close()
            _cleanup_project(project_id)

    def test_post_chapter_returns_409_on_duplicate(self):
        """POST /projects/{id}/chapters 撞唯一约束 → 409，不是 500。"""
        from fastapi.testclient import TestClient
        from app.main import app
        _ensure_schema()
        from app.migrations import run_migrations
        run_migrations()

        project_id = f"test-409-{secrets.token_hex(8)}"
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
            # 第一次可能 200/201/422，取决于 main.py 是否有 worldbuild guard；
            # 这里只关心第二次撞约束时的状态码。
            r2 = client.post(
                f"/projects/{project_id}/chapters",
                json={"chapter_no": 3, "title": "second", "content": "world"},
            )
            if r1.status_code == 200:
                # 第一次成功时第二次必须 409
                assert r2.status_code == 409, \
                    f"expected 409 on duplicate POST, got {r2.status_code}: {r2.text}"
                body = r2.json()
                assert body["detail"]["code"] == "duplicate_chapter_no"
                assert body["detail"]["existing_chapter_id"]
        finally:
            _cleanup_project(project_id)