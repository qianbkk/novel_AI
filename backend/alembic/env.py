"""alembic/env.py — Alembic 配置（Phase 4）

要点：
  - URL 从 app.config.settings.database_url 动态注入（覆盖 alembic.ini 里的占位）
  - target_metadata 接 SQLAlchemy Base.metadata，让 --autogenerate 能检测模型变化
  - render_as_batch=True 处理 SQLite ALTER TABLE 的限制（SQLite 没有真正的 ALTER，需要
    "create new + copy + rename"模式）

环境一致性：和 app/main.py lifespan 的 run_migrations 共存——alembic 主要
给"显式版本化的结构变更"用，run_migrations（idempotent ALTER）继续负责
启动时的加列补漏。详见 alembic.ini 顶部注释。
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

# 把 backend 加到 sys.path，alembic 才会找到 app.*
BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from alembic import context  # noqa: E402
from sqlalchemy import engine_from_config, pool  # noqa: E402

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _resolve_database_url() -> str:
    """从 app.config 拿 DB URL，让 alembic 用同一份配置。

    优先级：
      1) os.environ['DATABASE_URL']（直接读，未走 settings 缓存）
      2) settings.database_url（config 默认 / 已有 .env 配置）
      3) alembic.ini 占位
    """
    env = os.environ.get("DATABASE_URL", "").strip()
    if env:
        return env
    try:
        from app.config import settings
        return settings.database_url
    except Exception as exc:  # pragma: no cover
        print(f"[alembic.env] 无法从 app.config 读 database_url：{exc}",
              file=sys.stderr)
        return config.get_main_option("sqlalchemy.url") or ""


# 把动态 URL 灌进 alembic config.section
config.set_main_option("sqlalchemy.url", _resolve_database_url())


# ── target_metadata：从 Base.metadata 拿所有模型 ──
target_metadata = None
try:
    from app.database import Base
    from app import models  # noqa: F401  — 触发模型注册
    target_metadata = Base.metadata
except Exception as exc:
    # models 导入失败（缺包等）→ 仍可跑 alembic（手写 migrations），
    # 只失去 --autogenerate 能力。
    print(f"[alembic.env] 无法自动注册模型：{exc}", file=sys.stderr)


def run_migrations_offline() -> None:
    """'offline' mode：只生成 SQL 不真跑（运维 review 用）。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite 必须
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """'online' mode：连 DB 跑迁移。"""
    # 仅保留 sqlalchemy.url 这一个 section 配置项
    cfg_section = {
        "sqlalchemy.url": config.get_main_option("sqlalchemy.url"),
    }
    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite ALTER 限制处理
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
