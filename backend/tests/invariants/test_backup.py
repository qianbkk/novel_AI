"""backup/ — Phase 3 测试拆分

不变量测试按业务域分文件存放。
测试按业务域直接收集，不再经过兼容 re-export 模块。
"""

from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(REPO_ROOT)
sys.path.insert(0, str(BACKEND))

# 共享 schema validator imports
from app.schema_validator import (  # noqa: E402,F401
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,
)

class TestBackupDB:
    """P1: SQLite 数据库自动备份 — 单租户本地原型的最高真实风险缓解。

    设计点:
      - 使用 sqlite3.Connection.backup() 而不是 shutil.copy：cp 会捕获半写页，
        backup() 走 SQLite online backup 协议拿一致性快照。
      - 启动时备份失败不应 crash startup：必须返回 None 让请求可以继续。
      - 保留最近 N 份（默认 10），通过 mtime 排序删除最旧的。
    """

    @pytest.fixture(autouse=True)
    def _enable_backup_for_unit_tests(self, monkeypatch):
        """The global test safety net skips backups; this class uses temp dirs."""
        from app import backup_db
        monkeypatch.delenv(backup_db.ENV_SKIP, raising=False)

    @staticmethod
    def _patch_dirs(monkeypatch, base):
        """Helper: redirect _data_dir & _backup_dir under base/.
        _backup_dir 的语义是『返回（必要时创建）的路径』 — mock 时必须保留 mkdir。
        """
        from app import backup_db as _bd
        monkeypatch.setattr(_bd, "_data_dir", lambda: base)

        def _mock_backup_dir():
            bdd = base / "backups"
            bdd.mkdir(parents=True, exist_ok=True)
            return bdd

        monkeypatch.setattr(_bd, "_backup_dir", _mock_backup_dir)

    def test_take_snapshot_creates_file(self, tmp_path, monkeypatch):
        """正常路径：take_snapshot 创建一个一致性快照，内容完整。"""
        from app import backup_db
        import sqlite3
        src = tmp_path / "test.db"
        conn = sqlite3.connect(str(src))
        conn.execute("CREATE TABLE foo (x INTEGER)")
        conn.execute("INSERT INTO foo VALUES (42)")
        conn.commit()
        conn.close()
        # 重定向 _data_dir / _backup_dir 到 tmp_path
        self._patch_dirs(monkeypatch, tmp_path)
        snap = backup_db.take_snapshot(src, label="test")
        assert snap is not None, "take_snapshot 应返回快照路径"
        assert snap.exists(), "snapshot 文件不存在"
        assert snap.parent.name == "backups", (
            f"快照应存入 backups/ 目录，实际着: {snap.parent}"
        )
        # 验证内容完整（backup API 的核心价值）
        verify = sqlite3.connect(str(snap))
        try:
            row = verify.execute("SELECT x FROM foo").fetchone()
        finally:
            verify.close()
        assert row is not None, "snapshot 里查询出异常"
        assert row[0] == 42, f"快照中的数据应该 == 42，实际: {row[0]}"

    def test_take_snapshot_missing_source_returns_none(self, tmp_path, monkeypatch):
        """源 DB 不存在时不应 crash startup：必须返回 None 并记录警告。"""
        from app import backup_db
        self._patch_dirs(monkeypatch, tmp_path)
        result = backup_db.take_snapshot(tmp_path / "nonexistent.db", label="nope")
        assert result is None, f"源不存在时应返 None，实际返: {result}"

    def test_take_snapshot_skip_env(self, tmp_path, monkeypatch):
        """NOVEL_AI_SKIP_BACKUP=1 时一定要 skip（用于 CI / 快速测试）。"""
        from app import backup_db
        import sqlite3
        src = tmp_path / "test.db"
        sqlite3.connect(str(src)).close()
        self._patch_dirs(monkeypatch, tmp_path)
        monkeypatch.setenv(backup_db.ENV_SKIP, "1")
        result = backup_db.take_snapshot(src, label="skip")
        assert result is None, f"设 NOVEL_AI_SKIP_BACKUP=1 时应 skip，实际: {result}"
        # 也不应创建任何文件
        backup_root = tmp_path / "backups"
        if backup_root.exists():
            leftovers = list(backup_root.glob("skip-*.db*"))
            assert not leftovers, (
                f"skip 时不应创建任何快照文件，却留了: {leftovers}"
            )

    def test_rotate_keeps_n_newest(self, tmp_path):
        """12 个伪快照 + keep_n=10，应删 2 个最旧的。"""
        from app import backup_db
        import time
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        # 创建 12 个文件，通过 sleep 拉开 mtime（保留递增顺序）
        for i in range(12):
            f = backup_dir / f"test-{i:02d}-20240101-000000.db"
            f.write_text("x")
            time.sleep(0.02)
        deleted = backup_db._rotate(backup_dir, "test", keep_n=10)
        assert deleted == 2, f"应该删除 2 个最旧的，实际删了: {deleted}"
        remaining = sorted(p.name for p in backup_dir.glob("test-*.db"))
        assert len(remaining) == 10, f"应该剩 10 个，实际剩: {len(remaining)}"
        # 最旧的 2 个 (i=00, i=01) 应该被删，剩下的从 i=02 开始
        assert not (backup_dir / "test-00-20240101-000000.db").exists()
        assert not (backup_dir / "test-01-20240101-000000.db").exists()
        assert (backup_dir / "test-02-20240101-000000.db").exists()

    def test_take_all_snapshots_returns_dict(self, tmp_path, monkeypatch):
        """take_all_snapshots 返回 {label: path|None}，两个 key 都到位。"""
        from app import backup_db
        import sqlite3
        # 在 tmp_path/data 准备两个 db
        data = tmp_path / "data"
        data.mkdir()
        # 重定向（必须等 data 存在后再 patch，否则 _data_dir 也可能用错）
        self._patch_dirs(monkeypatch, data)
        for name in ("novel_assistant.db", "checkpoints.sqlite"):
            sqlite3.connect(str(data / name)).close()
        result = backup_db.take_all_snapshots()
        assert set(result.keys()) == {"novel_assistant", "checkpoints"}
        # 两个都应成功（空文件也算合法 DB）
        assert result["novel_assistant"] is not None
        assert result["checkpoints"] is not None
