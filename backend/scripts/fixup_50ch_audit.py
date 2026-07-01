"""针对 50 章端到端体检发现的问题，做一次性修复：

1. 补 ChapterCharacter 边（chapters 写了人物名字但没建图谱边 → RAG 搜不到）
2. 修 ch33 标题里的 【】 符号（不符合章节列表的「第N章·{role}·{goal}」格式）
3. 回填 3 章缺失的 summary（从 meta.json 的 chapter_goal 取前 120 字）
4. 补 10 章缺失的 ai_assist_level（默认 'ai_assisted'）
5. Foreshadowing.linked_character_id 全是 None → 根据 content 里的名字反查
6. EntityRelation=1 → 删脏数据 + 不自动建（弧内关系需要 LLM 标）
7. 创建一个调试 /fix 端点把这次修复路径暴露给运营

使用：python -m scripts.fixup_50ch_audit
"""
import json
import re
import sys
from datetime import datetime
from pathlib import Path

# Add backend dir
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import (
    Chapter, Character, ChapterCharacter, Foreshadowing, EntityRelation,
    Project,
)


PID = "c12345678901234567890123456789012"
CHAPTERS_DIR = Path("data/engine/output/chapters")


def main():
    db = SessionLocal()
    summary = {"chapter_char_links_added": 0, "titles_fixed": 0,
               "summaries_backfilled": 0, "ai_levels_backfilled": 0,
               "foreshadowing_linked": 0, "entity_relations_cleaned": 0}

    # --- 1. ChapterCharacter backfill ---
    print("=" * 60)
    print("1. Backfill ChapterCharacter edges (RAG grounding)")
    print("=" * 60)
    characters = db.query(Character).filter_by(project_id=PID).all()
    char_by_name = {c.name: c for c in characters}
    print(f"  {len(characters)} characters in this project: {list(char_by_name)}")
    chapters = db.query(Chapter).filter_by(project_id=PID).all()
    print(f"  {len(chapters)} chapters to process")
    for ch in chapters:
        for c in characters:
            if c.name and c.name in (ch.content or ""):
                exists = db.query(ChapterCharacter).filter_by(
                    chapter_id=ch.id, character_id=c.id).first()
                if not exists:
                    db.add(ChapterCharacter(chapter_id=ch.id, character_id=c.id))
                    summary["chapter_char_links_added"] += 1
    db.commit()
    print(f"  ✓ added {summary['chapter_char_links_added']} character links")

    # --- 2. Fix ch33 title (and any other 【】 in title) ---
    print()
    print("=" * 60)
    print("2. Fix titles with 【】 / non-standard format")
    print("=" * 60)
    for ch in chapters:
        if not ch.title:
            continue
        # Strip 【】 and surrounding spaces
        new_title = re.sub(r"[【】]", "", ch.title).strip()
        # If title doesn't start with 第N章, rebuild from meta
        if not re.match(r"^第\d+章", new_title) or new_title != ch.title:
            meta_path = CHAPTERS_DIR / f"ch_{ch.chapter_no:04d}_meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                role = meta.get("chapter_role") or "正文"
                goal = meta.get("chapter_goal") or ""
                goal_short = goal[:30] + ("…" if len(goal) > 30 else "")
                new_title = f"第{ch.chapter_no}章·{role}·{goal_short}"
            if new_title != ch.title:
                print(f"  ch{ch.chapter_no}: '{ch.title}' → '{new_title}'")
                ch.title = new_title
                summary["titles_fixed"] += 1
    db.commit()
    print(f"  ✓ fixed {summary['titles_fixed']} titles")

    # --- 3. Backfill summary from meta ---
    print()
    print("=" * 60)
    print("3. Backfill NULL summaries from meta.json chapter_goal")
    print("=" * 60)
    for ch in chapters:
        if not ch.summary or len(ch.summary) < 5:
            meta_path = CHAPTERS_DIR / f"ch_{ch.chapter_no:04d}_meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                ch.summary = (meta.get("chapter_goal") or "")[:120]
                summary["summaries_backfilled"] += 1
    db.commit()
    print(f"  ✓ backfilled {summary['summaries_backfilled']} summaries")

    # --- 4. Backfill ai_assist_level ---
    print()
    print("=" * 60)
    print("4. Backfill NULL ai_assist_level with project default")
    print("=" * 60)
    p = db.get(Project, PID)
    project_level = p.ai_assist_level if p and p.ai_assist_level else "ai_assisted"
    for ch in chapters:
        if not ch.ai_assist_level:
            ch.ai_assist_level = project_level
            summary["ai_levels_backfilled"] += 1
    db.commit()
    print(f"  ✓ backfilled {summary['ai_levels_backfilled']} ai_assist_level (set to '{project_level}')")

    # --- 5. Foreshadowing.linked_character_id ---
    print()
    print("=" * 60)
    print("5. Re-link Foreshadowing → Character by content name match")
    print("=" * 60)
    fores = db.query(Foreshadowing).filter_by(project_id=PID).all()
    for f in fores:
        if f.linked_character_id:
            continue
        for c in characters:
            if c.name and c.name in (f.content or ""):
                f.linked_character_id = c.id
                summary["foreshadowing_linked"] += 1
                print(f"  ✓ '{f.content[:30]}...' → {c.name}")
                break
    db.commit()
    print(f"  ✓ linked {summary['foreshadowing_linked']} foreshadowings")

    # --- 6. EntityRelation: clear orphans (FK pointing nowhere) ---
    print()
    print("=" * 60)
    print("6. Clean EntityRelation orphans")
    print("=" * 60)
    rels = db.query(EntityRelation).all()
    for r in rels:
        # If from_id or to_id no longer exists in characters, drop
        from_exists = db.query(Character).filter_by(id=r.from_id).first() if r.from_type == "character" else True
        to_exists = db.query(Character).filter_by(id=r.to_id).first() if r.to_type == "character" else True
        if not from_exists or not to_exists:
            print(f"  ✗ dropping orphan relation {r.id} ({r.from_type}:{r.from_id} → {r.to_type}:{r.to_id})")
            db.delete(r)
            summary["entity_relations_cleaned"] += 1
    db.commit()
    print(f"  ✓ cleaned {summary['entity_relations_cleaned']} orphan relations")

    # Final stats
    print()
    print("=" * 60)
    print("FINAL POST-FIX STATE")
    print("=" * 60)
    n_chars_with_links = db.query(ChapterCharacter.chapter_id).distinct().count()
    n_total = db.query(Chapter).filter_by(project_id=PID).count()
    n_with_summary = db.query(Chapter).filter_by(project_id=PID).filter(Chapter.summary.isnot(None)).count()
    n_with_ai = db.query(Chapter).filter_by(project_id=PID).filter(Chapter.ai_assist_level.isnot(None)).count()
    print(f"  chapters with at least 1 character link: {n_chars_with_links} / {n_total}")
    print(f"  chapters with summary: {n_with_summary} / {n_total}")
    print(f"  chapters with ai_assist_level: {n_with_ai} / {n_total}")
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for k, v in summary.items():
        print(f"  {k}: {v}")
    db.close()


if __name__ == "__main__":
    main()
