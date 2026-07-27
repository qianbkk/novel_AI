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
    def no_evidence_reviser(memory, setting, state):
        return [_revision(evidence="")]

    register_reviser("no_evidence_reviser", no_evidence_reviser)
    state = _state()
    revisions = run_revisers(_memory(), _setting(), state)

    assert revisions == []
    assert state["human_pending"] == []


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
    def broken_reviser(memory, setting, state):
        if state.get("exercise_broken_reviser"):
            raise RuntimeError("reviser unavailable")
        return []

    register_reviser("broken_reviser", broken_reviser)
    result = run_revisers(_memory(), _setting(), state)

    assert result == []
    assert state["human_pending"] == []
    assert state.get("current_arc") == 2


def test_second_reviser_requires_only_contract_and_registration():
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

    assert {revision.target for revision in revisions} == {"setting", "arc_plan"}
    assert {task["payload"]["target"] for task in state["human_pending"]} == {
        "setting",
        "arc_plan",
    }


@pytest.mark.parametrize("bad_target", ["chapter", "", None])
def test_revision_target_outside_canon_is_rejected(bad_target):
    def invalid_reviser(memory, setting, state):
        if not state.get("exercise_invalid_target"):
            return []
        return [_revision(target=bad_target)]

    register_reviser(f"invalid_target_{bad_target}", invalid_reviser)
    state = _state()
    state["exercise_invalid_target"] = True

    assert run_revisers(_memory(), _setting(), state) == []
    assert state["human_pending"] == []
