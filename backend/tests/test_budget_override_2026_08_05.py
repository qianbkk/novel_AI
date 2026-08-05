"""test_budget_override_2026_08_05.py

2026-08-05 修复（清单衍生）：实现 NOVEL_BUDGET_HARD_OVERRIDE env var，
之前 patch #201 文档承诺但代码没读。

覆盖 4 个分支：
  1. 不设 env → 默认 1.50（150% hard stop）
  2. env=1.0 → 严格 100% 硬停
  3. env=0.05 (超出 [0.5, 5.0] 范围) → 警告 + fallback 默认
  4. env=非数字 → 警告 + fallback 默认
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _state(used, limit=100.0):
    return {"budget_used_usd": used, "budget_limit_usd": limit}


def _reload_with_env(monkeypatch, env_value):
    """Reset 模块级缓存并设置/清除 env，然后 reload 模块重新解析。"""
    import importlib
    if env_value is None:
        monkeypatch.delenv("NOVEL_BUDGET_HARD_OVERRIDE", raising=False)
    else:
        monkeypatch.setenv("NOVEL_BUDGET_HARD_OVERRIDE", env_value)
    if "engine.orchestrator" in sys.modules:
        importlib.reload(sys.modules["engine.orchestrator"])
    else:
        importlib.import_module("engine.orchestrator")


def test_default_hard_stop_150pct(monkeypatch):
    """不设 env 时使用默认 1.50。"""
    _reload_with_env(monkeypatch, env_value=None)
    from engine import orchestrator as orch

    state = _state(used=140)        # 140 < 100 * 1.50 = 150 → 允许
    assert orch._budget_ok(state) is True
    state_over = _state(used=160)  # 160 > 150 → 拒
    assert orch._budget_ok(state_over) is False


def test_override_to_1p0_strict_hard_stop(monkeypatch):
    """env=1.0 时硬停 100%：刚好等于也要拒。"""
    _reload_with_env(monkeypatch, env_value="1.0")
    from engine import orchestrator as orch

    state = _state(used=99)        # 99 < 100*1.0 = 100 → 允许
    assert orch._budget_ok(state) is True
    state_over = _state(used=100)  # 100 < 100 false → 拒
    assert orch._budget_ok(state_over) is False


def test_override_out_of_range_falls_back(monkeypatch):
    """env=0.05 超出 [0.5, 5.0] → warning + 回默认 1.50。"""
    _reload_with_env(monkeypatch, env_value="0.05")
    from engine import orchestrator as orch

    # 应回默认 1.50：_budget_ok(used=149) 仍 True，used=151 False
    assert orch._budget_ok(_state(used=149)) is True
    assert orch._budget_ok(_state(used=151)) is False


def test_override_invalid_float_falls_back(monkeypatch):
    """env='abc' 非浮点 → warning + 回默认。"""
    _reload_with_env(monkeypatch, env_value="abc")
    from engine import orchestrator as orch

    # 默认 1.50：149 允许，151 拒
    assert orch._budget_ok(_state(used=149)) is True
    assert orch._budget_ok(_state(used=151)) is False


def test_reset_budget_override_for_test_isolation(monkeypatch):
    """reset_budget_override 后再次 _budget_ok 应重读 env。"""
    # 设 env=1.0
    monkeypatch.setenv("NOVEL_BUDGET_HARD_OVERRIDE", "1.0")
    # reload 让模块加载时缓存生效
    import importlib
    if "engine.orchestrator" in sys.modules:
        importlib.reload(sys.modules["engine.orchestrator"])
    else:
        importlib.import_module("engine.orchestrator")
    from engine import orchestrator as orch

    # 第一次调 1.0 生效
    assert orch._budget_ok(_state(used=99)) is True
    assert orch._budget_ok(_state(used=100)) is False

    # reset + 改 env=1.50 + reload: 应该回到 default
    orch.reset_budget_override()
    # 缓存已清空，下一次调 _budget_ok 会重新读 env —— 但 reload 改变了模块
    # 状态，单独 reset_budget_override() 之后必须再次 reload（或直接读 env 一次）
    # 在本测试里我们 reload 来确保 module 内缓存的引用也是新值。
    monkeypatch.setenv("NOVEL_BUDGET_HARD_OVERRIDE", "1.50")
    importlib.reload(sys.modules["engine.orchestrator"])
    from engine import orchestrator as orch2
    assert orch2._budget_ok(_state(used=149)) is True
    assert orch2._budget_ok(_state(used=151)) is False
