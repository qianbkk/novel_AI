"""backend/tests/test_tracker_drift.py — Phase 8 发现 #7-#10 修复回归

防止 tracker 在 LLM 状态抽取不完美时静默丢失核心状态（active_threads /
scene_location / time_context / cold 三件套）。审计师担心的核心场景：
"如果状态抽取本身不准，前面这套精心设计的记忆分层用的都是错的输入。"

3 类修复：
  1. chapter_text[:2000] → head+tail 保留（跟 checker 一致，弧高潮能看到尾）
  2. active_threads 破坏性 `=` → _merge_threads（防 LLM 漏列）
  3. scene_location/time_context 破坏性 `=` → 空字符串保护 + 用旧值
  4. cold 三件套 (world_events/closed_threads/resolved_foreshadowing) → _append_dedup

所有 fix 都在 tracker.py 模块级函数 _merge_threads / _append_dedup 上：
  - _merge_threads(existing, llm_returned) —— 取并集，去同义重复
  - _append_dedup(existing, additions) —— 添加只不同的项

测试点：
  - merge 不会丢旧线（核心防回归）
  - merge 后顺序：LLM 当前列表优先，旧线追加
  - substring 同义去重（"主角觉醒金手指" vs "主角觉醒"）
  - cap 50 防止假阳孤儿线无限堆
  - append_dedup 跨章节重复事件不写两次
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


# ────────────────────────────────────────────────────────────
# _merge_threads 单元测试
# ────────────────────────────────────────────────────────────

def test_merge_threads_preserves_old_lines_when_llm_omits():
    """核心防回归：LLM 漏列一条仍在跑的线，必须保留，不能静默删除。"""
    from engine.agents.tracker import _merge_threads
    existing = ["主线: 主角觉醒金手指", "暗线: 师父身份之谜"]
    # LLM 只返主线，漏列暗线
    llm_returned = ["主线: 主角觉醒金手指"]
    result = _merge_threads(existing, llm_returned)
    # 两条线都得在
    assert "暗线: 师父身份之谜" in result, (
        f"LLM 漏列暗线 — 之前的破坏性替换会丢这条。实际={result}"
    )
    assert "主线: 主角觉醒金手指" in result


def test_merge_threads_dedups_substring_rewrite():
    """同义改写去重：「主角觉醒金手指」和「主角觉醒」应合并为一条。"""
    from engine.agents.tracker import _merge_threads
    existing = ["主角觉醒金手指"]
    llm_returned = ["主角觉醒"]  # 同一主题的轻微改写
    result = _merge_threads(existing, llm_returned)
    # 不应出现两份
    count = sum(1 for t in result if "主角觉醒" in t)
    assert count == 1, f"应合并为一条，实际={result}"


def test_merge_threads_llm_order_first():
    """LLM 当前列表项应该排在前面（当前活跃顺序），旧线追加在后面。"""
    from engine.agents.tracker import _merge_threads
    existing = ["旧线: 师父身份之谜"]
    llm_returned = ["当前线: 师父身份揭晓"]
    result = _merge_threads(existing, llm_returned)
    # LLM 当前的应该排在第一位
    assert result[0] == "当前线: 师父身份揭晓", (
        f"LLM 当前活跃线应排第一，实际={result}"
    )


def test_merge_threads_preserves_same_string():
    """完全相同的字符串不会重复出现两次。"""
    from engine.agents.tracker import _merge_threads
    existing = ["主线X"]
    llm_returned = ["主线X"]
    result = _merge_threads(existing, llm_returned)
    assert result.count("主线X") == 1


def test_merge_threads_caps_at_50():
    """超出 50 条之后 cap 防止 LLM 漏列无限堆孤儿线。"""
    from engine.agents.tracker import _merge_threads
    existing = [f"line_{i}" for i in range(60)]
    llm_returned = []  # LLM 全部漏列
    result = _merge_threads(existing, llm_returned)
    assert len(result) <= 50, (
        f"应有 cap 50 防止孤儿线无限堆，实际={len(result)}"
    )


def test_merge_threads_handles_non_string_inputs():
    """LLM 偶尔返非 str（字典、数字）—— 容错，不应抛异常。"""
    from engine.agents.tracker import _merge_threads
    existing = ["旧线"]
    llm_returned = [{"desc": "invalid"}, 123, None, "正常线"]
    # 不应抛
    result = _merge_threads(existing, llm_returned)
    # 只收下能转 str 的部分
    assert "正常线" in result


def test_merge_threads_empty_inputs():
    """空输入：existing=空 + llm_returned=空 → 空列表。"""
    from engine.agents.tracker import _merge_threads
    assert _merge_threads([], []) == []
    assert _merge_threads(["existing_only"], []) == ["existing_only"]
    assert _merge_threads([], ["only_llm"]) == ["only_llm"]


# ────────────────────────────────────────────────────────────
# _append_dedup 单元测试
# ────────────────────────────────────────────────────────────

def test_append_dedup_skips_exact_duplicate():
    """添加已存在的项不应重复。"""
    from engine.agents.tracker import _append_dedup
    existing = ["主角受雷劫"]
    additions = ["主角受雷劫"]
    result = _append_dedup(existing, additions)
    assert result.count("主角受雷劫") == 1


def test_append_dedup_skips_substring_duplicate():
    """substring 互相包含视为同一条记录。"""
    from engine.agents.tracker import _append_dedup
    existing = ["主角受雷劫"]
    additions = ["主角受雷劫在临江市"]  # 已有短串是它的 prefix
    result = _append_dedup(existing, additions)
    # substring 包含关系，dedup
    assert len(result) == 1


def test_append_dedup_preserves_new_distinct_items():
    """添加真正不同的项应保留。"""
    from engine.agents.tracker import _append_dedup
    existing = ["A事件"]
    additions = ["B事件", "C事件"]
    result = _append_dedup(existing, additions)
    assert "A事件" in result
    assert "B事件" in result
    assert "C事件" in result


def test_append_dedup_handles_non_string_addition():
    """LLM 偶尔返 dict / list —— 容忍。"""
    from engine.agents.tracker import _append_dedup
    additions = [{"event": "structured"}, "正常", None]
    result = _append_dedup([], additions)
    assert len(result) >= 2  # 至少接受非空 str / dict


# ────────────────────────────────────────────────────────────
# 集成测试：模拟 run_tracker 完整流程
# ────────────────────────────────────────────────────────────

class _FakeRouterCapture:
    """记录每次 call 并返回预设 response。"""
    def __init__(self, response_json: dict):
        self.calls = []
        self._response = response_json

    def call(self, *, agent_name, system_prompt, user_prompt, max_tokens, temperature):
        self.calls.append({
            "agent": agent_name,
            "system": system_prompt,
            "user": user_prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        return json_dumps(self._response), 0.05


def json_dumps(d):
    import json
    return json.dumps(d, ensure_ascii=False)


def _make_memory(hot=None, cold=None, constraints=None):
    return {
        "hot": hot or {"active_threads": [], "recent_summaries": [], "character_states": {}},
        "cold": cold or {"compressed_history": "", "world_events": [], "closed_threads": [], "resolved_foreshadowing": []},
        "constraints": constraints or {"forbidden_constraints": [], "established_facts": [], "foreshadowing_planted": []},
        "meta": {"last_updated_chapter": 0, "total_chapters_tracked": 0},
    }


def test_run_tracker_preserves_active_threads_when_llm_omits(monkeypatch, tmp_path):
    """集成场景：先创建有 3 条线 → 下一章 LLM 只返 1 条 → 应保留 3 条。"""
    from engine.agents import tracker as _tracker
    from engine import memory as _memory_pkg
    from engine.llm_router import set_active_router

    # 上一次 tracker 已记录 3 条活跃线
    mem = _make_memory(hot={"active_threads": [
        "线A: 师父身份",
        "线B: 金手指觉醒",
        "线C: 师徒矛盾",
    ], "recent_summaries": [], "character_states": {}})
    # 这一次 LLM 只返回线A（漏列）
    fake = _FakeRouterCapture({
        "active_threads": ["线A: 师父身份"],
        "chapter_summary": "本章推进线A",
        "last_chapter_ending": "师父剑下留情",
    })
    set_active_router(fake)  # type: ignore[arg-type]
    monkeypatch.setattr(_tracker, "get_active_router", lambda: fake)
    # 用临时 L2 目录避免污染真实数据
    monkeypatch.setattr(_memory_pkg.manager, "L2_DIR_STR", str(tmp_path))

    task = {"chapter_number": 2, "main_characters": [], "chapter_role": "发展"}
    # 假装 short chapter
    novel_id = "test-novel-omit"
    chapter_text = "本章推进线A。师父剑下留情。"

    out, cost = _tracker.run_tracker(chapter_text, task, mem, novel_id)

    # 三条线都应该被保留
    threads = out["hot"]["active_threads"]
    assert "线A: 师父身份" in threads
    assert "线B: 金手指觉醒" in threads, f"线B 应被保留，实际={threads}"
    assert "线C: 师徒矛盾" in threads, f"线C 应被保留，实际={threads}"

    monkeypatch.undo()


def test_run_tracker_preserves_scene_location_when_llm_omits(monkeypatch, tmp_path):
    """场景：新章节 LLM 不写地点 → 主角位置不应归零。"""
    from engine.agents import tracker as _tracker
    from engine import memory as _memory_pkg
    from engine.llm_router import set_active_router

    mem = _make_memory(hot={
        "scene_location": "临江市旧城街",
        "active_threads": [], "recent_summaries": [], "character_states": {},
    })
    # LLM 这章没给 scene_location（field 缺席）
    fake = _FakeRouterCapture({
        "chapter_summary": "本章短打",
    })
    set_active_router(fake)  # type: ignore[arg-type]
    monkeypatch.setattr(_tracker, "get_active_router", lambda: fake)
    monkeypatch.setattr(_memory_pkg.manager, "L2_DIR_STR", str(tmp_path))

    task = {"chapter_number": 5, "chapter_role": "发展"}
    out, cost = _tracker.run_tracker(
        "本章内容", task, mem, "test-novel-scene"
    )

    # scene_location 应保留旧值
    assert out["hot"]["scene_location"] == "临江市旧城街", (
        f"LLM 没提供地点，应保留旧值，实际={out['hot']['scene_location']!r}"
    )

    monkeypatch.undo()


def test_run_tracker_dedups_cold_world_events_across_chapters(monkeypatch, tmp_path):
    """同一事件跨章节被 LLM 重提时，cold.world_events 不应双倍记录。"""
    from engine.agents import tracker as _tracker
    from engine import memory as _memory_pkg
    from engine.llm_router import set_active_router

    # 第 1 章 tracker 已记: '主角受雷劫'
    mem = _make_memory(cold={
        "world_events": ["主角受雷劫"],
        "closed_threads": [],
        "resolved_foreshadowing": [],
        "compressed_history": "",
    })
    # 第 2 章 LLM 又返同一事件
    fake = _FakeRouterCapture({
        "new_world_events": ["主角受雷劫在临江市"],  # substring fuzzy
    })
    set_active_router(fake)  # type: ignore[arg-type]
    monkeypatch.setattr(_tracker, "get_active_router", lambda: fake)
    monkeypatch.setattr(_memory_pkg.manager, "L2_DIR_STR", str(tmp_path))

    task = {"chapter_number": 2, "chapter_role": "发展"}
    out, _ = _tracker.run_tracker("本章", task, mem, "test-novel-cold")

    # 不应该出现两条
    events = out["cold"]["world_events"]
    matching = [e for e in events if "雷劫" in str(e)]
    assert len(matching) == 1, (
        f"同一事件跨章节被 dedup，应只 1 条，实际={events}"
    )

    monkeypatch.undo()


def test_run_tracker_handles_long_chapter_without_truncating_ending(monkeypatch, tmp_path):
    """Phase 8 fix #7：弧高潮章节（3000-3300 字）不应被截断尾段。

    验证方式：构造 5000 字章节 → tracker.send() 接收 user_prompt 应包含尾段
    而不是只有前 2000 字。
    """
    from engine.agents import tracker as _tracker
    from engine import memory as _memory_pkg
    from engine.llm_router import set_active_router

    mem = _make_memory()
    fake = _FakeRouterCapture({})
    set_active_router(fake)  # type: ignore[arg-type]
    monkeypatch.setattr(_tracker, "get_active_router", lambda: fake)
    monkeypatch.setattr(_memory_pkg.manager, "L2_DIR_STR", str(tmp_path))

    # 5000+ 字章节（>4000 触发 head+tail）：头 1500 + 中段 + 尾 2000
    chapter_text = "F" * 1500 + "MIDDLE_CONTENT_OMITTED" + "E" * 2000 + "X" * 600
    assert len(chapter_text) > 4000, "应大于截断阈值 4000"

    task = {"chapter_number": 1, "chapter_role": "弧高潮"}
    try:
        _tracker.run_tracker(chapter_text, task, mem, "test-novel-long")
    except Exception:
        pass  # 我们只关心 user_prompt 内容

    user_prompt = fake.calls[0]["user"]
    # fix 后应包含 "中段省略" 标记（说明走的是 head+tail 路径）
    assert "中段省略" in user_prompt, (
        f"长章节 >4000 应触发 head+tail 保留 + 中段省略标记；"
        f"实际 prompt 头部={user_prompt[:200]!r}"
    )
    # 头尾特征也应保留（证明两条都送入了）
    head_marker_count = user_prompt.count("F" * 50)  # 50 个连续 F
    tail_marker_count = user_prompt.count("E" * 50)  # 50 个连续 E
    assert head_marker_count >= 1, "头段特征应保留"
    assert tail_marker_count >= 1, "尾段特征应保留（这是防 #7 regression 的核心）"

    monkeypatch.undo()
