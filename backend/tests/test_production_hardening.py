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


# 生产模式必填的 env vars（除测试场景明确要"缺 X 触发 fail-fast"外一律用本 fixture 设置）。
# Task 08 batch 2：8 个测试之前都要写 5-7 行 monkeypatch.setenv，重复明显，
# 抽到一处后每条用例只需声明"哪个值被改 / 哪个 key 不设"。
_PROD_REQUIRED_KEYS = ("NOVEL_PRODUCTION", "MASTER_KEY", "JWT_SECRET")
_PROD_OPTIONAL_KEYS = ("ALLOWED_ORIGINS", "RATE_LIMIT_EXEMPT_LOCALHOST", "ALLOWED_PROXIES")


def _apply_prod_env(monkeypatch, *, drop=(), **overrides):
    """设置生产模式基线 env vars（任务 08 batch 2 抽取的 helper）。

    - 默认设 NOVEL_PRODUCTION=1 + MASTER_KEY + JWT_SECRET
    - overrides 里给 ALLOWED_ORIGINS / RATE_LIMIT_EXEMPT_LOCALHOST / ALLOWED_PROXIES 覆盖值
    - drop 里的 key 故意不设（用于测"缺 X → fail-fast"）
    """
    monkeypatch.setenv("NOVEL_PRODUCTION", "1")
    monkeypatch.setenv("MASTER_KEY", "x" * 44)
    monkeypatch.setenv("JWT_SECRET", "x" * 64)
    for k in _PROD_OPTIONAL_KEYS:
        if k in overrides:
            monkeypatch.setenv(k, overrides[k])
        elif k not in drop:
            # 默认给安全值，保证 prod 校验能通过
            if k == "ALLOWED_ORIGINS":
                monkeypatch.setenv(k, "https://app.example.com")
            elif k == "RATE_LIMIT_EXEMPT_LOCALHOST":
                monkeypatch.setenv(k, "0")
            # ALLOWED_PROXIES 默认不设（可选）


def _reload_settings():
    """重读 app.config.settings（env 改了 → 必须重新构造 Settings 实例）。"""
    import app.config as _cfg
    _cfg.settings = _cfg.Settings()
    return _cfg


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """每个测试前清掉可能干扰的 env vars。

    auth.py 用 JWT_SECRET/dev cache，需要在 main.py import 之前清掉，
    所以 monkeypatch.delenv（严格清理 "not present"）而不是 set。
    """
    for k in _PROD_REQUIRED_KEYS + _PROD_OPTIONAL_KEYS + ("DATABASE_URL",):
        monkeypatch.delenv(k, raising=False)


def test_dev_mode_passes(monkeypatch):
    """dev 模式（NOVEL_PRODUCTION 未设）→ 校验函数直接返回。"""
    from app.main import _check_production_hardening
    # 没设任何 prod 标志，应该立刻返回
    _check_production_hardening()  # no raise


def test_production_localhost_origin_fails(monkeypatch):
    """NOVEL_PRODUCTION=1 + ALLOWED_ORIGINS 含 localhost → fail-fast。"""
    _apply_prod_env(monkeypatch, ALLOWED_ORIGINS="https://app.example.com,http://localhost:5293")
    _reload_settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "localhost" in str(exc_info.value).lower()


def test_production_wildcard_origin_fails(monkeypatch):
    """NOVEL_PRODUCTION=1 + ALLOWED_ORIGINS=* → fail-fast。

    通配 * 也是 dev 风格——会让 CORS 放行任何来源到生产 backend。
    """
    _apply_prod_env(monkeypatch, ALLOWED_ORIGINS="*")
    _reload_settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "*" in str(exc_info.value).lower() or "wildcard" in str(exc_info.value).lower() \
        or "允许" in str(exc_info.value) or "ALLOWED_ORIGINS" in str(exc_info.value)


def test_production_localhost_exempt_fails(monkeypatch):
    """NOVEL_PRODUCTION=1 + RATE_LIMIT_EXEMPT_LOCALHOST 未设 0 → fail-fast。"""
    _apply_prod_env(monkeypatch, drop=("RATE_LIMIT_EXEMPT_LOCALHOST",))
    _reload_settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "RATE_LIMIT_EXEMPT_LOCALHOST" in str(exc_info.value)


def test_production_jwt_secret_required(monkeypatch):
    """NOVEL_PRODUCTION=1 + JWT_SECRET 未设 → fail-fast。"""
    _apply_prod_env(monkeypatch)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    _reload_settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    assert "JWT_SECRET" in str(exc_info.value)


def test_production_full_hardening_passes(monkeypatch):
    """全套 prod 配置 → 不抛。"""
    _apply_prod_env(
        monkeypatch,
        ALLOWED_ORIGINS="https://app.example.com,https://www.app.example.com",
    )
    monkeypatch.setenv("ALLOWED_PROXIES", "10.0.0.0/8")
    _reload_settings()

    from app.main import _check_production_hardening
    _check_production_hardening()  # no raise


def test_production_multiple_issues_combined(monkeypatch):
    """多个 issue 合并到一条错误（不让用户修一个重启一次）。"""
    _apply_prod_env(
        monkeypatch,
        ALLOWED_ORIGINS="http://localhost:5293",
        drop=("RATE_LIMIT_EXEMPT_LOCALHOST",),
    )
    monkeypatch.delenv("JWT_SECRET", raising=False)
    _reload_settings()

    from app.main import _check_production_hardening
    with pytest.raises(RuntimeError) as exc_info:
        _check_production_hardening()
    msg = str(exc_info.value)
    # 至少 3 个不同 issue 都应在错误里出现
    assert "ALLOWED_ORIGINS" in msg or "localhost" in msg
    assert "RATE_LIMIT_EXEMPT_LOCALHOST" in msg
    assert "JWT_SECRET" in msg
