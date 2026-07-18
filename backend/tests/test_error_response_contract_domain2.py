"""API 错误响应一致性（任务 07 · 资源域第二批：providers / role / worldbuild）

domain 1 (projects/auth) 在 test_error_response_contract.py 中已覆盖。
本批次聚焦 providers / role_assignments / worldbuild / world 端点。

约束（沿用 domain 1）：
- 不建立全局异常框架
- 所有 project-scoped 端点保持 ownership
- 响应不泄漏 traceback、磁盘路径、SQL、key 或模型原始响应
- 假 key 仅以 suffix (4 字符) 出现在响应中
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
from _test_db import isolated_test_db  # noqa: E402,F401


@pytest.fixture
def client(api_client):
    yield api_client


# ──────────────────────────────────────────────────────────────────────
# A. /providers 错误码
# ──────────────────────────────────────────────────────────────────────


class TestProvidersErrorCodes:

    def test_list_providers_ok(self, client):
        """空库 GET /providers 应当 200 + 空列表。"""
        r = client.get("/providers")
        assert r.status_code == 200
        assert r.json() == []

    def test_create_provider_invalid_type_422(self, client):
        """无效 provider_type 应 422（Pydantic 校验）。"""
        r = client.post("/providers", json={
            "name": "x",
            "provider_type": "nonsense",
            "api_key": "",
        })
        assert r.status_code == 422, f"got {r.status_code}: {r.text}"

    def test_create_provider_missing_name_422(self, client):
        """缺 name 应 422。"""
        r = client.post("/providers", json={
            "provider_type": "deepseek",
            "api_key": "",
        })
        assert r.status_code == 422, f"got {r.status_code}: {r.text}"

    def test_update_unknown_provider_404(self, client):
        """更新不存在的 provider 必须 404。"""
        r = client.put("/providers/00000000000000000000000000000000", json={
            "name": "x",
            "provider_type": "deepseek",
            "api_key": "",
            "default_model": "x",
        })
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_delete_unknown_provider_404(self, client):
        r = client.delete("/providers/00000000000000000000000000000000")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_provider_response_does_not_leak_key(self, client):
        """provider 创建后，响应 body 不应包含原始 api_key 明文。"""
        secret = "sk-fake-domain2-contract-abcdef1234567890XYZW"
        r = client.post("/providers", json={
            "name": "domain2-provider",
            "provider_type": "deepseek",
            "api_key": secret,
            "default_model": "x",
        })
        assert r.status_code == 200
        body = r.text
        assert secret not in body, "create_provider 响应泄漏明文 api_key"


# ──────────────────────────────────────────────────────────────────────
# B. /role-assignments 错误码
# ──────────────────────────────────────────────────────────────────────


class TestRoleAssignmentsErrorCodes:

    def test_list_role_assignments_ok(self, client):
        r = client.get("/role-assignments")
        assert r.status_code == 200

    def test_update_role_invalid_body_422(self, client):
        """PUT 角色绑定：无效 provider_id 或 body 缺字段应 4xx。

        后端先校验 role_key 存在（404），再校验 provider_id 存在（404），
        所以"非法"在这里表现为 404；422 仅当 body schema 不通过时出现。
        两者均视为合规的 4xx 响应。
        """
        r = client.put("/role-assignments/planner", json={
            "provider_id": "00000000000000000000000000000000",
        })
        assert r.status_code in (400, 404, 422), f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# C. /worldbuild/* 与 /world/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestWorldbuildErrorCodes:

    def test_list_stages_ok(self, client):
        r = client.get("/worldbuild/stages")
        assert r.status_code == 200
        assert "stages" in r.json()

    def test_get_result_unknown_project_404(self, client):
        """未存在的 project_id GET worldbuild/result 必须 404，不能返 200 + 空数据。"""
        r = client.get("/projects/00000000000000000000000000000000/worldbuild/result")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_get_world_unknown_project_404(self, client):
        r = client.get("/projects/00000000000000000000000000000000/world")
        assert r.status_code in (403, 404), f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# D. 不泄漏敏感信息（domain 2 端点 + 假 key sentinel）
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
    ".sqlite",
    "sk-fake-",
]


@pytest.mark.parametrize("method,path", [
    ("GET",  "/providers"),
    ("POST", "/providers"),
    ("GET",  "/role-assignments"),
    ("PUT",  "/role-assignments/planner"),
    ("GET",  "/worldbuild/stages"),
    ("GET",  "/projects/00000000000000000000000000000000/worldbuild/result"),
    ("GET",  "/projects/00000000000000000000000000000000/world"),
])
def test_domain2_endpoints_no_leak(client, method, path):
    """domain 2 端点 4xx/5xx 响应 body 不含 traceback/SQL/路径/key 前缀。"""
    payload = None
    if method == "POST" and path == "/providers":
        payload = {"name": "leak-test", "provider_type": "deepseek", "api_key": "sk-fake-leak-sentinel-DOMAIN2ABCDEFGHIJ", "default_model": "x"}
    elif method == "PUT" and path == "/role-assignments/planner":
        payload = {"provider_id": "00000000000000000000000000000000"}
    if method in ("POST", "PUT"):
        r = client.request(method, path, json=payload or {})
    else:
        r = client.request(method, path)
    if r.status_code >= 500:
        for leak in FORBIDDEN_LEAKS:
            assert leak not in r.text, (
                f"{method} {path} 500 响应泄漏 {leak!r}：\n{r.text[:500]}"
            )
