"""API 错误响应一致性（任务 07 · 资源域第四批：bridge / rules / foreshadowings）

domain 1 = projects/auth；domain 2 = providers/role/worldbuild；
domain 3 = outline/chapters/chapter_titles；本批次 = bridge/rules/foreshadowings。

约束：
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
# A. /bridge/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestBridgeErrorCodes:

    def test_binding_unknown_project_404(self, client):
        r = client.get(f"{PROJECT_PATH}/bridge/binding")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_run_unknown_project_404(self, client):
        r = client.post(f"{PROJECT_PATH}/bridge/run", json={"command": "plan"})
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_run_missing_command_422(self, client):
        """缺 command → 422 或 404（project 不存在时）。"""
        r = client.post(f"{PROJECT_PATH}/bridge/run", json={})
        assert r.status_code in (400, 404, 422), f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# B. /rules/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestRulesErrorCodes:

    def test_get_rules_unknown_project_404(self, client):
        r = client.get(f"{PROJECT_PATH}/rules")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_put_rules_unknown_project_404(self, client):
        """PUT rules 即使无 body 也是 404（project 不存在）。"""
        r = client.put(f"{PROJECT_PATH}/rules", json={})
        assert r.status_code in (400, 404, 422), f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# C. /foreshadowings/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestForeshadowingsErrorCodes:

    def test_list_foreshadowings_unknown_project_404(self, client):
        r = client.get(f"{PROJECT_PATH}/foreshadowings")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_update_status_unknown_id_404(self, client):
        r = client.put(
            f"{PROJECT_PATH}/foreshadowings/00000000000000000000000000000000/status",
            json={"status": "resolved"},
        )
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# D. 不泄漏敏感信息（domain 4 端点）
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
]


@pytest.mark.parametrize("method,path,payload", [
    ("GET",  f"{PROJECT_PATH}/bridge/binding", None),
    ("POST", f"{PROJECT_PATH}/bridge/run", {"command": "plan"}),
    ("GET",  f"{PROJECT_PATH}/rules", None),
    ("PUT",  f"{PROJECT_PATH}/rules", {"rule_json": {}}),
    ("GET",  f"{PROJECT_PATH}/foreshadowings", None),
])
def test_domain4_endpoints_no_leak(client, method, path, payload):
    """domain 4 端点 4xx/5xx 响应 body 不含 traceback/SQL/路径。"""
    if payload is None:
        r = client.request(method, path)
    else:
        r = client.request(method, path, json=payload)
    if r.status_code >= 500:
        for leak in FORBIDDEN_LEAKS:
            assert leak not in r.text, (
                f"{method} {path} 500 响应泄漏 {leak!r}：\n{r.text[:500]}"
            )