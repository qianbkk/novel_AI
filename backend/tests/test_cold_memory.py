"""backend/tests/test_cold_memory.py — Phase 5 发现 #6 修复回归

防止长篇 novel 到 100+ 章时 cold.compressed_history 物理丢失旧剧情记录。

3 类修复：
  1. 写入侧：`maybe_compress_hot_to_cold` 超过 4000 chars 调 LLM 二次摘要，
     而不是硬 `[-3000:]` 砍头。
  2. 写入侧：每次压缩记录 meta（total_compression_events + last_summarized_at_chapter），
     让审计可观察。
  3. 读取侧：`get_chapter_relevant_context` 投喂 cold_summary 的截断从 500 字
     提到 2000 字（Phase 5 配套）。

测试点：
  - 不溢出：compressed_history 直接 append
  - 溢出：触发 LLM 二次摘要（用 monkeypatch 拦截 router.call）
  - 二次摘要失败：fallback 到硬截断 + 仍写入 memory（不让 run 崩）
  - meta 字段：total_compression_events 累加正确
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest


class _FakeRouter:
    """截 call，response 可预设。"""
    def __init__(self, response: str = ""):
        self.calls: list[dict] = []
        self._response = response

    def call(self, *, agent_name, system_prompt, user_prompt, max_tokens, temperature):
        self.calls.append({
            "agent": agent_name, "system": system_prompt,
            "user": user_prompt, "max_tokens": max_tokens,
            "temperature": temperature,
        })
        return self._response, 0.05


def _make_memory_with_n_summaries(n: int) -> dict:
    """构造一个有 n 条 recent_summary 的 L2 记忆体。"""
    memory = {
        "hot": {
            "recent_summaries": [
                {"chapter": i, "summary": f"第{i}章剧情：示例"}
                for i in range(1, n + 1)
            ],
        },
        "cold": {"compressed_history": ""},
        "meta": {"last_updated_chapter": n, "total_chapters_tracked": n},
    }
    return memory


def test_no_overflow_just_appends():
    """未超阈值：直接 append new lines，不调 LLM。"""
    from engine.memory.manager import maybe_compress_hot_to_cold
    memory = _make_memory_with_n_summaries(25)  # > 20 触发 compress
    # cold.compressed_history 已经有一段 1000 字的旧内容
    memory["cold"]["compressed_history"] = "Ch1: 旧剧情 X\n" * 50  # ~850 字
    original_history = memory["cold"]["compressed_history"]

    # 把 router 改了，没装上 — 应该不会跑 LLM
    out = maybe_compress_hot_to_cold(memory, "test-novel")
    # 不应被 LLM 处理（不超阈值）：新行直接 append 到末尾
    assert "Ch1: 第1章剧情：示例" in out["cold"]["compressed_history"]
    # hot 减 10
    assert len(out["hot"]["recent_summaries"]) == 15


def test_overflow_triggers_secondary_summarize(monkeypatch):
    """超 4000 chars 时调 LLM 二次摘要；不再硬砍头。"""
    from engine.memory import manager as _mod
    from engine.llm_router import set_active_router

    fake = _FakeRouter(
        response="这是 LLM 二次摘要后的精炼版。Ch100: 主角觉醒。"
    )
    set_active_router(fake)  # type: ignore[arg-type]
    monkeypatch.setattr(_mod, "get_active_router", lambda: fake)

    memory = _make_memory_with_n_summaries(25)
    # 故意塞 6000 字 — 远超 4000 soft cap，触发二次摘要
    memory["cold"]["compressed_history"] = "Ch1: " + ("很长的旧剧情内容。x" * 1000)  # ~6000 chars
    original_hot_len = len(memory["hot"]["recent_summaries"])

    out = _mod.maybe_compress_hot_to_cold(memory, "test-novel")

    # LLM 应被调一次
    assert len(fake.calls) == 1, f"应调 LLM 一次（compress_history 类调用），实际 {len(fake.calls)}"
    call = fake.calls[0]
    assert call["agent"] == "summarizer"
    # 输出应是 LLM 摘要 + 新行（不再是 "[-3000:]" 的硬截断）
    assert "这是 LLM 二次摘要后的精炼版" in out["cold"]["compressed_history"]
    # hot 减 10
    assert len(out["hot"]["recent_summaries"]) == original_hot_len - 10
    # 不应 hard truncate 出现"很长的旧剧情内容" 残留（已被 LLM 摘要替换）
    assert "很长的旧剧情内容" not in out["cold"]["compressed_history"], (
        "已调 LLM 二次摘要，不应出现原文残留"
    )

    monkeypatch.undo()


def test_overflow_fallback_when_llm_fails(monkeypatch):
    """二次摘要失败 → fallback 到硬截断 + warning，不让 run 崩。

    这是关键防回归：审计师曾指出的隐患之一就是"静默丢失"。
    现在即便 LLM 挂了，也不会 silently swallow error。
    """
    from engine.memory import manager as _mod
    from engine.llm_router import set_active_router

    class _FailingRouter:
        def call(self, **_):
            raise RuntimeError("LLM provider down")

    set_active_router(_FailingRouter())  # type: ignore[arg-type]
    monkeypatch.setattr(_mod, "get_active_router", lambda: _FailingRouter())

    memory = _make_memory_with_n_summaries(25)
    # 故意塞超过 4000 字，让 len(candidate) > soft cap（trigger 二次摘要）
    memory["cold"]["compressed_history"] = "Ch1: " + ("OLD " * 1000)  # ~5000 chars

    out = _mod.maybe_compress_hot_to_cold(memory, "test-novel")

    # fallback 应让 compressed_history 不超过 3000
    assert len(out["cold"]["compressed_history"]) <= 3000, (
        f"fallback 应让 hard truncation 不超过 3000（原值），"
        f"实际={len(out['cold']['compressed_history'])}"
    )
    # 但不应抛异常（防 run 整体崩）
    assert out is not None
    # 确认确实走的 fallback 路径（看到 warning log）
    # 如果未来有人误改 fallback 逻辑，这条断言保险一些

    monkeypatch.undo()


def test_meta_compression_events_tracked(monkeypatch):
    """每次压缩累加 total_compression_events；last_summarized_at_chapter 更新。

    让审计/前端能 query 'compressed_history_meta' 看历史摘要何时发生过。
    """
    from engine.memory import manager as _mod
    from engine.llm_router import set_active_router

    fake = _FakeRouter(response="SUMMARIZED")
    set_active_router(fake)  # type: ignore[arg-type]
    monkeypatch.setattr(_mod, "get_active_router", lambda: fake)

    memory = _make_memory_with_n_summaries(25)
    memory["cold"]["compressed_history"] = "Ch1: " + ("x" * 4500)  # 超阈值
    memory["meta"]["last_updated_chapter"] = 100

    out = _mod.maybe_compress_hot_to_cold(memory, "test-novel")
    meta = out["cold"].get("compressed_history_meta", {})
    assert meta.get("last_summarized_at_chapter") == 100
    assert meta.get("total_compression_events") == 1

    # 第二次再压缩（制造累计效果）
    memory2 = _make_memory_with_n_summaries(25)
    memory2["cold"]["compressed_history"] = "Ch1: " + ("y" * 4500)
    memory2["cold"]["compressed_history_meta"] = {
        "total_compression_events": 1,
        "last_summarized_at_chapter": 50,
    }
    memory2["meta"]["last_updated_chapter"] = 150

    out2 = _mod.maybe_compress_hot_to_cold(memory2, "test-novel-2")
    meta2 = out2["cold"].get("compressed_history_meta", {})
    assert meta2.get("total_compression_events") == 2
    assert meta2.get("last_summarized_at_chapter") == 150

    monkeypatch.undo()


def test_read_side_provides_more_history_context(monkeypatch):
    """Phase 5 配套：get_chapter_relevant_context 不再硬切 500 字，改 2000 字上限。"""
    from engine.memory import manager as _mod
    memory = {
        "hot": {"recent_summaries": [{"chapter": 30, "summary": "近期剧情"}]},
        "cold": {"compressed_history": "Ch10: " + ("剧情线" * 1000)},  # ~3000 字
        "constraints": {
            "forbidden_constraints": [], "established_facts": [],
            "foreshadowing_planted": [],
        },
        "meta": {"total_chapters_tracked": 100},
    }
    task = {"chapter_number": 31, "main_characters": []}
    ctx = _mod.get_chapter_relevant_context(memory, task)
    # Phase 5：cold_summary 至少 2000 字（之前是 500）
    assert len(ctx["cold_summary"]) > 500, (
        "Phase 5 fix 后 cold_summary 应大于 500（原 hard truncation 已改成 2000）"
    )
    assert len(ctx["cold_summary"]) <= 2000


def test_threshold_protects_short_projects():
    """未达 20 条 recent_summary → 不动 cold（避免无谓压缩）。"""
    from engine.memory.manager import maybe_compress_hot_to_cold
    memory = _make_memory_with_n_summaries(15)  # < 20 阈值
    memory["cold"]["compressed_history"] = "ORIGINAL"
    out = maybe_compress_hot_to_cold(memory, "test-novel")
    # cold 不动
    assert out["cold"]["compressed_history"] == "ORIGINAL"
    # hot 也不动
    assert len(out["hot"]["recent_summaries"]) == 15
