"""backend/tests/test_outline_phase2_foreshadow.py — 二期大纲细纲体系回归测试

覆盖 4 个目标：
  1. normalize_foreshadow_ops 兼容 LLM 返回的 dict/str/null
  2. plant_seeds_from_tasks 幂等去重，灌入 L2.constraints.foreshadowing_planted
  3. format_foreshadow_ops_for_prompt 按 op 分组渲染中文片段
  4. OUTLINE_SYSTEM 新字段（core_conflict / emotion_shift / plot_progression /
     foreshadowing_ops）在 prompt 中有要求
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


# ────────────────────────────────────────────────────────────
# 1. normalize 兼容
# ────────────────────────────────────────────────────────────

def test_normalize_dict_input():
    from engine.agents.foreshadow_helper import normalize_foreshadow_ops
    ops = [
        {"op": "plant", "desc": "陈昭的疤", "target_chapter": 50},
        {"op": "reinforce", "desc": "再次提到周先生", "target_chapter": None},
        {"op": "resolve", "desc": "揭疤真相", "target_chapter": 80},
    ]
    out = normalize_foreshadow_ops(ops)
    assert len(out) == 3
    assert out[0]["op"] == "plant"
    assert out[1]["op"] == "reinforce"
    assert out[2]["op"] == "resolve"
    assert out[0]["target_chapter"] == 50


def test_normalize_string_input():
    """老 LLM 可能只返字符串。"""
    from engine.agents.foreshadow_helper import normalize_foreshadow_ops
    ops = ["陈昭的疤", "再次提到周先生"]
    out = normalize_foreshadow_ops(ops)
    assert len(out) == 2
    assert all(o["op"] == "plant" for o in out)


def test_normalize_invalid_op_inferred():
    """未识别的 op → 启发式推断（plant/reinforce/resolve）"""
    from engine.agents.foreshadow_helper import normalize_foreshadow_ops
    ops = [
        {"op": "foo", "desc": "回收周先生的债"},
        {"op": "bar", "desc": "强化陈昭对系统的不信任"},
        {"op": "baz", "desc": "苏晚萤的秘密"},
    ]
    out = normalize_foreshadow_ops(ops)
    assert out[0]["op"] == "resolve"
    assert out[1]["op"] == "reinforce"
    assert out[2]["op"] == "plant"


def test_normalize_drops_empty_desc():
    from engine.agents.foreshadow_helper import normalize_foreshadow_ops
    ops = [{"op": "plant", "desc": ""}, {"op": "plant", "desc": "valid"}]
    out = normalize_foreshadow_ops(ops)
    assert len(out) == 1
    assert out[0]["desc"] == "valid"


def test_normalize_none_returns_empty():
    from engine.agents.foreshadow_helper import normalize_foreshadow_ops
    assert normalize_foreshadow_ops(None) == []
    assert normalize_foreshadow_ops([]) == []


# ────────────────────────────────────────────────────────────
# 2. plant_seeds_from_tasks 幂等
# ────────────────────────────────────────────────────────────

def test_plant_seeds_from_tasks_basic():
    from engine.agents.foreshadow_helper import plant_seeds_from_tasks
    tasks = [
        {"chapter_number": 1, "foreshadowing_ops": [
            {"op": "plant", "desc": "伏笔A", "target_chapter": 10},
            {"op": "reinforce", "desc": "伏笔X"},
        ]},
        {"chapter_number": 2, "foreshadowing_ops": [
            {"op": "plant", "desc": "伏笔B", "target_chapter": 20},
        ]},
    ]
    fake_l2 = {"hot": {}, "cold": {}, "constraints": {"foreshadowing_planted": []}, "meta": {}}
    with patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        n = plant_seeds_from_tasks(tasks, "test_novel")
        # 仅 plant 操作被灌入；reinforce 不算新种子
        assert n == 2
        planted = fake_l2["constraints"]["foreshadowing_planted"]
        assert {p["desc"] for p in planted} == {"伏笔A", "伏笔B"}
        assert planted[0]["planted_at_chapter"] == 1
        assert planted[1]["target_chapter"] == 20


def test_plant_seeds_idempotent():
    from engine.agents.foreshadow_helper import plant_seeds_from_tasks
    tasks = [{"chapter_number": 1, "foreshadowing_ops": [
        {"op": "plant", "desc": "伏笔A"},
    ]}]
    fake_l2 = {"hot": {}, "cold": {}, "constraints": {"foreshadowing_planted": []}, "meta": {}}
    with patch("engine.memory.manager.get_l2", return_value=fake_l2), \
         patch("engine.memory.manager.save_l2"):
        n1 = plant_seeds_from_tasks(tasks, "x")
        n2 = plant_seeds_from_tasks(tasks, "x")
        assert n1 == 1
        assert n2 == 0


# ────────────────────────────────────────────────────────────
# 3. format for prompt
# ────────────────────────────────────────────────────────────

def test_format_for_prompt_groups_by_op():
    from engine.agents.foreshadow_helper import format_foreshadow_ops_for_prompt
    task = {"chapter_number": 5, "foreshadowing_ops": [
        {"op": "plant", "desc": "埋设陈昭的疤"},
        {"op": "reinforce", "desc": "强化周先生的神秘感"},
        {"op": "resolve", "desc": "揭示王栋的转账"},
    ]}
    out = format_foreshadow_ops_for_prompt([task])
    assert "埋设" in out
    assert "强化" in out
    assert "回收" in out or "揭示" in out
    assert "Ch5" in out


def test_format_for_prompt_empty_returns_empty():
    from engine.agents.foreshadow_helper import format_foreshadow_ops_for_prompt
    assert format_foreshadow_ops_for_prompt([]) == ""
    assert format_foreshadow_ops_for_prompt([{"foreshadowing_ops": []}]) == ""


# ────────────────────────────────────────────────────────────
# 4. OUTLINE_SYSTEM 含新字段
# ────────────────────────────────────────────────────────────

def test_outline_system_prompt_has_new_fields():
    from engine.agents.outline import OUTLINE_SYSTEM
    for field in ("core_conflict", "emotion_shift", "plot_progression",
                  "foreshadowing_ops", "plant", "reinforce", "resolve"):
        assert field in OUTLINE_SYSTEM, f"{field} 应在 OUTLINE_SYSTEM 中"