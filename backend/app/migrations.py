"""app/migrations.py — 轻量级启动时 schema 迁移

历史背景：
  Project 从 Phase 1 演化到 Phase 1.5+ 时 schema 多次扩展（如 Provider 加
  api_key_encrypted 列、roles 加新 role_key）。SQLite 的
  Base.metadata.create_all 只建不存在的表，不会给已有表加新列。
  这里放一组 idempotent ALTER TABLE，启动时跑一遍，缺啥补啥。

不要在这里写"删除列"或"重命名"——SQLite 的 ALTER TABLE DROP COLUMN
是 3.35+ 才支持的，老版本会炸。删/改字段都靠手动跑 sqlite shell。
"""
from __future__ import annotations

import logging
import sqlite3
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .database import engine

log = logging.getLogger("novel_ai.migrations")


# ─── /simplify-2026-07-13-round3: 异常分类（fail-fast + TOCTOU 并存）───
# 之前 _apply_one_migration 一刀切 except Exception + 外层再兜一刀，导致
# 真实 DDL 错也被吞 → startup 静默"成功"但表结构不完整。修法：
#   1) 把 benign race（duplicate column / already exists）独立识别并吞
#   2) 任何其他异常原样 raise，让外层 + 调用方看到真相
# 这是 Phase 3 fail-fast（finding #2）和 security-2026-07-13 #4（TOCTOU）
# 共同达成的目标——两者不冲突，之前只是没分清。
_BENIGN_ALTER_PATTERNS = ("duplicate column name", "already exists")


def _is_benign_alter_error(exc: BaseException) -> bool:
    """ALTER TABLE 失败但属于良性竞态（另一进程已加）→ 返回 True，外层吞。

    两层判定：
      1. 类型预检：必须是 sqlite3.Error（或其子类 OperationalError /
         IntegrityError 等），或 SQLAlchemy 包了 sqlite3 异常的
         sqlalchemy.exc.DBAPIError（看 .orig）。排除 RuntimeError /
         ValueError 等非 DB 异常（即使消息巧合含 'duplicate column
         name' 也不误判）。
         round 3 follow-up F-7：实测 test_migration_safety 加的 exc5
         用例暴露了原实现没有类型检查。
      2. 消息模式匹配：duplicate column name / already exists。不能用
         sqlite_errorcode（对语法错 / 类型不兼容这些真错误返回
         OperationalError 21，业务侧无法可靠区分）。
    """
    from sqlalchemy.exc import DBAPIError
    if isinstance(exc, sqlite3.Error):
        msg = str(exc)
    elif isinstance(exc, DBAPIError) and isinstance(exc.orig, sqlite3.Error):
        # SQLAlchemy 包了 sqlite3 异常 → 取 .orig 的消息（消息更准确）
        msg = str(exc.orig)
    else:
        return False
    msg_lower = msg.lower()
    return any(pat in msg_lower for pat in _BENIGN_ALTER_PATTERNS)


