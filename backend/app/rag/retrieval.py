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
"""
import sqlite3
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Chapter, ChapterCharacter, Character, EmbeddingChunk
from .embedding import embed_text, cosine_similarity

REPETITION_THRESHOLD = 0.85  # 经验阈值；接入真实 embedding 模型后需要按实际相似度分布重新校准

# SQLite 错误码常量（python sqlite3 模块暴露的 sqlite_errorcode 返回值）
SQLITE_CONSTRAINT_UNIQUE = 2067  # UNIQUE 约束失败


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
