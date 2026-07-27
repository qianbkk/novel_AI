"""
对应调研结论里"图谱覆盖不到、但向量检索能补上"的场景，这里只做两件事：

1. check_repetition         —— 章节级语义重复度自检。直接对应番茄2026年2月
   整治公告里点名的检测维度"连续性章节重复率"——这是把平台拿来打击你的
   东西，先在自己手里跑一遍，而不是等发出去被系统判一遍。
2. semantic_search_chapters —— 图谱先筛候选（这个角色出现过的章节），
   向量再排序（语义最相关的排前面）。这是马良写作博客里那句"所有模型
   都会结合知识图谱（RAG），弥补上下文窗口的限制"的本地实现思路：
   图谱负责"精确范围"，向量负责"模糊相关性排序"，两者分工不同，
   不是二选一。

明确不做的事：不用向量相似度去判断"人物年龄/势力归属"这类事实性
一致性问题——这类问题该交给 stage_consistency_check 那种结构化查询，
向量检索对这类任务并不可靠（参考调研里"多跳推理在长上下文/向量检索
下都会衰减"的结论）。

2026-07-27 改造（修 §A1 缺口）：
- 整章 embed 一条向量 → 按场景切块（300-500 字/块，块间重叠 1-2 句）
- text_snippet 只存前 200 字 → 存块全文
- 引擎侧零引用 → 新增 retrieve_relevant_chunks / split_chapter_to_chunks
  供 writer prompt 接入"往事回响"块
- cosine_similarity 维度不一致的 fail-safe 不能被破坏（历史数据 mock-ngram
  256 维 vs 新 provider 1024 维混存时不能让 chunk 假阳性命中）
"""
from __future__ import annotations
import sqlite3
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Chapter, ChapterCharacter, Character, EmbeddingChunk
from .embedding import embed_text, cosine_similarity

REPETITION_THRESHOLD = 0.85  # 经验阈值；接入真实 embedding 模型后需要按实际相似度分布重新校准

# SQLite 错误码常量（python sqlite3 模块暴露的 sqlite_errorcode 返回值）
SQLITE_CONSTRAINT_UNIQUE = 2067  # UNIQUE 约束失败

# 切块目标区间（参考 lorebook 的 budget 做法，给 writer prompt 留预算是同一类约束）
CHUNK_SIZE_TARGET_MIN = 300
CHUNK_SIZE_TARGET_MAX = 500
CHUNK_OVERLAP_SENTENCES = 2  # 块间重叠句数（防切断因果）
SEARCH_BUDGET_CHARS = 900     # 单次检索注入 writer 的总字数预算（与 LOREBOOK_BUDGET_CHARS 对齐）


@dataclass(frozen=True)
class Chunk:
    """场景块。一章切出的可检索单元。

    text 是块全文（300-500 字），不再截前 200 字 —— 这是 §A1 改造的物理前提：
    检索命中后能拿回的不是章节首段，而是真正命中的那段剧情。
    """
    text: str
    chapter_no: int
    chunk_index: int          # 章内块序号，从 0 起
    source_id: str            # chapter.id；后续要回指原章节
    source_type: str = "chapter"  # chapter | character | foreshadowing（预留扩展）


class DuplicateChapterError(Exception):
    """同一 project 下 chapter_no 已存在时抛出 (security-2026-07-13 #1)。

    API 层捕获后返回 409 + 已有 chapter_id，提示前端做"跳到现有章节"或
    "先删除再新建"的二选一决策，而不是撞 500 让用户误以为后端崩了。
    """
    def __init__(self, chapter_no: int, existing_chapter_id: str):
        self.chapter_no = chapter_no
        self.existing_chapter_id = existing_chapter_id


