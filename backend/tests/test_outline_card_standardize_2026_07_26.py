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
    # 清单 issue #7：shuang_type 缺值/非法值兜底为 None，保留合法值原样
    assert t["shuang_type"] is None


def test_standardize_tasks_shuang_type_validates():
    """2026-08-05 清单 issue #7：shuang_type 必须与 SHUANG_TYPES 集合对齐，
    非法值兜底为 None，合法值原样保留。beat_checker 三阶段节拍匹配表
    只认 SHUANG_TYPES.keys()，非法值会让 AC-10/11 静默失效。
    """
    tasks = [
        {"shuang_type": "打脸"},        # 合法 — 保留
        {"shuang_type": "碾压"},        # 合法 — 保留
        {"shuang_type": "竞猜"},        # 非法 — 兜底 None
        {"shuang_type": ""},            # 空串 — 兜底 None
        {"shuang_type": None},          # 本就 None — 保留
        {"shuang_type": 123},           # 非字符串 — 兜底 None
    ]
    outline_mod._standardize_tasks(tasks, start_chapter=10)
    assert tasks[0]["shuang_type"] == "打脸"
    assert tasks[1]["shuang_type"] == "碾压"
    assert tasks[2]["shuang_type"] is None
    assert tasks[3]["shuang_type"] is None
    assert tasks[4]["shuang_type"] is None
    assert tasks[5]["shuang_type"] is None


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


def test_run_outline_batches_long_arc_and_preserves_count(monkeypatch):
    """30 章长弧必须分成 3 批，最终章号连续且数量完整。"""
    calls = []

    def fake_request(_router, arc, start_chapter, _setting, _memory, prompt_suffix=""):
        count = arc["estimated_chapters"]
        calls.append((start_chapter, count))
        return ([{
            "chapter_number": start_chapter + i,
            "chapter_role": "发展",
            "chapter_goal": f"推进 Ch{start_chapter + i}",
            "ending_hook_type": "悬念钩",
            "emotion_core": ["压抑", "震惊", "爽快"][i % 3],
            "emotion_intensity": 3,
        } for i in range(count)], 0.01)

    monkeypatch.setattr(outline_mod, "_request_outline_batch", fake_request)
    monkeypatch.setattr(outline_mod, "get_active_router", lambda: object())
    tasks, cost = outline_mod.run_outline(
        {"arc_id": 1, "arc_name": "长弧", "estimated_chapters": 30},
        start_chapter=11,
        setting={},
        memory={},
    )

    assert calls == [(11, 10), (21, 10), (31, 10)]
    assert len(tasks) == 30
    assert [task["chapter_number"] for task in tasks] == list(range(11, 41))
    assert cost == pytest.approx(0.03)


def test_run_outline_rejects_short_batch_instead_of_padding(monkeypatch):
    """模型少返回章节时必须显式失败，不能用 placeholder 静默补齐。"""
    monkeypatch.setattr(outline_mod, "get_active_router", lambda: object())
    monkeypatch.setattr(
        outline_mod,
        "_request_outline_batch",
        lambda *_args, **_kwargs: ([{"chapter_number": 1}], 0.01),
    )

    with pytest.raises(RuntimeError, match="数量契约失败"):
        outline_mod.run_outline(
            {"arc_id": 1, "arc_name": "长弧", "estimated_chapters": 10},
            start_chapter=1,
            setting={},
            memory={},
        )


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


def test_shared_batch_runner_calls_standardize_helper():
    """源码静态检查:共享分批生成器必须执行任务标准化。"""
    import inspect
    src = inspect.getsource(outline_mod._run_outline_batches)
    assert "_standardize_tasks(" in src, (
        "run_outline 没用 _standardize_tasks helper —— "
        "审计修 Medium#4 是否被吃掉了?"
    )


def test_outline_and_card_share_batch_runner():
    """普通大纲与抽卡 B/C 必须共享同一分批和数量契约实现。"""
    import inspect
    outline_src = inspect.getsource(outline_mod.run_outline)
    card_src = inspect.getsource(outline_mod.run_outline_card)
    assert "_run_outline_batches(" in outline_src
    assert "_run_outline_batches(" in card_src
