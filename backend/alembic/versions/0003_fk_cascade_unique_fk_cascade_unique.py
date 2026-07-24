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
    # 1) Project.owner_id FK → users.id (nullable)
    #    SQLite 缺 ALTER TABLE ADD FOREIGN KEY，只能 recreate 表
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
                created_at DATETIME,
                FOREIGN KEY(owner_id) REFERENCES users(id)
            )
        """)
        op.execute("INSERT INTO projects_new SELECT * FROM projects")
        op.execute("DROP TABLE projects")
        op.execute("ALTER TABLE projects_new RENAME TO projects")
    finally:
        op.execute("PRAGMA foreign_keys=ON")

    # 2) 所有 project_id FK 加 ondelete=CASCADE
    #    简单做法：直接 drop+recreate constraint。SQLite ALTER TABLE
    #    支持 DROP/ADD FOREIGN KEY（在 3.35+）但 constraint 名需已知。
    #    用 PRAGMA foreign_key_list 查不到 constraint 名，但 sqlite_master
    #    sql 字段含完整 CREATE TABLE 文本，可 regex 提取。
    #    更简单：直接重建表（同 #1 模式）
    for table in _TABLES_WITH_PROJECT_FK:
        # 检查表是否有 project_id 列
        cols = op.get_bind().execute(
            sa.text(f"PRAGMA table_info({table})")
        ).fetchall()
        col_names = [c[1] for c in cols]
        if "project_id" not in col_names:
            continue
        # 用 batch_alter_table 让 alembic 自动管理 schema（不加 FK 名
        # 重建，让 alembic 用新名字）
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(
                f"fk_{table}_project_id_projects", type_="foreignkey"
            )
            batch_op.create_foreign_key(
                f"fk_{table}_project_id_projects",
                "projects",
                ["project_id"], ["id"],
                ondelete="CASCADE",
            )

    # 3) ChapterCharacter(chapter_id, character_id) UNIQUE
    op.create_unique_constraint(
        "uq_chapter_characters_chapter_character",
        "chapter_characters",
        ["chapter_id", "character_id"],
    )

    # 4) Outline(project_id, arc_id) UNIQUE
    op.create_unique_constraint(
        "uq_outlines_project_arc",
        "outlines",
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
