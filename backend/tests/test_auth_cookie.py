"""backend/tests/test_auth_cookie.py — Phase B2 后端 Set-Cookie 回归

防止 Phase E 切到生产时 cookie 属性漏配（XSS/CSRF 风险敞口）。

验证：
- /auth/login 成功后 Set-Cookie 头含 HttpOnly + SameSite=Strict
- Secure 属性在 NOVEL_PRODUCTION=1 时为 True，否则为 False
- cookie 名为 novel_ai_token，path=/，max-age=7 天
- cookie value 与 body.access_token 一致（保证 cookie 可被后续 fetch 复用）
- /auth/register 同样下发 cookie
- body.access_token 仍保留（向后兼容，前端暂不切）
- 现有 token-based 流程不被破坏（test_auth_isolation.py 全部通过）
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
from app.middleware.rate_limit import reset_login_limiter_for_testing  # noqa: E402


Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _clean_state():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    reset_jwt_secret_cache()
    reset_login_limiter_for_testing()
    # 关键：每个测试 NOVEL_PRODUCTION 默认关（dev 模式）
    os.environ.pop("NOVEL_PRODUCTION", None)
    yield
    os.environ.pop("NOVEL_PRODUCTION", None)
    reset_login_limiter_for_testing()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ───────── Tests ─────────

def test_login_sets_httponly_cookie(client):
    """登录成功 → Set-Cookie 头含 HttpOnly + SameSite=Strict。"""
    client.post("/auth/register", json={
        "email": "alice@example.com", "password": "longenough123",
    })

    r = client.post("/auth/login", json={
        "email": "alice@example.com", "password": "longenough123",
    })
    assert r.status_code == 200
    cookies = r.cookies
    assert "novel_ai_token" in cookies, (
        f"登录响应应含 novel_ai_token cookie，实际 cookies={list(cookies.keys())}"
    )

    # 解析 Set-Cookie 头的属性（httpx TestClient 暴露 raw headers）。
    # 重要：cookie 属性按 RFC 6265 是大小写不敏感；Python's set_cookie 把
    # 属性名转小写（httponly / samesite / path），断言都用 lowercase 比较。
    set_cookie_header = r.headers.get("set-cookie", "").lower()
    assert "novel_ai_token=" in set_cookie_header
    assert "httponly" in set_cookie_header
    assert "samesite=strict" in set_cookie_header
    assert "path=/" in set_cookie_header


def test_login_cookie_value_matches_body_token(client):
    """cookie 值必须等于 body.access_token（保证后续 fetch 自动带 cookie 时能用同一 token）。"""
    client.post("/auth/register", json={
        "email": "bob@example.com", "password": "longenough123",
    })
    r = client.post("/auth/login", json={
        "email": "bob@example.com", "password": "longenough123",
    })
    assert r.status_code == 200
    body_token = r.json()["access_token"]
    cookie_token = r.cookies.get("novel_ai_token")
    assert cookie_token == body_token, (
        "cookie value 必须等于 body.access_token，否则前端切到 cookie-only 时"
        " 会拿不到一致的 token"
    )


def test_login_cookie_secure_flag_off_in_dev_mode(client):
    """dev 模式（NOVEL_PRODUCTION 未设）→ Secure=False（http://localhost 不发）。"""
    client.post("/auth/register", json={
        "email": "dev@example.com", "password": "longenough123",
    })
    r = client.post("/auth/login", json={
        "email": "dev@example.com", "password": "longenough123",
    })
    assert r.status_code == 200
    set_cookie_header = r.headers.get("set-cookie", "").lower()
    # Secure 不应出现 ——dev 模式下 Secure=True 会导致浏览器拒绝在
    # http://localhost 下发送 cookie
    assert "secure" not in set_cookie_header, (
        f"dev 模式 Secure 应 False，实际 Set-Cookie={set_cookie_header}"
    )


def test_login_cookie_secure_flag_on_in_production_mode(client):
    """生产模式（NOVEL_PRODUCTION=1）→ Secure=True。"""
    os.environ["NOVEL_PRODUCTION"] = "1"
    client.post("/auth/register", json={
        "email": "prod@example.com", "password": "longenough123",
    })
    r = client.post("/auth/login", json={
        "email": "prod@example.com", "password": "longenough123",
    })
    assert r.status_code == 200
    set_cookie_header = r.headers.get("set-cookie", "").lower()
    assert "secure" in set_cookie_header, (
        f"生产模式 Secure 必须 True（强制 HTTPS），实际 Set-Cookie={set_cookie_header}"
    )
    assert "httponly" in set_cookie_header
    assert "samesite=strict" in set_cookie_header


def test_register_sets_httponly_cookie(client):
    """/auth/register 也下发 cookie（首次注册用户也能拿到 HttpOnly token）。"""
    r = client.post("/auth/register", json={
        "email": "newuser@example.com", "password": "longenough123",
    })
    assert r.status_code == 201
    assert "novel_ai_token" in r.cookies
    set_cookie_header = r.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie_header
    assert "samesite=strict" in set_cookie_header


def test_login_failure_does_not_set_cookie(client):
    """登录失败时不应下发 cookie（避免给攻击者任何回执）。"""
    client.post("/auth/register", json={
        "email": "carol@example.com", "password": "longenough123",
    })
    r = client.post("/auth/login", json={
        "email": "carol@example.com", "password": "wrongwrong",
    })
    assert r.status_code == 401
    assert "novel_ai_token" not in r.cookies, (
        "登录失败不应下发 cookie，避免给攻击者任何回执"
    )


def test_body_token_still_present_for_backward_compat(client):
    """body.access_token 仍保留（前端暂不切，前端 login dialog 用 body 字段）。"""
    client.post("/auth/register", json={
        "email": "backward@example.com", "password": "longenough123",
    })
    r = client.post("/auth/login", json={
        "email": "backward@example.com", "password": "longenough123",
    })
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body, (
        "body.access_token 必须保留（向后兼容，前端 login dialog 还在用）"
    )
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 7 * 86400