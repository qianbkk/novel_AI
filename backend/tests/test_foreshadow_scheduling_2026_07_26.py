"""test_foreshadow_scheduling_2026_07_26.py

架构审视 — 伏笔到期调度的三处真实缺陷修复。

背景（见 docs/wiki/03-Writing-Engine.md 记忆系统一节）：
伏笔的"种"有 3 个入口，"回收"靠 tracker 事后识别，中间靠
`get_chapter_relevant_context` 把「即将到期的伏笔」塞进 writer prompt。
这条提醒链是长篇里伏笔不烂尾的唯一保障，但原实现有三个缺陷：

1. **静默丢弃**（最严重）：`[f["desc"] for f in planted if ...][:5]` —— 不排序
   就截断。planted 按埋下顺序追加，所以一条早已超期的伏笔完全可能被排在它
   前面、其实还早得很的 5 条挤掉，且无任何信号。长篇里等于伏笔静默烂尾。
2. **超期无信号**：「还有 29 章到期」和「已超期 40 章」在 prompt 里长得一模一样
   （都是 `→ 描述`），writer 无从区分轻重。
3. **弧长写死 30**：`target_arc * 30` 假定每弧恰好 30 章。本项目 arc_plans 的
   estimated_chapters 是可变的，弧长非 30 时到期章号系统性偏移，弧越靠后偏越远。

修法：按到期章号升序排（最紧急优先）→ 加到期状态前缀 → 截断条数显式写出 →
弧长由参数/L2 meta 传入。另新增 overdue/pending 计数，让"伏笔超期堆积"
第一次成为可观测、可断言的信号。
"""
from __future__ import annotations

import pytest

from engine.memory.manager import (
    DEFAULT_CHAPTERS_PER_ARC,
    FORESHADOW_DUE_CAP,
    FORESHADOW_DUE_WINDOW,
    _build_foreshadow_worklist,
    _foreshadow_target_chapter,
    get_chapter_relevant_context,
)


def _memory(planted, resolved=None, meta=None):
    return {
        "hot": {},
        "cold": {"resolved_foreshadowing": list(resolved or [])},
        "constraints": {"foreshadowing_planted": list(planted)},
        "meta": dict(meta or {}),
    }


def _f(desc, **kw):
    return {"desc": desc, **kw}


# ─── 1. 到期章号换算 ─────────────────────────

def test_explicit_target_chapter_wins():
    assert _foreshadow_target_chapter({"target_arc": 4, "target_chapter": 50}) == 50


def test_target_arc_uses_default_arc_length():
    assert _foreshadow_target_chapter({"target_arc": 4}) == 4 * DEFAULT_CHAPTERS_PER_ARC


@pytest.mark.parametrize("arc_len,expected", [(10, 40), (20, 80), (50, 200)])
def test_target_arc_honours_real_arc_length(arc_len, expected):
    """核心回归：弧长不是 30 时不得再按 30 换算。"""
    assert _foreshadow_target_chapter({"target_arc": 4}, arc_len) == expected


@pytest.mark.parametrize("bad", [0, None, -5])
def test_invalid_arc_length_falls_back_to_default(bad):
    """弧长非法时退回默认值，不得出现 target=0 让所有伏笔立刻「超期」。"""
    assert _foreshadow_target_chapter({"target_arc": 4}, bad) == 4 * DEFAULT_CHAPTERS_PER_ARC


def test_planted_at_fallback():
    assert _foreshadow_target_chapter({"planted_at_chapter": 10}) == 10 + FORESHADOW_DUE_WINDOW


def test_no_info_never_triggers():
    assert _foreshadow_target_chapter({}) >= 10 ** 8


# ─── 2. 静默丢弃（核心回归） ─────────────────────────

def test_overdue_foreshadow_is_not_crowded_out_by_later_ones():
    """埋下顺序靠后的超期伏笔，不得被排在前面但还早得很的条目挤出 prompt。"""
    planted = [_f(f"不急的伏笔{i}", target_chapter=125) for i in range(FORESHADOW_DUE_CAP)]
    planted.append(_f("早就该回收的伏笔", target_chapter=40))

    lines, overdue, pending = _build_foreshadow_worklist(
        planted, resolved=set(), ch_num=100, chapters_per_arc=DEFAULT_CHAPTERS_PER_ARC,
    )
    assert any("早就该回收的伏笔" in ln for ln in lines), (
        f"超期伏笔被静默丢弃了，实际={lines}"
    )
    assert overdue == 1
    assert pending == FORESHADOW_DUE_CAP + 1


