"""test_rag_chunking_2026_07_27.py

架构审视 — 向量检索按场景切块 + 检索命中语义相关块 + 预算控制 + cosine fail-safe 保护。

背景（docs/drafts/architecture-roadmap-2026-07-27.md §A1）：

- 现状缺口（`app/rag/retrieval.py:76-85`）：
    * 整章 embed 成单一向量——一章 2000-3000 字压成一个向量，语义被平均掉，
      检索只能告诉你"这章大概相关"，没法定位到具体段落
    * `text_snippet=content[:200]`——只存前 200 字，开头往往是承接上章的过场，
      恰恰不是命中的那段内容
    * 引擎侧零引用，`app/rag/retrieval.py:111` 的 `semantic_search_chapters` 唯一的
      消费者是 `app/api/chapters.py:86` 的前端搜索框

- 本次改造按"切块 + 全文 snippet + 块级检索"实现：
    * `split_chapter_to_chunks()` 同章产出多条 Chunk，目标 300-500 字/块、
      块间 1-2 句重叠（防止切断因果）
    * `text_snippet` 存**块全文**（不截前 200 字）
    * `retrieve_relevant_chunks()` 命中的是**语义相关的那一块**，不是整章第一段
    * 检索受预算上限控制（参考 `LOREBOOK_BUDGET_CHARS=900`）
    * 维度不一致时 `cosine_similarity()` 返回 0.0 的 fail-safe 不能被破坏

测试文件**本身是 spec**：
- 待实现符号从 `app.rag.retrieval` 导入（`split_chapter_to_chunks` / `Chunk` /
  `retrieve_relevant_chunks`），目前不存在 → 收集阶段即 ImportError，强制先实现。
- 本文件**只写测试**，不写实现代码。

对照 §A1 验收列出的 5 条：
    1. 切块后同一章产生多条 chunk，块内容可完整取回
    2. 检索命中的是语义相关的那一块，不是整章第一段
    3. 查询无命中时注入空块，不影响写作（降级不阻断）
    4. 预算上限生效
    5. 维度不一致时 `cosine_similarity` 返回 0.0 的 fail-safe 行为不被破坏
"""
from __future__ import annotations

import asyncio

import pytest

from app.rag.embedding import cosine_similarity
from app.rag.retrieval import Chunk, retrieve_relevant_chunks, split_chapter_to_chunks


# ─── 常量（spec 锚点，对照 architecture-roadmap-2026-07-27.md §A1）─────────

# 切块目标区间：300-500 字/块、块间 1-2 句重叠
CHUNK_SIZE_TARGET_MIN = 300
CHUNK_SIZE_TARGET_MAX = 500

# 检索预算：与 LOREBOOK_BUDGET_CHARS 同量级，但**不强制相等**
# A1 改法说"参考 LOREBOOK_BUDGET_CHARS 的做法"——具体值由实现自定
SEARCH_BUDGET_CHARS = 900


# ─── fixtures ────────────────────────────


@pytest.fixture
def chapter_factory(isolated_test_db):
    """造一个 project，返回 (add_chapter, project_id) 供检索测试插入数据。"""
    from app.database import SessionLocal
    from app.models import Project, Chapter

    db = SessionLocal()
    try:
        p = Project(
            title="rag-chunking-spec",
            genre="西幻",
            audience="男频·青年向",
            config_json={},
        )
        db.add(p)
        db.commit()
        db.refresh(p)
        pid = p.id
    finally:
        db.close()

    def _add(chapter_no: int, content: str, title: str | None = None) -> str:
        d = SessionLocal()
        try:
            ch = Chapter(
                project_id=pid,
                chapter_no=chapter_no,
                title=title or f"第{chapter_no}章",
                content=content,
            )
            d.add(ch)
            d.commit()
            d.refresh(ch)
            return ch.id
        finally:
            d.close()

    return _add, pid


