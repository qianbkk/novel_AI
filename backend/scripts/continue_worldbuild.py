"""continue_worldbuild.py — 续跑卡住的 draft 项目。

背景：preset_worldbuild 在 2026-07-23 22:46 MiniMax 突发 429 限流后死在
stage 2（world_basics），项目 real30ch-16862056 留在 draft 状态。
该脚本读 config_json 后只跑 10 stages，并把 project.status 标 ready。
幂等：每次跑前清掉该项目现有的 WorldSetting/Character/... rows，
避免上一轮失败的脏数据影响新一轮。

用法：
    cd backend
    python -m scripts.continue_worldbuild <project_id>
"""
import os
import sys
import time
import asyncio
import traceback
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 不强制 mock（用户明确要求真 LLM）
os.environ.setdefault("NOVEL_LLM_PROVIDER", "")

from sqlalchemy import delete

from app.database import SessionLocal
from app.models import (
    Project, WorldSetting, Character, EntityRelation, Faction,
    PowerSystem, Foreshadowing, MapNode, Currency, GenerationJob,
)
from app.worldbuild.stages import STAGES


def cleanup_partial(db, project_id: str) -> None:
    """把该项目之前 10 stages 的中间产物全删掉，让重跑干净开始。

    之前 preset 死在 stage 2 → 只创建了 Project 行；如果重试跑过几轮
    失败，可能留有 partial WorldSetting / Character 等脏数据。
    """
    deleted = {}
    for Model in (WorldSetting, Character, EntityRelation, Faction,
                  PowerSystem, Foreshadowing, MapNode, Currency):
        n = db.execute(
            delete(Model).where(Model.project_id == project_id)
        ).rowcount
        if n:
            deleted[Model.__name__] = n
    if deleted:
        print(f"  cleanup: {deleted}")


async def run_one_stage(stage_fn, ctx, db, max_attempts: int = 6) -> bool:
    """单 stage 带重试：捕获 429 / 5xx / JSON 解析失败，sleep 后重试。"""
    for attempt in range(1, max_attempts + 1):
        try:
            await stage_fn(ctx, db)
            db.commit()
            return True
        except Exception as e:
            db.rollback()
            msg = str(e)
            is_rate = "429" in msg or "rate" in msg.lower() or "Token Plan" in msg
            wait = min(60, 2 ** attempt) if is_rate else min(20, 2 ** attempt)
            print(f"    ✗ attempt {attempt}/{max_attempts}: {msg[:120]}"
                  + (f" — sleeping {wait}s (rate limit)" if is_rate else ""))
            if attempt == max_attempts:
                print(f"    ✗ give up after {max_attempts} attempts")
                return False
            await asyncio.sleep(wait)
    return False


async def main_async(project_id: str) -> int:
    db = SessionLocal()
    try:
        proj = db.get(Project, project_id)
        if not proj:
            print(f"✗ project {project_id} not found")
            return 1

        print(f"continuing worldbuild for {project_id} (current status={proj.status!r})")
        cleanup_partial(db, project_id)

        ctx = {"project": proj, "normalized_config": proj.config_json}
        started = time.time()
        for idx, (stage_key, stage_label, stage_fn) in enumerate(STAGES, start=1):
            t0 = time.time()
            print(f"  [{idx}/10] {stage_key} ({stage_label})...", flush=True)
            ok = await run_one_stage(stage_fn, ctx, db)
            if not ok:
                print(f"✗ FAILED at stage {stage_key}")
                return 2
            print(f"    ok ({time.time() - t0:.1f}s)")

        proj.status = "ready"
        job = db.query(GenerationJob).filter_by(
            project_id=project_id, job_type="worldbuild",
        ).order_by(GenerationJob.created_at.desc()).first()
        if job:
            job.status = "done"
            job.progress_percent = 100
            job.current_stage = "一致性校验"
        else:
            db.add(GenerationJob(
                project_id=project_id, job_type="worldbuild",
                status="done", progress_percent=100,
                current_stage="一致性校验",
            ))
        db.commit()
        elapsed = time.time() - started
        print(f"\n✓ project.status=ready (took {elapsed:.1f}s)")
        return 0
    finally:
        db.close()


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python -m scripts.continue_worldbuild <project_id>")
        return 1
    project_id = sys.argv[1]
    try:
        return asyncio.run(main_async(project_id))
    except KeyboardInterrupt:
        return 130
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())