"""test_outline_card_standardize_2026_07_26.py

审计修 Medium#4 回归测试。

历史问题:run_outline_card B/C 分支只 json.loads(resp) 后直接塞进 candidates,
没经过 stakes/emotion/章号契约等所有标准化。选中 B/C 时下游拿到的 task 可能
缺字段、章号不连续、narrative_thread 无效。

修法:抽出 _standardize_tasks helper,run_outline + run_outline_card B/C
两处共用同一份标准化。

本测试覆盖:
- _standardize_tasks helper 直接测试
- run_outline_card B/C 分支标准化生效(field defaults + 章节重编号)
- run_outline_card 与 run_outline 走同一 helper(行内调用引用一致)
"""
from __future__ import annotations

import json
import pytest

from engine.agents import outline as outline_mod


# ─── 1. _standardize_tasks helper 直接测试 ─────────────────────────


def test_standardize_tasks_normalizes_all_methodology_fields():
    """_standardize_tasks 必须把所有方法论字段兜底到合法值。"""
    tasks = [
        {
            "chapter_number": 999,  # 异常章号,会被重编号
            "chapter_role": "发展",
            "ending_hook_type": "非法钩子",  # 应被兜底为"悬念钩"
            "shuang_type": "打脸",
            "stakes": {"if_lose": ["失去"], "if_win": ["获得"]},
            "dilemma": {"option_a": "A", "option_b": "B"},  # 缺 both_cost
            "narrative_thread": "非法值",  # 应被兜底为 "main"
            "info_asymmetry": {"reader_knows": ["x"]},  # 缺 2 子字段
            "anchor_to": 0,  # 应被兜底为 None
            "emotion_core": "未知情绪",  # 应被兜底为"压抑"
            "emotion_intensity": 99,  # 应被兜底为 3
        },
    ]
    outline_mod._standardize_tasks(tasks, start_chapter=5)

    t = tasks[0]
    # 章号契约:从 5 开始连续
    assert t["chapter_number"] == 5
    # 钩子兜底
    assert t["ending_hook_type"] == "悬念钩"
    # stakes 不变
    assert t["stakes"] == {"if_lose": ["失去"], "if_win": ["获得"]}
    # dilemma 补 both_cost
    assert t["dilemma"] == {"option_a": "A", "option_b": "B", "both_cost": ""}
    # narrative_thread 兜底
    assert t["narrative_thread"] == "main"
    # info_asymmetry 补子字段
    assert t["info_asymmetry"]["reader_knows"] == ["x"]
    assert t["info_asymmetry"]["protagonist_knows"] == []
    # anchor_to 兜底
    assert t["anchor_to"] is None
    # emotion_core 兜底
    assert t["emotion_core"] == "压抑"
    # emotion_intensity 兜底
    assert t["emotion_intensity"] == 3


def test_standardize_tasks_continuous_chapter_numbers():
    """LLM 返回的章号会被强制从 start_chapter 连续重编号。"""
    tasks = [{"chapter_number": n} for n in [1, 5, 10]]  # 乱的
    outline_mod._standardize_tasks(tasks, start_chapter=42)
    assert [t["chapter_number"] for t in tasks] == [42, 43, 44]


def test_standardize_tasks_handles_missing_fields_gracefully():
    """task 完全空字典也不崩,所有字段都有默认值。"""
    tasks = [{}]
    outline_mod._standardize_tasks(tasks, start_chapter=1)
    t = tasks[0]
    assert t["chapter_number"] == 1
    assert t["ending_hook_type"] == "悬念钩"
    assert t["stakes"] is None
    assert t["dilemma"] is None
    assert t["narrative_thread"] == "main"
    assert t["info_asymmetry"] is None
    assert t["anchor_to"] is None
    assert t["emotion_core"] == "压抑"
    assert t["emotion_intensity"] == 3


# ─── 2. run_outline_card B/C 分支走同一 helper ─────────────────────────


