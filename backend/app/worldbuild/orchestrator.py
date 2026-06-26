"""
跑一次完整的世界构建任务，并把进度事件推到一个按 job_id 区分的队列里，
api/worldbuild.py 的 SSE 端点订阅这个队列。

这里没有引入 LangGraph —— 对于线性的 8 步流水线，
一个 for 循环 + 每步存档 已经够用，且最省心。
等以后需要"某步失败可重试/某步可并行/某步要人工审核再继续"这类需求时，
再把这个 for 循环换成 LangGraph 的 StateGraph 也不迟，
接口（ctx 输入输出）基本不用变。
"""
import asyncio
from sqlalchemy.orm import Session

from ..models import Project, GenerationJob
from .stages import STAGES

# job_id -> asyncio.Queue，进程内简单实现；多进程部署时换成 Redis pub/sub
_job_queues: dict[str, asyncio.Queue] = {}


def get_job_queue(job_id: str) -> asyncio.Queue:
    if job_id not in _job_queues:
        _job_queues[job_id] = asyncio.Queue()
    return _job_queues[job_id]


async def run_worldbuild_job(job_id: str, project_id: str, db: Session):
    job = db.get(GenerationJob, job_id)
    project = db.get(Project, project_id)
    queue = get_job_queue(job_id)

    job.status = "running"
    db.commit()

    ctx = {"project": project}
    total = len(STAGES)

    for idx, (stage_key, stage_label, stage_fn) in enumerate(STAGES, start=1):
        job.current_stage = stage_label
        db.commit()
        await queue.put({"event": "stage_start", "stage": stage_key, "label": stage_label})

        try:
            await stage_fn(ctx, db)
            db.commit()
        except Exception as e:  # noqa: BLE001  原型阶段先粗粒度兜底，后续可按阶段细化重试策略
            job.status = "failed"
            job.error_message = f"{stage_key}: {e}"
            db.commit()
            await queue.put({"event": "job_failed", "stage": stage_key, "error": str(e)})
            await queue.put({"event": "done"})
            return

        job.progress_percent = int(idx / total * 100)
        db.commit()
        await queue.put({
            "event": "stage_done",
            "stage": stage_key,
            "label": stage_label,
            "progress_percent": job.progress_percent,
        })

    job.status = "done"
    job.consistency_warnings_json = ctx.get("consistency_warnings", [])
    project.status = "ready"
    db.commit()
    await queue.put({
        "event": "job_done",
        "progress_percent": 100,
        "consistency_warnings": job.consistency_warnings_json,
    })
    await queue.put({"event": "done"})
