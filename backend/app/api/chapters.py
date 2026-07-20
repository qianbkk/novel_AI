from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth_scope import require_owned_project
from ..chapter_rewrite import (
    ChapterNotFoundError, RewriteConflictError, rewrite_chapter,
)
from ..database import get_db
from ..llm_client import LLMError
from ..models import Chapter, ChapterCharacter, Character
from ..novel_extract import ExtractConflictError, extract_setting_from_chapters
from ..schemas import ChapterCreate, ChapterFull
from ..rag.retrieval import add_chapter, semantic_search_chapters, DuplicateChapterError

router = APIRouter(prefix="/projects/{project_id}/chapters", tags=["chapters"])


def _owner_check(request: Request, project_id: str, db: Session = Depends(get_db)):
    """Phase 4 跨用户隔离：所有 project-scoped 路由都强制 owner 校验。

    dev 模式默认放行（兼容旧客户端），prod 模式（NOVEL_PRODUCTION=1）严格 403。
    """
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        raise HTTPException(401, "authentication required")
    require_owned_project(db, project_id, user)
    return user  # 路由侧若需要 user 可读取（当前未用）


@router.get("")
def list_chapters(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """章节列表，按章节号排序，正文只给前 80 字预览——列表页不需要整章内容。

    Phase 4：依赖 _owner_check，已登录 user 仅看到自己的 project 的章节。
    """
    rows = (
        db.query(Chapter)
        .filter_by(project_id=project_id)
        .order_by(Chapter.chapter_no.asc())
        .all()
    )
    # 复用 chapter_import 的 _clean_content_for_import：剥 [待修订] 前缀 + JSON 包装
    # —— list 端是 raw content，但 import 流程已经在写入前清理过，这里再兜一次
    # 防御旧数据（之前 run 未做内容清理时落盘的 chapter 行）。
    from ..bridge.chapter_import import _clean_content_for_import
    return [
        {
            "id": r.id,
            "chapter_no": r.chapter_no,
            "title": r.title,
            "content_preview": _clean_content_for_import(r.content or "")[:80],
            "word_count": len(_clean_content_for_import(r.content or "")),
            "created_at": r.created_at,
        }
        for r in rows
    ]


@router.get("/search")
async def search_chapters(
    project_id: str,
    request: Request,
    query: str,
    character_id: str | None = None,
    top_k: int = 5,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """
    语义检索：传 character_id 时，先用图谱把候选范围收窄到"这个角色出现过的章节"，
    再用向量相似度排序——图谱负责精确范围，向量负责模糊相关性，分工不是二选一。

    注意：这个路由必须注册在 "/{chapter_id}" 之前——否则 FastAPI 会把
    "search" 当成 chapter_id 的值匹配到 get_chapter 上去。

    Phase 4：跨项目越权读取向量检索结果也属于资源泄漏，必加 owner 校验。
    """
    return await semantic_search_chapters(project_id, query, character_id, top_k, db)


@router.get("/{chapter_id}", response_model=ChapterFull)
def get_chapter(
    project_id: str,
    chapter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """单章详情（含完整正文 + 出场人物列表）。

    Phase 4：跨用户读别人章节正文=严重信息泄漏，必加 owner 校验。
    """
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "chapter not found")
    # 关联出场人物
    edges = (
        db.query(ChapterCharacter, Character)
        .join(Character, ChapterCharacter.character_id == Character.id)
        .filter(ChapterCharacter.chapter_id == chapter_id)
        .all()
    )
    characters = [
        {
            "id": edge.ChapterCharacter.id,
            "character_id": edge.Character.id,
            "character_name": edge.Character.name,
            "character_role": edge.Character.role,
        }
        for edge in edges
    ]
    from ..bridge.chapter_import import _clean_content_for_import
    return ChapterFull(
        id=chapter.id,
        chapter_no=chapter.chapter_no,
        title=chapter.title,
        content=_clean_content_for_import(chapter.content or ""),
        created_at=chapter.created_at,
        characters=characters,
    )


@router.get("/{chapter_id}/characters")
def get_chapter_characters(
    project_id: str,
    chapter_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """某章出场的角色列表（来自 ChapterCharacter 图谱边）。"""
    chapter = db.get(Chapter, chapter_id)
    if not chapter or chapter.project_id != project_id:
        raise HTTPException(404, "chapter not found")
    rows = (
        db.query(ChapterCharacter, Character)
        .join(Character, ChapterCharacter.character_id == Character.id)
        .filter(ChapterCharacter.chapter_id == chapter_id)
        .all()
    )
    return [
        {
            "id": edge.ChapterCharacter.id,
            "character_id": edge.Character.id,
            "character_name": edge.Character.name,
            "character_role": edge.Character.role,
        }
        for edge in rows
    ]


@router.post("")
async def create_chapter(
    project_id: str,
    payload: ChapterCreate,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """
    新增一章正文：落库 + 自动标记出场人物（图谱边）+ embed 全文（向量）+
    跑一次重复度检测。返回结果里的 repetition_warnings 是"建议复核"，
    不会拦截保存——拦不拦是作者的判断，不是系统的判断。

    Phase 4：写入类更要校验 — 否则用户能直接往别人 project 里塞内容。
    """
    try:
        return await add_chapter(project_id, payload.chapter_no, payload.title, payload.content, db)
    except DuplicateChapterError as e:
        # security-2026-07-13 #1: 同一 (project_id, chapter_no) 唯一约束触发，
        # 前端可以做"跳到已有章节 / 先删除再新建"二选一。
        raise HTTPException(
            status_code=409,
            detail={
                "code": "duplicate_chapter_no",
                "message": f"chapter_no={e.chapter_no} already exists",
                "existing_chapter_id": e.existing_chapter_id,
            },
        )


class NovelTextImport(BaseModel):
    text: str
    start_chapter_no: int = Field(default=1, ge=1)


@router.post("/import-text")
async def import_novel_text(
    project_id: str,
    payload: NovelTextImport,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """导入已有小说的整本纯文本：确定性切章（split_novel_text）后逐章
    走 add_chapter（embedding + 人物标记 + 重复度检测，与手动建章同链路）。

    幂等语义：chapter_no 已存在的章节跳过（skipped），**绝不覆盖**——
    覆盖已有正文属于破坏性操作，需要用户显式删除后重导。
    续篇导入用 start_chapter_no 接在已有章节之后。
    正文为空的切分产物（标题连标题）不入库，计入 skipped_empty。
    """
    from ..novel_import import split_novel_text

    if not payload.text or not payload.text.strip():
        raise HTTPException(400, "text 为空")
    parts = split_novel_text(payload.text, start_chapter_no=payload.start_chapter_no)
    if not parts:
        raise HTTPException(400, "未能从文本中切分出任何章节")

    imported: list[dict] = []
    skipped: list[int] = []
    skipped_empty: list[int] = []
    for p in parts:
        if not p["content"].strip():
            skipped_empty.append(p["chapter_no"])
            continue
        try:
            result = await add_chapter(
                project_id, p["chapter_no"], p["title"], p["content"], db,
            )
            imported.append({
                "chapter_no": p["chapter_no"],
                "chapter_id": result["chapter_id"],
                "title": p["title"],
                "repetition_warnings": result.get("repetition_warnings", []),
            })
        except DuplicateChapterError:
            skipped.append(p["chapter_no"])
    return {
        "total_parsed": len(parts),
        "imported": imported,
        "skipped": skipped,
        "skipped_empty": skipped_empty,
    }


class ExtractSettingRequest(BaseModel):
    max_chapters: int = Field(default=20, ge=1, le=200)
    replace: bool = False


@router.post("/extract-setting")
async def extract_setting(
    project_id: str,
    payload: ExtractSettingRequest,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """从已导入的章节反向提取世界观/人物/关系/势力/力量体系/伏笔，
    写入现有 WorldSetting / Character / Faction / PowerSystem /
    Foreshadowing / EntityRelation 表。push-concept 会自动把这些结构化
    数据打包进 novel_config.json 的 worldbuild_snapshot 字段，引擎
    planner 有则沿用——无需额外接入即可让续写带着提取设定。

    幂等：默认拒绝覆盖已有设定（409，detail.code=setting_exists），
    设 replace=true 时删旧重建（单事务）。
    """
    try:
        result = await extract_setting_from_chapters(
            project_id=project_id,
            db=db,
            max_chapters=payload.max_chapters,
            replace=payload.replace,
        )
        return result
    except ValueError as e:
        # "没有可提取的章节" 等 caller 错误
        raise HTTPException(400, str(e))
    except ExtractConflictError as e:
        # 409 风格与 duplicate_chapter_no 一致
        raise HTTPException(
            status_code=409,
            detail={
                "code": "setting_exists",
                "message": str(e),
                "hint": "重跑请带 {\"replace\": true}",
            },
        )
    except LLMError:
        # 不暴露 provider/role 等内部信息（CLAUDE.md 敏感信息不变量；
        # LLMError 本身只含 provider/role/重试次数，相对安全，但统一文案更稳）
        raise HTTPException(502, "LLM 提取失败，请重试")


class ChapterRewriteRequest(BaseModel):
    instruction: str
    version_label: str | None = Field(default=None, min_length=1, max_length=1)
    replace: bool = False


@router.post("/{chapter_no}/rewrite")
async def rewrite_chapter_endpoint(
    project_id: str,
    chapter_no: int,
    payload: ChapterRewriteRequest,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """单章改写：保留原章，产出新候选版本（ch_NNNN_vX.txt）。

    与 novel_import / novel_extract 同链路（goal 2026-07-19 授权）。
    不变量（CLAUDE.md）：绝不覆盖原章节 —— ch_NNNN.txt 与 Chapter.content
    不动；候选只写到 ch_NNNN_vX.txt（version_label 默认 D 之后，避免与
    bootstrap A/B/C 撞）。同 label 已存在时默认 409，需 replace=true 才覆盖。

    链路：rewrite_chapter() → engine mock writer（task #6 已注入 snapshot
    关键词：林渊/苏晚栀/云州 等）→ 原子写候选文件 → 更新 rewrite_candidates.json
    索引。
    """
    try:
        result = await rewrite_chapter(
            project_id=project_id,
            chapter_no=chapter_no,
            instruction=payload.instruction,
            db=db,
            version_label=payload.version_label,
            replace=payload.replace,
        )
        return result
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ChapterNotFoundError as e:
        raise HTTPException(404, str(e))
    except RewriteConflictError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "rewrite_label_exists",
                "message": str(e),
                "hint": "覆盖请带 {\"replace\": true}",
            },
        )
    except LLMError:
        raise HTTPException(502, "LLM 改写失败，请重试")


@router.get("/{chapter_no}/candidates")
async def list_chapter_candidates(
    project_id: str,
    chapter_no: int,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """列出某章的所有改写候选（ch_NNNN_vX.txt 元信息 + bootstrap A/B/C）。

    简单实现：扫 chapters_dir + 读 rewrite_candidates.json 与
    bootstrap_candidates.json 两个索引文件并集返回。
    """
    from ..chapter_rewrite import _resolve_engine_paths
    dirs = _resolve_engine_paths(project_id, db)
    ch_dir = dirs["chapters_dir"]
    candidates: list[dict] = []

    # bootstrap A/B/C + 改写 D-Z 一起扫
    if ch_dir.exists():
        import re as _re
        for p in sorted(ch_dir.glob(f"ch_{chapter_no:04d}_v*.txt")):
            m = _re.match(rf"^ch_{chapter_no:04d}_v([A-Z])\.txt$", p.name)
            if not m:
                continue
            try:
                text = p.read_text(encoding="utf-8")
                word_count = len(text)
                snippet = text[:120].replace("\n", " ")
            except Exception:
                word_count = 0
                snippet = ""
            candidates.append({
                "version": m.group(1),
                "path": str(p.relative_to(dirs["novel_ai_dir"])),
                "word_count": word_count,
                "snippet": snippet,
            })

    # 改写索引里的 instruction_preview 附加
    import json as _json
    index_path = dirs["output_dir"] / "rewrite_candidates.json"
    if index_path.exists():
        try:
            idx = _json.loads(index_path.read_text(encoding="utf-8"))
            for e in idx.get(f"chapter_{chapter_no}", []):
                for c in candidates:
                    if c["version"] == e.get("version"):
                        c["instruction_preview"] = e.get("instruction_preview", "")
                        c["created_at"] = e.get("created_at")
                        break
        except Exception:
            pass

    return {"chapter_no": chapter_no, "candidates": candidates}