@pytest.fixture
def fixed_vocab_embed(monkeypatch):
    """把 retrieval 模块里的 embed_text 替换成字典化 mock。

    设计：构造一个固定词表，把文本里出现的词映射成 0/1 向量——这样"共享关键词"
    的两段文字余弦相似度天然接近 1.0，"完全不沾边"的相似度天然接近 0.0。
    便于精确控制"哪个块相关、哪个不相关"，不必依赖真实 embedding 行为。

    注：实际实现里 `embed_text` 应该走 `app.rag.embedding.embed_text`（带 mock
    fallback）。这里直接 monkeypatch retrieval 模块的属性，让测试不依赖网络。
    """
    vocab = sorted(set("艾德里安莉拉凯恩深渊回廊夺回徽记对峙法师魔纹魔石回廊封锁雨夜市集"))

    def _embed(text: str) -> list[float]:
        text_chars = set(text)
        return [1.0 if c in text_chars else 0.0 for c in vocab]

    import app.rag.retrieval as retrieval_mod
    monkeypatch.setattr(retrieval_mod, "embed_text", _embed)
    return vocab, _embed


def _run(coro):
    """统一包裹 asyncio.run，与仓库其它测试一致（test_chapter_uniqueness 同模式）。"""
    return asyncio.run(coro)


# ─── 1. 切块后同一章产生多条 chunk，块内容可完整取回 ─────────────────────────


def test_long_chapter_produces_multiple_chunks():
    """长章（>1000 字）必须切出多条 chunk。"""
    long_content = (
        "这是第一章的开头。" + "无关的过渡文字。" * 80
        + "主角在深渊回廊夺回家族徽记。" + "打斗声渐渐远去。" * 80
        + "魔石耗尽，夜色降临。" + "他回到宿营地。" * 80
    )
    chunks = split_chapter_to_chunks(long_content, chapter_no=1, source_id="c1")
    assert len(chunks) > 1, f"长章应切多块，实际 {len(chunks)} 块"


def test_short_chapter_produces_at_least_one_chunk():
    """短章（< 块大小）至少 1 块（不能因切不下而丢弃整章）。"""
    chunks = split_chapter_to_chunks("只有一句话。", chapter_no=1, source_id="c1")
    assert len(chunks) >= 1


def test_each_chunk_is_chunk_dataclass():
    """返回值必须是 Chunk 实例，不是裸 str/dict（spec 锚点：Chunk 必须存在）。"""
    chunks = split_chapter_to_chunks("一些内容。" * 200, chapter_no=1, source_id="c1")
    for c in chunks:
        assert isinstance(c, Chunk)


def test_chunk_carries_chapter_no_and_chunk_index():
    """每块必须带回 chapter_no 与 chunk_index（用于回指和排序）。"""
    chunks = split_chapter_to_chunks("一些内容。" * 200, chapter_no=7, source_id="c7")
    for i, c in enumerate(chunks):
        assert c.chapter_no == 7
        assert c.chunk_index == i  # 块序号从 0 起连续


def test_chunk_text_is_full_text_not_truncated_to_200():
    """核心改造：text 存块全文（300-500 字），不再是 content[:200]。"""
    # 一段超过 200 字的"中段"剧情，必须在 chunk 里完整可见
    long_block_text = "夺回徽记的具体过程。" + "细节描述。" * 100  # ~500+ 字
    full_content = (
        "开篇过场。魔石耗尽。他回到宿营地。" * 5
        + long_block_text
        + " 尾声。" * 5
    )
    chunks = split_chapter_to_chunks(full_content, chapter_no=1, source_id="c1")
    hit = next((c for c in chunks if long_block_text[:50] in c.text), None)
    assert hit is not None, "应至少有一块包含完整原文段"
    # 块内容应包含 long_block_text 的尾部（说明不是截前 200 字）
    assert long_block_text[-20:] in hit.text, (
        "chunk.text 只截了前段——A1 改法要求存块全文，不能退化为 content[:200]"
    )


def test_chunk_size_stays_in_target_range():
    """块大小在 300-500 字目标区间。允许小幅溢出（重叠部分）。"""
    long_content = "。" * 1500  # 1500 字，全句号
    chunks = split_chapter_to_chunks(long_content, chapter_no=1, source_id="c1")
    assert len(chunks) >= 3
    # 上下限留余量（重叠 + 末块短）
    upper = CHUNK_SIZE_TARGET_MAX + 100
    lower = max(1, CHUNK_SIZE_TARGET_MIN - 200)
    for c in chunks:
        assert len(c.text) <= upper, (
            f"块过大 {len(c.text)} > {upper}"
        )
        assert len(c.text) >= lower or c is chunks[-1], (
            f"非末块过小 {len(c.text)} < {lower}"
        )


