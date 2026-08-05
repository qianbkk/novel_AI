"""test_run_outline_talk_2026_08_05.py

2026-08-05 清单 P15 修复：run_outline_talk 不再返 hardcoded 三个模板问题。

覆盖三条路径：
  1. router 在线 + LLM 返回合规 JSON → 用 LLM 生成的 questions
  2. router 在线 + LLM 返回非 JSON → 走 arc-data 驱动的 fallback
  3. router 不可用（None）→ fallback（这条路径以前就存在，但回的是 hardcoded 模板）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _set_active_router(router):
    from engine.llm_router import set_active_router
    set_active_router(router)


def _patch_run_outline(monkeypatch):
    """跳过 run_outline LLM 调用：talk 模式复用它但我们只关心 questions 部分。"""
    import engine.agents.outline as om
    monkeypatch.setattr(om, "run_outline", lambda arc, start, setting, memory: ([], 0.0))


def _build_arc(arc_id=99, name="暗流", goal="找到内鬼"):
    return {"arc_id": arc_id, "arc_name": name, "arc_goal": goal}


def _build_setting(mc_name="沈岚", world="远环殖民地"):
    return {
        "protagonist": {"name": mc_name},
        "power_system": {"name": "遗迹共鸣"},
        "world_setting": {"surface_world_name": world},
    }


def test_talk_uses_llm_json_when_parseable(monkeypatch):
    """router 在线 + LLM 回 JSON 时，questions 直接用 LLM 输出。"""
    from engine.agents.outline import run_outline_talk
    router = MagicMock()
    router.call.return_value = (
        '{"questions":[{"qid":"q1","question":"沈岚的追击到什么程度收手？","context":"deadline压力"}]}',
        0.01,
    )
    _set_active_router(router)
    _patch_run_outline(monkeypatch)

    result, cost = run_outline_talk(_build_arc(), 1, _build_setting(), {"hot": {}})

    assert cost >= 0.01, f"router.call 应该被调用并计费；cost={cost}"
    qs = result["questions"]
    assert len(qs) == 1, f"应保留 LLM 唯一一条，got {len(qs)}"
    assert qs[0]["qid"] == "q1"
    assert "沈岚" in qs[0]["question"]


def test_talk_falls_back_to_arc_data_when_llm_unparseable(monkeypatch):
    """router 在线但 LLM 回非 JSON 时，走 arc-data 驱动的 fallback。"""
    from engine.agents.outline import run_outline_talk
    router = MagicMock()
    router.call.return_value = ("not json at all", 0.005)
    _set_active_router(router)
    _patch_run_outline(monkeypatch)

    arc = _build_arc(arc_id=42, name="巅峰", goal="突破境界")
    setting = _build_setting(mc_name="顾舟", world="云州")
    result, cost = run_outline_talk(arc, 1, setting, {"hot": {}})

    qs = result["questions"]
    # fallback 必须真的引用本弧数据，而不是泛泛模板
    assert len(qs) >= 1
    blob = " ".join(q["question"] for q in qs) + " " + " ".join(q.get("context", "") for q in qs)
    assert "顾舟" in blob or "巅峰" in blob or "突破境界" in blob, (
        f"fallback 应该引用 arc/setting 真数据；实际={blob[:120]}"
    )


def test_talk_handles_list_form_response(monkeypatch):
    """LLM 直接返 list（不是 dict with 'questions' key）时也能吃下。"""
    from engine.agents.outline import run_outline_talk
    router = MagicMock()
    router.call.return_value = (
        '[{"question":"q1文本","context":"c1"},{"question":"q2文本","context":"c2"}]',
        0.01,
    )
    _set_active_router(router)
    _patch_run_outline(monkeypatch)

    result, _ = run_outline_talk(_build_arc(), 1, _build_setting(), {"hot": {}})
    qs = result["questions"]
    assert len(qs) == 2
    assert qs[0]["question"] == "q1文本"


def test_talk_fallback_when_router_none(monkeypatch):
    """router 没装（test/unit 环境）时仍应返 3 条真数据驱动的问题，不抛异常。"""
    from engine.llm_router import reset_active_router, set_active_router
    reset_active_router()  # 显式清空
    _patch_run_outline(monkeypatch)

    from engine.agents.outline import run_outline_talk
    arc = _build_arc(arc_id=7, name="破局", goal="撕开封印")
    setting = _build_setting(mc_name="林渊", world="云州")
    result, cost = run_outline_talk(arc, 1, setting, {"hot": {}})

    qs = result["questions"]
    assert len(qs) >= 1, "router 缺失必须仍回 fallback"
    blob = " ".join(q["question"] for q in qs)
    assert "林渊" in blob or "破局" in blob or "云州" in blob, (
        f"fallback 必须引用 arc 真数据，不能用空弧名；blob={blob[:120]}"
    )
