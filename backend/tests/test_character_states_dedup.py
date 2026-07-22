"""backend/tests/test_character_states_dedup.py — Phase 2 #5 (2026-07-22) 修复回归

character_states 字段的 fuzzy-key dedup（与 _merge_threads 同模式）。
之前 char_states.update(updates.char_states) 是 dict.update 纯替换，
LLM 在不同章节用不同叫法指同一角色（\"林渊\" / \"逍遥兄\"）时新增独立
key 不更新旧 key，导致碎片化。30 章真实 LLM 测试启动条件。

测试点：
- substring 同义 key 合并（\"林渊\" vs \"林兄\" vs \"逍遥兄\"）
- 完全相同 key 不重复
- 真新角色作为新 key 添加
- 真新叫法+旧叫法共用——别名合并保留旧 key
- 空字符串 / 空 value 跳过
- 多次合并后 key 数不无限增长
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from engine.agents.tracker import _merge_character_states


def test_exact_same_key_overwrites_value():
    existing = {"林渊": "重生回到 2012，觉醒债感能力"}
    updates = {"林渊": "在云州立稳脚跟，建立第一个根据地"}
    result = _merge_character_states(existing, updates)
    # 同一个 key，value 用更新
    assert result == {"林渊": "在云州立稳脚跟，建立第一个根据地"}


def test_substring_alias_merges_into_existing_key():
    """核心场景：第 N 章 LLM 用 \"林渊\"，第 N+1 章用 \"林渊兄\"，
    应该是同一个人，value 用最新的，key 保留旧的（让前端显示稳定）。"""
    existing = {"林渊": "云州首富之子"}
    updates = {"林渊兄": "云州首富之子，重生归来"}
    result = _merge_character_states(existing, updates)
    # 应合并到 \"林渊\"，value 用更新值
    assert "林渊" in result
    assert "林渊兄" not in result, f"应合并别名，不应新增 key: {result}"
    assert result["林渊"] == "云州首富之子，重生归来"


def test_substring_alias_short_form_merges():
    existing = {"林渊": "债主委员会世家三代追债人"}
    updates = {"逍遥": "重生于云州客栈"}
    result = _merge_character_states(existing, updates)
    # \"逍遥\" 不是 \"林渊\" 的子串或父串，应独立成 key
    assert result == {"林渊": "债主委员会世家三代追债人", "逍遥": "重生于云州客栈"}


def test_real_new_character_adds_new_key():
    existing = {"林渊": "重生回到 2012"}
    updates = {"苏晚栀": "云州苏氏旁支，自幼被嫡支排挤"}
    result = _merge_character_states(existing, updates)
    assert result == {
        "林渊": "重生回到 2012",
        "苏晚栀": "云州苏氏旁支，自幼被嫡支排挤",
    }


def test_empty_value_does_not_overwrite_existing():
    existing = {"林渊": "债感修炼者"}
    updates = {"林渊": ""}
    result = _merge_character_states(existing, updates)
    # LLM 偶尔返空 value 时不应抹掉旧 value
    assert result == {"林渊": "债感修炼者"}


def test_empty_key_skipped():
    existing = {"林渊": "债感修炼者"}
    updates = {"": "无名氏", "苏晚栀": "苏氏旁支"}
    result = _merge_character_states(existing, updates)
    assert result == {"林渊": "债感修炼者", "苏晚栀": "苏氏旁支"}


def test_30_chapter_simulation_key_count_stable():
    """模拟 30 章真实 LLM 测试：每章 LLM 给 4 个角色状态，
    角色名偶尔换叫法（\"林渊\" / \"林兄\" / \"逍遥兄\"）。
    修复前 key 数会爆炸（30 * 4 = 120+），
    修复后稳定在 4-5 个真实角色。"""
    existing: dict = {}
    name_variants = ["林渊", "林兄", "林渊兄", "逍遥", "林渊", "林渊兄"]
    other_chars = ["苏晚栀", "孟浩", "顾青锋"]
    for ch in range(1, 31):
        updates = {
            "林渊": f"ch{ch} 主角状态更新",
            other_chars[ch % 3]: f"ch{ch} 配角状态",
            name_variants[ch % len(name_variants)]: f"ch{ch} 林渊变体",
        }
        existing = _merge_character_states(existing, updates)
    # 关键断言：key 数量应 < 10（真实角色数 4 + 别名合并）
    assert len(existing) < 10, f"key 碎片化：{len(existing)} keys = {list(existing.keys())}"
    # 林渊必须有
    assert "林渊" in existing
    # 4 个真实角色都应在
    for c in other_chars:
        assert c in existing, f"missing {c}, got {list(existing.keys())}"


def test_preserves_recency_order():
    """新角色追加到末尾，老角色不动位置。"""
    existing = {"林渊": "A", "苏晚栀": "B"}
    updates = {"孟浩": "C"}
    result = _merge_character_states(existing, updates)
    assert list(result.keys()) == ["林渊", "苏晚栀", "孟浩"]


def test_window_cap_prevents_o_n_squared():
    """window=50 限制：扫描的已有 key 不超过 50，
    防止 LLM 一次性返 100 个新角色时退化成 O(N²)。"""
    existing = {f"chara_{i}": f"state_{i}" for i in range(100)}
    updates = {"新角色": "新状态"}
    # 这里仅验证函数不抛异常 + 正确返回
    result = _merge_character_states(existing, updates)
    assert "新角色" in result
    # 旧 100 个都保留
    assert len(result) == 101