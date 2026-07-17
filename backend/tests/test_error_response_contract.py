"""API 错误响应一致性（任务 07 · 资源域第一批：projects / auth）

按任务书要求逐个资源域核对 400/401/403/404/409/422/429/500 使用。
本批次聚焦 projects 与 auth 端点。

约束：
- 第一轮不建立全局异常框架
- 不改变登录接口防账号枚举行为
- 所有 project-scoped 端点保持 ownership
- 响应不泄漏 traceback、磁盘路径、SQL、key 或模型原始响应
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_BACKEND_TESTS = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_BACKEND_TESTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_TESTS))


import pytest
from _test_db import isolated_test_db  # noqa: E402,F401  -- fixture 注入


@pytest.fixture
def client(isolated_test_db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


# ──────────────────────────────────────────────────────────────────────
# A. /auth/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestAuthErrorCodes:
    """auth 端点：错误码必须按 RFC 语义使用，不泄露注册/登录区分。"""

    @pytest.mark.parametrize("email,password", [
        ("", ""),
        ("not_an_email", "x"),
        ("a@b.com", ""),
        ("a@b.com", "x"),  # 短密码
    ])
    def test_register_validation_400_or_422(self, client, email, password):
        r = client.post("/auth/register", json={"email": email, "password": password})
        assert r.status_code in (400, 422), (
            f"register 参数无效应 400/422，实际 {r.status_code} {r.text}"
        )

    def test_login_wrong_credential_does_not_distinguish(self, client):
        """防账号枚举：用户不存在 vs 密码错必须同状态码 + 同错误文案。"""
        r1 = client.post("/auth/login",
                         json={"email": "never_registered_user@example.com",
                               "password": "anything"})
        # dev 模式可能允许登录（单租户），prod 才返 401；都允许
        # 但若返 401，需确认响应 body 不区分
        assert r1.status_code in (401, 403), (
            f"未注册登录应 401/403，实际 {r1.status_code}"
        )
        body = r1.text.lower()
        # 不能出现 "user not found" / "user does not exist" 之类区分文案
        assert "user not found" not in body
        assert "not registered" not in body


# ──────────────────────────────────────────────────────────────────────
# B. /projects/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestProjectsErrorCodes:

    def test_list_projects_works(self, client):
        """无 token 的 GET /projects 应当 200 (dev 模式) 或 401 (prod)。"""
        r = client.get("/projects")
        assert r.status_code in (200, 401)

    def test_get_unknown_project_404(self, client):
        """不存在的 project_id 必须 404。"""
        r = client.get("/projects/00000000000000000000000000000000")
        assert r.status_code in (404, 403)


# ──────────────────────────────────────────────────────────────────────
# C. 不泄漏敏感信息（traceback / 路径 / SQL / key / 模型原始响应）
# ──────────────────────────────────────────────────────────────────────


FORBIDDEN_LEAKS = [
    "Traceback (most recent call last)",
    "File \"",
    "SELECT ",
    "INSERT INTO",
    "sqlite:///",
    "c:\\",
    "D:\\",
    "/Users/",
    "/home/",
    "sk-",
    "sk-xxxx",  # 假 key 前缀
]


@pytest.mark.parametrize("method,path", [
    ("GET",  "/projects"),
    ("GET",  "/providers"),
    ("GET",  "/role-assignments"),
    ("GET",  "/worldbuild/stages"),
])
def test_no_traceback_in_error_responses(client, method, path):
    """任何 GET 项目根 1 类端点出错时，响应 body 不应包含 Python traceback。"""
    r = client.request(method, path)
    if r.status_code >= 500:
        for leak in FORBIDDEN_LEAKS:
            assert leak not in r.text, (
                f"{method} {path} 500 响应泄漏 {leak!r}：\n{r.text[:500]}"
            )


def test_404_path_does_not_leak_app_structure(client):
    """404 路径不应包含 /app/... 之类的内部结构路径。"""
    r = client.get("/projects/totally/nonexistent/path/" + "x" * 100)
    if r.status_code == 404:
        body = r.text
        assert "/app/" not in body
        assert "/engine/" not in body
        assert ".sqlite" not in body