def test_chunks_overlap_to_preserve_causality():
    """块间重叠 1-2 句：相邻块的尾句应出现在下一块的开头（防切断因果）。"""
    sentences = [f"第{i}句内容。" for i in range(1, 31)]
    content = "".join(sentences)
    chunks = split_chapter_to_chunks(content, chapter_no=1, source_id="c1")
    assert len(chunks) >= 2
    # 第一块的尾部应至少有一个完整句出现在任一后续块里
    tail = chunks[0].text[-30:]
    assert any(tail in c.text for c in chunks[1:]), (
        f"块间无重叠：块 0 尾部 {tail!r} 不在任何后续块里"
    )


def test_chunks_preserve_head_and_tail():
    """合并所有块的原文不应丢失首尾关键信息（保证覆盖率）。"""
    head = "开篇标志性内容"
    tail = "结尾标志性内容"
    full = head + "。" + "中间过渡。" * 200 + tail
    chunks = split_chapter_to_chunks(full, chapter_no=1, source_id="c1")
    joined = "".join(c.text for c in chunks)
    assert head in joined, "首段应保留"
    assert tail in joined, "尾段应保留"


# ─── 2. 检索命中的是语义相关的那一块，不是整章第一段 ─────────────────────────


def test_chunking_lets_relevant_segment_stand_alone():
    """切块必须让"语义相关段"独立成块——这是"检索命中相关块"的物理前提。"""
    head_padding = "开篇过场，与检索无关。" + "无意义的过渡。" * 20
    relevant = "主角在深渊回廊夺回家族徽记，与凯恩对峙。"
    tail_padding = "结尾过场，无关。" + "收尾。" * 20
    content = head_padding + relevant + tail_padding
    chunks = split_chapter_to_chunks(content, chapter_no=1, source_id="c1")
    # 至少有一块以 relevant 为主（不是把它拆散到多个块里稀释）
    standalone = [c for c in chunks if relevant[:40] in c.text]
    assert standalone, "切块应让相关剧情独立成块，否则检索无法精准命中"


def test_search_returns_top_chunks_by_similarity(
    chapter_factory, fixed_vocab_embed,
):
    """search 返回按相似度降序的块列表。"""
    _add, pid = chapter_factory
    # 三章：第一章讲夺回徽记，第二章无关，第三章讲凯恩对峙
    _add(1, "主角夺回家族徽记。回到宿营地。魔石耗尽。" * 10)
    _add(2, "雨夜的市集。买魔石。回旅店。" * 10)
    _add(3, "凯恩派人封锁回廊，与主角对峙。" * 10)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回徽记", db=db, top_k=3,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        sims = [r["similarity"] for r in results]
        assert sims == sorted(sims, reverse=True), (
            f"结果未按相似度降序：{sims}"
        )
        assert results, "至少应返回第一章"
        assert results[0]["chapter_no"] == 1, (
            f"top-1 应该是讲夺回徽记的第 1 章，实际 chapter_no={results[0]['chapter_no']}"
        )
    finally:
        db.close()


def test_search_hits_relevant_block_not_chapter_head(
    chapter_factory, fixed_vocab_embed,
):
    """核心验收：检索命中的是**语义相关块**，不是整章第一段。"""
    _add, pid = chapter_factory

    # 一章：开头是过场（雨/市集），中段才是夺回徽记，结尾过场
    head = "本章开头：雨天的市集，买了一袋魔石。" * 5
    middle = "主角在深渊回廊夺回家族徽记，与凯恩对峙。"
    tail = "本章结尾：他回到宿营地。" * 5
    content = head + middle + tail + ("无关的过渡填充文字。" * 50)
    _add(1, content)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回家族徽记", db=db, top_k=2,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        assert results, "应至少返回一个块"
        top_text = results[0]["text"]
        # 命中块应包含 middle 的核心关键词（"夺回""徽记"），不应只是 head 的市集描述
        assert "夺回" in top_text and "徽记" in top_text, (
            f"应命中 middle 块，实际命中：{top_text[:80]!r}"
        )
        # 不应只命中 head——head 里只有"市集""魔石"，没有"夺回"
        assert "夺回家族徽记" in top_text, (
            "命中块只命中了开头/结尾过场，没命中真正的剧情段"
        )
    finally:
        db.close()


# ─── 3. 查询无命中时注入空块，不影响写作（降级不阻断）─────────────────────────


def test_search_with_no_chapters_returns_empty_list(
    chapter_factory, fixed_vocab_embed,
):
    """库里没章节 → 返回空列表，不抛、不返 None。"""
    _add, pid = chapter_factory

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回徽记", db=db, top_k=3,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        assert results == [], f"应返回空列表，实际 {results!r}"
    finally:
        db.close()


