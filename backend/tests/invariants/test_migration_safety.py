"""migrations.py 容错性 (security-2026-07-13 #4)

锁定：
  - 单条迁移抛 "duplicate column" (--reload 并发竞态) → 视为 race-loser 成功，
    不让 startup 崩溃
  - 单条迁移抛其他 DDL 错误 → 记 warning 但继续 startup（不让一条坏迁移拖垮整个进程）
  - 正常情况下 run_migrations 仍然 idempotent
"""


class TestMigrationRacingSafety:
    """ALTER TABLE ADD COLUMN 撞 duplicate column 应被吞掉。"""

    def test_duplicate_column_error_swallowed(self, db_bootstrap):
        """模拟 race-loser 场景：列已存在（PRAGMA 没刷新过来），ALTER 抛错应被吞。"""
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
            # 应不抛异常，return applied 数 ≥ 0
            applied = mig_mod.run_migrations()
            assert isinstance(applied, int)
            assert applied >= 0
        finally:
            mig_mod._column_exists = original


class TestMigrationIsolatedFailure:
    """单条迁移失败不应让整个 startup 崩溃。"""

    def test_bad_migration_does_not_crash_run(self, db_bootstrap):
        """模拟一条会失败的迁移—— 不应 raise。"""
        from app import migrations as mig_mod

        # 临时把 _MIGRATIONS 换成一条必败的迁移（指向不存在的表）
        original = mig_mod._MIGRATIONS
        bad = [("totally_nonexistent_table_xyz", "col1", "INTEGER")]
        mig_mod._MIGRATIONS = bad
        try:
            # 不应抛异常——单条坏迁移应被吞掉只记 warning
            # （注：指向不存在表时 `_apply_one_migration` 会因为 _table_exists
            # 返回 False 而跳过，所以 applied=0 也不抛）
            applied = mig_mod.run_migrations()
            assert applied == 0  # 没成功执行的迁移
        finally:
            mig_mod._MIGRATIONS = original

    def test_normal_migrations_still_apply_after_bad_one(self, db_bootstrap):
        """坏 migration 之后好 migration 仍应继续跑（直接 patch _apply_one_migration）。"""
        from app import migrations as mig_mod

        original = mig_mod._apply_one_migration
        # 第一次调用 raise（模拟坏 DDL），第二次调用正常返回 True
        call_count = {"n": 0}
        def patched(conn, table, column, ddl_type):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("simulated DDL failure")
            return True
        mig_mod._apply_one_migration = patched
        try:
            # 不抛即可；第二次"好"的迁移应被 apply，applied=1
            applied = mig_mod.run_migrations()
            assert applied >= 1, f"坏 migration 后的好 migration 应继续 apply，实际 applied={applied}"
            assert call_count["n"] >= 2, "两次迁移应都被尝试执行"
        finally:
            mig_mod._apply_one_migration = original