"""backend/tests/test_auth_login_rate_limit.py — Phase B1 登录限流回归测试

防止同一 (IP, email) 组合被暴力破解：
- 同一账号连续登录失败 6 次：第 6 次返回 429 而不是 401
- 登录成功后失败计数清零（让真实用户偶尔输错后立即能用）
- 不同 email 的登录互不干扰（共享 IP 也独立计数）
- 不同 IP 的同一 email 互不干扰
- 限流仅作用于登录失败：注册、me、change-password 不受影响
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

# 跟 test_auth.py 同样的 DB 隔离策略
_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_db.close()
_tmp_db_path = _tmp_db.name + f".{_uuid.uuid4().hex[:6]}.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db_path}"
os.environ["JWT_SECRET"] = "test-secret-for-pytest-only-this-is-a-long-enough-key-1234567890"

from app.config import Settings as _Settings
import app.config as _cfg
_cfg.settings = _Settings()

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.auth import reset_jwt_secret_cache  # noqa: E402
from app.database import Base, engine  # noqa: E402
from app.middleware.rate_limit import (  # noqa: E402
    get_login_limiter, reset_login_limiter_for_testing,
)


Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_state():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_jwt_secret_cache()
    reset_login_limiter_for_testing()
    yield
    reset_login_limiter_for_testing()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ───────── Tests ─────────

def test_login_6th_failure_returns_429(client):
    """同一 (IP, email) 连续 5 次失败后，第 6 次返 429 而不是 401。"""
    # 先注册一个真实账号（密码已知）
    r = client.post("/auth/register", json={
        "email": "victim@example.com", "password": "longenough123",
    })
    assert r.status_code == 201

    # 5 次失败（IP=127.0.0.1 在 TestClient 默认 client）
    for _ in range(5):
        r = client.post("/auth/login", json={
            "email": "victim@example.com", "password": "wrongpassword",
        })
        assert r.status_code == 401, f"前 5 次应返 401，实际 {r.status_code}"

    # 第 6 次返 429
    r = client.post("/auth/login", json={
        "email": "victim@example.com", "password": "wrongpassword",
    })
    assert r.status_code == 429, f"第 6 次应返 429，实际 {r.status_code}"
    assert "Retry-After" in r.headers


def test_login_success_resets_failure_counter(client):
    """登录成功后失败计数清零 → 偶尔输错不会被永久锁。"""
    # 注册
    client.post("/auth/register", json={
        "email": "user@example.com", "password": "longenough123",
    })

    # 输错 2 次（未达阈值 5）
    for _ in range(2):
        r = client.post("/auth/login", json={
            "email": "user@example.com", "password": "wrong1",
        })
        assert r.status_code == 401

    # 用正确密码登录成功 → 计数器清零
    r_ok = client.post("/auth/login", json={
        "email": "user@example.com", "password": "longenough123",
    })
    assert r_ok.status_code == 200

    # 现在再输错 5 次仍应只返 401（不会立即 429）
    for _ in range(5):
        r = client.post("/auth/login", json={
            "email": "user@example.com", "password": "wrong2",
        })
        assert r.status_code == 401, (
            f"登录成功后计数器应清零，第 {_+1} 次错仍应 401，实际 {r.status_code}"
        )


def test_login_rate_limit_per_email_isolated(client):
    """同一 IP 攻击不同 email：每个 email 的失败计数独立。"""
    # 注册两个 email
    client.post("/auth/register", json={
        "email": "a@example.com", "password": "longenough123",
    })
    client.post("/auth/register", json={
        "email": "b@example.com", "password": "longenough123",
    })

    # a@ 被刷 5 次失败
    for _ in range(5):
        r = client.post("/auth/login", json={
            "email": "a@example.com", "password": "wrong",
        })
        assert r.status_code == 401

    # b@ 此时还能正常失败/成功，不受 a@ 的限流影响
    r = client.post("/auth/login", json={
        "email": "b@example.com", "password": "longenough123",
    })
    assert r.status_code == 200, (
        f"不同 email 的限流应独立，b@ 不应被 a@ 的失败计数影响，实际 {r.status_code}"
    )


def test_login_failure_count_shared_across_ips_for_same_email():
    """key 用 (ip, email) 组合，所以同 email 不同 IP 应该是独立的 bucket。
    这一点不能跨进程测（TestClient 单一 client IP），但可以直测 limiter。
    """
    limiter = get_login_limiter()
    limiter.reset()

    # 同一 email 不同 IP 各 5 次失败
    for i in range(5):
        assert limiter.is_allowed(f"10.0.0.{i}", "target@example.com") is True
        limiter.record_failure(f"10.0.0.{i}", "target@example.com")
    # 同 email 第 6 个 IP 仍允许（因为 key 不同）
    assert limiter.is_allowed("10.0.0.99", "target@example.com") is True

    # 同一 IP 第 6 次被拒
    for _ in range(4):
        limiter.record_failure("10.0.0.0", "target@example.com")
    assert limiter.is_allowed("10.0.0.0", "target@example.com") is False


def test_register_not_rate_limited(client):
    """注册端点不应被 login 限流影响（注册本身是另一类问题）。"""
    # 同一 IP 注册多个 email：不应被 login limiter 影响
    for i in range(10):
        r = client.post("/auth/register", json={
            "email": f"new{i}@example.com", "password": "longenough123",
        })
        assert r.status_code == 201, (
            f"第 {i+1} 次注册应 201，实际 {r.status_code}"
        )


def test_me_not_affected_by_login_rate_limit(client):
    """me 端点（认证后查询）不被 login 限流。"""
    # 注册 + 故意让 login 失败 5 次
    r = client.post("/auth/register", json={
        "email": "me@example.com", "password": "longenough123",
    })
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    for _ in range(5):
        client.post("/auth/login", json={
            "email": "me@example.com", "password": "wrong",
        })

    # me 仍能访问（login 限流只针对 login 端点）
    r = client.get("/auth/me", headers=h)
    assert r.status_code == 200