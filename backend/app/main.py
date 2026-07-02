import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, SessionLocal, engine
from .api import bridge, chapters, projects, providers, role_assignments, worldbuild, rules, foreshadowings, ai_assist
from .api.role_assignments import seed_role_assignments
from .logging_setup import configure_root, get_logger
from .middleware.rate_limit import RateLimitMiddleware

configure_root()
log = get_logger("novel_ai.main")
log.info("后端启动中... CWD=%s", os.getcwd())

Base.metadata.create_all(bind=engine)

# 增量迁移：给已存在的表加新列（SQLite 的 create_all 不会给已有表加列）。
# 当前只有 providers 表的 api_key_encrypted / api_key_suffix 两列。
from .migrations import run_migrations


def _recover_orphan_bridge_runs() -> int:
    """启动时清理孤儿 BridgeRun。

    历史背景：并发保护是双重的（内存 asyncio.Lock + DB BridgeRun.status='running' 唯一约束）。
    但如果后端进程在 run 进行中崩溃 / 被 kill / 部署重启，内存锁清空，
    DB 里那条 status='running' 且 finished_at IS NULL 的记录会永久卡住。
    下次任何 /bridge/run 调用 → 409 Conflict → 项目永久无法再生成。

    修法：启动时把所有未结束的 running 行标为 'failed'，写入 finished_at。
    """
    from datetime import datetime, timezone
    from .models import BridgeRun
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        stuck = (
            db.query(BridgeRun)
            .filter(BridgeRun.status == "running")
            .filter(BridgeRun.finished_at.is_(None))
            .all()
        )
        for run in stuck:
            log.warning(
                "recovering orphan BridgeRun: id=%s project=%s command=%s started_at=%s",
                run.id, run.project_id, run.command, run.started_at,
            )
            run.status = "failed"
            run.finished_at = now
            # exit_code 留空（None），stdout_text 留空（None）— 与正常失败行字段一致
        db.commit()
        return len(stuck)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan handler（替代已 deprecated 的 startup/shutdown 装饰器）。

    启动时同时做三件事：
      1. run_migrations — 给已存在的表加新列（schema 演进）
      2. seed_role_assignments — 初始化 15 个写作角色
      3. _recover_orphan_bridge_runs — 清理上一轮崩溃遗留的孤儿 running 行
    """
    log.info("startup: migrations + seed_role_assignments + recover_orphan_bridge_runs begin")
    applied = run_migrations()
    db = SessionLocal()
    try:
        seed_role_assignments(db)
    finally:
        db.close()
    recovered = _recover_orphan_bridge_runs()
    log.info(
        "startup: done. routes=%d migrations_applied=%d recovered_orphan_bridge_runs=%d",
        len(app.routes), applied, recovered,
    )
    yield
    log.info("shutdown: complete")


app = FastAPI(title="AI小说写作助手 - 原型后端", lifespan=lifespan)

# CORS: 从 env 读 ALLOWED_ORIGINS（逗号分隔），默认放行前端 dev 端口 5293
# 部署时设置 ALLOWED_ORIGINS="https://your-frontend.example.com"
_allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
if _allowed_origins_env:
    allow_origins = [o.strip() for o in _allowed_origins_env.split(",") if o.strip()]
else:
    allow_origins = [
        "http://localhost:5293",      # Vite dev server
        "http://127.0.0.1:5293",
    ]
log.info("CORS allow_origins=%s (from %s)",
         allow_origins,
         "env ALLOWED_ORIGINS" if _allowed_origins_env else "default")
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 速率限制中间件：仅写端点限速（防刷 /bridge/run 触发昂贵 LLM 调用）
# 通过 env RATE_LIMIT_PER_MINUTE 调整阈值（默认 60）
app.add_middleware(RateLimitMiddleware)

app.include_router(projects.router)
app.include_router(worldbuild.router)
app.include_router(chapters.router)
app.include_router(providers.router)
app.include_router(role_assignments.router)
app.include_router(bridge.router)
app.include_router(rules.router)
app.include_router(foreshadowings.router)
app.include_router(ai_assist.router)


@app.get("/health")
def health():
    return {"status": "ok"}
