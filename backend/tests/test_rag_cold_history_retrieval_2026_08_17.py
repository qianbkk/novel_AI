"""test_rag_cold_history_retrieval_2026_08_17.py

P2-11 修复验证：RAG 必须能检索 cold.compressed_history（10+ 章前剧情）。

历史 bug（审计发现，docs/wiki/03-Writing-Engine.md 自承）：
- backend/app/rag/retrieval.py:semantic_search_chapters 只查
  `EmbeddingChunk.source_type == "chapter"`，不查 cold.compressed_history。
- 影响：长篇 10+ 章前的承诺/约定/伏笔（只在 L2 cold 文本里有）召不回，
  writer 拿不到"前情提要"，长程一致性崩塌。
- 比 RAG 不接 lorebook alias 更致命：lorebook 至少有 query 触发匹配机制，
  cold 完全没有 query path。

修复（任务 P2-11 2026-08-17）：
1. 新增 embed_cold_history() 帮助函数：把 cold.compressed_history 摘要
   单独 embed 后写入 EmbeddingChunk，source_type="cold_history"
2. semantic_search_chapters 同时查 source_type in ["chapter", "cold_history"]，
   按 similarity 排序后取 top_k
3. cold_history 命中加 source_type 标记（前端可显示"来源：弧 X 摘要"）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ── 1. semantic_search_chapters 必须支持 cold_history ─────────────────

def test_semantic_search_includes_cold_history():
    """semantic_search_chapters 必须查 source_type 含 cold_history 的 chunks。"""
    import inspect
    from app.rag import retrieval

    src = inspect.getsource(retrieval.semantic_search_chapters)
    assert "cold_history" in src, (
        "semantic_search_chapters 必须查 EmbeddingChunk.source_type = 'cold_history'，"
        "否则 10+ 章前的剧情承诺召不回（长程一致性崩塌）"
    )


def test_semantic_search_returns_cold_history_results():
    """mock EmbeddingChunk：同时含 chapter 与 cold_history，verify 两者都返。"""
    from app.rag import retrieval

    # mock DB session
    fake_db = MagicMock()
    fake_chunk_chapter = MagicMock()
    fake_chunk_chapter.source_id = "ch_0010"
    fake_chunk_chapter.embedding_json = [0.5] * 256
    fake_chunk_chapter.text_snippet = "10 章前主角主角主角承诺过承诺过..."

    fake_chunk_cold = MagicMock()
    fake_chunk_cold.source_id = "arc_1_cold"
    fake_chunk_cold.embedding_json = [0.5] * 256  # 与上面相同 → cosine=1.0
    fake_chunk_cold.text_snippet = "弧1 冷层摘要：主角主角承诺过..."

    # query 走 filter_by(project_id=..., source_type=...)
    fake_query = MagicMock()
    fake_query.all.return_value = [fake_chunk_chapter, fake_chunk_cold]
    fake_db.query.return_value.filter.return_value = fake_query

    # import embed_text 模块，避免走真实 LLM
    from app.rag import embedding as emb_mod
    orig_embed = emb_mod.embed_text
    emb_mod.embed_text = lambda text: __import__("asyncio").coroutine(
        lambda: [0.5] * 256
    )() if False else (lambda t: __import__("asyncio").run(_async_embed(t)))
    async def _async_embed(t):
        return [0.5] * 256

    # 简单方式：直接 patch embed_text 为同步 fake
    async def fake_embed_text(text):
        return [0.5] * 256
    retrieval.embed_text = fake_embed_text

    import asyncio
    result = asyncio.run(retrieval.semantic_search_chapters(
        project_id="test", query="承诺", character_id=None, top_k=5, db=fake_db
    ))

    # 至少要返 cold_history 命中（不能只返 chapter）
    has_cold = any(r.get("chapter_id") == "arc_1_cold" for r in result)
    assert has_cold, (
        f"semantic_search_chapters 必须把 cold_history 命中包含在结果里，实际: "
        f"{[r.get('chapter_id') for r in result]}"
    )


# ── 2. embed_cold_history 帮助函数存在 ─────────────────────────

def test_embed_cold_history_helper_exists():
    """backend/app/rag/retrieval.py 必须有 embed_cold_history 帮助函数
    把冷层摘要 embed 后写入 EmbeddingChunk（source_type='cold_history'）。"""
    from app.rag import retrieval
    assert hasattr(retrieval, "embed_cold_history"), (
        "retrieval.py 必须导出 embed_cold_history，"
        "供 orchestrator 在 arc_end 时调用，把摘要写入向量库"
    )


def test_embed_cold_history_writes_with_source_type_cold():
    """embed_cold_history 必须把 source_type 标为 'cold_history'（区分章节）。"""
    import inspect
    from app.rag import retrieval

    src = inspect.getsource(retrieval.embed_cold_history) if hasattr(retrieval, "embed_cold_history") \
        else ""
    assert "cold_history" in src, (
        "embed_cold_history 必须用 source_type='cold_history' 写入 EmbeddingChunk"
    )