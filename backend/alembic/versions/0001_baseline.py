"""baseline migration — 标记当前 schema 为"已应用"

revision = 0001_baseline
down_revision = None

Phase 4 引入 Alembic 的第一步：把现有 DB 的 schema 状态拍一个 baseline，
用 alembic stamp head 一次性 record。

为什么不写完整的 CREATE TABLE 迁移？
  本项目起步时全是 SQLAlchemy Base.metadata.create_all + app/migrations.py
  那套裸 ALTER 模式引入的，Alembic 是事后加入。如果写一份完整 CREATE TABLE
  的 baseline migration，已存在 DB 上跑 upgrade head 会因为表已存在而抛
  "table already exists"。
  修法：让 alembic 提交"baseline"这个**空 migration**，再 `alembic stamp head`
  把当前 DB 标记为"已应用 baseline"。新 schema 变更走 revision 路径，干净分界。

适用场景（迁移路径）：
  - Phase 4 之前：alembic stamp head（一次性，从此 alembic 接管变更）
  - 之后：alembic revision --autogenerate -m "..." → alembic upgrade head

注意：app/migrations.py 仍然跑（启动时给已存在表补 idempotent ALTER）。
这是有意并存，两个系统职责分明：
  - migrations.py：列补漏（每次启动，安全 idempotent）
  - alembic：建新表 / 改 FK / 删列 / 改类型（显式版本化）
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """baseline 不做任何 DDL——只让 alembic 知道"我们从这开始"。

    历史数据全在 schema 已经按 Base.metadata 跑过 create_all；
    app/migrations.py 把后期加的列（owner_id / audit_mode / Phase 1 各列）补上。
    这里空 upgrade 即可。
    """
    pass


def downgrade() -> None:
    """baseline 不能 downgrade——回到 baseline 之前意味着放弃 alembic 整条线。
    在历史数据上 revert 风险太大，留 raise 提示。
    """
    raise NotImplementedError(
        "baseline migration 不能 downgrade——如需回退请手动维护。"
    )