def test_search_with_unrelated_query_returns_empty(
    chapter_factory, fixed_vocab_embed,
):
    """query 与库内所有章节都无关 → 返回空（不能把所有块都假阳性命中）。"""
    _add, pid = chapter_factory

    # 库里只有市集/赶路/看星星
    _add(1, "雨天的市集。买魔石。回旅店。" * 10)
    _add(2, "夜里赶路。爬山。看星星。" * 10)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        # query 用字典里完全不存在的字符 → fake_embed 全 0 → cosine 0
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="ZZZZZZZZ", db=db, top_k=3,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        assert results == [], (
            f"无相关 query 应返回空，实际 {len(results)} 个结果："
            f"{[r.get('similarity') for r in results]}"
        )
    finally:
        db.close()


def test_search_does_not_raise_on_empty_query(
    chapter_factory, fixed_vocab_embed,
):
    """空 query 不崩。"""
    _add, pid = chapter_factory
    _add(1, "一些内容。夺回徽记。" * 10)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="", db=db, top_k=3,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        assert isinstance(results, list), f"应返回 list，实际 {type(results)!r}"
    finally:
        db.close()


def test_search_exception_does_not_silently_swallow(
    chapter_factory, monkeypatch,
):
    """检索抛异常时必须**响亮**：要么显式返回空、要么显式抛——绝不能伪装成功。

    与 worldbook 失败降级（test_lorebook_wiring::test_block_failure_…）对齐：
    检索是增强项，坏了不能阻断写作，但不能让模型误以为"没检索到相关剧情"
    = "没有相关剧情"。
    """
    _add, pid = chapter_factory
    _add(1, "一些内容。" * 50)

    import app.rag.retrieval as retrieval_mod

    async def _boom(*a, **kw):
        raise RuntimeError("embedding 服务挂了")

    monkeypatch.setattr(retrieval_mod, "embed_text", _boom)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        # 实现可选两种策略：显式返回 [] 或显式抛——但不能静默吞
        try:
            results = _run(retrieve_relevant_chunks(
                project_id=pid, query="夺回徽记", db=db, top_k=3,
                budget_chars=SEARCH_BUDGET_CHARS,
            ))
        except RuntimeError:
            return  # 响亮失败，OK；调用方负责 try/except
        assert results == [], (
            f"embed 失败时应返回空或抛，实际返回 {len(results)} 个结果"
        )
    finally:
        db.close()


# ─── 4. 预算上限生效 ─────────────────────────


def test_search_respects_total_budget(
    chapter_factory, fixed_vocab_embed,
):
    """返回的块总字符数必须 <= budget。"""
    _add, pid = chapter_factory

    # 塞 5 章，每章都很"相关"
    for i in range(1, 6):
        _add(i, "主角夺回家族徽记。凯恩派人封锁回廊。魔石耗尽。" * 20)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回徽记", db=db, top_k=10,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        total = sum(len(r["text"]) for r in results)
        assert total <= SEARCH_BUDGET_CHARS, (
            f"超出预算：total={total} > budget={SEARCH_BUDGET_CHARS}，"
            f"top_k={len(results)}，results={[len(r['text']) for r in results]}"
        )
    finally:
        db.close()


def test_search_budget_smaller_than_one_block_truncates(
    chapter_factory, fixed_vocab_embed,
):
    """预算 < 单块大小时，应只返回部分块或不返回，而不是单块超出。"""
    _add, pid = chapter_factory
    _add(1, "夺回家族徽记的完整剧情。" * 30)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回徽记", db=db, top_k=5,
            budget_chars=50,  # 故意压到比一块还小
        ))
        total = sum(len(r["text"]) for r in results)
        assert total <= 50, f"应严格遵守预算，实际 total={total}"
    finally:
        db.close()


def test_search_zero_budget_returns_empty(
    chapter_factory, fixed_vocab_embed,
):
    """budget=0 是合法值（"不要注入"），必须返回空。"""
    _add, pid = chapter_factory
    _add(1, "夺回徽记的完整剧情。" * 30)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回徽记", db=db, top_k=5,
            budget_chars=0,
        ))
        assert results == [], f"budget=0 应返回空，实际 {len(results)} 个结果"
    finally:
        db.close()


