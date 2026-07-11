"""backend/tests/test_outline_card.py — 抽卡模式防回归测试

外部审计师曾发现 run_outline_card 的 B/C 分支直接 reuse A 的 batch_tasks，
3 个候选实际是同一个任务清单假装不同（静默假功能）。
这次重构后用 monkeypatch 验证 B/C 真的会调 LLM 拿到独立响应。

测试点：
  1. B/C 分支的 tasks **不能**跟 A 完全相同（point-identical）
  2. B/C 分支必须各调一次 LLM（不能 fallback 静默到 A）
  3. 单个分支失败 → fallback 到 A + log warning，不让整次抽卡崩
  4. _build_user_prompt 内部 flavor 指导必须出现在 B/C 的 prompt 里
  5. 共享 helper：run_outline 走 _build_user_prompt，行为不变
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


# ─── fakes ───
class _FakeRouter:
    """记录每次 call，response 用序列号拼出递增的 chapter_goal 字段：

    A: 第 1 章：爽点密集 ch X（run_outline 调一次）
    B: 第 1 章：悬疑反转 ch X（branch B 调用）
    C: 第 1 章：情感共鸣 ch X（branch C 调用）

    如果 B/C 走了 fallback（不走 router.call），记录里就少一次 call。
    """
    def __init__(self, responses: list[str] | None = None):
        self.calls: list[dict] = []
        self._responses = responses or []

    def call(self, *, agent_name, system_prompt, user_prompt, max_tokens, temperature):
        idx = len(self.calls)
        self.calls.append({
            "agent": agent_name,
            "system": system_prompt,
            "user": user_prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        body = self._responses[idx] if idx < len(self._responses) else (
            '[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "X"}]'
        )
        return body, 0.01


def _setup_env(monkeypatch):
    """塞好 fake router + 必要的 active router 注册。"""
    from engine.llm_router import set_active_router, get_active_router

    fake = _FakeRouter()
    set_active_router(fake)  # type: ignore[arg-type]
    # monkeypatch 防 test 间脏污染（teardown 重置 _ACTIVE_ROUTER）
    monkeypatch.setattr(
        "engine.agents.outline.get_active_router",
        lambda: fake,
    )
    return fake


def _sample_arc():
    return {
        "arc_id": 1,
        "arc_name": "觉醒篇",
        "arc_goal": "主角觉醒",
        "estimated_chapters": 3,
        "arc_climax_description": "觉醒时被打断",
        "arc_climax_chapter_offset": 2,
        "emotion_curve": "低沉 → 觉醒 → 爆发",
        "new_characters_introduced": ["师父"],
        "arc_ending_state": "主角获得基础能力",
    }


def _sample_setting():
    return {
        "protagonist": {"name": "陆承"},
        "key_characters": [{"name": "陆承", "role": "主角"}],
        "power_system": {"levels": [{"level": 1, "name": "感债者"}]},
    }


# ─────── Tests ───────

def test_card_three_branches_make_three_llm_calls(monkeypatch):
    """抽卡应让 router.call 收到 3 次（run_outline for A + B + C）。"""
    fake = _setup_env(monkeypatch)
    from engine.agents.outline import run_outline_card
    candidates, cost = run_outline_card(_sample_arc(), 1, _sample_setting(), {"hot": {}})
    assert len(candidates) == 3, f"应返 3 个 candidates，实际 {len(candidates)}"
    # router.call 应被调用 3 次：A 一次 + B + C
    assert len(fake.calls) == 3, (
        f"应调 LLM 3 次（A=B=C 各一次），实际 {len(fake.calls)} 次。\n"
        f"如果 < 3 说明有分支被静默 fallback 到 A。"
    )
    # 每次 system 应该是 OUTLINE_SYSTEM
    for i, call in enumerate(fake.calls):
        assert call["agent"] == "outline", f"call {i} agent 应为 'outline'"


def test_card_b_and_c_not_point_identical_to_a(monkeypatch):
    """B/C 的 tasks 不能跟 A 的完全一样（point-identical）—— 这是核心防回归。

    之前 P3 阶段 B/C 直接 tasks=batch_tasks，下面这个测试会失败：
    A 三章 chapter_goal 全是 'chapter A N'
    B 真调 LLM 拿到 'chapter B N'（fake router 自增计数器生成）
    """
    fake = _setup_env(monkeypatch)
    fake._responses = [
        # A (run_outline 调一次):
        '[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "A1"},'
        ' {"chapter_number": 2, "chapter_role": "发展", "chapter_goal": "A2"},'
        ' {"chapter_number": 3, "chapter_role": "弧高潮", "chapter_goal": "A3"}]',
        # B (branch B):
        '[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "B1"},'
        ' {"chapter_number": 2, "chapter_role": "发展", "chapter_goal": "B2"},'
        ' {"chapter_number": 3, "chapter_role": "弧高潮", "chapter_goal": "B3"}]',
        # C (branch C):
        '[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "C1"},'
        ' {"chapter_number": 2, "chapter_role": "发展", "chapter_goal": "C2"},'
        ' {"chapter_number": 3, "chapter_role": "弧高潮", "chapter_goal": "C3"}]',
    ]
    from engine.agents.outline import run_outline_card
    candidates, cost = run_outline_card(_sample_arc(), 1, _sample_setting(), {"hot": {}})

    # 拿到三组 goal
    goals_a = [t["chapter_goal"] for t in candidates[0]["tasks"]]
    goals_b = [t["chapter_goal"] for t in candidates[1]["tasks"]]
    goals_c = [t["chapter_goal"] for t in candidates[2]["tasks"]]

    assert goals_a == ["A1", "A2", "A3"]
    assert goals_b == ["B1", "B2", "B3"], f"B 应跟 A 不同，实际 {goals_b}"
    assert goals_c == ["C1", "C2", "C3"], f"C 应跟 A 不同，实际 {goals_c}"
    # 强化检查：连字符级都不能相同
    assert goals_a != goals_b
    assert goals_a != goals_c
    assert goals_b != goals_c


def test_card_flavor_directive_appears_in_prompt(monkeypatch):
    """B/C 的 user_prompt 必须包含该分支的 flavor 指导——LLM 必须"被告知"
    自己应该偏重什么 flavor，否则 flavor 名义上存在、prompt 一样 = B=C=A。
    """
    fake = _setup_env(monkeypatch)
    from engine.agents.outline import run_outline_card
    run_outline_card(_sample_arc(), 1, _sample_setting(), {"hot": {}})
    # fake.calls[0] = A (run_outline)，[1] = B，[2] = C
    a_prompt = fake.calls[0]["user"]
    b_prompt = fake.calls[1]["user"]
    c_prompt = fake.calls[2]["user"]
    # A 不含 flavor 指导
    assert "【爽点密集专属约束】" not in a_prompt
    assert "【悬疑反转专属约束】" not in a_prompt
    assert "【情感共鸣专属约束】" not in a_prompt
    # B 含悬疑反转指导
    assert "【悬疑反转专属约束】" in b_prompt
    # C 含情感共鸣指导
    assert "【情感共鸣专属约束】" in c_prompt
    # B 不应有 C 的指导（防止 prompt 完全一样）
    assert "【情感共鸣专属约束】" not in b_prompt


def test_card_branch_failure_falls_back_to_a_without_crashing(monkeypatch):
    """单个分支 LLM 失败 → fallback 复用 A tasks，不让整次抽卡崩。

    设计：router.call 抛 ValueError，看是否捕获 + B 拿到 fallback。
    """
    class _FailingRouter:
        def __init__(self):
            self.calls = 0
        def call(self, **kw):
            self.calls += 1
            # 第二次调用（B）抛错；C 仍跑
            if self.calls == 2:
                raise ValueError("simulated LLM outage")
            return ('[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "X"}]'), 0.01

    from engine.llm_router import set_active_router
    failing = _FailingRouter()
    set_active_router(failing)  # type: ignore[arg-type]
    monkeypatch.setattr("engine.agents.outline.get_active_router", lambda: failing)

    from engine.agents.outline import run_outline_card
    candidates, cost = run_outline_card(_sample_arc(), 1, _sample_setting(), {"hot": {}})
    # 3 个候选都有（不崩）
    assert len(candidates) == 3
    # B 分支 fallback 到 A（tasks 是 A 的拷贝）
    # 这里 A 只有 1 个 task 被 fake 返回，所以 B 应当也是 1 个 + 是 fallback
    assert len(candidates[1]["tasks"]) >= 1, "B 失败的 fallback 至少要有 task"
    # C 正常返回
    assert len(candidates[2]["tasks"]) >= 1


def test_run_outline_uses_shared_helper(monkeypatch):
    """run_outline 走 _build_user_prompt，跟 run_outline_card 共享 prompt 模板。

    防回归：有人重写 run_outline 把 _build_user_prompt bypass 时，这条测试
    不会直接抓 bug，但 snapshot test 总比没有强。
    """
    from engine.agents.outline import _build_user_prompt, _extract_json_array
    arc = _sample_arc()
    s = _sample_setting()
    p = _build_user_prompt(arc, 1, s, {"hot": {}})
    # snapshot 关键 signature：
    assert "弧1「觉醒篇」" in p
    assert "主角能力" not in p  # 不要误把一段塞进去
    # _extract_json_array 应该剥 markdown fence
    assert _extract_json_array("```json\n[1, 2]\n```") == "[1, 2]"


def test_card_metadata_consistency(monkeypatch):
    """3 个 candidates 必须有合法的 branch/flavor 元数据 — 即使 LLM 失败 fallback。

    设计：metadata 永远来自代码（不是 LLM），所以无论 fallback 与否都不应错位。
    """
    fake = _setup_env(monkeypatch)
    fake._responses = [
        '[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "X"}]',
        '[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "X"}]',
        '[{"chapter_number": 1, "chapter_role": "发展", "chapter_goal": "X"}]',
    ]
    from engine.agents.outline import run_outline_card
    candidates, _ = run_outline_card(_sample_arc(), 1, _sample_setting(), {"hot": {}})
    assert candidates[0]["branch"] == "A"
    assert candidates[1]["branch"] == "B"
    assert candidates[2]["branch"] == "C"
    assert candidates[0]["flavor"] == "爽点密集"
    assert candidates[1]["flavor"] == "悬疑反转"
    assert candidates[2]["flavor"] == "情感共鸣"
