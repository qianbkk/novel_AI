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


def _check_master_key_in_production() -> None:
    """生产模式下强制要求 MASTER_KEY 已设置。

    历史背景（独立审查 + 本轮深度审计标记的高危点）：
      security.py 在 MASTER_KEY 未设时临时生成一个 + 警告，继续运行。
      这在 dev 模式 OK，但生产部署忘设 MASTER_KEY → 后端启动 + Provider
      写入 api_key_encrypted 成功 → **重启后无法解密**（临时 key 丢失）。
      数据永久不可逆损坏。

    修法：通过 env NOVEL_PRODUCTION=1 标记生产环境，启动时显式检查。
    dev 模式（默认）保持原行为：warn 但继续运行，方便本地开发。
    """
    if os.environ.get("NOVEL_PRODUCTION") != "1":
        return  # dev / test 模式不强制
    from .security import get_master_key
    try:
        get_master_key()
        # 成功读到了 — 但要确认不是临时生成的（warning log）
        # 检查 env 是否真设了 MASTER_KEY
        if not os.environ.get("MASTER_KEY", "").strip():
            raise RuntimeError(
                "PRODUCTION 模式下必须设置 MASTER_KEY 环境变量。\n"
                "  生成：python -m scripts.generate_master_key\n"
                "  设置：export MASTER_KEY='<44 字符 base64-urlsafe>'\n"
                "  否则重启后已加密的 Provider.api_key 无法解密 → 数据损坏"
            )
    except RuntimeError as e:
        # 真设了但解码失败 / 真没设但 get_master_key 走到 fallback
        if "MASTER_KEY" in str(e):
            raise
        raise RuntimeError(str(e)) from e


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

    启动时同时做四件事：
      0. _check_master_key_in_production — 生产模式强制 MASTER_KEY 已设
      1. run_migrations — 给已存在的表加新列（schema 演进）
      2. seed_role_assignments — 初始化 15 个写作角色
      3. _recover_orphan_bridge_runs — 清理上一轮崩溃遗留的孤儿 running 行
    """
    log.info("startup: master_key_check + migrations + seed_role_assignments + recover_orphan_bridge_runs begin")
    _check_master_key_in_production()  # 生产模式 fail-fast（在 run_migrations 前）
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
    """健康检查：除了返回 status=ok，还验证 DB 可达。

    为什么要加 DB ping：
      - 之前 /health 永远返回 ok（不管 DB 是否锁、磁盘满、migration 失败）
      - k8s livenessProbe / readinessProbe 拿到 ok 会继续发流量
      - 实际后端挂但 health 还绿 → 用户请求全 5xx 但监控看不见

    返回结构：
      - 200 OK: {"status": "ok", "db": "ok"}
      - 503 Service Unavailable: {"status": "degraded", "db": "error", "detail": "..."}
    """
    from .database import SessionLocal
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception as e:
        log.warning("/health DB ping failed: %s", e)
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "db": "error", "detail": str(e)[:200]},
        )
    finally:
        db.close()
