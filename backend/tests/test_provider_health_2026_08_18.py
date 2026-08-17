"""test_provider_health_2026_08_18.py

2026-08-18 新增 /providers/health 端点（用户报告 #3 + #5：开始构建/生成大纲时
LLM 调用失败，前端无法提前知道 provider 是否就绪，必须等真正跑起来才能看到报错）。

修法：暴露 /providers/health 给前端，让前端在「开始构建」之前先 ping 一次，
不可用时直接给「去配置供应商」的引导，而不是让用户等 30 秒后失败。

本测试覆盖：
  1. mock 模式 can_run_llm=True
  2. live 模式所有 env key 都缺失 → can_run_llm=False + message 指明原因
  3. live 模式全局 provider key 配置 → can_run_llm=True（角色路由都退回兜底）
  4. /providers/{id}/test 缺 key → 400
  5. /providers/{id}/test 上游 4xx → 错误码透传给上游信息
"""

from __future__ import annotations

import pytest


# ════════════════════════════════════════════════════════════════
# Fixtures: ensure clean env per test
# ════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_env(monkeypatch):
    """模拟 settings.llm_provider == 'mock'，不需要任何 key。"""
    from app import config as cfg
    # 直接修改 Pydantic settings 实例（重新构造避免污染别处测试）
    monkeypatch.setattr(cfg.settings, "llm_provider", "mock", raising=False)


@pytest.fixture
def live_no_key(monkeypatch):
    """live 模式但所有 env key 都清空 —— 必须不可用。"""
    from app import config as cfg
    monkeypatch.setattr(cfg.settings, "llm_provider", "deepseek", raising=False)
    monkeypatch.setattr(cfg.settings, "llm_api_key", "", raising=False)
    monkeypatch.setattr(cfg.settings, "deepseek_api_key", "", raising=False)
    monkeypatch.setattr(cfg.settings, "kimi_api_key", "", raising=False)
    monkeypatch.setattr(cfg.settings, "minimax_api_key", "", raising=False)


@pytest.fixture
def live_with_key(monkeypatch):
    """live 模式，全局 provider key 已配（角色 provider 缺失时退回全局）。"""
    from app import config as cfg
    monkeypatch.setattr(cfg.settings, "llm_provider", "deepseek", raising=False)
    monkeypatch.setattr(cfg.settings, "llm_api_key", "test-global-key", raising=False)
    monkeypatch.setattr(cfg.settings, "llm_api_base", "https://api.deepseek.com/v1", raising=False)
    monkeypatch.setattr(cfg.settings, "llm_model", "deepseek-chat", raising=False)
    monkeypatch.setattr(cfg.settings, "deepseek_api_base", "https://api.deepseek.com/v1", raising=False)
    monkeypatch.setattr(cfg.settings, "deepseek_api_key", "test-deepseek-key", raising=False)
    monkeypatch.setattr(cfg.settings, "deepseek_model", "deepseek-chat", raising=False)


# ════════════════════════════════════════════════════════════════
# /providers/health 端点
# ════════════════════════════════════════════════════════════════


def test_health_mock_mode(mock_env):
    """mock 模式：can_run_llm=True，所有角色路由到 mock。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/providers/health")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "mock"
    assert data["can_run_llm"] is True
    # 三个角色都标 ok
    assert all(data["roles"][r]["ok"] for r in ("structured_logic", "creative_detail", "consistency_check"))


def test_health_live_no_keys_unavailable(live_no_key):
    """live 模式无任何 key：can_run_llm=False，每角色都报具体原因。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/providers/health")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "live"
    assert data["can_run_llm"] is False
    # 每个角色都标 ok=False + reason 提到 key 缺失
    for role, status in data["roles"].items():
        assert status["ok"] is False, f"{role} 应该 ok=False"
        assert "API key" in (status["reason"] or ""), f"{role} reason 应提到 API key"


def test_health_live_with_global_key_fallback(live_with_key):
    """live 模式全局 key 配齐：can_run_llm=True，角色路由若对应 provider
    无 key 则退回全局。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/providers/health")
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "live"
    assert data["can_run_llm"] is True
    # structured_logic → deepseek（已配 key）→ 直接走 deepseek
    assert data["roles"]["structured_logic"]["ok"] is True
    # creative_detail → kimi（未配 key）→ 退回全局 deepseek → ok
    assert data["roles"]["creative_detail"]["ok"] is True
    if "reason" in data["roles"]["creative_detail"] and data["roles"]["creative_detail"]["reason"]:
        assert "退回" in data["roles"]["creative_detail"]["reason"]


# ════════════════════════════════════════════════════════════════
# /providers/{id}/test 端点
# ════════════════════════════════════════════════════════════════


def test_test_provider_no_api_key():
    """DB provider 存在但未配 api_key → 400，不静默返 ok。"""
    from app.database import SessionLocal
    from app.models import Provider
    from fastapi.testclient import TestClient
    from app.main import app

    db = SessionLocal()
    try:
        p = Provider(
            name="test-no-key",
            provider_type="openai_compatible",
            api_base="https://api.example.com/v1",
            default_model="gpt-test",
            api_key_encrypted=None,
            api_key_suffix=None,
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        pid = p.id
    finally:
        db.close()

    client = TestClient(app)
    try:
        r = client.post(f"/providers/{pid}/test")
        assert r.status_code == 400
        assert "key" in r.json()["detail"].lower()
    finally:
        # cleanup
        db = SessionLocal()
        try:
            db.query(Provider).filter_by(id=pid).delete()
            db.commit()
        finally:
            db.close()


def test_test_provider_unreachable(monkeypatch):
    """provider 配置齐全但 api_base 网络不通 → 502 + 错误信息透传。"""
    from app.database import SessionLocal
    from app.models import Provider
    from app.security import encrypt_api_key
    from fastapi.testclient import TestClient
    from app.main import app

    encrypted = encrypt_api_key("test-key-value")
    db = SessionLocal()
    try:
        p = Provider(
            name="test-unreachable",
            provider_type="openai_compatible",
            api_base="http://127.0.0.1:1",  # 端口 1 必连不通
            default_model="gpt-test",
            api_key_encrypted=encrypted,
            api_key_suffix="…alue",
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        pid = p.id
    finally:
        db.close()

    client = TestClient(app)
    try:
        r = client.post(f"/providers/{pid}/test")
        assert r.status_code in (400, 502), f"unexpected status: {r.status_code} body: {r.text}"
        # 错误信息必须可见（不是空字符串）
        assert r.json()["detail"]
    finally:
        db = SessionLocal()
        try:
            db.query(Provider).filter_by(id=pid).delete()
            db.commit()
        finally:
            db.close()


def test_test_provider_404():
    """不存在的 provider_id → 404。"""
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.post("/providers/nonexistent-id/test")
    assert r.status_code == 404