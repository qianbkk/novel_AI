"""cleanup_test_projects.py — 清理 Dashboard 测试残留项目

Dashboard 上 310 个项目里只有 13 个是用户真正在做的，其余 297 个是
自动化测试生成的噪音（id 以 test-resilient-* / title='test' /
title='Alignment Smoke Test'）。

清理策略（保守 — 只删纯噪音，保留所有中文名 / 端到端 / iter84 /
测试小说 / mock测试 等手动建的项目）：
  1. id LIKE 'test-resilient-%'
  2. title = 'test'
  3. title = 'Alignment Smoke Test'

执行：
  python -m scripts.cleanup_test_projects --dry-run    # 预览
  python -m scripts.cleanup_test_projects --confirm    # 真删（不可逆！）

实现要点：FK 级联删除按子表→父表顺序，必须先删 EmbeddingChunk / Chapter
等子表才能删 Project。EmbeddingChunk 是 Phase 1 加的表，setting_sync.py
修过的 FK 顺序没把它列上 — 这是单点疏漏。
"""
import argparse
import sys
from pathlib import Path

# 让脚本可独立运行
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import or_, select
from app.database import SessionLocal
from app.models import (
    Project, Chapter, ChapterCharacter, EntityRelation, Foreshadowing,
    MapNode, Currency, PowerSystem, Faction, Character, WorldSetting,
    RuleConfig, GenerationJob, BridgeRun, NovelAIBinding, EmbeddingChunk,
)


def find_targets(db):
    """返回 Project 行列表（要删的）"""
    return db.query(Project).filter(or_(
        Project.id.like("test-resilient-%"),
        Project.title == "test",
        Project.title == "Alignment Smoke Test",
    )).all()


def delete_with_cascade(db, project_ids: list[str]) -> dict[str, int]:
    """按子表→父表顺序删除，返回每表删除条数。"""
    steps = [
        ("ChapterCharacter", lambda: db.query(ChapterCharacter).filter(
            ChapterCharacter.chapter_id.in_(
                select(Chapter.id).where(Chapter.project_id.in_(project_ids))
            )).delete(synchronize_session=False)),
        ("EntityRelation", lambda: db.query(EntityRelation).filter(
            EntityRelation.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("Foreshadowing", lambda: db.query(Foreshadowing).filter(
            Foreshadowing.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("MapNode", lambda: db.query(MapNode).filter(
            MapNode.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("Currency", lambda: db.query(Currency).filter(
            Currency.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("PowerSystem", lambda: db.query(PowerSystem).filter(
            PowerSystem.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("Faction", lambda: db.query(Faction).filter(
            Faction.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("Character", lambda: db.query(Character).filter(
            Character.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("WorldSetting", lambda: db.query(WorldSetting).filter(
            WorldSetting.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("RuleConfig", lambda: db.query(RuleConfig).filter(
            RuleConfig.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("GenerationJob", lambda: db.query(GenerationJob).filter(
            GenerationJob.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("BridgeRun", lambda: db.query(BridgeRun).filter(
            BridgeRun.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("NovelAIBinding", lambda: db.query(NovelAIBinding).filter(
            NovelAIBinding.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("EmbeddingChunk", lambda: db.query(EmbeddingChunk).filter(
            EmbeddingChunk.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("Chapter", lambda: db.query(Chapter).filter(
            Chapter.project_id.in_(project_ids)).delete(synchronize_session=False)),
        ("Project", lambda: db.query(Project).filter(
            Project.id.in_(project_ids)).delete(synchronize_session=False)),
    ]
    counts = {}
    for name, fn in steps:
        n = fn()
        db.commit()
        counts[name] = n
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="预览要删的项目，不执行")
    parser.add_argument("--confirm", action="store_true", help="真删（不可逆）")
    args = parser.parse_args()

    if not (args.dry_run or args.confirm):
        parser.print_help()
        sys.exit(1)

    db = SessionLocal()
    try:
        targets = find_targets(db)
        total = db.query(Project).count()
        keep = total - len(targets)

        print(f"\n总项目数: {total}")
        print(f"将被删除: {len(targets)}")
        print(f"将保留:   {keep}\n")

        # 按 title 模式分组预览
        from collections import defaultdict
        by_reason = defaultdict(list)
        for p in targets:
            if p.id.startswith("test-resilient-"):
                by_reason["id LIKE 'test-resilient-%'"].append(p)
            elif p.title == "test":
                by_reason["title == 'test'"].append(p)
            elif p.title == "Alignment Smoke Test":
                by_reason["title == 'Alignment Smoke Test'"].append(p)

        for reason, items in by_reason.items():
            print(f"── {reason}  ({len(items)} 个)")
            for p in items[:5]:
                print(f"   {p.id[:20]:20s}  title={p.title!r:25s}  genre={p.genre}  status={p.status}")
            if len(items) > 5:
                print(f"   ... 还有 {len(items) - 5} 个")
            print()

        if args.dry_run:
            print("(dry-run 模式，没真删)")
            return

        if args.confirm:
            print("⚠️  即将真删上述项目。")
            print("   自动备份已存在 backend/data/backups/，删错可恢复。\n")
            ids = [p.id for p in targets]
            counts = delete_with_cascade(db, ids)
            print("FK 级联删除明细：")
            for name, n in counts.items():
                print(f"  {name}: deleted={n}")
            print(f"\n✅ 完成。剩余项目数: {db.query(Project).count()}")
    finally:
        db.close()


if __name__ == "__main__":
    main()