def test_search_higher_similarity_wins_under_budget(
    chapter_factory, fixed_vocab_embed,
):
    """预算有限时，更相似的块应优先入选（不是按 chapter_no 或入库顺序）。"""
    _add, pid = chapter_factory

    # 第 1 章只沾一点点边，第 2 章完全命中，第 3 章又只沾一点点边
    _add(1, "雨夜的市集，买了一袋魔石。" * 10)  # 含 "魔石"，弱命中
    _add(2, "主角在深渊回廊夺回家族徽记，与凯恩对峙。" * 10)  # 完全命中
    _add(3, "夜里赶路，爬山看星星。" * 10)  # 含 "夜里"，几乎不命中

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回家族徽记", db=db, top_k=1,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        assert results, "应返回至少一个块"
        assert results[0]["chapter_no"] == 2, (
            f"top-1 应是最相似的第 2 章，实际 chapter_no={results[0]['chapter_no']}"
        )
    finally:
        db.close()


# ─── 5. cosine_similarity 维度不一致的 fail-safe 不能被破坏 ─────────────────────────


def test_cosine_similarity_returns_zero_for_dim_mismatch():
    """fail-safe 单元：维度不一致 → 0.0。"""
    a256 = [0.1] * 256
    a1024 = [0.1] * 1024
    assert cosine_similarity(a256, a1024) == 0.0
    assert cosine_similarity(a1024, a256) == 0.0


def test_cosine_similarity_returns_zero_for_unequal_arbitrary_dims():
    """fail-safe 单元：任意维度不匹配都 → 0.0。"""
    assert cosine_similarity([1.0, 0.0], [0.5, 0.5, 0.5]) == 0.0
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0]) == 0.0


def test_cosine_similarity_returns_zero_for_empty_vectors():
    """fail-safe 单元：空向量 → 0.0。"""
    assert cosine_similarity([], []) == 0.0
    assert cosine_similarity([0.1, 0.2], []) == 0.0
    assert cosine_similarity([], [0.1, 0.2]) == 0.0


def test_cosine_similarity_with_matching_dims_returns_normalized_score():
    """回归保护：fail-safe 不能扩大到"所有维度相同的相似度也归零"。"""
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert abs(cosine_similarity(a, b) - 1.0) < 1e-6, (
        "维度相同且方向一致时相似度应 ≈ 1.0，不能误归零"
    )
    a2 = [1.0, 0.0, 0.0]
    b2 = [0.0, 1.0, 0.0]
    assert abs(cosine_similarity(a2, b2)) < 1e-6, (
        "维度相同且正交时相似度应 ≈ 0.0"
    )


def test_search_with_dim_mismatch_does_not_produce_false_positives(
    chapter_factory, monkeypatch,
):
    """fail-safe 集成：query 与 chunk 维度不一致时，整库 cosine 应为 0，
    不能让 chunk 假阳性命中（**这是 fail-safe 在检索路径上不能被破坏的关键**）。

    模拟真实场景：历史数据是 256 维（mock-ngram），切块改造后用新 provider（1024 维），
    旧 chunk 与新 query 不能错配。
    """
    _add, pid = chapter_factory
    _add(1, "主角夺回家族徽记。回到宿营地。" * 10)

    import app.rag.retrieval as retrieval_mod

    # query 走 1024 维，chunk 走 256 维（模拟新旧混存）
    async def _embed_query(text):
        return [0.01] * 1024

    async def _embed_chunk_text(text):
        return [0.01] * 256

    # 通过 stub 控制：先调一次 embed_text(query)，再调多次 embed_text(chunk_text)
    # 简单做法：让 query 第一次调用后切换 provider
    state = {"called": 0}

    async def _fake_embed_text(text):
        state["called"] += 1
        if state["called"] == 1:
            return [0.01] * 1024
        return [0.01] * 256

    monkeypatch.setattr(retrieval_mod, "embed_text", _fake_embed_text)

    from app.database import SessionLocal
    db = SessionLocal()
    try:
        results = _run(retrieve_relevant_chunks(
            project_id=pid, query="夺回徽记", db=db, top_k=3,
            budget_chars=SEARCH_BUDGET_CHARS,
        ))
        # 维度不一致时 cosine_similarity 必须返回 0.0 → 不命中
        assert all(r["similarity"] == 0.0 for r in results), (
            f"维度不一致却命中了：{[(r.get('chapter_no'), r.get('similarity')) for r in results]}"
        )
    finally:
        db.close()