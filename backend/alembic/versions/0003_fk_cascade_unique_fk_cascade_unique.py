"""fk_cascade_unique

Revision ID: 0003_fk_cascade_unique
Revises: 0002_phase4_users
Create Date: 2026-07-25 00:02:05.657325

效果：
  1) Project.owner_id FK → users.id (nullable)
  2) 所有 project_id FK 加 ondelete='CASCADE'（删 Project 自动级联）
  3) 联合唯一约束：ChapterCharacter(chapter_id, character_id) / Outline(project_id, arc_id)

为什么用纯 SQL 而不是 alembic batch_alter_table：
  - SQLite PRAGMA foreign_key_list 不显示 FK 约束名，alembic drop_constraint 需要
    显式 FK 名（无法自动发现）
  - 改用 op.execute(\"PRAGMA foreign_keys=OFF\") + raw SQL recreate 表
  - 简单可靠，跨 alembic / SQLAlchemy 版本一致
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0003_fk_cascade_unique'
down_revision: Union[str, None] = '0002_phase4_users'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES_WITH_PROJECT_FK = [
    "world_settings", "characters", "entity_relations", "factions",
    "power_systems", "currencies", "map_nodes", "foreshadowings",
    "chapters", "outlines", "novel_ai_bindings",
    "bridge_runs", "generation_jobs", "rule_configs",
]


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("projects"):
        # 干净数据库路径：0001 是兼容老库的空 baseline，0002 只创建 users。
        # 到 0003 时若核心表仍不存在，直接按当前 ORM metadata 创建最终 schema；
        # 老库已有 projects 时继续走下面的显式重建迁移。
        from app.database import Base
        from app import models  # noqa: F401  — 注册全部 ORM 表

        Base.metadata.create_all(bind=bind)
        return

    # 1) Project.owner_id FK → users.id (nullable)
    #    SQLite 缺 ALTER TABLE ADD FOREIGN KEY，只能 recreate 表
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        # SQLite DDL 非事务：旧版 0003 若中途失败可能遗留 projects_new。
        # projects 仍存在时该表只是失败迁移的临时副本，可清理后重试。
        op.execute("DROP TABLE IF EXISTS projects_new")
        op.execute("""
            CREATE TABLE projects_new (
                id VARCHAR PRIMARY KEY,
                title VARCHAR,
                genre VARCHAR NOT NULL,
                audience VARCHAR,
                config_json JSON NOT NULL,
                status VARCHAR DEFAULT 'draft',
                ai_assist_level VARCHAR DEFAULT 'ai_assisted',
                budget_limit_usd FLOAT,
                novel_ai_status VARCHAR DEFAULT 'not_started',
                owner_id VARCHAR,
                audit_mode VARCHAR DEFAULT 'full',
                created_at DATETIME,
                FOREIGN KEY(owner_id) REFERENCES users(id)
            )
        """)
        op.execute("INSERT INTO projects_new SELECT * FROM projects")
        op.execute("DROP TABLE projects")
        op.execute("ALTER TABLE projects_new RENAME TO projects")
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    # 2) 所有 project_id FK 加 ondelete=CASCADE。
    # SQLite 旧库的 FK 通常是匿名约束；batch drop 一个推测出来的名字必然失败。
    # 通过反射复制整张表，只替换 project_id -> projects.id 这一条 FK，再由
    # batch_alter_table(copy_from=...) 完成安全重建。保留其它列、索引和约束。
    for table in _TABLES_WITH_PROJECT_FK:
        if not inspector.has_table(table):
            continue
        metadata = sa.MetaData()
        reflected = sa.Table(table, metadata, autoload_with=bind)
        if "project_id" not in reflected.c:
            continue

        old_fks = [
            fk for fk in reflected.foreign_key_constraints
            if list(fk.column_keys) == ["project_id"]
            and fk.referred_table.name == "projects"
        ]
        if old_fks and all((fk.ondelete or "").upper() == "CASCADE" for fk in old_fks):
            continue
        for fk in old_fks:
            reflected.constraints.discard(fk)
        reflected.append_constraint(sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name=f"fk_{table}_project_id_projects",
            ondelete="CASCADE",
        ))
        with op.batch_alter_table(table, schema=None, copy_from=reflected) as batch_op:
            batch_op.alter_column("project_id", existing_type=reflected.c.project_id.type)

    # 3) ChapterCharacter(chapter_id, character_id) UNIQUE。
    # SQLite 不能 ALTER ADD CONSTRAINT，统一用 batch copy-and-move。
    if inspector.has_table("chapter_characters"):
        unique_sets = {
            tuple(item.get("column_names") or [])
            for item in sa.inspect(bind).get_unique_constraints("chapter_characters")
        }
        if ("chapter_id", "character_id") not in unique_sets:
            with op.batch_alter_table("chapter_characters", schema=None) as batch_op:
                batch_op.create_unique_constraint(
                    "uq_chapter_characters_chapter_character",
                    ["chapter_id", "character_id"],
                )

    # 4) Outline(project_id, arc_id) UNIQUE
    if inspector.has_table("outlines"):
        unique_sets = {
            tuple(item.get("column_names") or [])
            for item in sa.inspect(bind).get_unique_constraints("outlines")
        }
        if ("project_id", "arc_id") not in unique_sets:
            with op.batch_alter_table("outlines", schema=None) as batch_op:
                batch_op.create_unique_constraint(
                    "uq_outlines_project_arc",
                    ["project_id", "arc_id"],
                )


def downgrade() -> None:
    # 倒序撤销
    op.drop_constraint("uq_outlines_project_arc", "outlines", type_="unique")
    op.drop_constraint(
        "uq_chapter_characters_chapter_character",
        "chapter_characters",
        type_="unique",
    )

    # FK 撤销：drop cascade constraint，create 无 cascade
    for table in _TABLES_WITH_PROJECT_FK:
        cols = op.get_bind().execute(
            sa.text(f"PRAGMA table_info({table})")
        ).fetchall()
        col_names = [c[1] for c in cols]
        if "project_id" not in col_names:
            continue
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table}_project_id_projects", type_="foreignkey"
            )
            batch_op.create_foreign_key(
                f"fk_{table}_project_id_projects",
                "projects",
                ["project_id"], ["id"],
            )

    # 撤销 Project.owner_id FK
    op.execute("PRAGMA foreign_keys=OFF")
    try:
        op.execute("""
            CREATE TABLE projects_new (
                id VARCHAR PRIMARY KEY,
                title VARCHAR,
                genre VARCHAR NOT NULL,
                audience VARCHAR,
                config_json JSON NOT NULL,
                status VARCHAR DEFAULT 'draft',
                ai_assist_level VARCHAR DEFAULT 'ai_assisted',
                budget_limit_usd FLOAT,
                novel_ai_status VARCHAR DEFAULT 'not_started',
                owner_id VARCHAR,
                audit_mode VARCHAR DEFAULT 'full',
                created_at DATETIME
            )
        """)
        op.execute("INSERT INTO projects_new SELECT * FROM projects")
        op.execute("DROP TABLE projects")
        op.execute("ALTER TABLE projects_new RENAME TO projects")
    finally:
        op.execute("PRAGMA foreign_keys=ON")
