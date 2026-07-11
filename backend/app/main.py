import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, SessionLocal, engine
from .api import auth, bridge, chapters, projects, providers, role_assignments, worldbuild, rules, foreshadowings, ai_assist, world
from .api.role_assignments import seed_role_assignments
from .backup_db import take_all_snapshots
from .config import get_allowed_origins_list
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


def _check_production_hardening() -> None:
    """生产模式启动校验（Phase 4）：fail-fast 把 dev-only 配置挡在外面。

    与 _check_master_key_in_production 不同：这条是"只警告 + 继续运行"，
    让用户能 review + 修后再启动；但**严重错配**仍 fail-fast。

    检查项：
      1. ALLOWED_ORIGINS 不允许 localhost / 127.0.0.1 / * 通配
      2. RATE_LIMIT_EXEMPT_LOCALHOST 必须设为 0（生产不再豁免本机）
      3. JWT_SECRET 必须设（不能用 dev 自动生成的）
      4. ALLOWED_PROXIES 建议设（反代 IP 白名单）
    """
    if os.environ.get("NOVEL_PRODUCTION") != "1":
        return

    from .config import get_allowed_origins_list
    issues: list[str] = []

    origins = get_allowed_origins_list()
    bad_origin_keywords = ("localhost", "127.0.0.1", "*")
    bad_origins = [o for o in origins
                   if any(k in o for k in bad_origin_keywords)]
    if bad_origins:
        issues.append(
            f"ALLOWED_ORIGINS 含 dev-only origin: {bad_origins}\n"
            f"  → 生产模式应只有真实前端域名（https://your-frontend）"
        )

    if os.environ.get("RATE_LIMIT_EXEMPT_LOCALHOST", "").strip() not in ("0", "false", "False"):
        issues.append(
            "RATE_LIMIT_EXEMPT_LOCALHOST 应设为 0（生产不再豁免本机）\n"
            "  当前配置会让任何打到 127.0.0.1 的请求绕过速率限制"
        )

    if not os.environ.get("JWT_SECRET", "").strip():
        issues.append(
            "JWT_SECRET 应显式设置（不能用 dev 自动生成的）\n"
            "  生成：python -c \"import secrets;print(secrets.token_urlsafe(64))\""
        )

    if not os.environ.get("ALLOWED_PROXIES", "").strip():
        log.warning(
            "ALLOWED_PROXIES 未配置 — 反代场景下 X-Forwarded-For 不可信，\n"
            "  生产部署建议显式设置（逗号分隔 IP/CIDR）"
        )

    if issues:
        msg = "PRODUCTION 模式启动校验失败：\n  - " + "\n  - ".join(issues)
        # 严重配置错配 → fail-fast。warn-only 的项（上面 ALLOWED_PROXIES）已经单独 log.warning
        raise RuntimeError(msg)


def _check_backup_path() -> int:
    """触发备份前确认数据库不存在 sync 漏洞警告路径。

    此函数只是占位 — 真备份由 take_all_snapshots() 单独跑。
    """
    return 0


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

    启动时同时做五件事：
      0. _check_master_key_in_production — 生产模式强制 MASTER_KEY 已设
      1. run_migrations — 给已存在的表加新列（schema 演进）
      2. seed_role_assignments — 初始化 15 个写作角色
      3. _recover_orphan_bridge_runs — 清理上一轮崩溃遗留的孤儿 running 行
      4. take_all_snapshots — SQLite 在线备份，最高风险的真实缓解（P1）
    """
    log.info("startup: master_key_check + migrations + seed_role_assignments + recover_orphan_bridge_runs + backup begin")
    _check_master_key_in_production()  # 生产模式 fail-fast（在 run_migrations 前）
    _check_production_hardening()     # Phase 4: production hardening（ALLOWED_ORIGINS 等）
    applied = run_migrations()
    db = SessionLocal()
    try:
        seed_role_assignments(db)
    finally:
        db.close()
    recovered = _recover_orphan_bridge_runs()
    # SQLite 在线备份：必须在种子/迁移完成后做（避免拿到迁移前的快照）。
    # 故意不 fail-fast：备份写不动也不应阻塞启动，用户可稍后手动 dev.bat backup。
    backups = take_all_snapshots()
    log.info(
        "startup: done. routes=%d migrations_applied=%d recovered_orphan_bridge_runs=%d "
        "backup novel_assistant=%s checkpoints=%s",
        len(app.routes), applied, recovered,
        backups.get("novel_assistant"), backups.get("checkpoints"),
    )
    yield
    log.info("shutdown: complete")


app = FastAPI(title="NovelAI 后端", lifespan=lifespan)

# CORS: 配置收口到 app.config.get_allowed_origins_list()，支持 NOVEL_ALLOWED_ORIGINS
# 或裸 ALLOWED_ORIGINS env（向后兼容）。默认放行前端 dev 端口 5293。
allow_origins = get_allowed_origins_list()
log.info("CORS allow_origins=%s (来源 Settings.allowed_origins)", allow_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
# 速率限制中间件：仅写端点限速（防刷 /bridge/run 触发昂贵 LLM 调用）
# 阈值收口到 app.config.settings.rate_limit_per_minute（默认 60）。
app.add_middleware(RateLimitMiddleware)

app.include_router(auth.router)               # Phase 4: 多用户认证（register/login/me/change-password）
app.include_router(projects.router)
app.include_router(worldbuild.router)
app.include_router(worldbuild.meta_router)  # /worldbuild/stages (无 project_id)
app.include_router(chapters.router)
app.include_router(providers.router)
app.include_router(role_assignments.router)
app.include_router(bridge.router)
app.include_router(rules.router)
app.include_router(foreshadowings.router)
app.include_router(ai_assist.router)
app.include_router(world.router)  # Phase 3: 5 个新 endpoint（worldview/rich + characters list/card/relations + relations/graph）


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
