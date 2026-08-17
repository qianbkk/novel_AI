"""characters_status_died_in_chapter

Revision ID: 0004_characters_status
Revises: 0003_fk_cascade_unique
Create Date: 2026-08-17 00:00:00

P2-12（2026-08-17）：Character 表加 status + died_in_chapter 列。

为什么需要：
- 审计发现 tracker._merge_character_states 用 substring fuzzy dedup
  把"死亡/濒死/半死"当成不同 key 持续追加，50 章后反派复活 / 死人冒头
  无前置闸门。
- writer prompt 在 main_characters 注入前不查 status → 把死人塞进 prompt。
- 加 status 列（active/dead/missing）+ died_in_chapter int nullable 后，
  writer 可过滤 dead 角色，beat_checker 可检测"已死又出现"。

迁移策略（SQLite）：
- ALTER TABLE ADD COLUMN 默认 NOT NULL DEFAULT 'active'（老行回填 active）
- died_in_chapter 允许 NULL（新老行一致）
- batch_alter_table 兼容 SQLite 列添加
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_characters_status"
down_revision: str | None = "0003_fk_cascade_unique"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("characters"):
        # 干净数据库路径：columns 已被 ORM metadata 创建，跳过
        return

    # 检查列是否已存在（避免重复添加）
    existing_cols = {
        c["name"] for c in inspector.get_columns("characters")
    }
    with op.batch_alter_table("characters", schema=None) as batch_op:
        if "status" not in existing_cols:
            batch_op.add_column(
                sa.Column(
                    "status",
                    sa.String(),
                    nullable=False,
                    server_default="active",
                    default="active",
                )
            )
        if "died_in_chapter" not in existing_cols:
            batch_op.add_column(
                sa.Column(
                    "died_in_chapter",
                    sa.Integer(),
                    nullable=True,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("characters"):
        return

    existing_cols = {
        c["name"] for c in inspector.get_columns("characters")
    }
    with op.batch_alter_table("characters", schema=None) as batch_op:
        if "died_in_chapter" in existing_cols:
            batch_op.drop_column("died_in_chapter")
        if "status" in existing_cols:
            batch_op.drop_column("status")