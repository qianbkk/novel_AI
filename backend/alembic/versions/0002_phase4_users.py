"""phase4_users — Phase 4: 创建 users 表（多用户认证）

revision = 0002_phase4_users
down_revision = 0001_baseline
branch_labels = None
depends_on = None

效果：建一张 users 表，对应 app/models.py:User（id/email/display_name/
password_hash/created_at）。app/main.py lifespan 已经通过
Base.metadata.create_all 会建好它，但走 alembic 路径更显式、可控。

为什么不在 Base.metadata.create_all 之外再让 alembic 建？
  - 显式版本化：能复现"这一步做了什么"
  - 与 alembic stamp 配套，未来 dev / staging / prod 走同一套迁移脚本
  - reverse 一键回滚

如果是新建项目（baseline 没 stamp 过）就跑 alembic upgrade head，
一张表已存在的情况不会出现（baseline 已经把 schema 拍齐，0 + 1 + 2 ...）。
升级路径：以前没 alembic 的 DB → alembic stamp head → 之后正常 upgrade。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_phase4_users"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("email", sa.String, nullable=False, unique=True),
        sa.Column("display_name", sa.String, nullable=True),
        sa.Column("password_hash", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("users")
