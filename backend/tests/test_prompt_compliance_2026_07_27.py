"""test_prompt_compliance_2026_07_27.py

§A5 先测量 —— writer prompt 指令遵循率工具的测试。

目的：构造可自动断言的约束（必须包含/必须回收/不得新增），让
build_chapter_constraints + score_compliance 跑起来。**不改 writer prompt**，
只验证测量代码本身正确 —— 真实 LLM 数据要等 Phase B。
"""
from __future__ import annotations

import pytest

from engine.tools.prompt_compliance import (
    ComplianceReport,
    Constraint,
    build_chapter_constraints,
    names_introduced,
    score_compliance,
)


def _task(**over):
    base = {
        "chapter_number": 7,
        "main_characters": ["林渊", "苏晚栀"],
        "chapter_goal": "在深渊回廊与凯恩对峙，夺回家族徽记",
    }
    base.update(over)
    return base


def _setting():
    return {
        "key_characters": [
            {"name": "林渊", "role": "主角"},
            {"name": "苏晚栀", "role": "女主"},
            {"name": "凯恩", "role": "反派"},
        ],
        "world_setting": {"surface_world_name": "云州"},
    }


# ─── 1. 约束派生 ─────────────────────────

def test_must_include_main_character_is_added():
    cs = build_chapter_constraints(task=_task(), setting=_setting(), context={})
    assert any(c.kind == "must_include"
               and "林渊" in c.phrases for c in cs)


def test_must_include_secondary_characters():
    """次要主角色应该也有独立约束。"""
    cs = build_chapter_constraints(task=_task(), setting=_setting(), context={})
    # 苏晚栀 应在 cs 里作为独立 Constraint 出现
    swx = [c for c in cs if c.kind == "must_include" and c.phrases == ("苏晚栀",)]
    assert swx, "苏晚栀 应有独立约束"


def test_must_recycle_foreshadow_uses_first_4_chars_of_desc():
    due = [
        {"desc": "深渊回廊伏笔", "due_chapter": 7},
        {"desc": "家徽失窃",     "due_chapter": 7},
    ]
    cs = build_chapter_constraints(
        task=_task(), setting=_setting(), context={},
        foreshadow_due=due,
    )
    recycle = [c for c in cs if c.kind == "must_must_recycle"]
    assert recycle, "应有 must_must_recycle 约束"
    assert "深渊回廊" in recycle[0].phrases
    assert "家徽失窃" in recycle[0].phrases


def test_must_not_new_character_only_when_task_mentions_extra_names():
    """task 描述里若没出现额外人名候选，约束列表为空（不强制"禁止新角色"）。

    2026-07-27 注：当前 build_chapter_constraints 用 task 文本提取的禁止集，
    实际"漏入新角色"的检测应基于正文跑 names_introduced —— 见 test_5。
    """
    cs = build_chapter_constraints(task=_task(), setting=_setting(), context={})
    # task 描述里没有"林渊/苏晚栀/凯恩"以外的人名候选
    assert not any(c.kind == "must_not_new_character" for c in cs), \
        "task 描述里没有额外人名时不应有 must_not_new_character 约束"


def test_empty_task_yields_no_constraints():
    cs = build_chapter_constraints(task={}, setting=_setting(), context={})
    assert cs == []


def test_malformed_setting_does_not_crash():
    cs = build_chapter_constraints(task=_task(), setting="字符串不是 dict", context={})
    assert isinstance(cs, list)  # 不崩、返空或返主体约束


# ─── 2. 分数判定 ─────────────────────────

def test_must_include_pass_when_phrase_present():
    c = Constraint(kind="must_include", description="必须出现林渊", phrases=("林渊",))
    rep = score_compliance("林渊夺回了家族徽记。", [c])
    assert rep.passed == 1 and rep.failed == 0


