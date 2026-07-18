"""backend/tests/conftest.py — pytest 共享配置

Phase D 修复：让 pytest 从任何 cwd 都能正确收集 backend/tests/ 下的测试。

核心问题：tests/invariants/test_X.py 子包用 `from tests.X import ...`
相对导入，需要 backend/ 在 sys.path。但老 conftest 不存在，pytest
自动发现无法保证 backend/ 在 sys.path 里（取决于 invocation cwd）。

修法：在 backend/tests/ 下放 conftest.py，pytest 收集时自动执行：
  1. 把 backend/ 插入 sys.path（解决 tests.X 相对导入）
  2. 暴露 REPO_ROOT / BACKEND_ROOT 给 fixture 路径测试使用
  3. 提供共享 fixture（任务 08 batch 3-5）：
     - `api_client` — FastAPI TestClient + 临时 DB 隔离（替代 20+ 处重复样板）
     - `api_client_factory` — 工厂形式用于生产模式特殊场景
"""
from __future__ import annotations

import sys
from pathlib import Path

# 把 backend/ 插入 sys.path（让 tests.invariants 等子包可被 import）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# 共享 fixture（任务 08 batch 3）
import pytest  # noqa: E402


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


@pytest.fixture
def api_client_factory(isolated_test_db):
    """TestClient 工厂 fixture，用于需要自定义 base_url 或 headers 的场景。

    Returns: 一个 callable，接受可选的 headers dict，返回 TestClient 实例。
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)

    def _make(headers=None):
        if headers:
            return TestClient(app, headers=headers)
        return TestClient(app)

    yield _make