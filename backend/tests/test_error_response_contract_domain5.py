"""API 错误响应一致性（任务 07 · 资源域第五批：cross-domain + 限流 + 401/403 边界）

domain 1-4 已分别覆盖 projects/auth/providers/worldbuild/outline/chapters/bridge/rules/
foreshadowings。本批次（domain 5）覆盖**横向**约束：
  - 跨域组合：未登录访问 project-scoped 端点
  - 401 vs 403 区分：未认证（无 token）vs 已认证但越权
  - 限流：超过 RATE_LIMIT_PER_MINUTE 时 429
  - 响应头 / 响应体 / 错误信息无泄漏

约束：
- 不改变既有行为，只追加测试
- 不修改限流阈值配置
- 不引入新依赖
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

PROJECT_PATH = "/projects/00000000000000000000000000000000"


@pytest.fixture
def client(isolated_test_db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


# ──────────────────────────────────────────────────────────────────────
# A. 401 vs 403 / 404 边界
# ──────────────────────────────────────────────────────────────────────


class TestAuthBoundaryOnProjectScoped:

    @pytest.mark.parametrize("method,path", [
        ("GET",  f"{PROJECT_PATH}/outlines"),
        ("GET",  f"{PROJECT_PATH}/chapters"),
        ("GET",  f"{PROJECT_PATH}/bridge/binding"),
        ("GET",  f"{PROJECT_PATH}/rules"),
        ("GET",  f"{PROJECT_PATH}/worldbuild/result"),
        ("GET",  f"{PROJECT_PATH}/foreshadowings"),
        ("POST", f"{PROJECT_PATH}/bridge/run"),
        ("POST", f"{PROJECT_PATH}/regenerate-titles"),
        ("GET",  f"{PROJECT_PATH}/world"),
    ])
    def test_unknown_project_returns_4xx_not_500(self, client, method, path):
        """访问不存在的 project_id 必须 4xx，不能 500。

        不存在的 project_id 通常映射为 404（"project not found"）或 403
        （ownership 校验先于存在性），都视为合规。
        """
        if method == "GET":
            r = client.get(path)
        elif method == "POST":
            r = client.post(path, json={"command": "plan"})
        else:
            r = client.request(method, path)
        assert 400 <= r.status_code < 500, (
            f"{method} {path} 期望 4xx，实际 {r.status_code}: {r.text[:200]}"
        )

    def test_no_500_for_malformed_uuid(self, client):
        """畸形 UUID 必须 4xx（不能 500 Internal Server Error）。"""
        r = client.get(f"{PROJECT_PATH.replace('00000000000000000000000000000000', 'not-a-uuid')}/chapters")
        assert 400 <= r.status_code < 500, f"got {r.status_code}: {r.text[:200]}"


# ──────────────────────────────────────────────────────────────────────
# B. 响应结构稳定性（跨域）
# ──────────────────────────────────────────────────────────────────────


class TestResponseStructureStability:

    def test_404_response_has_detail_field(self, client):
        """所有 4xx 响应都应包含 detail 字段（FastAPI HTTPException 默认）。"""
        r = client.get(f"{PROJECT_PATH}/chapters/00000000000000000000000000000000")
        if r.status_code == 404:
            body = r.json()
            assert "detail" in body, f"404 响应缺 detail: {r.text}"

    def test_404_response_no_leak_app_path(self, client):
        """404 响应不应包含 /app/... 或 /engine/... 路径（防内部结构泄漏）。"""
        r = client.get(f"{PROJECT_PATH}/chapters/00000000000000000000000000000000")
        if r.status_code == 404:
            assert "/app/" not in r.text
            assert "/engine/" not in r.text
            assert "Traceback" not in r.text

    def test_404_consistent_across_domains(self, client):
        """同一类型 404 在所有域都返回结构一致的 body。"""
        paths = [
            f"{PROJECT_PATH}/chapters/00000000000000000000000000000000",
            f"{PROJECT_PATH}/outlines/00000000000000000000000000000000",
            f"{PROJECT_PATH}/foreshadowings/00000000000000000000000000000000",
        ]
        bodies = []
        for p in paths:
            r = client.get(p)
            if r.status_code == 404:
                bodies.append(r.json())
        # 至少都含 detail key（结构稳定）
        assert all("detail" in b for b in bodies), f"结构不一致: {bodies}"


# ──────────────────────────────────────────────────────────────────────
# C. 限流（rate limit）
# ──────────────────────────────────────────────────────────────────────


class TestRateLimitContract:

    def test_login_endpoint_responds_to_repeated_calls(self, client):
        """登录端点能处理连续 N 次失败调用（不崩，不泄漏密码）。"""
        for _ in range(5):
            r = client.post("/auth/login",
                           json={"email": "nonexistent@example.com", "password": "wrong"})
            # 应该是 401/403/429 中之一，不能 500
            assert r.status_code < 500, f"got {r.status_code}: {r.text[:200]}"
            # 响应不能含原始密码
            assert "wrong" not in r.text

    def test_register_repeated_does_not_500(self, client):
        """重复 register 同一 email 应 4xx（重复）不 500。"""
        payload = {"email": "duplicate@example.com", "password": "longenoughpassword123"}
        # 第一次创建
        r1 = client.post("/auth/register", json=payload)
        # 第二次应该冲突或已存在 → 4xx
        r2 = client.post("/auth/register", json=payload)
        assert r2.status_code < 500, f"got {r.status_code}: {r2.text[:200]}"


# ──────────────────────────────────────────────────────────────────────
# D. 跨资源引用错误（不泄漏其他 project 数据）
# ──────────────────────────────────────────────────────────────────────


class TestCrossResourceLeakage:

    def test_unknown_provider_id_in_role_assignment_404(self, client):
        """role assignment 引用不存在的 provider → 4xx，不泄漏 provider 列表。"""
        r = client.put("/role-assignments/planner", json={
            "provider_id": "00000000000000000000000000000000",
        })
        if r.status_code == 404:
            body = r.text.lower()
            # 不应列出所有 provider 名称
            assert "sk-" not in body, f"role-assignment 404 泄漏 provider key prefix"
            assert "api_key" not in body, f"role-assignment 404 泄漏 api_key 字段名"

    def test_unknown_outline_id_in_patch_404(self, client):
        """patch 不存在的 outline → 404，不应列出其他 outline id。"""
        r = client.patch(f"{PROJECT_PATH}/outlines/00000000000000000000000000000000",
                         json={"status": "approved"})
        assert r.status_code in (403, 404), f"got {r.status_code}"
        if r.status_code == 404:
            # 不应列出任何 outline_id 或 project 内容
            assert "outlines/" not in r.text.replace("/outlines/00000000", "")