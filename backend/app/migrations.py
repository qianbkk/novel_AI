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
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .database import engine

log = logging.getLogger("novel_ai.migrations")


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

    security-2026-07-13 #4:
      - ALTER 撞 "duplicate column" → race-loser 视为成功，返回 False（已存在）
      - 真实 DDL 错误 → raise，让 run_migrations 的外层 except 捕获并 log warning
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
        msg = str(alter_exc).lower()
        if "duplicate column" in msg or "already exists" in msg:
            # --reload 时新旧 uvicorn 并存会撞 TOCTOU——
            # 两个进程都读到"列不存在"→ 都尝试 ALTER → 后提交者抛
            # "duplicate column name"。捕获并视为 race-loser 的成功。
            log.info(
                "migration raced (column already added by another process): "
                "%s.%s — treating as success",
                table, column,
            )
            return False
        # 真实 DDL 错误（语法错 / 类型不被支持等）→ 让外层 catch
        raise
    log.info("migration applied: %s.%s (%s)", table, column, ddl_type)
    return True


def _apply_one_index_migration(conn, table: str, index_name: str,
                                columns_sql: str) -> bool:
    """应用一条 CREATE UNIQUE INDEX 迁移。返回是否真的执行。"""
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

    ─── Phase 3: 异常收窄 ───
    之前的实现是 except Exception 一刀切吞掉所有错误（包括真实 DDL 失败），
    只剩 warning。这有隐藏风险：DDL 写错（语法/表名拼错/字段类型不被 SQLite 支持）
    会被静默吞掉，启动看似正常但表结构其实不完整，后续 INSERT/SELECT 会炸。

    新策略：
      1. 表不存在 → 跳过该列（Base.metadata.create_all 创建表后下次启动会补加）
      2. 列已存在 → 跳过（idempotent）
      3. ALTER TABLE 真实失败 → raise，让外层 catch 记录 warning 但不 crash

    ─── security-2026-07-13 #4: TOCTOU + 单条失败隔离 ───
      - ALTER 撞 "duplicate column" 视为 race-loser 成功（--reload 并发启动）
      - 单条迁移失败 → warning + 继续下一条，绝不让 startup 崩溃
    """
    target_engine = target_engine or engine
    applied = 0
    with target_engine.begin() as conn:
        # security-2026-07-13 #4: 单条迁移失败不应拖垮 startup。
        # 真实场景：用户跑 `git pull` 后改 schema 列类型，ALTER TABLE 因
        # "type incompatibility" 失败——这不该让后端进程退出；logging + 继续
        # 跑下一条即可。开发者发现 SQL 错误时看 startup log 即可定位。
        for table, column, ddl_type in _MIGRATIONS:
            try:
                if _apply_one_migration(conn, table, column, ddl_type):
                    applied += 1
            except Exception as loop_exc:  # noqa: BLE001
                log.warning(
                    "migration entry failed (continuing to next): %s.%s (%s) — %s",
                    table, column, ddl_type, loop_exc,
                )
        # 唯一索引迁移：与 _MIGRATIONS 分开，因为 CREATE UNIQUE INDEX 走
        # IF NOT EXISTS 而不是 PRAGMA + ALTER 两步。
        for table, index_name, columns_sql in _UNIQUE_INDEX_MIGRATIONS:
            try:
                if _apply_one_index_migration(conn, table, index_name, columns_sql):
                    applied += 1
            except Exception as loop_exc:  # noqa: BLE001
                log.warning(
                    "unique-index migration failed (continuing): %s ON %s%s — %s",
                    index_name, table, columns_sql, loop_exc,
                )
    return applied


if __name__ == "__main__":
    # python -m app.migrations 单独跑
    n = run_migrations()
    print(f"applied {n} migration(s)")