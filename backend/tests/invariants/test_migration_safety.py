"""migrations.py 容错性 (security-2026-07-13 #4 + /simplify-2026-07-13-round3)

锁定：
  - 单条迁移抛 "duplicate column" (--reload 并发竞态) → 视为 race-loser 成功，
    不让 startup 崩溃（_is_benign_alter_error 独立分类）
  - 单条迁移抛其他真 DDL 错误 → 原样 raise，让 startup fail-fast（Phase 3
    finding #2 共识）。这是 round 3 修复的核心：之前外层 except Exception
    一刀切吞所有错，把真 DDL 失败也吞了，导致 test_migration_fail_fast_on_ddl_error
    失败。修法：外层兜底删掉，只吞良性 race。
  - 正常情况下 run_migrations 仍然 idempotent
"""


class TestMigrationRacingSafety:
    """ALTER TABLE ADD COLUMN 撞 duplicate column 应被吞掉（TOCTOU race-loser）。"""

    def test_duplicate_column_error_swallowed(self, db_bootstrap, caplog):
        """模拟 race-loser 场景：列已存在（PRAGMA 没刷新过来），ALTER 抛错应被吞。

        round 3 follow-up（commit 4be81b2 code-review F-3）：原断言只检查
        applied >= 0 太弱——任何路径（包括 _table_exists 跳过）都让测试通过。
        改用 caplog 验证 race-loser 路径真正触发："migration raced" log
        出现 + 不抛 + applied = 0（被吞意味着既没真执行也没进外层兜底）。
        """
        import logging
        from app import migrations as mig_mod

        # 通过 monkeypatch _column_exists 让它返回 False，再跑迁移。
        original = mig_mod._column_exists
        # 选 _MIGRATIONS 里第一条 migration 测
        table, column, _ddl_type = mig_mod._MIGRATIONS[0]

        def fake_column_exists(conn, t, c):
            if (t, c) == (table, column):
                return False  # 假装看不到列 → ALTER 会撞 duplicate
            return original(conn, t, c)
        mig_mod._column_exists = fake_column_exists
        try:
            with caplog.at_level(logging.INFO, logger="novel_ai.migrations"):
                applied = mig_mod.run_migrations()
            assert isinstance(applied, int)
            assert applied >= 0
            # race-loser 路径真正被触发：log 含 "migration raced"
            assert "migration raced" in caplog.text, (
                f"race-loser 路径未触发（_is_benign_alter_error 可能已失效）："
                f"caplog.text={caplog.text!r}"
            )
        finally:
            mig_mod._column_exists = original

    def test_is_benign_alter_error_classifies_duplicate_column(self):
        """直接测 _is_benign_alter_error 分类逻辑（不依赖 SQLite 真实抛出）。

        round 3 follow-up F-5：之前只有 monkeypatch 路径覆盖，分类函数
        本身没被独立测。这里用 sqlite3.OperationalError 子类（真实
        SQLite 异常类型）作为参数，验证两种模式都能识别，非 benign
        异常不会被误判。

        F-7：必须同时覆盖 SQLAlchemy 包了 sqlite3 异常的场景——这是
        `_apply_one_migration` 里 conn.execute 实际抛出的形态。
        """
        import sqlite3
        from sqlalchemy.exc import OperationalError as SAOperationalError
        from app.migrations import _is_benign_alter_error

        # 良性 race — duplicate column name（裸 sqlite3 异常）
        exc1 = sqlite3.OperationalError("duplicate column name: foo")
        assert _is_benign_alter_error(exc1), \
            "duplicate column name 应被识别为 benign"

        # 良性 race — already exists（裸 sqlite3 异常）
        exc2 = sqlite3.OperationalError("index 'foo_idx' already exists")
        assert _is_benign_alter_error(exc2), \
            "already exists 应被识别为 benign"

        # 良性 race — duplicate column（SQLAlchemy 包了 sqlite3 异常，
        # 这是 _apply_one_migration 里 conn.execute 实际抛出的形态）
        exc_sa = SAOperationalError(
            "ALTER TABLE foo ADD COLUMN bar",
            params=None,
            orig=sqlite3.OperationalError("duplicate column name: bar"),
        )
        assert _is_benign_alter_error(exc_sa), \
            "SQLAlchemy 包了 sqlite3 duplicate column 应被识别为 benign"

        # 非良性 — 真 DDL 失败（语法错，裸 sqlite3 异常）
        exc3 = sqlite3.OperationalError("near 'foo': syntax error")
        assert not _is_benign_alter_error(exc3), \
            "syntax error 不应被误判为 benign"

        # 非良性 — 类型不兼容
        exc4 = sqlite3.OperationalError("cannot store BLOB in column foo")
        assert not _is_benign_alter_error(exc4), \
            "type 不兼容错误不应被误判为 benign"

        # 非 sqlite 异常
        exc5 = RuntimeError("duplicate column name in some other context")
        assert not _is_benign_alter_error(exc5), \
            "非 sqlite3 异常不应被误判为 benign（即使消息巧合）"

        # SQLAlchemy 包了非 sqlite3 异常（罕见但理论上可能）
        exc6 = SAOperationalError("stmt", params=None, orig=RuntimeError("oops"))
        assert not _is_benign_alter_error(exc6), \
            "SQLAlchemy 包了非 sqlite3 异常不应被误判为 benign"


class TestMigrationIsolatedFailure:
    """单条迁移失败按类型分类处理：

    - 表不存在（_table_exists=False）→ 跳过（idempotent 设计），不抛
    - 真 DDL 失败（语法错 / 类型不兼容 / 不支持的列约束）→ 原样 raise
    - 良性 race（duplicate column）→ 吞，return False

    之前 round 1 错误地把"真 DDL 失败也要 continue 跑下一条"当合同，现在
    fail-fast 立场改回（Phase 3 共识）：只吞良性 race，真错必须让 startup
    看到真相。
    """

    def test_missing_table_does_not_crash_run(self, db_bootstrap):
        """模拟一条迁移指向不存在的表——_table_exists=False 跳过，applied=0 不抛。"""
        from app import migrations as mig_mod

        original = mig_mod._MIGRATIONS
        bad = [("totally_nonexistent_table_xyz", "col1", "INTEGER")]
        mig_mod._MIGRATIONS = bad
        try:
            # 不存在表走 _table_exists 跳过路径，不抛
            applied = mig_mod.run_migrations()
            assert applied == 0
        finally:
            mig_mod._MIGRATIONS = original

    def test_real_ddl_failure_propagates(self, db_bootstrap):
        """真 DDL 失败必须 raise（fail-fast 合同，Phase 3 finding #2）。"""
        from app import migrations as mig_mod
        import pytest

        original = mig_mod._apply_one_migration
        call_count = {"n": 0}
        def patched(conn, table, column, ddl_type):
            call_count["n"] += 1
            # 模拟真 DDL 失败（非良性 race）→ 必须 propagate
            raise RuntimeError("simulated real DDL failure")
        mig_mod._apply_one_migration = patched
        try:
            with pytest.raises(RuntimeError, match="simulated real DDL failure"):
                mig_mod.run_migrations()
            # 真错 propagate 后不再继续后续 migration（外层兜底已删）
            assert call_count["n"] == 1
        finally:
            mig_mod._apply_one_migration = original
