"""
backfill_worldbuild.py — 幂等回灌 worldbuild 数据到已有项目。

2026-07-25 新增：诊断发现 real30ch-16862056 跑完 31 章后
世界构建数据有几大缺陷（pull-setting 5 步根因 + EntityRelation 漏写）：
  - Character 重建时只写 detail_json，没回填 stage_characters 已写的
    8 段 card_*_json 列 → CharacterCard.tsx 看不到 8 段
  - factions=0：旧关键词法只识别"人/妖/魔/灵/神/鬼族"6 种族，没匹配
    "周氏/陈家/苏氏/林家"家族 → 人物阵营 tab 显示空
  - entity_relations=0：pull-setting 删了 EntityRelation 但从未重建
    → 关系图谱 / 角色关系栏全空
  - currencies=1 行空模板：只从 power_system.currency 抓单名字符串
  - plot_skeleton_json=4 段：但前端 WorldBuild 顶部"世界观"tab 的
    M03.9 卷级骨架才显示 — 用户不知道去看

本脚本幂等地重新调用 setting_sync.pull_setting_package()，
把已落盘的 novel_ai_raw_setting_json 解析后回填：
  - characters: 8 段 card_*_json（仅当 detail.card 段在 novel_ai_raw 中时）
  - factions: 4 类关键词 + character role 推断
  - entity_relations: 父子/兄弟/夫妻/盟友 + 角色-势力归属
  - currencies: currency_detail/currencies[]/currency 字符串 + 文本扫

注意：本脚本不动 novel_ai_raw_setting_json（setting_package.json 原文），
不动 chapters 章节表，不动 foreshadowings 之外的表。

用法：
    cd backend
    set -a; . ./.env; set +a  # 同 uvicorn 一致的 env（避免 master_key 漂移）
    python -m scripts.backfill_worldbuild real30ch-16862056
    # 或传 --dry-run 模式仅打印 diff，不写库
"""
import argparse
import asyncio
import json
import os
import sys
import sqlite3
from pathlib import Path

# 允许从仓库根目录 / backend 目录运行
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import SessionLocal  # noqa: E402
from app.models import Project, NovelAIBinding  # noqa: E402
from app.bridge.setting_sync import pull_setting_package  # noqa: E402


def _check_prereq(db, project_id: str) -> tuple[Path | None, bool]:
    """检查项目存在 + 有 novel_ai 绑定 + 有 setting_package.json"""
    project = db.query(Project).filter_by(id=project_id).first()
    if project is None:
        print(f"❌ project {project_id} 不存在", file=sys.stderr)
        return None, False
    binding = db.query(NovelAIBinding).filter_by(project_id=project_id).first()
    if binding is None or not binding.novel_ai_dir:
        print(f"❌ project {project_id} 没有 NovelAIBinding，无法定位 setting_package.json",
              file=sys.stderr)
        return None, False
    novel_ai_dir = Path(binding.novel_ai_dir)
    setting_path = novel_ai_dir / "output" / "setting_package.json"
    if not setting_path.exists():
        print(f"❌ setting_package.json 不存在：{setting_path}", file=sys.stderr)
        return None, False
    return novel_ai_dir, True


def _print_diff(before: dict, after: dict) -> None:
    """打印回填前后的数据差异（人类可读）"""
    print("\n=== 回填前 (DB 当前状态) ===")
    for k, v in before.items():
        print(f"  {k}: {v}")
    print("\n=== 回填后 (pull_setting_package 返回) ===")
    for k, v in after.items():
        print(f"  {k}: {v}")
    print("\n=== 差异 (after - before) ===")
    keys = set(before) | set(after)
    for k in sorted(keys):
        b, a = before.get(k, 0), after.get(k, 0)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            delta = a - b
            if delta != 0:
                print(f"  {k}: {b} → {a} (Δ {delta:+d})")
        else:
            if b != a:
                print(f"  {k}: {b} → {a}")


def _snapshot_state(db, project_id: str) -> dict:
    """快照当前 DB 状态用于对比"""
    from app.models import (
        Character, Faction, EntityRelation, PowerSystem, Currency,
        Foreshadowing, MapNode, WorldSetting,
    )
    return {
        "characters":  db.query(Character).filter_by(project_id=project_id).count(),
        "factions":    db.query(Faction).filter_by(project_id=project_id).count(),
        "relations":   db.query(EntityRelation).filter_by(project_id=project_id).count(),
        "power_systems": db.query(PowerSystem).filter_by(project_id=project_id).count(),
        "currencies":  db.query(Currency).filter_by(project_id=project_id).count(),
        "foreshadowings": db.query(Foreshadowing).filter_by(project_id=project_id).count(),
        "map_nodes":   db.query(MapNode).filter_by(project_id=project_id).count(),
        "world_settings": db.query(WorldSetting).filter_by(project_id=project_id).count(),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project_id", help="要回填的项目 ID")
    p.add_argument("--dry-run", action="store_true",
                   help="仅打印当前状态，不实际写库")
    args = p.parse_args()

    db = SessionLocal()
    try:
        novel_ai_dir, ok = _check_prereq(db, args.project_id)
        if not ok:
            return 1

        print(f"🔍 项目：{args.project_id}")
        print(f"   绑定目录：{novel_ai_dir}")
        print(f"   setting_package.json: {novel_ai_dir / 'output' / 'setting_package.json'}")

        before = _snapshot_state(db, args.project_id)
        _print_diff_before_only(before)

        if args.dry_run:
            print("\n🔶 --dry-run：不调用 pull_setting_package")
            return 0

        # 调 pull_setting_package 触发幂等回填
        print("\n⏳ 调用 pull_setting_package（幂等）...")
        result = asyncio.run(pull_setting_package(
            project_id=args.project_id,
            novel_ai_dir=str(novel_ai_dir),
            db=db,
        ))

        after = _snapshot_state(db, args.project_id)
        _print_diff(before, after)

        print("\n✅ 回填完成")
        return 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"\n❌ 回填失败：{e}", file=sys.stderr)
        return 2
    finally:
        db.close()


def _print_diff_before_only(before: dict) -> None:
    print("\n=== 回填前 (DB 当前状态) ===")
    for k, v in before.items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    raise SystemExit(main())
