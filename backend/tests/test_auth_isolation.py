"""backend/tests/test_auth_isolation.py — Phase 4 多用户隔离回归测试

核心验收：
  - 用户 A 注册并创建一个 Project → 用户 B 无法通过任何 API 读取/修改那个 Project
  - Project listing：已登录 user 仅看到自己的 Project（+ 共享 NULL 历史数据）
  - list_projects dev 模式未登录可见全部；生产模式 401

不需要起 uvicorn（TestClient）。bcrypt + JWT 用同一份 auth.py 实现。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest
import uuid as _uuid

_tmp_db_path = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False).name + f".{_uuid.uuid4().hex[:6]}.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db_path}"
os.environ["JWT_SECRET"] = "test-secret-for-pytest-only-this-is-a-long-enough-key-1234567890"

# Reinit settings since pydantic-settings caches at module import time.
from app.config import Settings as _Settings
import app.config as _cfg
_cfg.settings = _Settings()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.auth import reset_jwt_secret_cache  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Project, GenerationJob, WorldSetting, NovelAIBinding  # noqa: E402


Base.metadata.create_all(bind=engine)


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
    r = client.post("/auth/register", json={
        "email": email, "password": password,
    })
    assert r.status_code == 201, r.text
    data = r.json()
    return data["access_token"], data["user"]["id"]


def _seed_worldbuild_done(db, pid):
    """让 bridge.* 路由的 _worldbuild_done 检查通过。"""
    p = Project(id=pid, title=pid, genre="玄幻", config_json={}, status="ready",
                owner_id=None)  # dev 模式接受 NULL
    db.merge(p)
    db.merge(WorldSetting(project_id=pid))
    db.merge(GenerationJob(
        project_id=pid, job_type="worldbuild", status="done",
        progress_percent=100,
    ))
    binding_dir = str(Path(__file__).resolve().parent.parent / "novel_AI")
    db.merge(NovelAIBinding(
        project_id=pid, novel_ai_dir=binding_dir, novel_id=pid,
    ))
    db.commit()


# ───────── Tests ─────────

def test_create_project_stamps_owner_id(client):
    """登录 user 创建 project → owner_id 被设为 user.id。"""
    token, uid = _register(client, "owner1@example.com")
    r = client.post("/projects", json={
        "title": "my novel",
        "genre": "玄幻",
        "config_json": {},
    }, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201
    pid = r.json()["id"]
    db = SessionLocal()
    try:
        p = db.get(Project, pid)
        assert p.owner_id == uid, f"owner_id 应被 stamp 为 user.id，实际 {p.owner_id!r}"
    finally:
        db.close()


def test_cross_user_cannot_read_project(client):
    """用户 B 拿自己的 token 访问用户 A 的 project → 403。"""
    token_a, _ = _register(client, "alice@example.com")
    token_b, _ = _register(client, "bob@example.com")

    r_create = client.post("/projects", json={
        "title": "alice's novel", "genre": "玄幻", "config_json": {},
    }, headers={"Authorization": f"Bearer {token_a}"})
    pid_alice = r_create.json()["id"]

    # B 拿自己的 token 试图读 A 的 project
    r = client.get(f"/projects/{pid_alice}",
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, r.text


def test_cross_user_cannot_modify_project(client):
    """用户 B 试图修改 A 的 project platform → 403。"""
    token_a, _ = _register(client, "alice2@example.com")
    token_b, _ = _register(client, "bob2@example.com")

    r_create = client.post("/projects", json={
        "title": "novel2", "genre": "玄幻", "config_json": {},
    }, headers={"Authorization": f"Bearer {token_a}"})
    pid = r_create.json()["id"]

    r = client.put(f"/projects/{pid}/platform",
                   json={"platform": "personal"},
                   headers={"Authorization": f"Bearer {token_b}"})
    assert r.status_code == 403, r.text


def test_owner_can_read_own_project(client):
    """用户 A 可以读自己创建的 project。"""
    token_a, _ = _register(client, "carol@example.com")
    r_create = client.post("/projects", json={
        "title": "carol's novel", "genre": "玄幻", "config_json": {},
    }, headers={"Authorization": f"Bearer {token_a}"})
    pid = r_create.json()["id"]

    r = client.get(f"/projects/{pid}",
                   headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200, r.text


def test_list_projects_scoped_to_owner(client):
    """list_projects 已登录 user 仅看自己的 + 共享 NULL。"""
    token_a, uid_a = _register(client, "dora@example.com")
    token_b, uid_b = _register(client, "ed@example.com")

    # A 创建 2 个 project
    client.post("/projects", json={
        "title": "dora-1", "genre": "玄幻", "config_json": {},
    }, headers={"Authorization": f"Bearer {token_a}"})
    client.post("/projects", json={
        "title": "dora-2", "genre": "都市", "config_json": {},
    }, headers={"Authorization": f"Bearer {token_a}"})

    # B 创建 1 个
    client.post("/projects", json={
        "title": "ed-1", "genre": "科幻", "config_json": {},
    }, headers={"Authorization": f"Bearer {token_b}"})

    # 不创建任何 owner_id=NULL 数据（第二个 user register 时不再 backfill）

    # A 列表
    r_a = client.get("/projects",
                     headers={"Authorization": f"Bearer {token_a}"})
    assert r_a.status_code == 200
    titles_a = {p["title"] for p in r_a.json()}
    assert titles_a == {"dora-1", "dora-2"}, f"A 列表应是自己的 2 个，实际 {titles_a}"

    # B 列表
    r_b = client.get("/projects",
                     headers={"Authorization": f"Bearer {token_b}"})
    assert r_b.status_code == 200
    titles_b = {p["title"] for p in r_b.json()}
    assert titles_b == {"ed-1"}, f"B 列表仅自己的 1 个，实际 {titles_b}"


def test_legacy_unowned_data_visible_to_any_logged_in_user(client):
    """历史数据（owner_id=NULL）在 dev 模式下对所有已登录 user 可见。

    设计：兼容垫片。dev 模式保持"NULL = 共享"语义，让旧 frontend 可以继续工作。
    真实隔离（生产模式）由 NOVEL_PRODUCTION=1 + 首次 register backfill 解决。

    实现细节：legacy-1 owner_id=NULL → 首次 register 的 user 自动 backfill 拿走，
    所以要看到 NULL 数据"仍可见"必须在两个 user register 之前+之间插入 legacy 行。
    这里直接用 raw SQL 把 owner_id 设回 NULL 来测试"NULL 确实共享"语义。
    """
    db = SessionLocal()
    try:
        db.add(Project(id="legacy-1", title="legacy-1", genre="玄幻",
                       config_json={}, status="draft", owner_id=None))
        db.commit()
    finally:
        db.close()

    token_a, uid_a = _register(client, "fiona@example.com")
    # register 把 legacy-1 backfill 给 fiona（首个 user）——这是 Phase 4 设计
    # 我们反向校验：把 owner_id 强制设回 NULL，模拟"未认领老项目"
    db = SessionLocal()
    try:
        p = db.get(Project, "legacy-1")
        p.owner_id = None
        db.commit()
    finally:
        db.close()

    # 注册第二个 user，不应拿走 legacy-1（因为已经"被回放"成 NULL）
    token_b, _ = _register(client, "g@example.com")

    # 两个 user 都能看到 legacy-1（owner_id=NULL 视为共享）
    r_a = client.get("/projects", headers={"Authorization": f"Bearer {token_a}"})
    r_b = client.get("/projects", headers={"Authorization": f"Bearer {token_b}"})
    assert any(p["id"] == "legacy-1" for p in r_a.json())
    assert any(p["id"] == "legacy-1" for p in r_b.json())


def test_unauthenticated_dev_mode_still_works(client):
    """dev 模式下，无 token 能创建并看到全部 projects。

    这是 Phase 4 的核心权衡：上线后设 NOVEL_PRODUCTION=1 才强制鉴权；
    dev 默认不强制。
    """
    r = client.post("/projects", json={
        "title": "unauth novel", "genre": "玄幻", "config_json": {},
    })
    assert r.status_code == 201, r.text  # dev 模式允许

    # 模拟本地用户登录创建项目后又退出登录。dev 模式仍是单租户兼容，
    # 因此未登录列表不能把已有 owner 的项目隐藏掉。
    db = SessionLocal()
    try:
        db.add(Project(
            title="owned but visible in dev",
            genre="都市",
            config_json={},
            owner_id="local-owner",
        ))
        db.commit()
    finally:
        db.close()

    r_list = client.get("/projects")
    assert r_list.status_code == 200
    assert {p["title"] for p in r_list.json()} == {
        "unauth novel",
        "owned but visible in dev",
    }


def test_token_with_garbage_signature_returns_none_user(client):
    """伪造签名 token → 等同未登录（dev 模式仍可访问）。"""
    # 拿到一个有效的 token，然后把最后 4 字符改掉（破坏签名）
    token, _ = _register(client, "h@example.com")
    bad = token[:-4] + "ZZZZ"
    r = client.get("/projects", headers={"Authorization": f"Bearer {bad}"})
    # 签名错 → decode_token 返回 None → dev 模式列表全部 → 200
    assert r.status_code == 200, r.text
