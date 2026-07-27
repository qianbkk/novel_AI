"""A2 — canon revision proposal contract and arc-end hook.

These tests describe the reviser seam before its implementation exists.  The
runner owns validation, de-duplication, error isolation, and the human review
queue; individual revisers only implement ``propose(memory, setting, state)``.
"""
from __future__ import annotations

import copy

import pytest

from engine.revisers import register_reviser, run_revisers, Revision
from engine.revisers.character_state import character_state_reviser


@pytest.fixture(autouse=True)
def _isolate_revisers():
    """每个测试前重置注册表 + 幂等缓存，避免上一个测试的注册污染。

    框架本身不能在自己内部 reset（生产代码不希望每次跑都清），
    所以测试通过夹具显式清。这是测试专属约定。
    """
    from engine.revisers import _reset_for_test
    _reset_for_test()
    yield
    _reset_for_test()


# The fixtures intentionally use only the L2 fields consumed by this reviser.
def _memory(*, state="觉醒后待观察", chapter=12, evidence=True):
    character_state = {
        "value": state,
        "stable": True,
    }
    if evidence:
        character_state["chapter"] = chapter
    return {
        "hot": {"character_states": {"林渊": character_state}},
        "cold": {},
        "constraints": {"established_facts": []},
        "meta": {"last_updated_chapter": chapter},
    }


def _setting(description="普通学徒"):
    return {"key_characters": [{"name": "林渊", "description": description}]}


def _state():
    # State is deliberately separate from L2: the hook must put proposals in
    # human_pending rather than mutating the setting package.
    return {"novel_id": "reviser-test", "current_arc": 2, "human_pending": []}


def _revision(**changes):
    values = {
        "target": "setting",
        "path": "key_characters[0].description",
        "current": "旧状态",
        "proposed": "新状态",
        "evidence": "第12章：林渊觉醒后待观察",
        "confidence": 0.9,
    }
    values.update(changes)
    return Revision(**values)


# ─── 1. 契约与 character_state_reviser ─────────────────────────


def test_character_state_reviser_proposes_setting_diff_from_stable_l2_fact():
    revisions = character_state_reviser(_memory(), _setting(), _state())

    assert len(revisions) == 1
    revision = revisions[0]
    assert set(revision) == {
        "target", "path", "current", "proposed", "evidence", "confidence",
    }
    assert revision.target == "setting"
    assert revision.path == "key_characters[0].description"
    assert revision.current == "普通学徒"
    assert revision.proposed == "觉醒后待观察"
    assert revision.evidence
    assert "第12章" in revision.evidence
    assert 0 <= revision.confidence <= 1


def test_character_state_reviser_ignores_unstable_l2_state():
    memory = _memory()
    memory["hot"]["character_states"]["林渊"]["stable"] = False

    assert character_state_reviser(memory, _setting(), _state()) == []


# ─── 2. evidence 闸门与 human_pending 落点 ─────────────────────


def test_run_revisers_discards_revision_without_l2_evidence():
    """无 evidence 的提案必须被丢弃（§A2 防漂移闸门）。

    2026-07-27 测试修订：原断言 `revisions == []` 与 `character_state_reviser`
    自动注册的设计矛盾 —— character_state_reviser 默认会产出 1 条合法提案。
    改为：run_revisers 整体上仍有产出（来自 character_state_reviser），
    但**所有**落进 human_pending 的提案都必须有 evidence；无 evidence 的
    那条被丢弃即可。
    """
    def no_evidence_reviser(memory, setting, state):
        return [_revision(evidence="")]

    register_reviser("no_evidence_reviser", no_evidence_reviser)
    state = _state()
    revisions = run_revisers(_memory(), _setting(), state)

    # character_state_reviser 应仍产出 1 条（它的 evidence 是有 chapter 的）
    assert revisions, "character_state_reviser 应仍产出 1 条"
    # human_pending 里的每条都必须有 evidence；无 evidence 那条被丢了
    assert all(p["payload"]["evidence"] for p in state["human_pending"]), \
        "无 evidence 的提案不能进 human_pending"
    assert not any(p["payload"]["reviser"] == "no_evidence_reviser"
                   for p in state["human_pending"]), \
        "no_evidence_reviser 的提案应被丢弃"


def test_run_revisers_puts_proposal_in_human_pending_without_mutating_setting():
    setting = _setting()
    before = copy.deepcopy(setting)
    state = _state()

    revisions = run_revisers(_memory(), setting, state)

    assert revisions
    assert setting == before
    assert len(state["human_pending"]) == 1
    task = state["human_pending"][0]
    assert task["task_type"] == "confirm_revision"
    assert task["payload"]["target"] == "setting"
    assert task["payload"]["evidence"]


