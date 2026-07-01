"""
读回 output/chapters/ch_<N>.txt + meta，触发既有的 embed + 人物标记 + 重复度检测——
独立于 novel_AI 自身的 Checker，两者并存，不是替代关系。score/rewrite_count
两个字段已从 run.py 的 cmd_show() 源码确认存在。

标题生成：meta.json 没有 title 字段（只有 chapter_role / chapter_goal），
我们自己从这两个字段派生"第N章 · {role} · {goal 前 30 字}"，确保章节管理页能
显示像样的标题，而不是"第1章 【修改后正文】"这种跑冒烟测试时的占位。
"""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Chapter
from ..rag.retrieval import add_chapter
from ..logging_setup import get_logger
from datetime import datetime

log = get_logger("novel_ai.chapter_import")


def _derive_title(n: int, meta: dict, content: str) -> str:
    """派生章节标题：优先 meta 的 chapter_role + chapter_goal，
    都没有再从正文首行抓。"""
    role = (meta.get("chapter_role") or "").strip()
    goal = (meta.get("chapter_goal") or "").strip()
    if role or goal:
        goal_short = goal[:30] + ("…" if len(goal) > 30 else "")
        return f"第{n}章·{role or '正文'}·{goal_short}"
    # 兜底：从正文第一句「真正的话」摘——跳过：
    #   1. 空行
    #   2. 纯 scene label 行（"【xxx】"，不带正文的）
    #   3. 「第N章 标题」/「第N卷 xxx」类重复标题
    import re
    title_re = re.compile(r"^第\d+[章卷]\s*\S+")
    for line in content.splitlines():
        line = line.strip()
        if not line or len(line) <= 4:
            continue
        if line.startswith("【") and line.endswith("】") and " " not in line and len(line) <= 30:
            continue
        if title_re.match(line):
            continue
        return f"第{n}章·{line[:24]}"
    return f"第{n}章"


async def import_chapters_from_novel_ai(project_id: str, novel_ai_dir: str, db: Session) -> list[dict]:
    imported = []
    chapters_dir = Path(novel_ai_dir, "output", "chapters")
    if not chapters_dir.exists():
        log.warning("import-chapters: %s 不存在", chapters_dir)
        return imported

    # force=True 时会覆盖已有行（更新 title/content/summary），用于"修了章节管理显示"场景
    force = False  # 调用方通过 import_chapters_force 单独传

    for txt_path in sorted(chapters_dir.glob("ch_*.txt")):
        n = int(txt_path.stem.split("_")[1])
        existing = db.query(Chapter).filter_by(project_id=project_id, chapter_no=n).first()
        if existing and not force:
            continue  # 已经导入过，跳过——避免重复 embed 同一章

        content = txt_path.read_text(encoding="utf-8")
        meta_path = txt_path.with_name(txt_path.stem + "_meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

        # 派生一个像样的标题，避免显示"【修改后正文】"
        derived_title = _derive_title(n, meta, content)

        if existing:
            # 覆盖：保留 id，更新内容 + 标题 + 摘要
            existing.title = derived_title
            existing.content = content
            existing.summary = (meta.get("chapter_goal") or "")[:120]
            db.commit()
            imported.append({
                "chapter_id": existing.id,
                "chapter_no": n,
                "title": derived_title,
                "novel_ai_score": meta.get("score"),
                "novel_ai_rewrite_count": meta.get("rewrite_count"),
                "mode": "overwrite",
            })
            continue

        result = await add_chapter(project_id, n, derived_title, content, db)
        result["novel_ai_score"] = meta.get("score")
        result["novel_ai_rewrite_count"] = meta.get("rewrite_count")
        imported.append(result)

    log.info("import-chapters project=%s, imported=%d, dir=%s",
             project_id, len(imported), chapters_dir)
    return imported


async def _force_reimport(project_id: str, novel_ai_dir: str, db: Session) -> list[dict]:
    """强制重新导入：覆盖已有行的 title/content/summary。专用于修章节管理显示。"""
    chapters_dir = Path(novel_ai_dir, "output", "chapters")
    if not chapters_dir.exists():
        log.warning("_force_reimport: %s 不存在", chapters_dir)
        return []

    updated = []
    for txt_path in sorted(chapters_dir.glob("ch_*.txt")):
        n = int(txt_path.stem.split("_")[1])
        content = txt_path.read_text(encoding="utf-8")
        meta_path = txt_path.with_name(txt_path.stem + "_meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

        derived_title = _derive_title(n, meta, content)
        existing = db.query(Chapter).filter_by(project_id=project_id, chapter_no=n).first()
        if existing:
            existing.title = derived_title
            existing.content = content
            existing.summary = (meta.get("chapter_goal") or "")[:120]
            if not existing.created_at:
                existing.created_at = datetime.utcnow()
            db.commit()
            updated.append({"chapter_no": n, "title": derived_title, "mode": "updated"})
        else:
            from ..rag.retrieval import add_chapter
            await add_chapter(project_id, n, derived_title, content, db)
            updated.append({"chapter_no": n, "title": derived_title, "mode": "created"})
    log.info("_force_reimport project=%s, updated=%d", project_id, len(updated))
    return updated
