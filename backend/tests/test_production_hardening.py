"""backend/tests/test_production_hardening.py — Phase 4 生产模式启动校验

覆盖：
  - NOVEL_PRODUCTION=1 + ALLOWED_ORIGINS 含 localhost → fail-fast
  - NOVEL_PRODUCTION=1 + RATE_LIMIT_EXEMPT_LOCALHOST 未设 0 → fail-fast
  - NOVEL_PRODUCTION=1 + JWT_SECRET 未设 → fail-fast
  - dev 模式（默认） → 不检查，行为不变
  - 多 issue 合并到一条错误
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest
import uuid as _uuid


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """每个测试前清掉可能干扰的 env vars。

    auth.py 用 JWT_SECRET/dev cache，需要在 main.py import 之前清掉，
    所以 monkeypatch.delenv（严格清理 "not present"）而不是 set。
    """
    for k in ("NOVEL_PRODUCTION", "JWT_SECRET", "ALLOWED_ORIGINS",
              "RATE_LIMIT_EXEMPT_LOCALHOST", "ALLOWED_PROXIES",
              "MASTER_KEY", "DATABASE_URL"):
        monkeypatch.delenv(k, raising=False)


def test_dev_mode_passes(monkeypatch):
    """dev 模式（NOVEL_PRODUCTION 未设）→ 校验函数直接返回。"""
    from app.main import _check_production_hardening
    # 没设任何 prod 标志，应该立刻返回
    _check_production_hardening()  # no raise


def test_production_localhost_origin_fails(monkeypatch):
    """NOVEL_PRODUCTION=1 + ALLOWED_ORIGINS 含 localhost → fail-fast。"""
    monkeypatch.setenv("NOVEL_PRODUCTION", "1")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com,http://localhost:5293")
    monkeypatch.setenv("RATE_LIMIT_EXEMPT_LOCALHOST", "0")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)

    from app.config import get_allowed_origins_list
    import app.config as _cfg
    _cfg.settings = _cfg.Settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "localhost" in str(exc_info.value).lower()


def test_production_wildcard_origin_fails(monkeypatch):
    """NOVEL_PRODUCTION=1 + ALLOWED_ORIGINS=* → fail-fast。

    通配 * 也是 dev 风格——会让 CORS 放行任何来源到生产 backend。
    """
    monkeypatch.setenv("NOVEL_PRODUCTION", "1")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")
    monkeypatch.setenv("RATE_LIMIT_EXEMPT_LOCALHOST", "0")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)

    import app.config as _cfg
    _cfg.settings = _cfg.Settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "*" in str(exc_info.value).lower() or "wildcard" in str(exc_info.value).lower() \
        or "允许" in str(exc_info.value) or "ALLOWED_ORIGINS" in str(exc_info.value)


def test_production_localhost_exempt_fails(monkeypatch):
    """NOVEL_PRODUCTION=1 + RATE_LIMIT_EXEMPT_LOCALHOST 未设 0 → fail-fast。"""
    monkeypatch.setenv("NOVEL_PRODUCTION", "1")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
    # 不设 RATE_LIMIT_EXEMPT_LOCALHOST（默认豁免）
    monkeypatch.setenv("JWT_SECRET", "x" * 64)

    import app.config as _cfg
    _cfg.settings = _cfg.Settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "RATE_LIMIT_EXEMPT_LOCALHOST" in str(exc_info.value)


def test_production_jwt_secret_required(monkeypatch):
    """NOVEL_PRODUCTION=1 + JWT_SECRET 未设 → fail-fast。"""
    monkeypatch.setenv("NOVEL_PRODUCTION", "1")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com")
    monkeypatch.setenv("RATE_LIMIT_EXEMPT_LOCALHOST", "0")
    # JWT_SECRET 不设

    import app.config as _cfg
    _cfg.settings = _cfg.Settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "JWT_SECRET" in str(exc_info.value)


def test_production_full_hardening_passes(monkeypatch):
    """全套 prod 配置 → 不抛。"""
    monkeypatch.setenv("NOVEL_PRODUCTION", "1")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://app.example.com,https://www.app.example.com")
    monkeypatch.setenv("RATE_LIMIT_EXEMPT_LOCALHOST", "0")
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    monkeypatch.setenv("ALLOWED_PROXIES", "10.0.0.0/8")

    import app.config as _cfg
    _cfg.settings = _cfg.Settings()

    from app.main import _check_production_hardening
    _check_production_hardening()  # no raise


def test_production_multiple_issues_combined(monkeypatch):
    """多个 issue 合并到一条错误（不让用户修一个重启一次）。"""
    monkeypatch.setenv("NOVEL_PRODUCTION", "1")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5293")
    # 不设 RATE_LIMIT_EXEMPT_LOCALHOST, 不设 JWT_SECRET

    import app.config as _cfg
    _cfg.settings = _cfg.Settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    msg = str(exc_info.value)
    # 至少 3 个不同 issue 都应在错误里出现
    assert "ALLOWED_ORIGINS" in msg or "localhost" in msg
    assert "RATE_LIMIT_EXEMPT_LOCALHOST" in msg
    assert "JWT_SECRET" in msg