# 增量迁移列表。每条是 (table, column, ddl_type)。
# 启动时检查 sqlite_master 看列是否存在，不存在就 ALTER TABLE ADD COLUMN。
_MIGRATIONS: list[tuple[str, str, str]] = [
    # ─── Phase 3: 单租户占位（nullable 业主 ID，暂不读写）──
    # 当前不启用多用户隔离；预埋该列避免将来上线时要对着历史数据做"回填 owner"高风险迁移。
    ("projects", "owner_id", "VARCHAR"),
    # ─── Phase 3: per-project audit_mode（去全局化）──
    # 替换原 os.environ["NOVEL_AUDIT_MODE"] 进程全局状态，避免多项目串扰。
    # 默认 'full' 兼容所有已有项目的预期行为。
    ("projects", "audit_mode", "VARCHAR"),
    # Provider 表：api_key → api_key_encrypted + api_key_suffix（commit 历史 bug 修复）
    ("providers", "api_key_encrypted", "TEXT"),
    ("providers", "api_key_suffix", "VARCHAR(8)"),
    # ─── Phase 1：世界构建板块结构化改造（世界构建 / 角色卡 / 富关系）───
    # 全部 nullable，老数据兼容
    # WorldSetting：7 段结构化世界观 + 故事核心 4 段 + 历史时间线
    ("world_settings", "world_view_rich_json", "JSON"),
    ("world_settings", "story_core_struct_json", "JSON"),
    ("world_settings", "history_timeline_json", "JSON"),
    # Character：角色卡 8 段
    ("characters", "card_basic_json", "JSON"),
    ("characters", "card_appearance_json", "JSON"),
    ("characters", "card_personality_json", "JSON"),
    ("characters", "card_background_json", "JSON"),
    ("characters", "card_abilities_json", "JSON"),
    ("characters", "card_catchphrase_json", "JSON"),
    ("characters", "card_props_json", "JSON"),
    ("characters", "card_arc_json", "JSON"),
    # EntityRelation：富关系（强度 / 标签 / 演化 / 关键事件）
    ("entity_relations", "mutual", "BOOLEAN"),
    ("entity_relations", "evolution_json", "JSON"),
    ("entity_relations", "key_events_json", "JSON"),
    ("entity_relations", "intensity", "INTEGER"),
    ("entity_relations", "tags_json", "JSON"),
    # ─── security-2026-07-13 #2: BridgeRun 追踪引擎子进程 ───
    # 双写损坏防护：lifespan 启动时用 pid 探测活体，只标"真孤儿"为 failed。
    ("bridge_runs", "pid", "INTEGER"),
    # /simplify 2026-07-13: pgid 列被移除（写了从不读；killpg 时直接用 proc.pid）。
]


def _table_exists(conn, table: str) -> bool:
    """检查 SQLite 里指定表是否存在（sqlite_master 查询）。"""
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table},
    ).fetchone()
    return row is not None


def _column_exists(conn, table: str, column: str) -> bool:
    """检查 SQLite 表是否已有某列。"""
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return any(row[1] == column for row in rows)


def _index_exists(conn, index_name: str) -> bool:
    """检查 SQLite 里指定索引是否存在（sqlite_master 查询）。"""
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"),
        {"n": index_name},
    ).fetchone()
    return row is not None


# 唯一索引迁移列表。每条 (table, index_name, columns_sql)。
# 与 _MIGRATIONS 分开：UniqueConstraint 在 __table_args__ 里只能影响新表；
# 对已有表，必须用 CREATE UNIQUE INDEX 显式补建。CREATE UNIQUE INDEX 支持
# IF NOT EXISTS，所以是天然 idempotent 的。
_UNIQUE_INDEX_MIGRATIONS: list[tuple[str, str, str]] = [
    # ─── security-2026-07-13 #1: Chapter 并发 POST 保护 ───
    # 修 chapter_no 唯一约束缺失——并发 POST /chapters 同号时两条都成功，
    # 破坏排序 + RAG 去重逻辑 (chapter_import.py 按 chapter_no 幂等去重的兜底)。
    ("chapters", "uq_chapters_project_chapter_no", "(project_id, chapter_no)"),
]


def _apply_one_migration(conn, table: str, column: str, ddl_type: str) -> bool:
    """应用一条 ADD COLUMN 迁移。返回是否真的执行了 ALTER。

    security-2026-07-13 #4 + /simplify-2026-07-13-round3:
      - ALTER 撞 TOCTOU（duplicate column / already exists）→ race-loser
        视为成功，返回 False（已存在），不抛
      - 真实 DDL 错误（语法错 / 类型不支持 / 列约束不被 SQLite 允许）→ 原样
        raise，让 startup 暴露失败而非静默"成功"
      - 失败分类走 _is_benign_alter_error 单一入口，避免散在 try/except 里
    """
    # 表不存在时跳过：通常意味着 Base.metadata.create_all 还没跑过
    # 或模型尚未引入这张表（开发期常见的"先删表再 migrate"场景）。
    # 下次启动 Base.metadata.create_all 把表创建好后，列会自动被加。
    if not _table_exists(conn, table):
        log.debug("migration skipped (table missing): %s.%s", table, column)
        return False
    if _column_exists(conn, table, column):
        return False  # 已存在 (idempotent)
    # SQLite 不支持 IF NOT EXISTS on ADD COLUMN。
    log.info("migration applying: %s.%s (%s)", table, column, ddl_type)
    try:
        conn.execute(
            text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")
        )
    except Exception as alter_exc:  # noqa: BLE001
        if _is_benign_alter_error(alter_exc):
            # --reload 时新旧 uvicorn 并存会撞 TOCTOU——
            # 两个进程都读到"列不存在"→ 都尝试 ALTER → 后提交者抛
            # "duplicate column name"。捕获并视为 race-loser 的成功。
            log.info(
                "migration raced (column already added by another process): "
                "%s.%s — treating as success",
                table, column,
            )
            return False
        # 真实 DDL 错误（语法错 / 类型不被支持 / 不支持的列约束等）→ 原样
        # raise，让调用方和启动流程看到真相，不要静默吞掉。
        raise
    log.info("migration applied: %s.%s (%s)", table, column, ddl_type)
    return True