async def add_chapter(project_id: str, chapter_no: int, title: str, content: str, db: Session) -> dict:
    chapter = Chapter(project_id=project_id, chapter_no=chapter_no, title=title, content=content)
    db.add(chapter)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        # 用 SQLite sqlite_errorcode 区分 UNIQUE 约束 vs 其他完整性错误——
        # 比字符串匹配更稳健，不受错误格式升级或新增 UniqueConstraint 影响。
        # 当前 Chapter 唯一的 UniqueConstraint 是 (project_id, chapter_no)，
        # 所以任何 UNIQUE 失败都视为重复；将来加第二个 UniqueConstraint 时
        # 需要进一步区分是哪个约束触发（可读 sqlite_message[table_name, *cols]）。
        if isinstance(e.orig, sqlite3.IntegrityError) and \
           getattr(e.orig, "sqlite_errorcode", None) == SQLITE_CONSTRAINT_UNIQUE:
            existing = (
                db.query(Chapter)
                .filter_by(project_id=project_id, chapter_no=chapter_no)
                .first()
            )
            raise DuplicateChapterError(
                chapter_no,
                existing.id if existing else "",
            ) from e
        raise

    # 图谱侧：标记这一章出现了哪些人物。原型阶段用字符串匹配够用；
    # 真实场景下这一步应该由生成阶段顺手标注（模型知道自己写了谁），
    # 而不是事后用人物名字扫一遍正文。
    characters = db.query(Character).filter_by(project_id=project_id).all()
    for c in characters:
        if c.name and c.name in content:
            db.add(ChapterCharacter(chapter_id=chapter.id, character_id=c.id))

    # 向量侧：embed 整章正文，存一份 chunk
    embedding = await embed_text(content)
    db.add(
        EmbeddingChunk(
            project_id=project_id,
            source_type="chapter",
            source_id=chapter.id,
            text_snippet=content[:200],
            embedding_json=embedding,
        )
    )
    db.flush()

    warnings = check_repetition(project_id, chapter.id, embedding, db)
    db.commit()
    return {"chapter_id": chapter.id, "repetition_warnings": warnings}


def check_repetition(project_id: str, chapter_id: str, embedding: list[float], db: Session) -> list[dict]:
    others = (
        db.query(EmbeddingChunk)
        .filter(
            EmbeddingChunk.project_id == project_id,
            EmbeddingChunk.source_type == "chapter",
            EmbeddingChunk.source_id != chapter_id,
        )
        .all()
    )
    warnings = [
        {"compared_chapter_id": other.source_id, "similarity": round(cosine_similarity(embedding, other.embedding_json), 3)}
        for other in others
    ]
    warnings = [w for w in warnings if w["similarity"] >= REPETITION_THRESHOLD]
    return sorted(warnings, key=lambda w: -w["similarity"])


async def semantic_search_chapters(
    project_id: str, query: str, character_id: str | None, top_k: int, db: Session
) -> list[dict]:
    candidate_chapter_ids = None
    if character_id:
        rows = db.query(ChapterCharacter.chapter_id).filter_by(character_id=character_id).all()
        candidate_chapter_ids = {r[0] for r in rows}
        if not candidate_chapter_ids:
            return []  # 这个角色还没在任何章节里出现过

    chunks = db.query(EmbeddingChunk).filter_by(project_id=project_id, source_type="chapter").all()
    if candidate_chapter_ids is not None:
        chunks = [c for c in chunks if c.source_id in candidate_chapter_ids]

    query_embedding = await embed_text(query)
    scored = [
        {
            "chapter_id": c.source_id,
            "similarity": round(cosine_similarity(query_embedding, c.embedding_json), 3),
            "snippet": c.text_snippet,
        }
        for c in chunks
    ]
    return sorted(scored, key=lambda s: -s["similarity"])[:top_k]


# ─── 场景切块（2026-07-27 §A1 改造） ────────────────────────────

_SENTENCE_END = ("。", "！", "？", "! ", "?", "\n")


def _split_into_sentences(text: str) -> list[str]:
    """把文本切成"句"。中文按 。！？/换行 分句，最后一段不足一句也算。

    实现刻意保持朴素：不引 hanlp / jieba / regex 库 —— 切块目标区间有 300-500
    字的容忍度，朴素分句的"句"略大或略小都不影响最终块大小落入区间。
    """
    sents: list[str] = []
    buf: list[str] = []
    for ch in text:
        buf.append(ch)
        if ch in _SENTENCE_END:
            sents.append("".join(buf).strip())
            buf = []
    if buf and "".join(buf).strip():
        sents.append("".join(buf).strip())
    return [s for s in sents if s]


