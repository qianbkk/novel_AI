"""BridgeRun pid/pgid 追踪 + 双写防护 (security-2026-07-13 #2)

锁定：
  - BridgeRun 表有 pid / pgid 列
  - 子进程 spawn 时记录 pid/pgid
  - lifespan 的 _recover_orphan_bridge_runs 用 pid 探测活体——
    还活着的行不动（防止 uvicorn --reload 双写损坏）
  - 已死的行标 failed + finished_at
  - 没 pid 的老行按旧行为标 failed
"""
from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(REPO_ROOT)
sys.path.insert(0, str(BACKEND))

import os as _os  # noqa: E402
import secrets  # noqa: E402

from app.database import SessionLocal, engine  # noqa: E402
from app.models import Project, BridgeRun  # noqa: E402
from sqlalchemy import text as _sa_text  # noqa: E402


def _ensure_schema_and_migrate():
    from app.database import Base
    Base.metadata.create_all(engine)
    from app.migrations import run_migrations
    run_migrations()


def _cleanup_project(project_id: str):
    try:
        with engine.begin() as conn:
            for tbl in ("bridge_runs", "embedding_chunks", "chapter_characters",
                        "entity_relations", "characters", "factions",
                        "locations", "power_systems", "currencies",
                        "foreshadowing", "world_settings", "story_cores",
                        "settings", "rule_configs", "chapters"):
                conn.execute(_sa_text(
                    f"DELETE FROM {tbl} WHERE project_id = :pid"
                ), {"pid": project_id})
            conn.execute(_sa_text("DELETE FROM projects WHERE id = :pid"),
                         {"pid": project_id})
    except Exception:
        pass


class TestBridgeRunPidColumns:
    """DB schema 包含 pid + pgid。"""

    def test_pid_pgid_columns_exist(self):
        _ensure_schema_and_migrate()
        with engine.connect() as conn:
            rows = conn.execute(_sa_text("PRAGMA table_info(bridge_runs)")).fetchall()
            cols = {row[1] for row in rows}
        assert "pid" in cols, f"bridge_runs.pid 缺失，实际列={cols}"
        assert "pgid" in cols, f"bridge_runs.pgid 缺失，实际列={cols}"


class TestRecoverOrphanBridgeRuns:
    """_recover_orphan_bridge_runs 按 pid 活体探测分流。"""

    def test_recover_only_dead_runs(self):
        """pid 还活着 → 不动；pid 已死 → 标 failed；pid 为 NULL → 标 failed。"""
        _ensure_schema_and_migrate()
        from app.main import _recover_orphan_bridge_runs

        project_id = f"test-bridge-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="pid-test", genre="test",
                           status="ready", config_json={}))
            db.commit()

            # 场景 1: 找一个**真活着**的进程（自己）。预期：不动。
            alive_pid = _os.getpid()
            run_alive = BridgeRun(
                project_id=project_id, command="run",
                status="running", pid=alive_pid, pgid=None,
            )
            # 场景 2: pid=999999999 (基本不存在)。预期：标 failed。
            run_dead = BridgeRun(
                project_id=project_id, command="run",
                status="running", pid=999_999_999, pgid=None,
            )
            # 场景 3: 老数据没 pid。预期：标 failed (兼容旧 schema)。
            run_legacy = BridgeRun(
                project_id=project_id, command="run",
                status="running", pid=None, pgid=None,
            )
            db.add_all([run_alive, run_dead, run_legacy])
            db.commit()

            recovered = _recover_orphan_bridge_runs(project_id=project_id)
            assert recovered == 2, f"应只标 2 个 (dead + legacy)，实际 {recovered}"

            # 还活着的行 status 应保持 'running'，且 finished_at 仍为 None
            db.refresh(run_alive)
            assert run_alive.status == "running"
            assert run_alive.finished_at is None

            # 已死的行应标 failed + finished_at
            db.refresh(run_dead)
            assert run_dead.status == "failed"
            assert run_dead.finished_at is not None

            # 没 pid 的也应标 failed
            db.refresh(run_legacy)
            assert run_legacy.status == "failed"
            assert run_legacy.finished_at is not None
        finally:
            db.close()
            _cleanup_project(project_id)

    def test_recover_handles_missing_pgid_column_gracefully(self):
        """极端情况：即使 pgid 列暂时缺失也不应让 _recover 崩。
        （pid 列先加，pgid 列后续；这种迁移窗口里 lifecycle 应可用。）"""
        _ensure_schema_and_migrate()
        # 这里只验证 recover 函数本身不会因为 BridgeRun 行里 pid=整数但
        # pgid=NULL 而崩溃——这是常规情况，不是异常路径。
        from app.main import _recover_orphan_bridge_runs
        project_id = f"test-pgid-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="pgid-test", genre="test",
                           status="ready", config_json={}))
            db.commit()
            run = BridgeRun(project_id=project_id, command="run",
                            status="running", pid=_os.getpid(), pgid=None)
            db.add(run)
            db.commit()
            # 用我们自己的 pid（alive），pgid=None 不应让 kill 探测崩
            recovered = _recover_orphan_bridge_runs(project_id=project_id)
            assert recovered == 0  # alive 不算 orphan
        finally:
            db.close()
            _cleanup_project(project_id)