def _apply_one_index_migration(conn, table: str, index_name: str,
                                columns_sql: str) -> bool:
    """应用一条 CREATE UNIQUE INDEX 迁移。返回是否真的执行。

    IF NOT EXISTS 已处理「index 自身重复创建」的良性 race；
    但**唯一约束违反**（数据层面，例如 chapters 表已存在重复
    (project_id, chapter_no) 行时 CREATE UNIQUE INDEX 会抛
    `IntegrityError: UNIQUE constraint failed`）仍会正常 propagate——
    这是 fail-fast 设计的预期行为：数据完整性问题必须让 startup 暴露，
    不能静默"成功"。run_migrations 外层不再吞任何异常。
    """
    if not _table_exists(conn, table):
        log.debug("unique-index migration skipped (table missing): %s.%s",
                  table, index_name)
        return False
    if _index_exists(conn, index_name):
        return False
    log.info("migration applying unique index: %s ON %s%s",
             index_name, table, columns_sql)
    conn.execute(
        text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
             f"ON {table} {columns_sql}")
    )
    log.info("migration applied unique index: %s", index_name)
    return True


def run_migrations(target_engine: Engine | None = None) -> int:
    """启动时跑所有增量迁移。返回成功执行的条数。

    ─── Phase 3 异常收窄 + security-2026-07-13 #4 TOCTOU + /simplify-round3 ───
    三个目标并存，且**不冲突**：

      1. 表不存在 → 跳过该列（Base.metadata.create_all 创建表后下次启动补加）
      2. 列已存在 → 跳过（idempotent）
      3. ALTER 撞 TOCTOU（duplicate column / already exists）→ race-loser
         视为成功，不抛（_apply_one_migration 内部已归类）
      4. ALTER 真实失败（语法错 / 类型不兼容 / 不支持的列约束）→ 原样
         raise，让 startup / 调用方看到真相，不要静默"成功"

    历史上这层 for-loop 套过 except Exception 来兜单条失败——但 `_apply_one_migration`
    已经把良性 race 单独识别并吞了，外层再 except 只会**反向把真 DDL 错也吞掉**，
    触发 test_migration_fail_fast_on_ddl_error 失败。删掉外层兜底。

    ─── 事务回滚语义（重要）───
    整个函数在 `with target_engine.begin() as conn:` 单事务里跑。
    任何一条迁移抛出（即使是中间第 N 条），整事务 ROLLBACK——**包括
    本次已成功执行的 #1 ~ #(N-1) 条 ALTER TABLE**。
    下次启动时这 #1 ~ #(N-1) 条会重新执行（_column_exists 返回 False）
    ——idempotent 设计保证重放安全，但日志会重复出现"migration applying"，
    这是预期行为，不是 bug。
    """
    target_engine = target_engine or engine
    applied = 0
    with target_engine.begin() as conn:
        for table, column, ddl_type in _MIGRATIONS:
            if _apply_one_migration(conn, table, column, ddl_type):
                applied += 1
        # 唯一索引迁移：与 _MIGRATIONS 分开，因为 CREATE UNIQUE INDEX 走
        # IF NOT EXISTS 而不是 PRAGMA + ALTER 两步。IF NOT EXISTS 本身不会
        # 抛 duplicate error，所以这条循环不需要 benign 判断。
        for table, index_name, columns_sql in _UNIQUE_INDEX_MIGRATIONS:
            if _apply_one_index_migration(conn, table, index_name, columns_sql):
                applied += 1
    return applied


if __name__ == "__main__":
    # python -m app.migrations 单独跑
    n = run_migrations()
    print(f"applied {n} migration(s)")