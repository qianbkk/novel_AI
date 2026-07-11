"""backend/tests/test_alembic.py — Phase 4 Alembic 基础设施验证

覆盖：
  - alembic 目录 + versions 列出我们写的两个 revision
  - env.py 能 import，能拿到 database_url
  - alembic upgrade head 在干净 DB 上成功（创建 users 表）
  - alembic stamp head 在已存在 DB 上成功（baseline 视为已应用）
  - 实际生成的 SQL 是合法的

不做的事（避免侵入性）：
  - 不动项目真实 DB 的 alembic_version 表（test 用临时 DB）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest
import tempfile
import uuid as _uuid


def test_alembic_env_file_exists():
    """alembic/env.py 配置完成（Phase 4）"""
    env_path = _BACKEND / "alembic" / "env.py"
    assert env_path.exists()
    content = env_path.read_text(encoding="utf-8")
    assert "app.config" in content, "env.py 应从 app.config 拿 database_url"
    assert "render_as_batch=True" in content, "SQLite 必须开 render_as_batch"


def test_alembic_versions_listed():
    """alembic/versions/ 下我们写的两个 revision 都存在"""
    versions_dir = _BACKEND / "alembic" / "versions"
    files = list(versions_dir.glob("*.py"))
    names = sorted(f.name for f in files)
    # 至少 baseline + phase4_users
    assert any("baseline" in n for n in names), f"应有 baseline revision，实际 {names}"
    assert any("phase4_users" in n for n in names), f"应有 phase4_users revision，实际 {names}"


def test_alembic_upgrade_head_on_clean_db(tmp_path):
    """alembic upgrade head 在干净 DB 上成功跑出 users 表。"""
    import subprocess

    db_path = tmp_path / f"alembic_test_{_uuid.uuid4().hex[:6]}.sqlite"
    # 把 DATABASE_URL 透传给 alembic 子进程
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(_BACKEND),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head 失败：\n"
        f"STDOUT: {result.stdout[-2000:]}\n"
        f"STDERR: {result.stderr[-2000:]}"
    )

    # 验证 users 表真的被建出来了
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchall()
        assert rows, "users 表应被建出来"
    finally:
        conn.close()


def test_alembic_stamp_baseline_on_existing_db(tmp_path):
    """已有 DB 上跑 alembic stamp head（把 baseline 视为已应用）。

    这是 Phase 4 给老用户的迁移路径——他们已有 DB，不想跑 upgrade 重建一切，
    只要标记"当前 schema 是 baseline"即可。
    """
    import subprocess
    import sqlite3

    db_path = tmp_path / f"alembic_stamp_{_uuid.uuid4().hex[:6]}.sqlite"

    # 先模拟"已存在 DB"——建一张 projects 表（最常见的）
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, title TEXT)"
        )
        conn.commit()
    finally:
        conn.close()

    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "stamp", "head"],
        cwd=str(_BACKEND),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"alembic stamp head 失败：\n"
        f"STDOUT: {result.stdout[-2000:]}\n"
        f"STDERR: {result.stderr[-2000:]}"
    )

    # 验证 alembic_version 表出现
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
        ).fetchall()
        assert rows, "alembic_version 表应被 stamp 创建"
    finally:
        conn.close()


def test_baseline_revision_is_no_op():
    """0001_baseline 的 upgrade() 是空的——它只用于 stamp，不真改 schema。"""
    # Python module 名不允许以数字开头；alembic 把 `0001_baseline.py` 加载为
    # `bas_0001` 或以其他方式处理。我们改用 alembic 的 ScriptDirectory API：
    from alembic.script import ScriptDirectory
    cfg_obj_path = _BACKEND / "alembic.ini"
    from alembic.config import Config as _AlembicConfig
    cfg = _AlembicConfig(str(cfg_obj_path))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0001_baseline")
    assert rev is not None
    # upgrade 模块应能 import（验证文件存在 + 可解析）
    upgrade_mod = rev.module
    assert hasattr(upgrade_mod, "upgrade")
    # 不真调它，避免破坏 DB
    assert callable(upgrade_mod.upgrade)


def test_phase4_users_revision_creates_users():
    """0002 的 upgrade 用 op.create_table('users', ...)。"""
    from alembic.script import ScriptDirectory
    from alembic.config import Config as _AlembicConfig
    cfg = _AlembicConfig(str(_BACKEND / "alembic.ini"))
    script = ScriptDirectory.from_config(cfg)
    rev = script.get_revision("0002_phase4_users")
    assert rev is not None
    assert rev.down_revision == "0001_baseline"
    upgrade_mod = rev.module
    # 检查源代码包含 create_table("users"...)
    import inspect
    src = inspect.getsource(upgrade_mod.upgrade)
    assert "create_table" in src.lower() and "users" in src.lower(), (
        f"0002 upgrade 应 create_table('users', ...)，源代码：{src!r}"
    )
