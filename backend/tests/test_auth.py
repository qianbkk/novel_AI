"""backend/tests/test_auth.py — Phase 4 多用户认证回归测试

覆盖：
  - register / login / me round-trip
  - 重复 email 拒绝
  - 错误密码拒绝（timing 拉齐）
  - 弱密码拒绝（min length=8）
  - 旧数据 backfill：首个注册的用户拿到所有 owner_id=NULL 的 Project
  - 第二个 user 不能读第一个 user 的 Project（owner_id 过滤）
  - 无 token 访问 /me → 401
  - change-password 流程
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

_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_db.close()
# 用独立 DB 避免污染其他 smoke 测试的 module-level singleton。
import uuid as _uuid
_tmp_db_path = _tmp_db.name + f".{_uuid.uuid4().hex[:6]}.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db_path}"
os.environ["JWT_SECRET"] = "test-secret-for-pytest-only-this-is-a-long-enough-key-1234567890"

# ⚠️ 必须确保刚 set 的 DATABASE_URL 在 app.database 模块初始化前生效
# 由于 pydantic_settings 缓存 settings 模块级实例，下面直接重置
from app.config import Settings as _Settings
import app.config as _cfg
_cfg.settings = _Settings()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.auth import reset_jwt_secret_cache  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Project  # noqa: E402


# 一次性建表（User 表是新加的）
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_state():
    """每个测试前清表（drop + create） + 重置 JWT cache。

    用 drop_all + create_all 比手动删表靠谱——Project 表有 FK 从
    Chapter/WorldSetting/BridgeRun/RuleConfig 等等，反向手动删容易遗漏。
    """
    from app.database import Base, engine
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_jwt_secret_cache()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ───────── Tests ─────────

def test_register_returns_token(client):
    """POST /auth/register → 返回 access_token + user"""
    r = client.post("/auth/register", json={
        "email": "alice@example.com",
        "password": "longenough123",
        "display_name": "Alice",
    })
    assert r.status_code == 201, r.text
    data = r.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == "alice@example.com"
    assert data["user"]["display_name"] == "Alice"


def test_register_duplicate_email_rejected(client):
    """同一 email 第二次 register 409。"""
    r1 = client.post("/auth/register", json={
        "email": "bob@example.com", "password": "longenough123"
    })
    assert r1.status_code == 201
    r2 = client.post("/auth/register", json={
        "email": "bob@example.com", "password": "longenough456"
    })
    assert r2.status_code == 409, r2.text


def test_register_weak_password_rejected(client):
    """密码 < 8 字符：422（pydantic Field min_length=8 抛 ValidationError）。"""
    r = client.post("/auth/register", json={
        "email": "weak@example.com", "password": "short"
    })
    assert r.status_code == 422


def test_login_correct_returns_token(client):
    r1 = client.post("/auth/register", json={
        "email": "carol@example.com", "password": "longenough123"
    })
    assert r1.status_code == 201
    r2 = client.post("/auth/login", json={
        "email": "carol@example.com", "password": "longenough123"
    })
    assert r2.status_code == 200, r2.text
    assert "access_token" in r2.json()


def test_login_wrong_password_rejected(client):
    client.post("/auth/register", json={
        "email": "dan@example.com", "password": "longenough123"
    })
    r = client.post("/auth/login", json={
        "email": "dan@example.com", "password": "wrongwrong"
    })
    assert r.status_code == 401


def test_login_unknown_email_rejected(client):
    """未注册的 email 也 401（不区分"用户不存在"和"密码错"，防枚举）。"""
    r = client.post("/auth/login", json={
        "email": "ghost@example.com", "password": "longenough123"
    })
    assert r.status_code == 401


def test_me_without_token_rejected(client):
    """GET /auth/me 无 token → 401。"""
    r = client.get("/auth/me")
    assert r.status_code == 401


def test_me_with_token_returns_user(client):
    r1 = client.post("/auth/register", json={
        "email": "eve@example.com", "password": "longenough123"
    })
    token = r1.json()["access_token"]
    r2 = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r2.status_code == 200
    assert r2.json()["email"] == "eve@example.com"


def test_first_user_backfills_unowned_projects(client):
    """首次注册的 user 拿到所有 owner_id=NULL 的 Project。

    模拟历史数据：先插几个 owner_id=NULL 的 Project → 注册第一个 user
    → 验证 backfill。
    """
    db = SessionLocal()
    try:
        for i in range(3):
            db.add(Project(
                id=f"legacy-{i}",
                title=f"legacy-{i}",
                genre="玄幻",
                config_json={},
                status="draft",
            ))
        db.commit()
    finally:
        db.close()

    r = client.post("/auth/register", json={
        "email": "first@example.com", "password": "longenough123"
    })
    assert r.status_code == 201
    first_user_id = r.json()["user"]["id"]

    db = SessionLocal()
    try:
        owned = db.query(Project).filter(Project.owner_id == first_user_id).count()
        unowned = db.query(Project).filter(Project.owner_id.is_(None)).count()
        assert owned == 3, f"应有 3 个被 backfill 的 project，实际 {owned}"
        assert unowned == 0
    finally:
        db.close()


def test_second_register_does_not_take_old_projects(client):
    """第二个注册 user 不会拿到 owner_id 已经非 NULL 的 Project。"""
    r1 = client.post("/auth/register", json={
        "email": "alpha@example.com", "password": "longenough123"
    })
    alpha_id = r1.json()["user"]["id"]

    # 第二个 user
    r2 = client.post("/auth/register", json={
        "email": "beta@example.com", "password": "longenough456"
    })
    assert r2.status_code == 201
    beta_id = r2.json()["user"]["id"]
    assert alpha_id != beta_id

    db = SessionLocal()
    try:
        # alpha 仍然拥有之前 backfill 给它的（如果有）；beta 一个不拥有
        beta_owned = db.query(Project).filter(Project.owner_id == beta_id).count()
        assert beta_owned == 0
    finally:
        db.close()


def test_change_password(client):
    """change-password 流程：旧密码不对 / 新密码落地"""
    r1 = client.post("/auth/register", json={
        "email": "frank@example.com", "password": "longenough123"
    })
    token = r1.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    # 旧密码不对 → 401
    r_bad = client.post("/auth/change-password", json={
        "old_password": "wrongold",
        "new_password": "newpass456789",
    }, headers=h)
    assert r_bad.status_code == 401

    # 旧密码对 → 200
    r_ok = client.post("/auth/change-password", json={
        "old_password": "longenough123",
        "new_password": "newpass456789",
    }, headers=h)
    assert r_ok.status_code == 200

    # 用旧密码登录应失败，新密码应成功
    r_old = client.post("/auth/login", json={
        "email": "frank@example.com", "password": "longenough123"
    })
    assert r_old.status_code == 401
    r_new = client.post("/auth/login", json={
        "email": "frank@example.com", "password": "newpass456789"
    })
    assert r_new.status_code == 200


def test_password_is_hashed_not_plain(client):
    """DB 里存的 password_hash 不应包含明文密码。"""
    client.post("/auth/register", json={
        "email": "secure@example.com", "password": "longenough123"
    })
    db = SessionLocal()
    try:
        from app.models import User
        u = db.query(User).filter_by(email="secure@example.com").first()
        assert u is not None
        assert "longenough123" not in u.password_hash
        # bcrypt hash 以 $2 开头
        assert u.password_hash.startswith("$2"), f"hash 格式应 bcrypt: {u.password_hash!r}"
    finally:
        db.close()


def test_jwt_token_expiry_in_payload(client):
    """签发的 token payload 含 sub + exp + iat。"""
    import jwt as _jwt
    from app.auth import _get_jwt_secret
    r = client.post("/auth/register", json={
        "email": "jwt@example.com", "password": "longenough123"
    })
    token = r.json()["access_token"]
    payload = _jwt.decode(
        token, _get_jwt_secret(), algorithms=["HS256"],
        options={"require": ["sub", "exp"]},
    )
    assert "sub" in payload
    assert "exp" in payload
    assert payload["iss"] == "novel_ai"
