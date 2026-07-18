"""backend/tests/conftest.py — pytest 共享配置

Phase D 修复：让 pytest 从任何 cwd 都能正确收集 backend/tests/ 下的测试。

核心问题：tests/invariants/test_X.py 子包用 `from tests.X import ...`
相对导入，需要 backend/ 在 sys.path。但老 conftest 不存在，pytest
自动发现无法保证 backend/ 在 sys.path 里（取决于 invocation cwd）。

修法：在 backend/tests/ 下放 conftest.py，pytest 收集时自动执行：
  1. 把 backend/ 插入 sys.path（解决 tests.X 相对导入）
  2. 暴露 REPO_ROOT / BACKEND_ROOT 给 fixture 路径测试使用
  3. 提供 `api_client`，让 API 合同测试使用隔离数据库。
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

# Establish a process-wide safety net before any app module is imported during
# collection. Tests may override DATABASE_URL locally, but the fallback must
# never be the user's working database.
_SESSION_DB = Path(tempfile.gettempdir()) / f"novel_ai_pytest_{uuid.uuid4().hex}.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_SESSION_DB.as_posix()}"
os.environ["NOVEL_AI_SKIP_BACKUP"] = "1"

# 把 backend/ 插入 sys.path（让 tests.invariants 等子包可被 import）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# 共享 fixture（任务 08 batch 3-4）
import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _session_database_schema():
    """Create the schema on the process-local database and align subprocesses."""
    from app import models  # noqa: F401
    from app.database import Base, engine

    os.environ["DATABASE_URL"] = str(engine.url)
    Base.metadata.create_all(bind=engine)
    yield


def pytest_sessionfinish(session, exitstatus):
    database = sys.modules.get("app.database")
    if database is not None:
        database.engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(_SESSION_DB) + suffix).unlink()
        except OSError:
            pass


@pytest.fixture
def api_client(isolated_test_db):
    """FastAPI TestClient + 真隔离临时 DB（任务 08 batch 3）。

    替代 ~20 处重复的：
        def client(isolated_test_db):
            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import Base, engine
            Base.metadata.create_all(bind=engine)
            with TestClient(app) as c:
                yield c

    用法：
        def test_foo(api_client):
            r = api_client.get("/auth/...")
            assert r.status_code == 200

    依赖 `isolated_test_db` → 测试用临时 SQLite，**不污染真实 backend/data**。
    yield 后自动 teardown：TestClient 关闭、engine dispose、temp 文件删除。
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
