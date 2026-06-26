"""
读回 output/chapters/ch_<N>.txt + meta，触发既有的 embed + 人物标记 + 重复度检测——
独立于 novel_AI 自身的 Checker，两者并存，不是替代关系。score/rewrite_count
两个字段已从 run.py 的 cmd_show() 源码确认存在。
"""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Chapter
from ..rag.retrieval import add_chapter


async def import_chapters_from_novel_ai(project_id: str, novel_ai_dir: str, db: Session) -> list[dict]:
    imported = []
    chapters_dir = Path(novel_ai_dir, "output", "chapters")
    if not chapters_dir.exists():
        return imported

    for txt_path in sorted(chapters_dir.glob("ch_*.txt")):
        n = int(txt_path.stem.split("_")[1])
        if db.query(Chapter).filter_by(project_id=project_id, chapter_no=n).first():
            continue  # 已经导入过，跳过——避免重复 embed 同一章

        content = txt_path.read_text(encoding="utf-8")
        meta_path = txt_path.with_name(txt_path.stem + "_meta.json")
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}

        result = await add_chapter(project_id, n, meta.get("title"), content, db)
        result["novel_ai_score"] = meta.get("score")
        result["novel_ai_rewrite_count"] = meta.get("rewrite_count")
        imported.append(result)

    return imported
