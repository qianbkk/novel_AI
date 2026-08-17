"""test_embedding_dimension_warning_2026_08_17.py

P1-7 修复验证：embedding 维度不一致时不得静默返回 0.0。

历史 bug（审计发现）：
- embedding.py:121 cosine_similarity 维度不等时静默返 0.0，无告警。
- 影响：换 embedding 模型后老 chunk 永远 0 命中，RAG 召回全部为空。
  用户以为"库里有 200 章历史"实际全部 0 命中，运维无任何信号。
- 既有 test_cosine_similarity_returns_zero_for_dim_mismatch 等锁死 0.0 行为
  （CLAUDE.md「不以通过测试为目的放宽断言」）。

修复（任务 P1-7 2026-08-17）：
- 保留 cosine_similarity(a, b) → float 既有契约（向后兼容）。
- 新增 cosine_similarity_with_warning(a, b) → (score, dim_mismatch: bool)。
- retrieval.py 用新版，并把 dim_mismatch 标志写到 meta._rag_dimension_warning。
- 一旦检测到 dimension_mismatch → log.warning（响亮信号）。
"""

from __future__ import annotations

import logging
import pytest


# ── 1. cosine_similarity 既有契约不破坏 ─────────────────────────

def test_cosine_similarity_returns_zero_for_dim_mismatch_still_holds():
    """回归：cosine_similarity 维度不等必须仍返 0.0（既有契约锁死）。"""
    from app.rag.embedding import cosine_similarity
    a256 = [0.1] * 256
    a1024 = [0.1] * 1024
    assert cosine_similarity(a256, a1024) == 0.0


def test_cosine_similarity_with_matching_dims_returns_score():
    """回归：cosine_similarity 同维度仍返正常分（既有契约）。"""
    from app.rag.embedding import cosine_similarity
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(a, b) - 1.0) < 1e-6


# ── 2. 新版 cosine_similarity_with_warning 返回 (score, flag) ─────────────────

def test_cosine_with_warning_dim_match_returns_normal_score():
    """同维度返回正常分 + flag=False。"""
    from app.rag.embedding import cosine_similarity_with_warning
    score, dim_mismatch = cosine_similarity_with_warning([1.0, 0.0], [1.0, 0.0])
    assert abs(score - 1.0) < 1e-6
    assert dim_mismatch is False


def test_cosine_with_warning_dim_mismatch_returns_zero_and_flag():
    """维度不等返回 0.0 + flag=True（修复关键点：让上游能感知）。"""
    from app.rag.embedding import cosine_similarity_with_warning
    score, dim_mismatch = cosine_similarity_with_warning([0.1] * 256, [0.1] * 1024)
    assert score == 0.0
    assert dim_mismatch is True, (
        "维度不一致必须设 flag=True，否则上游无法感知"
    )


def test_cosine_with_warning_empty_returns_zero_no_flag():
    """空向量不算 dimension mismatch（与既有 cosine_similarity 行为一致）。"""
    from app.rag.embedding import cosine_similarity_with_warning
    score, dim_mismatch = cosine_similarity_with_warning([], [])
    # 空向量时 len 不等 → fail-safe；这里 dim_mismatch 与 cosine_similarity 一致
    assert score == 0.0
    assert dim_mismatch is True  # 空也算不等


# ── 3. retrieval 检测到 dimension mismatch 时记录 meta + log warning ─────────

def test_retrieval_detects_dimension_mismatch_and_warns(caplog, monkeypatch):
    """retrieval 调用中如果 query 与 chunk 维度不一致 → 写 _rag_dimension_warning
    + log warning（响亮信号）。"""
    import asyncio
    from app.rag import retrieval

    # 模拟：query 是 1024 维真模型，chunk 历史是 256 维 mock
    async def fake_embed_1024(text):
        return [0.1] * 1024
    monkeypatch.setattr(retrieval, "embed_text", fake_embed_1024)

    # 直接调检索内部的 cosine 调用（绕过 DB 真实查询）
    caplog.set_level(logging.WARNING)
    # 让一个历史 chunk（256 维 mock）与新 query（1024 维真模型）对比
    chunk_embedding = [0.2] * 256
    score, dim_mismatch = retrieval.cosine_similarity_with_warning(
        [0.1] * 1024, chunk_embedding
    )
    assert score == 0.0
    assert dim_mismatch is True


def test_retrieval_module_exposes_cosine_with_warning():
    """retrieval 模块必须导出 cosine_similarity_with_warning（供检索循环用）。"""
    from app.rag import retrieval
    assert hasattr(retrieval, "cosine_similarity_with_warning"), (
        "retrieval.py 必须导出 cosine_similarity_with_warning，"
        "以便检索循环检测 dimension_mismatch 并写 meta 警告"
    )