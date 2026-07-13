"""backend/tests/invariants/conftest.py — security-2026-07-13 测试共享 fixture

/simplify-2026-07-13: 把重复的 _ensure_schema_and_migrate + _cleanup_project
从 5 个测试文件抽到 conftest.py。

/simplify-2026-07-13 (跟进): CHILD_TABLES 改为从 Base.metadata 自动派生，
避免 schema 演进时手工维护漂移。
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import pytest
from sqlalchemy import text as _sa_text

# 让 backend/ 在 sys.path（与 tests/conftest.py 同样的兜底）
_BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


def _child_tables_with_project_id() -> tuple[str, ...]:
    """从 Base.metadata 自动派生所有含 project_id FK 指向 projects.id 的表名。

    schema 演进时新加 Project 子表无需更新本 fixture——SQLAlchemy 反射自动跟进。
    """
    from app.database import Base
    out = []
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if col.name == "project_id" and col.foreign_keys:
                for fk in col.foreign_keys:
                    if fk.target_fullname == "projects.id":
                        out.append(table.name)
                        break
    return tuple(out)


def _delete_project(project_id: str) -> None:
    """删除指定 project 的所有子表行 + project 行。失败不抛。"""
    try:
        from app.database import engine
        with engine.begin() as conn:
            for tbl in _child_tables_with_project_id():
                conn.execute(
                    _sa_text(f"DELETE FROM {tbl} WHERE project_id = :pid"),
                    {"pid": project_id},
                )
            conn.execute(
                _sa_text("DELETE FROM projects WHERE id = :pid"),
                {"pid": project_id},
            )
    except Exception:
        pass


@pytest.fixture
def db_bootstrap():
    """为需要 DB schema 的测试建表 + 跑迁移（不 autouse；调用方按需声明）。"""
    from app.database import Base, engine
    from app.migrations import run_migrations
    Base.metadata.create_all(engine)
    run_migrations()
    yield


@pytest.fixture
def tracked_project_id():
    """生成唯一 project_id；测试结束后自动清理。"""
    pid = f"test-{secrets.token_hex(8)}"
    yield pid
    _delete_project(pid)


@pytest.fixture
def tracked_project_ids():
    """生成多个唯一 project_id；测试结束后批量清理。"""
    pids = [f"test-{secrets.token_hex(8)}" for _ in range(2)]
    yield pids
    for pid in pids:
        _delete_project(pid)


@pytest.fixture
def db_session():
    """提供 SessionLocal + 自动关闭；测试 boilerplate 进一步简化。

    用法：
        def test_x(db_session, tracked_project_id):
            db_session.add(Project(id=tracked_project_id, ...))
            db_session.commit()
    """
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()