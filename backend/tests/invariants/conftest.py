"""backend/tests/invariants/conftest.py — 5 个 security-2026-07-13 测试共享 fixture

/simplify 2026-07-13: 把重复的 _ensure_schema_and_migrate + _cleanup_project
从 5 个测试文件抽到 conftest.py。

设计：
  - `db_bootstrap`：建表 + 跑迁移（调用方按需声明，非 autouse）
  - `tracked_project_id`：每个测试用 yield 一个新生成的 project_id，
    测试结束自动清理。测试要 Project 行的话直接用这个 fixture。
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


# Project 的所有子表（任何有 project_id FK 的表）。这是 Project 级清理
# 必须 delete 的全集——5 个测试共享同一个列表，防止 drift。
CHILD_TABLES: tuple[str, ...] = (
    "bridge_runs",
    "embedding_chunks",
    "chapter_characters",
    "entity_relations",
    "characters",
    "factions",
    "locations",
    "power_systems",
    "currencies",
    "foreshadowing",
    "world_settings",
    "story_cores",
    "settings",
    "rule_configs",
    "chapters",
)


def _delete_project(project_id: str) -> None:
    """删除指定 project 的所有子表行 + project 行。失败不抛（测试已通过即可）。"""
    try:
        from app.database import engine
        with engine.begin() as conn:
            for tbl in CHILD_TABLES:
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
    """生成唯一 project_id；测试结束后自动清理（避免跨测试数据污染）。"""
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