def split_chapter_to_chunks(
    text: str, *, chapter_no: int, source_id: str,
) -> list[Chunk]:
    """按场景切块（300-500 字/块、块间重叠 1-2 句）。

    切法：贪心堆句，目标块长在 [CHUNK_SIZE_TARGET_MIN, CHUNK_SIZE_TARGET_MAX]
    之间；超出的句单独成块（保证末段不被吞）；块尾预留 CHUNK_OVERLAP_SENTENCES
    句进入下一块，防止切断因果。

    边界：
    - 短章（< MIN）至少产出 1 块（不能因切不下而丢弃整章）
    - 空文本返回 []（调用方拿空列表走降级路径，不抛）
    """
    sentences = _split_into_sentences(text or "")
    if not sentences:
        return []

    chunks: list[Chunk] = []
    cur: list[str] = []
    cur_len = 0
    idx = 0

    def _flush() -> None:
        nonlocal cur, cur_len, idx
        body = "".join(cur).strip()
        if body:
            chunks.append(Chunk(text=body, chapter_no=chapter_no,
                                chunk_index=idx, source_id=source_id))
            idx += 1
        cur = []
        cur_len = 0

    for s in sentences:
        s_len = len(s)
        # 累计加上这句是否仍 <= MAX；超了就先 flush
        if cur and cur_len + s_len > CHUNK_SIZE_TARGET_MAX:
            # 块尾预留 2 句进入下一块（重叠）：从 cur 末尾取走 CHUNK_OVERLAP_SENTENCES
            tail_n = min(CHUNK_OVERLAP_SENTENCES, len(cur))
            overlap = cur[-tail_n:]
            _flush()
            # overlap 是字符串列表，不能直接 .append 句子对象；写成句子片段
            cur = list(overlap)
            cur_len = sum(len(x) for x in cur)
        cur.append(s)
        cur_len += s_len
        # 达到 MIN 后允许 flush（即使未到 MAX）—— 保证短段不被拖入超大块
        if cur_len >= CHUNK_SIZE_TARGET_MIN:
            # 若已是块尾（后一句会越 MAX），由下次循环负责 flush
            pass

    _flush()  # 末段必 flush
    return chunks


# ─── 检索（§A1 验收 2/3/4） ────────────────────────────


