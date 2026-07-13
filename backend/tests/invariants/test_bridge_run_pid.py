"""BridgeRun pid 追踪 + 双写防护 (security-2026-07-13 #2)

锁定：
  - BridgeRun 表有 pid 列
  - 子进程 spawn 时记录 pid
  - lifespan 的 _recover_orphan_bridge_runs 用 pid 探测活体——
    还活着的行不动（防止 uvicorn --reload 双写损坏）
  - 已死的行标 failed + finished_at
  - 没 pid 的老行按旧行为标 failed
"""
import os

import pytest
from sqlalchemy import text as _sa_text

from app.database import SessionLocal, engine
from app.models import Project, BridgeRun


class TestBridgeRunPidColumns:
    """DB schema 包含 pid。"""

    def test_pid_column_exists(self, db_bootstrap):
        with engine.connect() as conn:
            rows = conn.execute(_sa_text("PRAGMA table_info(bridge_runs)")).fetchall()
            cols = {row[1] for row in rows}
        assert "pid" in cols, f"bridge_runs.pid 缺失，实际列={cols}"


class TestRecoverOrphanBridgeRuns:
    """_recover_orphan_bridge_runs 按 pid 活体探测分流。"""

    def test_recover_only_dead_runs(self, db_bootstrap, tracked_project_id):
        """pid 还活着 → 不动；pid 已死 → 标 failed；pid 为 NULL → 标 failed。"""
        from app.main import _recover_orphan_bridge_runs

        project_id = tracked_project_id
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="pid-test", genre="test",
                           status="ready", config_json={}))
            db.commit()

            # 场景 1: 找一个**真活着**的进程（自己）。预期：不动。
            alive_pid = os.getpid()
            run_alive = BridgeRun(
                project_id=project_id, command="run",
                status="running", pid=alive_pid,
            )
            # 场景 2: pid=999999999 (基本不存在)。预期：标 failed。
            run_dead = BridgeRun(
                project_id=project_id, command="run",
                status="running", pid=999_999_999,
            )
            # 场景 3: 老数据没 pid。预期：标 failed (兼容旧 schema)。
            run_legacy = BridgeRun(
                project_id=project_id, command="run",
                status="running", pid=None,
            )
            db.add_all([run_alive, run_dead, run_legacy])
            db.commit()

            # recover 处理整个 DB 的所有 running 行——只断言我们这 3 行的状态变化。
            _recover_orphan_bridge_runs()

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

    def test_alive_pid_not_marked_as_orphan(self, db_bootstrap, tracked_project_id):
        """活着的 pid（自己）— recover 不应把它标 failed。"""
        from app.main import _recover_orphan_bridge_runs
        project_id = tracked_project_id
        db = SessionLocal()
        try:
            db.add(Project(id=project_id, title="pid-alive-test", genre="test",
                           status="ready", config_json={}))
            db.commit()
            run = BridgeRun(project_id=project_id, command="run",
                            status="running", pid=os.getpid())
            db.add(run)
            db.commit()
            recovered = _recover_orphan_bridge_runs()
            assert recovered == 0  # alive 不算 orphan
        finally:
            db.close()