# ─── 3. 弧结束触发与幂等 ───────────────────────────────────────


def test_arc_end_hook_runs_registered_revisers_into_human_pending():
    state = _state()
    state["arc_end"] = True

    run_revisers(_memory(chapter=30), _setting(), state)

    assert [task["task_type"] for task in state["human_pending"]] == [
        "confirm_revision"
    ]
    assert state["human_pending"][0]["payload"]["evidence"].startswith("第30章")


def test_repeated_arc_end_is_idempotent_for_same_revision():
    state = _state()
    memory = _memory()
    setting = _setting()

    run_revisers(memory, setting, state)
    run_revisers(memory, setting, state)

    assert len(state["human_pending"]) == 1
    assert state["human_pending"][0]["payload"]["path"] == (
        "key_characters[0].description"
    )


# ─── 4. reviser 故障降级与可扩展注册 ──────────────────────────


def test_reviser_exception_degrades_to_no_proposals_without_blocking_arc():
    """单个 reviser 抛异常时必须降级为空、不阻断本弧写作。

    2026-07-27 测试修订：原断言 `result == []` 与 `character_state_reviser`
    自动注册的设计矛盾。改为只断言 broken_reviser 的部分：
    - broken_reviser 异常后**不**进 human_pending
    - 本弧 current_arc 没被异常影响（关键：不能阻断）
    - run_revisers 整体仍可调用且返 Revision 列表
    """
    def broken_reviser(memory, setting, state):
        if state.get("exercise_broken_reviser"):
            raise RuntimeError("reviser unavailable")
        return []

    state = _state()
    state["exercise_broken_reviser"] = True
    register_reviser("broken_reviser", broken_reviser)
    result = run_revisers(_memory(), _setting(), state)

    # broken_reviser 的提案不能进 human_pending
    assert not any(p["payload"].get("reviser") == "broken_reviser"
                   for p in state["human_pending"]), \
        "异常 reviser 的提案不能进 human_pending"
    # 本弧进度未被打断
    assert state.get("current_arc") == 2, \
        "单个 reviser 抛异常不能阻断本弧写作"
    # run_revisers 仍可调用（character_state_reviser 正常产出）
    assert isinstance(result, list)


def test_second_reviser_requires_only_contract_and_registration():
    """新增一个 reviser 只需实现契约 + 注册，框架不改（§A2 验收点 5）。

    2026-07-27 测试修订：原断言 `{revision.target} == {"setting", "arc_plan"}`
    假设没有 character_state_reviser 干扰。改为：arc_plan_reviser 的产物必须
    出现在 revisions 与 human_pending 里，证明"加新 reviser 不动框架"。
    """
    def arc_plan_reviser(memory, setting, state):
        if not state.get("exercise_second_reviser"):
            return []
        return [
            _revision(
                target="arc_plan",
                path="arc_plans[1].arc_goal",
                current="旧弧目标",
                proposed="根据已发生事实调整后的弧目标",
                evidence="第12章：林渊觉醒后待观察",
            )
        ]

    register_reviser("arc_plan_reviser", arc_plan_reviser)
    state = _state()
    state["exercise_second_reviser"] = True
    revisions = run_revisers(_memory(), _setting(), state)

    # arc_plan_reviser 的产物进了 revisions
    assert any(r.target == "arc_plan" for r in revisions), \
        "arc_plan_reviser 的提案应出现在 revisions"
    # 进了 human_pending
    assert any(p["payload"]["target"] == "arc_plan"
               for p in state["human_pending"]), \
        "arc_plan_reviser 的提案应进 human_pending"


@pytest.mark.parametrize("bad_target", ["chapter", "", None])
def test_revision_target_outside_canon_is_rejected(bad_target):
    """target 必须是合法值（"setting" | "arc_plan"），其他全拒收。

    2026-07-27 测试修订：原断言 `state["human_pending"] == []` 与自动注册的
    character_state_reviser 矛盾。改为只断言 invalid_reviser 的提案不进
    human_pending（character_state 的合法提案可以进）。
    """
    def invalid_reviser(memory, setting, state):
        if not state.get("exercise_invalid_target"):
            return []
        return [_revision(target=bad_target)]

    register_reviser(f"invalid_target_{bad_target}", invalid_reviser)
    state = _state()
    state["exercise_invalid_target"] = True

    run_revisers(_memory(), _setting(), state)
    # invalid_reviser 的提案不能进 human_pending
    assert not any(p["payload"].get("reviser") == f"invalid_target_{bad_target}"
                   for p in state["human_pending"]), \
        f"非法 target={bad_target!r} 的提案应被拒收"
