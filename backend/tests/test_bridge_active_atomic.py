"""test_bridge_active_atomic.py — 2026-07-25 新增（修 P0 短板 TOCTOU）

验证 BridgeRun per-project partial unique index：
1) 同一 project 已有 active pending 行，POST /bridge/run 应返 409
2) partial unique index 在 DB 层硬保证 single active row（直接 SQL 验证）
3) done/failed 状态允许多条（partial index WHERE 子句排除）
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import SessionLocal
from app.main import app
from app.models import BridgeRun, NovelAIBinding, Project


@pytest.fixture(scope="module")
def client():
    """lifespan 走完整 startup（包括 run_migrations 创建 partial index）。"""
    with TestClient(app) as c:
        yield c


@pytest.fixture
def pid(client):
    """函数级项目 + NovelAIBinding（与 client 同步，client 是 module-scope）。"""
    db = SessionLocal()
    try:
        from app.models import gen_id
        pid_ = gen_id()
        p = Project(
            id=pid_,
            title="__test_bridge_atomic__",
            genre="测试",
            audience="测试",
            config_json={},
        )
        db.add(p)
        db.flush()  # 让 Project.id 物化（让后续 binding FK 能找到）
        # binding 是 /bridge/run 的前置条件
        db.add(NovelAIBinding(
            id=gen_id(),
            project_id=pid_,
            novel_ai_dir="D:/tmp/__test_novel_ai__",
            novel_id=pid_,
        ))
        db.commit()
    finally:
        db.close()
    yield pid_
    # teardown
    db = SessionLocal()
    try:
        db.query(BridgeRun).filter_by(project_id=pid_).delete()
        db.query(NovelAIBinding).filter_by(project_id=pid_).delete()
        db.query(Project).filter_by(id=pid_).delete()
        db.commit()
    finally:
        db.close()


def test_409_when_active_pending_exists(client, pid):
    """场景 1：DB 已有一条 active pending 行，POST /bridge/run 应返 409。
    用只读命令 "dashboard"（不需 worldbuild 完成）。"""
    # 手动插一条 pending
    db = SessionLocal()
    try:
        from app.models import gen_id
        run = BridgeRun(
            id=gen_id(),
            project_id=pid,
            command="dashboard",
            status="pending",
        )
        db.add(run)
        db.commit()
    finally:
        db.close()

    # 再 POST /bridge/run —— 期望 409
    r = client.post(
        f"/projects/{pid}/bridge/run",
        json={"command": "dashboard", "args": []},
    )
    assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text}"
    assert "already active" in r.json()["detail"]


def test_unique_index_blocks_second_active_row():
    """场景 2：partial unique index 在 DB 层硬保证 single active row。
    直接 SQL 写第二条 pending 应该抛 IntegrityError。"""
    db = SessionLocal()
    try:
        from app.models import gen_id
        pid_ = gen_id()
        p = Project(
            id=pid_, title="__test_atomic__",
            genre="测试", audience="测试",
            config_json={},
        )
        db.add(p)
        db.commit()
        try:
            # 写第一条 pending
            db.add(BridgeRun(id=gen_id(), project_id=pid_, command="planner", status="pending"))
            db.commit()

            # 写第二条 pending（partial index 应拒绝）
            db.add(BridgeRun(id=gen_id(), project_id=pid_, command="planner", status="pending"))
            with pytest.raises(Exception) as excinfo:
                db.commit()
            assert "UNIQUE" in str(excinfo.value).upper(), \
                f"unexpected exception: {excinfo.value}"
            db.rollback()

            # 验证 partial index 真的存在
            idx = db.execute(
                text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"),
                {"n": "uq_bridge_runs_active_per_project"},
            ).fetchone()
            assert idx is not None, "partial unique index 未创建"

            # 把第一条标 failed，第二条 pending 应该能写进去
            first = db.query(BridgeRun).filter(
                BridgeRun.project_id == pid_, BridgeRun.status == "pending"
            ).first()
            first.status = "failed"
            db.add(BridgeRun(id=gen_id(), project_id=pid_, command="planner", status="pending"))
            db.commit()  # 应成功
        finally:
            db.query(BridgeRun).filter_by(project_id=pid_).delete()
            db.query(Project).filter_by(id=pid_).delete()
            db.commit()
    finally:
        db.close()


def test_done_and_failed_rows_unlimited():
    """场景 3：done/failed 状态允许多条（partial index WHERE 排除它们）。"""
    db = SessionLocal()
    try:
        from app.models import gen_id
        pid_ = gen_id()
        p = Project(
            id=pid_, title="__test_history__",
            genre="测试", audience="测试",
            config_json={},
        )
        db.add(p)
        db.commit()
        try:
            for st in ["done", "done", "done", "failed", "failed"]:
                db.add(BridgeRun(id=gen_id(), project_id=pid_, command="planner", status=st))
            db.commit()
            count = db.query(BridgeRun).filter_by(project_id=pid_).count()
            assert count == 5, f"done/failed 历史应允许多条，实际 {count}"
        finally:
            db.query(BridgeRun).filter_by(project_id=pid_).delete()
            db.query(Project).filter_by(id=pid_).delete()
            db.commit()
    finally:
        db.close()
