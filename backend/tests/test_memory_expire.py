"""expire_constraints 鲁棒性测试（P7-fix 回归）

300 章 v2 run 在 ch8 卡住：
  TypeError: '>' not supported between instances of 'NoneType' and 'int'

根因：`c.get("expires_at_chapter", 9999)` 在 key 存在但 value=None
时不触发 default，None > int 报错。LLM 偶发返 None。

修法：_safe_expires 显式 isinstance 检查 + 视 None 为 9999。
"""
from __future__ import annotations
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from engine.memory.manager import expire_constraints


def _mk_memory(forbidden: list) -> dict:
    return {"constraints": {"forbidden_constraints": forbidden}}


def test_expire_constraints_with_none_expires_does_not_crash():
    """expires_at_chapter=None 的 constraint 不应让 expire_constraints 崩。"""
    memory = _mk_memory([
        {"desc": "主角不能死", "expires_at_chapter": None, "reason": "test"},
        {"desc": "主角不能死", "expires_at_chapter": 5, "reason": "test"},
    ])
    # 之前会 TypeError，现在应正常返回
    updated, expired_count = expire_constraints(memory, current_chapter=3)
    # None 视作 9999（不约束），5 > 3 还活
    assert len(updated["constraints"]["forbidden_constraints"]) == 2
    assert expired_count == 0


def test_expire_constraints_with_negative_int_treated_as_9999():
    """expires_at_chapter=-1（异常值）应视作 9999 不约束。"""
    memory = _mk_memory([
        {"desc": "x", "expires_at_chapter": -1, "reason": "test"},
    ])
    updated, _ = expire_constraints(memory, current_chapter=10)
    assert len(updated["constraints"]["forbidden_constraints"]) == 1


def test_expire_constraints_with_string_expires_treated_as_9999():
    """expires_at_chapter="invalid"（异常值）应视作 9999 不约束，不崩。"""
    memory = _mk_memory([
        {"desc": "x", "expires_at_chapter": "invalid", "reason": "test"},
    ])
    updated, _ = expire_constraints(memory, current_chapter=10)
    assert len(updated["constraints"]["forbidden_constraints"]) == 1


def test_expire_constraints_normal_expiry_still_works():
    """正常过期逻辑不应受影响。"""
    memory = _mk_memory([
        {"desc": "x", "expires_at_chapter": 5, "reason": "test"},
        {"desc": "y", "expires_at_chapter": 15, "reason": "test"},
    ])
    updated, expired = expire_constraints(memory, current_chapter=10)
    # 5 <= 10 已过期，应只剩 y
    assert len(updated["constraints"]["forbidden_constraints"]) == 1
    assert updated["constraints"]["forbidden_constraints"][0]["desc"] == "y"
    assert expired == 1


def test_expire_constraints_missing_key_treated_as_9999():
    """expires_at_chapter key 缺失 → 9999 默认值（不约束），跟原 dict.get 行为兼容。"""
    memory = _mk_memory([
        {"desc": "x", "reason": "test"},  # 没 expires_at_chapter 字段
    ])
    updated, _ = expire_constraints(memory, current_chapter=10)
    assert len(updated["constraints"]["forbidden_constraints"]) == 1
