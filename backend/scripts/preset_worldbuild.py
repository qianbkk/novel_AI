"""30-chapter real LLM test — Phase 1: pre-populate worldbuild via the
existing mock payloads. Avoids the 5-10min worldbuild with non-deterministic
real LLM; the real LLM portion is reserved for the 30-chapter bridge.run.
"""
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# 用 mock 模式跑：settings.llm_provider 必须是 "mock"
os.environ["NOVEL_LLM_PROVIDER"] = "mock"

import asyncio
from app.database import SessionLocal
from app.models import (
    Project, WorldSetting, Character, EntityRelation, Faction,
    PowerSystem, Foreshadowing, MapNode, Currency, GenerationJob,
)
from app.worldbuild.stages import STAGES


def find_stage(key):
    for k, _lbl, fn in STAGES:
        if k == key:
            return fn
    raise KeyError(key)


def main():
    import uuid
    pid = f"real30ch-{uuid.uuid4().hex[:8]}"
    db = SessionLocal()
    try:
        # 1. 建项目
        db.add(Project(
            id=pid,
            title="债起云州",
            genre="都市",
            status="draft",
            config_json={
                "tropes": ["重生", "商战", "家族恩怨", "财务逆袭"],
                "platform": "fanqie",
                "main_conflict": "林渊重生回到 2012 年的云州，带着前世的记忆重新布局商业版图。",
                "length_range": "200-400万字（长篇）",
                "protagonist_name": "林渊",
                "world_setting_concept": "表世界云州三线城市工商业生态，里世界债感修炼。",
                "tone": "现实题材硬派商战。",
            },
        ))
        db.commit()
        proj = db.get(Project, pid)
        print(f"project {pid} created")

        # 2. 跑 10 stages（用 mock payload）
        ctx = {"project": proj, "normalized_config": proj.config_json}
        for idx, (stage_key, stage_label, stage_fn) in enumerate(STAGES, start=1):
            print(f"  [{idx}/10] {stage_key} ({stage_label})")
            try:
                asyncio.run(stage_fn(ctx, db))
                db.commit()
            except Exception as e:
                print(f"    FAILED: {e}")
                raise

        # 3. 标 project.status = ready + 写 generation job=done
        proj.status = "ready"
        job = GenerationJob(
            project_id=pid, job_type="worldbuild", status="done",
            progress_percent=100, current_stage="一致性校验",
        )
        db.add(job)
        db.commit()
        print(f"  project.status=ready, job=done")
        print(f"\nPROJECT_ID={pid}")
        pid_dir = Path(os.environ.get(
            "NOVEL_RUN_DIR",
            "D:/AI/Codex_workspace/Novel_AI/docs/runs/30ch-real-2026-07-22",
        ))
        pid_dir.mkdir(parents=True, exist_ok=True)
        with open(pid_dir / "pid.txt", "w") as f:
            f.write(pid)
    finally:
        db.close()


if __name__ == "__main__":
    main()
