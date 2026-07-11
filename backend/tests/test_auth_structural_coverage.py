"""backend/tests/test_auth_structural_coverage.py — Phase 4 find-#4 修复验证

上一轮 Phase 4 commit (d91db8d) 只在 projects.py / bridge.py 挂了 owner 校验，
其他 6 个项目子资源路由（chapters / world / worldbuild / foreshadowings /
rules / ai_assist）全部漏挂——用户能跨项目读到别人章节、角色、世界观。

这一组测试做两件事：

1. **结构性测试** `test_project_scoped_routes_protected`：
   遍历 FastAPI app.routes，凡是 path 里含 {project_id} 的，
   必须依赖 owner 校验路径（即调用 require_owned_project / 我们的 _owner_check）。
   漏挂一个新路由会立刻被这个测试拦截。

2. **行为性测试** `test_cross_user_*`：
   用户 B 拿自己的合法 token 试图读用户 A 的 project 下任何资源，
   必返 403（prod）/ 200（dev 兼容 / legacy 数据共享）。

测试前确保：app 路由表完整，phase 4 修补过的全部 6 个路由都被覆盖到。
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid as _uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_db.close()
db_path = f"{_tmp_db.name}.{_uuid.uuid4().hex[:6]}.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["JWT_SECRET"] = "test-structural-coverage-this-key-is-at-least-32-chars-long-12345"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.auth import reset_jwt_secret_cache  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Project, Chapter  # noqa: E402


Base.metadata.create_all(bind=engine)


# ──────────────────────────────────────────────────────────────────────────
# 1. 结构性测试（不依赖 DB / HTTP，只读 route 表）
# ──────────────────────────────────────────────────────────────────────────

# Phase 4 豁免白名单：这些路径虽然含 {project_id}，但本身是 meta/global 端点，
# 不带项目数据 / 不该挂 owner 校验；写这里是为了让人改代码时一眼能看出豁免理由。
PROJECT_SCOPED_EXCEPTIONS = {
    "/worldbuild/stages",  # 全局静态清单，meta 路由
}


def _is_project_scoped(path: str) -> bool:
    """path 含 `{project_id}` 占位符 → project-scoped，需要 owner 校验。"""
    return "{project_id}" in path


def _depends_on_owner_check(route) -> bool:
    """判断 route 是否依赖 owner 校验。

    简单粗暴但够用：检查依赖链里出现以下任一防护符号：
      - `_owner_check`: Phase 4 标准模式（chapters/world/worldbuild/...用的）
      - `require_owned_project`: bare 调用（projects.py 部分 fallback）
      - `is_production_mode`: 本身在 _owner_check 里用，但 Projects 详情端点也裸用
      - `_current_user_or_401`: bridge.py 的等效 helper
      - `_get_project_and_binding`: bridge.py 的封装（含 owner 校验）

    用 inspect.getsource 抓函数源码（closure + 字符串包含匹配）。
    """
    import inspect

    candidate_names = {
        "_owner_check",
        "require_owned_project",
        "is_production_mode",
        "_current_user_or_401",
        "_get_project_and_binding",
    }

    def _check_callable(fn) -> bool:
        if fn is None:
            return False
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            return False
        return any(name in src for name in candidate_names)

    # FastAPI route 的 dependencies 列表 + 自身 callable 都可能挂 owner check
    candidates = [route.endpoint] if hasattr(route, "endpoint") else []
    if hasattr(route, "dependencies"):
        for d in route.dependencies or []:
            if hasattr(d, "dependency"):
                candidates.append(d.dependency)

    for fn in candidates:
        if _check_callable(fn):
            return True
    return False


def test_project_scoped_routes_protected():
    """遍历 FastAPI app.routes，凡是含 {project_id} 的路由必须挂 owner 校验。

    这条测试是 Phase 4 find-#4 修复的核心保护——以后新加 /projects/{pid}/...
    路由忘了挂 _owner_check 会被立刻拦截。
    """
    bad: list[tuple[str, str]] = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path or not _is_project_scoped(path):
            continue
        if path in PROJECT_SCOPED_EXCEPTIONS:
            continue
        if not _depends_on_owner_check(route):
            bad.append((path, getattr(route, "methods", "")))

    assert not bad, (
        f"以下 project-scoped 路由没挂 owner 校验（Phase 4 find-#4 防漏）：\n"
        f"  " + "\n  ".join(f"{p} [{m}]" for p, m in bad)
        + "\n\n修法：参考 chapters.py / world.py / worldbuild.py / foreshadowings.py / "
        + "rules.py / ai_assist.py，每个 handler 加 Depends(_owner_check)。"
    )


def test_worldbuild_meta_router_is_global():
    """/worldbuild/stages 不带 {project_id}，应当属于 meta_router 全局路由。

    这条是反向断言：豁免白名单里的"全局性"成立，防止未来误把它移成 project-scoped
    但忘了更新白名单。
    """
    found_in_meta = False
    for route in app.routes:
        path = getattr(route, "path", None)
        if path == "/worldbuild/stages":
            # 应该没有 _owner_check 依赖
            assert not _depends_on_owner_check(route), (
                "/worldbuild/stages 不该挂 owner check（全局静态清单）"
            )
            found_in_meta = True
    assert found_in_meta, "expected /worldbuild/stages route present"


# ──────────────────────────────────────────────────────────────────────────
# 2. 行为性测试（实际跑 HTTP，dev 模式 + 跨 user）
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_state():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_jwt_secret_cache()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _register(client, email, password="longenough123"):
    r = client.post("/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()["access_token"], r.json()["user"]["id"]


def _seed_project_a(db, owner_id="alice"):
    """在 DB 里建一个项目（owner_id=owner_id），返回 project_id。"""
    p = Project(
        title="alice novel",
        genre="玄幻",
        config_json={},
        owner_id=owner_id,
        status="ready",  # 让 worldbuild done / bridge run 通过
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p.id


def test_cross_user_cannot_read_chapters(client):
    """跨用户 → GET /projects/{A}/chapters 必 403。"""
    token_a, uid_a = _register(client, "alice@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob@example.com")

    r = client.get(f"/projects/{pid_a}/chapters",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"cross-user 读章节必须 403，实际 {r.status_code}: {r.text}"


def test_cross_user_cannot_read_worldview(client):
    """跨用户 → GET /projects/{A}/worldview/rich 必 403。"""
    token_a, uid_a = _register(client, "alice2@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob2@example.com")

    r = client.get(f"/projects/{pid_a}/worldview/rich",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"跨用户读世界观必 403，实际 {r.status_code}"


def test_cross_user_cannot_read_characters(client):
    """跨用户 → GET /projects/{A}/characters 必 403。"""
    token_a, uid_a = _register(client, "alice3@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob3@example.com")

    r = client.get(f"/projects/{pid_a}/characters",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"跨用户读角色列表必 403，实际 {r.status_code}"


def test_cross_user_cannot_read_relations_graph(client):
    """跨用户 → GET /projects/{A}/relations/graph 必 403。"""
    token_a, uid_a = _register(client, "alice4@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob4@example.com")

    r = client.get(f"/projects/{pid_a}/relations/graph",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"跨用户读关系图谱必 403，实际 {r.status_code}"


def test_cross_user_cannot_read_foreshadowings(client):
    """跨用户 → GET /projects/{A}/foreshadowings 必 403。"""
    token_a, uid_a = _register(client, "alice5@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob5@example.com")

    r = client.get(f"/projects/{pid_a}/foreshadowings",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"跨用户读伏笔必 403，实际 {r.status_code}"


def test_cross_user_cannot_read_rules(client):
    """跨用户 → GET /projects/{A}/rules 必 403。"""
    token_a, uid_a = _register(client, "alice6@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob6@example.com")

    r = client.get(f"/projects/{pid_a}/rules",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"跨用户读规则中心必 403，实际 {r.status_code}"


def test_cross_user_cannot_read_ai_assist_level(client):
    """跨用户 → GET /projects/{A}/ai-assist-level 必 403。"""
    token_a, uid_a = _register(client, "alice7@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob7@example.com")

    r = client.get(f"/projects/{pid_a}/ai-assist-level",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"跨用户读 ai-assist-level 必 403，实际 {r.status_code}"


def test_cross_user_cannot_read_worldbuild_result(client):
    """跨用户 → GET /projects/{A}/worldbuild/result 必 403。"""
    token_a, uid_a = _register(client, "alice8@example.com")
    db = SessionLocal()
    try:
        pid_a = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    token_b, _ = _register(client, "bob8@example.com")

    r = client.get(f"/projects/{pid_a}/worldbuild/result",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, f"跨用户读 worldbuild result 必 403，实际 {r.status_code}"


def test_owner_can_still_read_own_chapters(client):
    """owner 自己仍能读到自己的资源（不能因为修了这 bug 把正经用法也废了）。"""
    token_a, uid_a = _register(client, "carol@example.com")
    db = SessionLocal()
    try:
        pid = _seed_project_a(db, owner_id=uid_a)
    finally:
        db.close()

    r = client.get(f"/projects/{pid}/chapters",
                   headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200, r.text


def test_worldbuild_stages_remains_global(client):
    """/worldbuild/stages 任何 user 都能看（meta 路由）。"""
    r = client.get("/worldbuild/stages")
    assert r.status_code == 200, r.text
    assert "stages" in r.json()
