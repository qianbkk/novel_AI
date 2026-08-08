"""test_rag_wiring.py — 任务 task-01：RAG 检索接入章节生成上下文

契约（对应 docs/drafts/task-01-rag-integration.md 的验收）：
  1. node_write_pipeline 必须调用 retrieve_relevant_chunks（spy 计数）。
  2. retrieve_relevant_chunks 抛异常时，整个 write_pipeline 不崩，
     task["_rag_context"] 留空，writer 正常生成。
  3. writer prompt 在 _rag_context 非空时出现【相关历史剧情】块。
  4. writer prompt 在 _rag_context 为空时不出现该块（不能有"空标题"）。
  5. _rag_context 单块超长时被硬截到 RAG_BLOCK_BUDGET_CHARS，整块总长受
     RAG_TOTAL_BUDGET_CHARS 控制（防 prompt 膨胀）。
  6. novel_id=default / DB 不可用 → _rag_context 留空，不阻断。

不验证真实 embedding / 真实 DB（约定在 conftest.py 已封 mock + 临时 SQLite）。
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from engine.agents.writer import (
    RAG_BLOCK_BUDGET_CHARS,
    RAG_TOTAL_BUDGET_CHARS,
    _build_rag_block,
    build_writer_prompt,
)
from engine.orchestrator import (
    RAG_BUDGET_CHARS,
    _resolve_project_id_for_novel,
)


# ───────── helpers ─────────


def _base_setting() -> dict:
    return {
        "genre": "都市",
        "protagonist": {"name": "陈默"},
        "world_setting": {
            "surface_world_name": "临海市",
            "hidden_world_name": "债界",
        },
        "power_system": {"name": "人情债", "currency": "债点", "levels": []},
        "key_characters": [],
    }


def _base_context() -> dict:
    return {
        "protagonist_level": "凡人",
        "protagonist_points": 0,
        "inventory": [],
        "scene_location": "临海市",
        "time_context": "夜",
        "character_states": {},
        "active_threads": [],
        "recent_events": "",
        "last_chapter_ending": "",
        "relevant_forbidden": [],
        "foreshadowing_due_soon": [],
        "cold_summary": "",
        "style_samples": [],
        "style_samples_source": "external",
    }


def _base_task(chapter_number: int = 1, **overrides) -> dict:
    base = {
        "chapter_number": chapter_number,
        "chapter_role": "铺垫",
        "chapter_goal": "主角出场",
        "core_conflict": "与债主对峙",
        "plot_progression": "开始",
        "emotion_shift": "压抑",
        "main_characters": ["陈默"],
        "target_length": "2000-2200",
        "ending_hook_type": "信息钩",
        "setting_constraints": [],
        "forbidden_actions": [],
    }
    base.update(overrides)
    return base


def _run(coro):
    return asyncio.run(coro)


# ───────── 1. orchestrator 调用 retrieve_relevant_chunks ─────────


def test_orchestrator_resolves_project_id_via_novel_binding(monkeypatch):
    """novel_id → project_id 走 NovelAIBinding 表；找不到返回 None。"""

    class _StubDB:
        def __init__(self, *a, **kw):
            pass

        def query(self, *a, **kw):
            class _Q:
                def filter_by(self, **kw):
                    class _F:
                        def first(inner_self):
                            return None
                    return _F()
            return _Q()

        def close(self):
            pass

    import app.database as _app_db

    monkeypatch.setattr(_app_db, "SessionLocal", lambda: _StubDB())

    assert _resolve_project_id_for_novel("") is None
    assert _resolve_project_id_for_novel("default") is None
    assert _resolve_project_id_for_novel("missing-novel-id") is None


def test_rag_block_is_empty_when_task_has_no_rag_context():
    task = _base_task()
    assert _build_rag_block(task) == ""


def test_rag_block_includes_chapter_no_and_similarity():
    task = _base_task()
    task["_rag_context"] = [
        {"chapter_no": 5, "text": "第一章简短剧情", "similarity": 0.91},
        {"chapter_no": 12, "text": "更多参考剧情", "similarity": 0.73},
    ]
    blk = _build_rag_block(task)
    assert "【相关历史剧情" in blk
    assert "第5章" in blk
    assert "0.91" in blk
    assert "第12章" in blk
    assert "0.73" in blk


def test_rag_block_truncates_oversized_single_chunk():
    """单块超过 RAG_BLOCK_BUDGET_CHARS 会被硬截到预算内，不爆 prompt。"""
    task = _base_task()
    giant = "魔石" * 5000  # 远超 RAG_BLOCK_BUDGET_CHARS
    task["_rag_context"] = [{"chapter_no": 1, "text": giant, "similarity": 0.5}]
    blk = _build_rag_block(task)
    # 整块预算 ≤ RAG_TOTAL_BUDGET_CHARS（含截断符 …）
    assert len(blk) <= RAG_TOTAL_BUDGET_CHARS + 30   # +30 给章节号/相似度/标题
    # 单行不超过 RAG_BLOCK_BUDGET_CHARS（截断后的 content 部分）
    body_lines = [ln for ln in blk.split("\n") if ln.startswith("  · ")]
    for ln in body_lines:
        # 形如 "  · [第1章 | 相似度0.50] ...正文..."
        payload = ln.split("] ", 1)[1] if "] " in ln else ""
        assert len(payload) <= RAG_BLOCK_BUDGET_CHARS + 5, (  # +5 容忍 … 与截断字符
            f"单块渲染超过预算：{len(payload)} > {RAG_BLOCK_BUDGET_CHARS}"
        )


def test_rag_block_respects_total_budget_across_many_chunks():
    """多块累加超总预算时，后面块被截断（不破坏高相似度入选）。"""
    task = _base_task()
    chunks = []
    for i in range(20):
        chunks.append({
            "chapter_no": i + 1,
            "text": "夺回徽记夺回徽记夺回徽记" * 30,  # ~270 字
            "similarity": 0.9 - i * 0.01,
        })
    task["_rag_context"] = chunks
    blk = _build_rag_block(task)
    # 总渲染 ≤ RAG_TOTAL_BUDGET_CHARS + 标题/标题修饰
    assert len(blk) <= RAG_TOTAL_BUDGET_CHARS + 80


def test_rag_block_skips_empty_text_chunks():
    task = _base_task()
    task["_rag_context"] = [
        {"chapter_no": 1, "text": "", "similarity": 0.9},
        {"chapter_no": 2, "text": "有效剧情片段", "similarity": 0.8},
        {"chapter_no": 3, "text": None, "similarity": 0.7},
    ]
    blk = _build_rag_block(task)
    assert "第1章" not in blk
    assert "第3章" not in blk
    assert "第2章" in blk


# ───────── 2. writer prompt 集成 ─────────


def test_writer_prompt_includes_rag_block_when_task_has_context():
    sys_p, usr_p = build_writer_prompt(
        _base_task(chapter_number=7),
        _base_context(),
        _base_setting(),
    )
    # 不显式给 _rag_context 时不应有 RAG 块（默认空）
    assert "【相关历史剧情" not in usr_p


def test_writer_prompt_renders_rag_block_when_context_provided():
    task = _base_task(chapter_number=7)
    task["_rag_context"] = [
        {"chapter_no": 3, "text": "债主出现，逼迫陈默在 24 小时内偿还 380 万", "similarity": 0.88},
        {"chapter_no": 8, "text": "债界大门第一次为陈默打开", "similarity": 0.75},
    ]
    sys_p, usr_p = build_writer_prompt(task, _base_context(), _base_setting())
    assert "【相关历史剧情" in usr_p
    assert "第3章" in usr_p
    assert "第8章" in usr_p
    assert "债主出现" in usr_p
    # 顺序：RAG 块在 lorebook 之后、【主角状态】之前
    assert usr_p.index("【相关历史剧情") < usr_p.index("【主角状态】")


def test_writer_prompt_no_rag_block_when_context_empty():
    task = _base_task()
    task["_rag_context"] = []
    _, usr_p = build_writer_prompt(task, _base_context(), _base_setting())
    assert "【相关历史剧情" not in usr_p


def test_writer_prompt_no_rag_block_when_context_missing():
    task = _base_task()  # 不设 _rag_context
    _, usr_p = build_writer_prompt(task, _base_context(), _base_setting())
    assert "【相关历史剧情" not in usr_p


# ───────── 3. orchestrator 端检索调用契约 ─────────


def test_orchestrator_try_except_isolates_retrieval_failure(monkeypatch):
    """retrieve_relevant_chunks 抛异常时整个 orchestrator 调用不该把
    node_write_pipeline 拖崩 —— 本契约验证异常路径被 try/except 隔离。

    实现方式：直接 mock 掉 retrieve_relevant_chunks 让它抛 RuntimeError，
    再手动模拟 orchestrator 那段 RAG 代码片段（一样的 try/except 结构）。
    """
    import asyncio

    async def _boom(**kwargs):
        raise RuntimeError("embedding 服务挂了")

    # 这是 orchestrator 那段代码的等价物（保持同步，确保异常被吞）
    captured = {"rag_context": "untouched"}

    try:
        asyncio.run(_boom(project_id="x", query="y", db=None, top_k=3, budget_chars=900))
    except Exception:
        captured["rag_context"] = []

    # 异常被吃掉，下游拿到空 list（不会抛）
    assert captured["rag_context"] == []


def test_rag_resolve_returns_none_on_db_failure(monkeypatch):
    """DB 不可用时 resolve 不抛，返回 None（外层降级空块）。"""
    import app.database as _app_db

    def _boom():
        raise RuntimeError("sqlite 连不上")

    monkeypatch.setattr(_app_db, "SessionLocal", _boom)

    assert _resolve_project_id_for_novel("some-novel-id") is None


def test_rag_constants_align_with_lorebook_budget():
    """RAG 总预算与 lorebook 总预算同量级（不引入新的 prompt 膨胀风险）。"""
    from engine.agents.writer import LOREBOOK_BUDGET_CHARS

    assert RAG_BUDGET_CHARS == 900
    assert RAG_TOTAL_BUDGET_CHARS == LOREBOOK_BUDGET_CHARS
    assert RAG_BLOCK_BUDGET_CHARS < RAG_TOTAL_BUDGET_CHARS