def test_worklist_sorted_most_urgent_first():
    planted = [_f("晚", target_chapter=120), _f("早", target_chapter=90),
               _f("最早", target_chapter=60)]
    lines, _, _ = _build_foreshadow_worklist(planted, set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    order = [next(k for k in ("最早", "早", "晚") if k in ln) for ln in lines
             if any(k in ln for k in ("最早", "早", "晚"))]
    assert order == ["最早", "早", "晚"]


def test_truncation_is_announced_not_silent():
    """超过 cap 时必须显式说明还剩多少条，不得静默截断。"""
    planted = [_f(f"伏笔{i}", target_chapter=100 + i) for i in range(FORESHADOW_DUE_CAP + 3)]
    lines, _, pending = _build_foreshadow_worklist(planted, set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert len(lines) == FORESHADOW_DUE_CAP + 1  # cap 条 + 1 条说明
    assert "另有 3 条待回收伏笔未列出" in lines[-1]
    assert pending == FORESHADOW_DUE_CAP + 3


def test_no_truncation_notice_when_within_cap():
    planted = [_f("唯一伏笔", target_chapter=110)]
    lines, _, _ = _build_foreshadow_worklist(planted, set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert len(lines) == 1
    assert "未列出" not in lines[0]


# ─── 3. 到期状态前缀 ─────────────────────────

def test_overdue_prefix_states_how_late():
    lines, _, _ = _build_foreshadow_worklist(
        [_f("欠账伏笔", target_chapter=88)], set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert lines == ["【已超期 12 章，必须优先回收】欠账伏笔"]


def test_due_this_chapter_prefix():
    lines, _, _ = _build_foreshadow_worklist(
        [_f("到期伏笔", target_chapter=100)], set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert lines == ["【本章到期，必须回收】到期伏笔"]


def test_future_prefix_names_deadline_chapter():
    lines, _, _ = _build_foreshadow_worklist(
        [_f("远期伏笔", target_chapter=120)], set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert lines == ["【第 120 章前回收】远期伏笔"]


def test_three_states_are_distinguishable_in_prompt():
    """三种到期状态在 prompt 里必须长得不一样（原缺陷 #2）。"""
    planted = [_f("超期的", target_chapter=80), _f("本章的", target_chapter=100),
               _f("未来的", target_chapter=115)]
    lines, _, _ = _build_foreshadow_worklist(planted, set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert len({ln.split("】")[0] for ln in lines}) == 3


# ─── 4. 过滤与去重 ─────────────────────────

def test_resolved_are_excluded():
    planted = [_f("已回收", target_chapter=90), _f("未回收", target_chapter=90)]
    lines, _, pending = _build_foreshadow_worklist(
        planted, {"已回收"}, 100, DEFAULT_CHAPTERS_PER_ARC)
    assert pending == 1
    assert all("已回收" not in ln for ln in lines)


def test_far_future_foreshadow_not_listed_yet():
    planted = [_f("很远的伏笔", target_chapter=100 + FORESHADOW_DUE_WINDOW + 1)]
    lines, _, pending = _build_foreshadow_worklist(planted, set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert lines == []
    assert pending == 0


@pytest.mark.parametrize("junk", [None, "字符串", 123, {}, {"desc": ""}])
def test_malformed_entries_are_skipped(junk):
    lines, _, pending = _build_foreshadow_worklist(
        [junk, _f("正常伏笔", target_chapter=90)], set(), 100, DEFAULT_CHAPTERS_PER_ARC)
    assert pending == 1
    assert any("正常伏笔" in ln for ln in lines)


# ─── 5. 接进 get_chapter_relevant_context ─────────────────────────

def test_context_exposes_overdue_counts():
    memory = _memory([_f("超期A", target_chapter=50), _f("超期B", target_chapter=60),
                      _f("未来", target_chapter=115)])
    ctx = get_chapter_relevant_context(memory, {"chapter_number": 100, "main_characters": []})
    assert ctx["foreshadowing_overdue_count"] == 2
    assert ctx["foreshadowing_pending_count"] == 3


def test_context_uses_arc_length_from_meta():
    """L2 meta 里记了真实弧长时必须用它，而不是默认 30。"""
    memory = _memory([_f("弧4伏笔", target_arc=4)], meta={"chapters_per_arc": 10})
    # 弧长 10 → target=40；当前第 100 章 → 已超期 60 章
    ctx = get_chapter_relevant_context(memory, {"chapter_number": 100, "main_characters": []})
    assert any("已超期 60 章" in s for s in ctx["foreshadowing_due_soon"])


def test_context_without_meta_falls_back_to_default_arc_length():
    memory = _memory([_f("弧4伏笔", target_arc=4)])
    ctx = get_chapter_relevant_context(memory, {"chapter_number": 100, "main_characters": []})
    # 弧长 30 → target=120 → 尚未到期
    assert any("第 120 章前回收" in s for s in ctx["foreshadowing_due_soon"])


def test_empty_memory_yields_empty_worklist():
    ctx = get_chapter_relevant_context(_memory([]), {"chapter_number": 1, "main_characters": []})
    assert ctx["foreshadowing_due_soon"] == []
    assert ctx["foreshadowing_overdue_count"] == 0
    assert ctx["foreshadowing_pending_count"] == 0


# ─── 6. record_arc_length：让弧长换算真的有据可依 ─────────────────────────

def test_record_arc_length_writes_median(tmp_path, monkeypatch):
    """真实弧长应落进 L2 meta，且用中位数（个别超短收尾弧不带偏基准）。"""
    from engine.memory import manager as m
    monkeypatch.setattr(m, "L2_DIR_STR", str(tmp_path))

    arc_plans = [{"estimated_chapters": 40}, {"estimated_chapters": 42},
                 {"estimated_chapters": 2}]
    got = m.record_arc_length("novel-x", arc_plans)
    assert got == 40
    assert m.get_l2("novel-x")["meta"]["chapters_per_arc"] == 40


def test_record_arc_length_ignores_invalid_entries(tmp_path, monkeypatch):
    from engine.memory import manager as m
    monkeypatch.setattr(m, "L2_DIR_STR", str(tmp_path))

    arc_plans = [{"estimated_chapters": 0}, {"estimated_chapters": "x"},
                 {"nope": 1}, None, {"estimated_chapters": 25}]
    assert m.record_arc_length("novel-y", arc_plans) == 25


def test_record_arc_length_returns_none_when_no_data(tmp_path, monkeypatch):
    from engine.memory import manager as m
    monkeypatch.setattr(m, "L2_DIR_STR", str(tmp_path))
    assert m.record_arc_length("novel-z", []) is None
    assert m.record_arc_length("novel-z", [{"estimated_chapters": -3}]) is None


def test_record_arc_length_is_idempotent(tmp_path, monkeypatch):
    from engine.memory import manager as m
    monkeypatch.setattr(m, "L2_DIR_STR", str(tmp_path))
    arc_plans = [{"estimated_chapters": 30}]
    assert m.record_arc_length("novel-i", arc_plans) == 30
    assert m.record_arc_length("novel-i", arc_plans) == 30
    assert m.get_l2("novel-i")["meta"]["chapters_per_arc"] == 30


def test_recorded_arc_length_flows_into_due_dates(tmp_path, monkeypatch):
    """端到端：记了弧长 10 之后，弧4 伏笔的到期章号应是 40 而不是 120。"""
    from engine.memory import manager as m
    monkeypatch.setattr(m, "L2_DIR_STR", str(tmp_path))

    m.record_arc_length("novel-e2e", [{"estimated_chapters": 10}])
    memory = m.get_l2("novel-e2e")
    memory["constraints"]["foreshadowing_planted"] = [
        {"desc": "弧4伏笔", "target_arc": 4}]
    ctx = m.get_chapter_relevant_context(
        memory, {"chapter_number": 45, "main_characters": []})
    assert any("已超期 5 章" in s for s in ctx["foreshadowing_due_soon"]), \
        ctx["foreshadowing_due_soon"]