def test_run_outline_card_standardizes_b_c_branches(monkeypatch):
    """run_outline_card B/C 分支必须调 _standardize_tasks。

    通过 mock router 让 B/C 分支拿到"未标准化"的任务清单(章号乱/字段缺),
    然后断言返回的 candidates 中 B/C 的 tasks 是标准化后的。
    """
    # 模拟 run_outline 的结果(A 分支用)
    a_tasks = [
        {
            "chapter_number": 1, "chapter_role": "铺垫", "chapter_goal": "t",
            "ending_hook_type": "悬念钩", "shuang_type": "碾压",
            "emotion_core": "压抑", "emotion_intensity": 3,
        },
    ]
    # 模拟 B/C 分支 LLM 返回的"未标准化"任务清单
    bc_tasks_raw = [
        {
            "chapter_number": 999,  # 错的章号
            "chapter_role": "发展", "chapter_goal": "B",
            "ending_hook_type": "非法钩子",  # 错的钩子
            "shuang_type": "打脸",
            "stakes": {"if_lose": ["x"], "if_win": ["y"]},
            "narrative_thread": "未知线",  # 错的线
            "emotion_core": "非法情绪",  # 错的情绪
            "emotion_intensity": 99,
        },
    ]

    # 拦截 router.call 让 B/C 都返回 bc_tasks_raw(JSON 字符串)
    call_count = {"n": 0}
    def fake_router_call(*args, **kwargs):
        call_count["n"] += 1
        return (json.dumps(bc_tasks_raw, ensure_ascii=False), 0.05)

    # 拦截 run_outline 让它直接返回 a_tasks(避免真 LLM)
    monkeypatch.setattr(outline_mod, "run_outline", lambda *a, **k: (a_tasks, 0.0))
    monkeypatch.setattr(outline_mod, "get_active_router", lambda: None)
    monkeypatch.setattr(outline_mod, "LLMRouter", lambda: type(
        "FakeRouter", (), {"call": staticmethod(fake_router_call)}
    )())

    arc = {"arc_id": 1, "arc_name": "测试弧", "estimated_chapters": 1}
    setting = {"protagonist": {"name": "林渊"}, "key_characters": [], "power_system": {}}
    memory = {"hot": {"active_threads": [], "inventory": []}}

    candidates, cost = outline_mod.run_outline_card(arc, 5, setting, memory)

    # 应该有 3 个分支(A + B + C)
    assert len(candidates) == 3
    branch_names = [c["branch"] for c in candidates]
    assert branch_names == ["A", "B", "C"]

    # B 分支的 tasks 应已标准化(章号重排 + 字段兜底)
    b_tasks = candidates[1]["tasks"]
    assert b_tasks[0]["chapter_number"] == 5  # 从 start_chapter=5 开始
    assert b_tasks[0]["ending_hook_type"] == "悬念钩"
    assert b_tasks[0]["narrative_thread"] == "main"
    assert b_tasks[0]["emotion_core"] == "压抑"
    assert b_tasks[0]["emotion_intensity"] == 3
    assert b_tasks[0]["stakes"] == {"if_lose": ["x"], "if_win": ["y"]}

    # C 分支同 B
    c_tasks = candidates[2]["tasks"]
    assert c_tasks[0]["chapter_number"] == 5
    assert c_tasks[0]["ending_hook_type"] == "悬念钩"
    assert c_tasks[0]["narrative_thread"] == "main"

    # 关键断言:router 被调了 2 次(B + C,A 用 run_outline 复用)
    assert call_count["n"] == 2


def test_run_outline_card_bc_chapters_renumbered_when_start_not_one(monkeypatch):
    """start_chapter != 1 时,B/C 分支的章号也会从 start_chapter 开始连续。"""
    a_tasks = [{"chapter_number": 10, "chapter_role": "t", "chapter_goal": "t"}]
    bc_raw = [{"chapter_number": 1, "chapter_role": "t", "chapter_goal": "B"}]

    monkeypatch.setattr(outline_mod, "run_outline", lambda *a, **k: (a_tasks, 0.0))

    def fake_router_call(*args, **kwargs):
        return (json.dumps(bc_raw, ensure_ascii=False), 0.01)
    monkeypatch.setattr(outline_mod, "get_active_router", lambda: None)
    monkeypatch.setattr(outline_mod, "LLMRouter", lambda: type(
        "FakeRouter", (), {"call": staticmethod(fake_router_call)}
    )())

    arc = {"arc_id": 2, "arc_name": "测试", "estimated_chapters": 1}
    setting = {"protagonist": {"name": "x"}, "key_characters": [], "power_system": {}}
    memory = {"hot": {"active_threads": [], "inventory": []}}

    candidates, _ = outline_mod.run_outline_card(arc, start_chapter=42, setting=setting, memory=memory)
    b_first_chapter = candidates[1]["tasks"][0]["chapter_number"]
    c_first_chapter = candidates[2]["tasks"][0]["chapter_number"]
    assert b_first_chapter == 42
    assert c_first_chapter == 42


# ─── 3. run_outline 与 run_outline_card 共享同一 helper(源码静态检查) ─────────────────────────


def test_run_outline_calls_standardize_helper():
    """源码静态检查:run_outline 必须调 _standardize_tasks。"""
    import inspect
    src = inspect.getsource(outline_mod.run_outline)
    assert "_standardize_tasks(" in src, (
        "run_outline 没用 _standardize_tasks helper —— "
        "审计修 Medium#4 是否被吃掉了?"
    )


def test_run_outline_card_calls_standardize_helper():
    """源码静态检查:run_outline_card B/C 分支必须调 _standardize_tasks。"""
    import inspect
    src = inspect.getsource(outline_mod.run_outline_card)
    assert "_standardize_tasks(" in src, (
        "run_outline_card B/C 分支没用 _standardize_tasks —— "
        "审计修 Medium#4 是否被吃掉了?"
    )