async def retrieve_relevant_chunks(
    *,
    project_id: str,
    query: str,
    db: Session,
    top_k: int = 3,
    budget_chars: int = SEARCH_BUDGET_CHARS,
) -> list[dict]:
    """按相似度返回 top-k 块，总字符数 ≤ budget_chars。

    输入侧约定：
    - 库内是按场景切块入库的 EmbeddingChunk（text_snippet 存块全文，不再
      是 content[:200]）。本函数不关心入库粒度，能搜就能用。
    - 维度不一致时（fail-safe）：embedding.cosine_similarity 已经返回 0.0，
      因此历史 256 维 mock 数据与新 1024 维 provider 混存时**整库 cosine 全
      为 0，结果为空**，不会假阳性命中（§A1 验收 5）。

    降级契约（与 lorebook _build_lorebook_block 对齐）：
    - 库里没章节、查询无相关、查询为空、embed 抛异常 → 返回 []
      或向上抛（由调用方决定），绝不静默吞。
    """
    if budget_chars <= 0:
        return []
    if not (query or "").strip():
        return []
    if top_k <= 0:
        return []

    rows = (db.query(EmbeddingChunk)
              .filter(EmbeddingChunk.project_id == project_id,
                      EmbeddingChunk.source_type == "chapter")
              .all())
    if not rows:
        return []

    q_vec = await embed_text(query)
    scored: list[tuple[float, EmbeddingChunk]] = []
    for row in rows:
        sim = cosine_similarity(q_vec, row.embedding_json)
        if sim <= 0:
            continue  # 维度不一致的 chunk 直接跳过；已经 fail-safe
        scored.append((sim, row))
    if not scored:
        return []

    # 按相似度降序取 top_k，再按 budget 截断（不破坏高相似度的入选资格）
    scored.sort(key=lambda t: (-t[0], t[1].id))
    chosen = scored[:max(top_k * 4, 8)]  # 候选池留余地，让 budget 截断有选择

    # 清掉进程级缓存（每次检索独立，避免跨测试污染；进程级是 in-memory 的简配）
    _chapter_no_cache.clear()

    kept: list[dict] = []
    used = 0
    for sim, row in chosen:
        snippet = row.text_snippet or ""
        ch_no = _chapter_no_cached(db, row.source_id)
        if used + len(snippet) > budget_chars:
            if used == 0:
                # 第一块就超预算：硬截到剩余预算，避免返回 0 块
                snippet = snippet[:budget_chars]
                used += len(snippet)
                kept.append({
                    "chunk_id": row.id,
                    "chapter_no": ch_no,
                    "source_id": row.source_id,
                    "text": snippet,
                    "similarity": round(sim, 4),
                })
            break
        used += len(snippet)
        kept.append({
            "chunk_id": row.id,
            "chapter_no": ch_no,
            "source_id": row.source_id,
            "text": snippet,
            "similarity": round(sim, 4),
        })
        if len(kept) >= top_k:
            break
    return kept


def self_chapter_no(row, db: Session | None = None) -> int:
    """从 EmbeddingChunk 反查所属章节号。

    库内结构：EmbeddingChunk.source_id = chapter.id；Chapter.id 是字符串主键，
    Chapter.chapter_no 才是用户视角的章号。EmbeddingChunk 与 Chapter 没建
    SQLAlchemy relationship，所以这里做一次显式查询。

    db 可选：retriever 主循环里已经查过一次 Chapter，缓存避免 N+1。
    """
    sid = getattr(row, "source_id", None)
    if not sid or db is None:
        return 0
    try:
        ch = db.query(Chapter).filter_by(id=sid).first()
    except Exception:
        return 0
    if ch is None:
        return 0
    return ch.chapter_no if isinstance(ch.chapter_no, int) else 0


# 简单的 in-call 缓存：source_id -> chapter_no，避免每个 chunk 都打一次 DB
_chapter_no_cache: dict[str, int] = {}


def _chapter_no_cached(db: Session, source_id: str) -> int:
    if source_id in _chapter_no_cache:
        return _chapter_no_cache[source_id]
    ch = db.query(Chapter).filter_by(id=source_id).first()
    no = ch.chapter_no if ch is not None and isinstance(ch.chapter_no, int) else 0
    _chapter_no_cache[source_id] = no
    return no


# ─── 块入库（供 chapter_import / 手工 add_chapter 调用，§A1 改造兼容层）────

async def persist_chapter_chunks(
    *, project_id: str, chapter_id: str, chapter_no: int, content: str,
    db: Session, source_type: str = "chapter",
) -> list[Chunk]:
    """把一章切块并落 EmbeddingChunk（text_snippet 存块全文）。

    与旧的 add_chapter() 路径并存：旧路径仍写一条整章向量以保兼容性；
    新路径写场景块。两条路径都以 source_id = chapter_id 关联章节。
    """
    chunks = split_chapter_to_chunks(content, chapter_no=chapter_no,
                                      source_id=chapter_id)
    if not chunks:
        return []
    out: list[Chunk] = []
    for c in chunks:
        emb = await embed_text(c.text)
        row = EmbeddingChunk(
            project_id=project_id,
            source_type=source_type,
            source_id=chapter_id,
            text_snippet=c.text,   # 块全文，不再截前 200 字
            embedding_json=emb,
            model="",  # 留空：从 embed_text 调用链自动记录（兼容旧字段）
        )
        db.add(row)
        out.append(c)
    db.flush()
    return out
