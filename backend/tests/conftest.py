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


# 共享 fixture（任务 08 batch 3-4）
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


# ─── 任务 08 batch 4：用户 + 项目 fixture ────────────────────────────


@pytest.fixture
def make_user(api_client):
    """工厂 fixture：注册并返回 (token, user_id) 的 callable。

    替代 test_auth_isolation / test_auth_structural_coverage / test_auth_cookie
    等文件里重复定义的 `_register(client, email, password="longenough123")`。

    用法：
        def test_cross_user(make_user, api_client):
            token_a, uid_a = make_user("alice@example.com")
            token_b, _    = make_user("bob@example.com")
            r = api_client.get(f"/users/{uid_a}/...", headers={"Authorization": f"Bearer {token_b}"})
    """
    def _make(email: str, password: str = "longenough123"):
        r = api_client.post("/auth/register", json={"email": email, "password": password})
        assert r.status_code == 201, f"register 失败 {r.status_code}: {r.text}"
        body = r.json()
        return body["access_token"], body["user"]["id"]
    return _make


@pytest.fixture
def make_project(api_client):
    """工厂 fixture：在 DB 里建一个项目，返回 project_id。

    owner_id 默认从 make_user 注册后的用户来；可显式传入 owner_id 字段。

    用法：
        def test_world(make_user, make_project, api_client):
            _, uid = make_user("alice@example.com")
            pid = make_project(owner_id=uid, title="alice novel", status="ready")
    """
    import uuid as _uuid
    from app.models import Project

    def _make(
        owner_id: str = "default-owner",
        title: str = "test novel",
        genre: str = "玄幻",
        status: str = "draft",
    ) -> str:
        # 直接走 ORM（api_client 已经在临时 DB 上）
        from app.database import SessionLocal
        db = SessionLocal()
        try:
            p = Project(
                id=str(_uuid.uuid4()),
                title=title,
                genre=genre,
                config_json={},
                owner_id=owner_id,
                status=status,
            )
            db.add(p)
            db.commit()
            db.refresh(p)
            return p.id
        finally:
            db.close()
    return _make