def test_must_include_fail_when_phrase_absent():
    c = Constraint(kind="must_include", description="必须出现林渊", phrases=("林渊",))
    rep = score_compliance("凯恩独自走过市集。", [c])
    assert rep.failed == 1 and rep.passed == 0


def test_must_must_recycle_passes_with_any_phrase():
    c = Constraint(kind="must_must_recycle",
                    description="应回收伏笔",
                    phrases=("深渊回廊", "家徽失窃"))
    rep = score_compliance("他回到了深渊回廊。", [c])
    assert rep.passed == 1


def test_must_not_new_character_passes_when_forbidden_absent():
    c = Constraint(kind="must_not_new_character",
                    description="不要新增",
                    forbid=("柳墨",))
    rep = score_compliance("林渊在云州与凯恩对峙。", [c])
    assert rep.passed == 1


def test_must_not_new_character_fails_when_forbidden_present():
    c = Constraint(kind="must_not_new_character",
                    description="不要新增柳墨",
                    forbid=("柳墨",))
    rep = score_compliance("柳墨走过市集。林渊在后面。", [c])
    assert rep.failed == 1
    assert any(not d["hit"] for d in rep.details)


def test_unknown_constraint_kind_is_a_free_pass():
    """未识别的 kind 不要让测试全挂 —— 当作未启用。"""
    c = Constraint(kind="experimental_xyz", description="x", phrases=())
    rep = score_compliance("任何正文", [c])
    assert rep.passed == 1
    assert rep.rate == 1.0


def test_empty_constraints_yields_zero_total():
    rep = score_compliance("任何正文", [])
    assert rep.total == 0 and rep.rate == 0.0


def test_report_total_passed_failed_consistent():
    cs = [
        Constraint(kind="must_include", description="a", phrases=("林渊",)),
        Constraint(kind="must_include", description="b", phrases=("林渊",)),
        Constraint(kind="must_include", description="c", phrases=("李",)),  # 漏
    ]
    rep = score_compliance("林渊出场", cs)
    assert rep.total == 3
    assert rep.passed == 2 and rep.failed == 1


# ─── 3. 漏入新角色检测 ─────────────────────────

def test_names_introduced_finds_unexpected_characters():
    """正文里有未列入 known 的角色 → 报出来（用于 prompt 调试：漏在哪了）。"""
    text = "林渊走进市集。柳墨在旁边看着。凯恩远远站着。"
    known = {"林渊", "凯恩"}
    leaked = names_introduced(text, known)
    assert "柳墨" in leaked


def test_names_introduced_ignores_known():
    text = "林渊与凯恩对峙。林渊说：「你终于来了。」"
    known = {"林渊", "凯恩"}
    assert names_introduced(text, known) == set()


def test_names_introduced_does_not_misfire_on_common_words():
    """不能把普通两字词误判为人名（"走向""看着"等）。"""
    text = "他走向市集。看着天色，他觉得事情不妙。"
    assert names_introduced(text, set()) == set()


# ─── 4. 端到端：派生 + 判定 ─────────────────────────

def test_e2e_compliance_report_round_trip():
    task = _task()
    setting = _setting()
    due = [{"desc": "深渊回廊伏笔", "due_chapter": 7}]
    cs = build_chapter_constraints(
        task=task, setting=setting, context={}, foreshadow_due=due,
    )
    # 写一篇"完美"的章节：主角色 + 伏笔关键词 + 无新增角色
    perfect = "林渊在深渊回廊与凯恩对峙，夺回家族徽记。苏晚栀在暗处。"
    rep = score_compliance(perfect, cs)
    assert rep.rate == 1.0, f"完美章节应全命中：{rep.details}"

    # 写一篇"漏"的章节：主角色缺失 + 伏笔缺失
    bad = "雨夜的市集冷冷清清。"
    rep_bad = score_compliance(bad, cs)
    assert rep_bad.rate < 1.0, "漏写应被检出"
    assert rep_bad.failed >= 2