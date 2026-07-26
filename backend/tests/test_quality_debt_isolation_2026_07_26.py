"""test_quality_debt_isolation_2026_07_26.py

架构审视 — 隔离质量债传染。

背景：
`orchestrator.node_human_escalation` 在章节没过质量门时仍然调用 `run_tracker`
（orchestrator.py 的「审计 P1」注释说明这是有意的折中：完全跳过会让 L2 缺这
一章，100+ 章长篇里漂移更严重）。但打的 `memory_gap=True` 标只存在 meta 里，
**没有任何下游消费者**。

后果：草稿质量的情节经 tracker 写进 `hot.recent_summaries`，再经
`get_chapter_relevant_context` 的 `recent_events` 进入后续每一章的 writer
prompt，被当成已确认的剧情事实 —— 质量债一路传染。文风层面有隔离
（`manager.py` 抽内部风格样本时跳过 `[待修订]` 开头的章节），剧情记忆层面没有。

修法：不改变"仍然记录"这个折中，而是让标记真正传播 ——
`run_tracker(..., unverified=True)` 给摘要打标，`_format_recent_events()`
渲染时显式标出「待修订，情节未定稿」，writer 因此知道这段可以顺也可以绕，
而不是当成不可动摇的既成事实。
"""
from __future__ import annotations

import pytest

from engine.memory.manager import _format_recent_events, get_chapter_relevant_context


def _mem(summaries):
    return {"hot": {"recent_summaries": list(summaries)},
            "cold": {}, "constraints": {}, "meta": {}}


def _ctx(summaries, ch=10):
    return get_chapter_relevant_context(
        _mem(summaries), {"chapter_number": ch, "main_characters": []})


# ─── 1. 渲染器 ─────────────────────────

def test_verified_summary_renders_plain():
    assert _format_recent_events([{"chapter": 1, "summary": "主角夺回徽记"}]) == "主角夺回徽记"


def test_unverified_summary_is_marked():
    out = _format_recent_events([
        {"chapter": 6, "summary": "与反派对峙", "unverified": True}])
    assert "与反派对峙" in out
    assert "待修订" in out and "第6章" in out


def test_marker_does_not_leak_onto_verified_neighbours():
    """核心：只标那一章，不能把整段近期剧情都染成不可信。"""
    out = _format_recent_events([
        {"chapter": 5, "summary": "夺回徽记"},
        {"chapter": 6, "summary": "对峙", "unverified": True},
        {"chapter": 7, "summary": "撤离"},
    ])
    parts = out.split(" | ")
    assert parts[0] == "夺回徽记"
    assert "待修订" in parts[1]
    assert parts[2] == "撤离"


def test_unverified_without_chapter_number_still_marked():
    out = _format_recent_events([{"summary": "某事", "unverified": True}])
    assert "待修订" in out


@pytest.mark.parametrize("junk", [None, "字符串", 42, {}, {"summary": ""},
                                  {"summary": "   "}])
def test_malformed_entries_are_skipped(junk):
    out = _format_recent_events([junk, {"chapter": 1, "summary": "正常"}])
    assert out == "正常"


def test_empty_input():
    assert _format_recent_events([]) == ""
    assert _format_recent_events(None) == ""


# ─── 2. 接进 writer 上下文 ─────────────────────────

def test_context_recent_events_carries_marker():
    ctx = _ctx([{"chapter": 6, "summary": "对峙", "unverified": True}])
    assert "待修订" in ctx["recent_events"]


def test_context_recent_events_clean_when_all_verified():
    ctx = _ctx([{"chapter": i, "summary": f"第{i}章事件"} for i in (5, 6, 7)])
    assert "待修订" not in ctx["recent_events"]
    assert ctx["recent_events"] == "第5章事件 | 第6章事件 | 第7章事件"


def test_only_last_five_summaries_are_rendered():
    """既有契约不能因为加标记而改变。"""
    ctx = _ctx([{"chapter": i, "summary": f"e{i}"} for i in range(1, 9)])
    assert ctx["recent_events"] == "e4 | e5 | e6 | e7 | e8"


def test_marked_summary_reaches_writer_prompt():
    """端到端：标记必须真的出现在 writer 看到的 prompt 里。"""
    from engine.agents.writer import build_writer_prompt
    ctx = _ctx([{"chapter": 6, "summary": "对峙失败", "unverified": True}])
    _, usr = build_writer_prompt(
        {"chapter_number": 7, "chapter_goal": "反击"}, ctx,
        {"protagonist": {"name": "甲"}})
    assert "对峙失败" in usr
    assert "待修订" in usr


# ─── 3. tracker 打标 ─────────────────────────

def _run_tracker_with(monkeypatch, *, unverified):
    """跑 run_tracker，把 LLM 调用换成固定返回，只观察摘要落库形态。"""
    import engine.agents.tracker as tr

    class _FakeRouter:
        def call(self, **kw):
            return ('{"chapter_summary": "本章摘要"}', 0.0)

    monkeypatch.setattr(tr, "get_active_router", lambda: _FakeRouter())
    monkeypatch.setattr(tr, "save_l2", lambda *a, **k: None)
    memory = {"hot": {"recent_summaries": []}, "cold": {},
              "constraints": {}, "meta": {}}
    updated, _cost = tr.run_tracker(
        "正文", {"chapter_number": 6}, memory, "novel-x", unverified=unverified)
    return updated["hot"]["recent_summaries"][-1]


def test_tracker_marks_summary_when_unverified(monkeypatch):
    entry = _run_tracker_with(monkeypatch, unverified=True)
    assert entry["unverified"] is True
    assert entry["chapter"] == 6


def test_tracker_leaves_summary_unmarked_by_default(monkeypatch):
    entry = _run_tracker_with(monkeypatch, unverified=False)
    assert "unverified" not in entry


def test_tracker_recent_events_uses_shared_renderer(monkeypatch):
    """tracker 自己写的 hot.recent_events 也要带标记，两处渲染不能漂移。"""
    import engine.agents.tracker as tr

    class _FakeRouter:
        def call(self, **kw):
            return ('{"chapter_summary": "对峙"}', 0.0)

    monkeypatch.setattr(tr, "get_active_router", lambda: _FakeRouter())
    monkeypatch.setattr(tr, "save_l2", lambda *a, **k: None)
    memory = {"hot": {"recent_summaries": []}, "cold": {},
              "constraints": {}, "meta": {}}
    updated, _ = tr.run_tracker("正文", {"chapter_number": 6}, memory,
                                "novel-x", unverified=True)
    assert "待修订" in updated["hot"]["recent_events"]


# ─── 4. escalation 路径确实传了标记 ─────────────────────────

def test_escalation_passes_unverified_to_tracker():
    """orchestrator 的 escalation 分支必须以 unverified=True 调 tracker，
    否则整条打标链路是死的。"""
    import inspect
    import engine.orchestrator as orch

    src = inspect.getsource(orch.node_human_escalation)
    assert "run_tracker(" in src, "escalation 不再调 tracker？契约变了要同步这条测试"
    assert "unverified=True" in src, "escalation 调 tracker 时没传 unverified=True"
