"""backend/tests/test_longform_e2e.py — Phase C 长篇端到端 mock 跑测

Phase 5-9 几个修复（冷记忆二次摘要、tracker 状态防破坏性覆盖、checker
保留结尾采样）目前都只有单元测试覆盖。本测试在 NOVEL_ENGINE_MOCK=1 之外，
手工跑一遍 100 章的 orchestrator 流程（只调 run_tracker + run_memory，
不写真章节正文），验证：

  1. cold.compressed_history_meta.total_compression_events 累计正确
     （大致 = 总章节数 / 25，因为每 25 章触发 1 次 compress，
     compress 本身可能触发 1 次二次摘要）
  2. Phase A 修复有效：每章 tracker 返回 cost 含 tracker + 二次摘要部分
  3. active_threads 不异常增长（fuzzy dedup 在长跑下不漂移）
  4. constraints 过期自动清理（不会无限堆）

设计取舍：
  - 不真跑 orchestrator.run_orchestrator（依赖太多 LangGraph / DB），
    直接调 run_tracker 100 次模拟 100 章的 tracker 路径
  - 用 NOVEL_ENGINE_MOCK 风格的 _FakeRouter 让 chapter_text / LLM 响应
    完全可控
  - 不用真实文件系统（用 in-memory dict 模拟 L2 memory），但保留
    get_l2/save_l2 的 IO 走 tmp_path
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


# ────────────────────────────────────────────────────────────
# Mock router + chapter text
# ────────────────────────────────────────────────────────────

class _MockRouter:
    """跟真实 router 接口一致；返回固定 cost 让累加行为可断言。"""

    # 每次调用的 cost 取决于 agent
    COST_TABLE = {
        "tracker":   0.01,   # 主 tracker LLM
        "summarizer": 0.05,  # 二次摘要（冷记忆超出 SOFT_CAP 时触发）
    }

    def __init__(self):
        self.calls = []  # [(agent, ...)]

    def call(self, *, agent_name, system_prompt, user_prompt, max_tokens, temperature):
        self.calls.append({"agent": agent_name, "max_tokens": max_tokens})
        cost = self.COST_TABLE.get(agent_name, 0.001)
        if agent_name == "summarizer":
            # 模拟 _secondary_summarize_cold_history 的响应：精炼后的 history
            return "LLM 二次摘要后的精炼历史。", cost
        # tracker 响应（valid JSON，phase 5+ 容忍 parse 失败有兜底）
        return json.dumps({
            "chapter_summary": f"本章剧情概要 — Ch{user_prompt[:50]}",
            "active_threads": ["主线: 主角觉醒金手指", "暗线: 师父身份之谜"],
            "character_states": {},
            "scene_location": "云州",
            "time_context": "昼",
        }), cost


def _chapter_text(n: int) -> str:
    """生成指定章节的模拟正文（长度够触发 tracker 的 head+tail 截断）。"""
    head = f"【第{n}章开端】 主角在云州城内行走，发生了一些事。" * 30
    body = f"中间描述。本章中心剧情。{n}" * 50
    tail = f"【第{n}章结尾】 主角与人争论，遇到一个谜团，结尾留下钩子。" * 30
    return head + body + tail


# ────────────────────────────────────────────────────────────
# Long-form run simulation
# ────────────────────────────────────────────────────────────

def _simulate_longform_run(num_chapters: int, monkeypatch, tmp_path):
    """模拟 num_chapters 章的 tracker 链路。

    Returns:
        (memory, total_cost, total_compression_events)
    """
    from engine.agents import tracker as _tracker_mod
    from engine.llm_router import set_active_router
    from engine.memory.manager import (
        empty_l2, get_l2, save_l2, L2_DIR_STR,
    )

    # 重定向 L2 目录到 tmp_path（不污染真实 backend/data/engine/memory/）
    import engine.memory.manager as _manager_mod
    monkeypatch.setattr(_manager_mod, "L2_DIR_STR", str(tmp_path))

    mock = _MockRouter()
    set_active_router(mock)
    monkeypatch.setattr(_tracker_mod, "get_active_router", lambda: mock)

    memory = empty_l2()
    memory["meta"]["novel_id"] = "longform-test"
    total_cost = 0.0

    for ch in range(1, num_chapters + 1):
        task = {
            "chapter_number": ch,
            "main_characters": ["主角"],
        }
        updated_mem, cost = _tracker_mod.run_tracker(
            chapter_text=_chapter_text(ch),
            task=task,
            current_memory=memory,
            novel_id="longform-test",
        )
        total_cost += cost
        memory = updated_mem

    return memory, total_cost, mock


def test_longform_100chapters_compression_event_count(monkeypatch, tmp_path):
    """跑 100 章后，total_compression_events 应大致 = 100 / 25 = 4 次。

    重要语义：`total_compression_events` 计的是 LLM 二次摘要触发的次数
    （不是普通 maybe_compress 的次数）。普通 compress 永远发生（每 10
    章一次），但只有当 cold.compressed_history 累计超过 SOFT_CAP=4000
    才会触发 LLM 二次摘要 + 累加此 counter。

    预填大 history（6000 字）保证第一次 compress 就触发二次摘要，
    之后每 10 章一次的 compress 在累积到阈值前都会二次摘要。
    """
    from engine.agents import tracker as _tracker_mod
    from engine.llm_router import set_active_router
    from engine.memory.manager import empty_l2

    mock = _MockRouter()
    set_active_router(mock)
    monkeypatch.setattr(_tracker_mod, "get_active_router", lambda: mock)

    # 预填 history 让每次 compress 都触发二次摘要
    memory = empty_l2()
    memory["meta"]["novel_id"] = "compress-test"
    memory["cold"]["compressed_history"] = "OLD " * 1500  # ~6000 chars

    num_chapters = 100
    for ch in range(1, num_chapters + 1):
        task = {"chapter_number": ch, "main_characters": ["主角"]}
        updated_mem, cost = _tracker_mod.run_tracker(
            chapter_text=_chapter_text(ch),
            task=task,
            current_memory=memory,
            novel_id="compress-test",
        )
        memory = updated_mem

    # 二次摘要 counter 应被累加（100 章里每 10 章 1 次 compress，
    # 预填 large history 保证每次都触发二次摘要 → ~10 次）
    meta = memory["cold"].get("compressed_history_meta", {})
    events = meta.get("total_compression_events", 0)
    # 至少 1 次（不可能 0，因为预填 6000 字保证第一次就触发）
    # 上限 10（100 章最多触发 10 次 compress，但实际触发次数取决于
    # 累积 history 是否持续超 SOFT_CAP — 二次摘要把 history 压回 ~1500
    # 字，后续 5 章累积 250 字，可能 5 章后才再次超阈值）
    assert 1 <= events <= 10, (
        f"100 章预填大 history，应至少 1 次、不超过 10 次二次摘要，实际 {events}"
    )
    # 最近一次压缩的章节号 ≤ num_chapters
    last_ch = meta.get("last_summarized_at_chapter", 0)
    assert 1 <= last_ch <= num_chapters


def test_longform_cost_includes_secondary_summarize(monkeypatch, tmp_path):
    """Phase A 修复生效：长篇跑测里如果有二次摘要触发，cost 应包含
    tracker + summarizer 两部分。

    我们手工触发一次超 SOFT_CAP 的二次摘要（预填 large existing history），
    跑 25 章，断言 tracker calls 含 summarizer，cost ≥ tracker_cost。
    """
    from engine.agents import tracker as _tracker_mod
    from engine.llm_router import set_active_router
    from engine.memory.manager import empty_l2

    mock = _MockRouter()
    set_active_router(mock)
    monkeypatch.setattr(_tracker_mod, "get_active_router", lambda: mock)

    # 预填 history 让第一次 compress 就超 SOFT_CAP=4000
    memory = empty_l2()
    memory["meta"]["novel_id"] = "cost-test"
    memory["cold"]["compressed_history"] = "OLD " * 1500  # ~6000 chars，超 SOFT_CAP

    # 25 章，触发 compress + 二次摘要
    total_cost = 0.0
    for ch in range(1, 26):
        task = {"chapter_number": ch, "main_characters": ["主角"]}
        updated_mem, cost = _tracker_mod.run_tracker(
            chapter_text=_chapter_text(ch),
            task=task,
            current_memory=memory,
            novel_id="cost-test",
        )
        total_cost += cost
        memory = updated_mem

    # 调过 summarizer（_secondary_summarize_cold_history 触发）
    summarizer_calls = [c for c in mock.calls if c["agent"] == "summarizer"]
    tracker_calls = [c for c in mock.calls if c["agent"] == "tracker"]
    assert len(tracker_calls) == 25
    assert len(summarizer_calls) >= 1, (
        "预填 6000 字 history + 25 章 → 应至少触发 1 次 summarizer 二次摘要"
    )

    # total_cost 应包含 tracker (25 * 0.01 = 0.25) + summarizer (≥1 * 0.05 = 0.05)
    expected_minimum = 25 * 0.01 + 1 * 0.05
    assert total_cost >= expected_minimum - 1e-9, (
        f"total_cost 应至少含 tracker 25 次 + summarizer 1 次，实际 {total_cost}"
    )


def test_longform_active_threads_does_not_blow_up(monkeypatch, tmp_path):
    """跑 100 章后，active_threads 应保持稳定（fuzzy dedup 有效）。

    LLM 始终返 ["主线: 主角觉醒金手指", "暗线: 师父身份之谜"]，这两条
    应在 _merge_threads fuzzy dedup 下被识别为重复，不会无限堆。
    """
    memory, total_cost, mock = _simulate_longform_run(
        100, monkeypatch, tmp_path,
    )

    threads = memory["hot"]["active_threads"]
    # 最多 2 条（dedup 后的稳定状态）
    assert len(threads) <= 2, (
        f"active_threads 在 fuzzy dedup 下应 ≤ 2，实际 {len(threads)}: {threads}"
    )
    # 不超过 cap 50
    assert len(threads) <= 50


def test_longform_constraints_expire(monkeypatch, tmp_path):
    """长篇跑测下 constraints 不应无限堆（每 20 章过期一批）。

    Mock router 不返回 new_constraints，所以本测试主要验证 expired 机制
    正常调用 + 不会抛异常。
    """
    memory, total_cost, mock = _simulate_longform_run(
        100, monkeypatch, tmp_path,
    )
    # 没有任何 constraints（mock 不返）→ 应保持空
    assert memory["constraints"]["forbidden_constraints"] == []
    assert memory["constraints"]["established_facts"] == []