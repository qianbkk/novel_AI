"""Phase 1.5 收尾排雷 — pytest-discoverable smoke test.

覆盖：
  - 冷启动 + role_assignments 15 行
  - SSE 端到端（status 命令 + stream + log/done + exit_code）
  - 并发互斥（DB status='running' → 409）
  - checkpoints.sqlite 路径
  - _NodeWrapper happy + exception 路径
  - run_mvp importable

pytest-discoverable 版本：所有函数命名 test_*，由 `pytest tests/` 自动收集。

环境隔离：用临时 SQLite DB 与原版对齐。
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path
from queue import Queue

# 把 backend/ 加到 sys.path，方便 import app.*
_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# 用临时 SQLite DB 避免污染真实数据
_tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp_db.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp_db.name}"

import pytest
from fastapi.testclient import TestClient

from app.main import app  # noqa: E402
from app.database import Base, engine, SessionLocal  # noqa: E402
from app.models import RoleAssignment, BridgeRun, Project, WorldSetting, NovelAIBinding  # noqa: E402


# 一次性建表
Base.metadata.create_all(bind=engine)


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture
def project_id():
    """每个测试一个独立 project_id（避免互污染）。"""
    return f"smoke-{uuid.uuid4().hex[:8]}"


def _seed_project_and_binding(db, project_id: str) -> None:
    """建一个 project + NovelAIBinding + 一个空的 WorldSetting，
    让 POST /bridge/run 能通过 _worldbuild_done 校验。
    """
    p = Project(
        id=project_id,
        title="smoke-test",
        genre="玄幻",
        config_json={},
        status="ready",
    )
    db.merge(p)
    db.merge(WorldSetting(project_id=project_id))
    db.merge(NovelAIBinding(
        project_id=project_id,
        novel_ai_dir=str(Path(__file__).resolve().parents[2] / "novel_AI"),
        novel_id=project_id,
    ))
    db.commit()


def _run_bridge_command(client: TestClient, project_id: str, command: str, args: list | None = None) -> list[dict]:
    """跑一个非 LLM bridge 命令，捕获全部 SSE 事件。"""
    import json
    db = SessionLocal()
    try:
        _seed_project_and_binding(db, project_id)
    finally:
        db.close()
    r = client.post(
        f"/projects/{project_id}/bridge/run",
        json={"command": command, "args": args or []},
    )
    assert r.status_code == 200, f"POST /bridge/run 返回 {r.status_code}: {r.text}"
    run_id = r.json()["id"]
    events = []
    with client.stream("GET", f"/projects/{project_id}/bridge/stream?run_id={run_id}") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            if events and events[-1].get("event") == "done":
                break
    return events


# ───────── Tests ─────────

def test_cold_start(client):
    """冷启动 + role_assignments 15 行"""
    assert client.get("/health").json() == {"status": "ok"}
    db = SessionLocal()
    try:
        n = db.query(RoleAssignment).count()
    finally:
        db.close()
    assert n == 15, f"role_assignments 应恰好 15 行，实际 {n}"


def test_sse_end_to_end(client, project_id):
    """SSE 端到端 — status 命令拉 stream + log/done + done.exit_code=0"""
    db = SessionLocal()
    try:
        _seed_project_and_binding(db, project_id)
    finally:
        db.close()
    r = client.post(f"/projects/{project_id}/bridge/run", json={"command": "status", "args": []})
    assert r.status_code == 200, f"POST /bridge/run 返回 {r.status_code}: {r.text}"
    run_id = r.json()["id"]
    import json
    events = []
    with client.stream("GET", f"/projects/{project_id}/bridge/stream?run_id={run_id}") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            if events and events[-1].get("event") == "done":
                break
    types = {e.get("event") for e in events}
    assert "log" in types, f"SSE 流中缺 'log' 事件: {types}"
    assert "done" in types, f"SSE 流中缺 'done' 事件: {types}"
    done_evt = [e for e in events if e.get("event") == "done"][-1]
    assert "exit_code" in done_evt
    assert done_evt["exit_code"] == 0


def test_concurrency_mutex_db(client, project_id):
    """并发互斥：DB BridgeRun.status='running' 兜底（DB 层 409 fallback）"""
    db = SessionLocal()
    try:
        _seed_project_and_binding(db, project_id)
        db.add(BridgeRun(project_id=project_id, command="test", args_json=[], status="running"))
        db.commit()
    finally:
        db.close()
    r = client.post(f"/projects/{project_id}/bridge/run", json={"command": "status", "args": []})
    assert r.status_code == 409, f"有 running BridgeRun 时 POST 应 409，实际 {r.status_code}: {r.text}"
    # 清理
    db = SessionLocal()
    try:
        db.query(BridgeRun).filter_by(project_id=project_id).delete()
        db.commit()
    finally:
        db.close()


def test_checkpoints_path():
    """checkpoints.sqlite 落在 backend/data/，不在 cwd（避免运行路径漂移）"""
    expected = Path(__file__).resolve().parents[1] / "data" / "checkpoints.sqlite"
    assert expected.exists(), f"checkpoints.sqlite 应在 {expected}，实际不在"
    stray = Path("checkpoints.sqlite")
    assert not stray.exists(), f"cwd 下不应有 stray checkpoints.sqlite ({stray.resolve()})"


def test_node_wrapper_happy_path():
    """_NodeWrapper happy path：节点进出 emit node_start / node_end"""
    from engine.graph import _NodeWrapper
    q = Queue()
    entered = []

    class _MockNode:
        def __call__(self, state):
            entered.append(state.get("x"))
            return {"x": state.get("x", 0) + 1}

    wrapped = _NodeWrapper("test_node", _MockNode(), q)
    wrapped({"x": 42})

    e1 = q.get_nowait()
    e2 = q.get_nowait()
    assert e1 == {"event": "node_start", "node": "test_node"}
    assert e2 == {"event": "node_end", "node": "test_node"}
    assert entered == [42]
    assert q.empty()


def test_node_wrapper_exception_still_emits_end():
    """_NodeWrapper exception path：node 抛异常时 node_end 仍 emit (finally 块)"""
    from engine.graph import _NodeWrapper
    q = Queue()

    def _boom(state):
        raise RuntimeError("simulated node failure")

    wrapped = _NodeWrapper("bad", _boom, q)
    try:
        wrapped({})
    except RuntimeError:
        pass
    assert q.get_nowait() == {"event": "node_start", "node": "bad"}
    assert q.get_nowait() == {"event": "node_end", "node": "bad"}


def test_run_mvp_importable():
    """run_mvp.py 可导入 + 关键函数签名对"""
    import importlib
    mod = importlib.import_module("scripts.run_mvp")
    assert callable(getattr(mod, "main", None))
    assert callable(getattr(mod, "call_bridge_run", None))
    assert callable(getattr(mod, "select_bootstrap_version", None))
    assert callable(getattr(mod, "stream_sse", None))


def test_dashboard_command(client, project_id):
    """dashboard 命令走通 — novel_AI 实现自身的 bug 不归我们管，只断言事件流完整。
    TestClient 下 event loop 复用；shared client 保证不会触发
    'is bound to a different event loop' 错误。
    """
    events = _run_bridge_command(client, project_id, "dashboard")
    types = {e.get("event") for e in events}
    assert "start" in types and "log" in types and "done" in types, f"事件流不完整: {types}"


def test_budget_command(client, project_id):
    """budget 命令走通"""
    events = _run_bridge_command(client, project_id, "budget")
    types = {e.get("event") for e in events}
    assert "start" in types and "log" in types and "done" in types


def test_scan_command(client, project_id):
    """scan 命令走通 — 一致性扫描"""
    events = _run_bridge_command(client, project_id, "scan")
    types = {e.get("event") for e in events}
    assert "start" in types and "log" in types and "done" in types


@pytest.mark.skip(reason="需要 npm + frontend install；非默认 CI 必跑")
def test_frontend_build():
    """前端 build 通过 — 默认 skip（仅在本地手测 / release 前跑）。
    跑法：移除 skip marker，`pytest tests/test_phase1_5_smoke.py::test_frontend_build -v`
    """
    import shutil
    import subprocess
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    cmd = ["npm", "run", "build"]
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        for p in (
            r"D:\AI\Node.js\npm.cmd",
            r"C:\Program Files\nodejs\npm.cmd",
            r"C:\Program Files (x86)\nodejs\npm.cmd",
        ):
            if os.path.exists(p):
                npm = p
                break
    if not npm:
        pytest.skip("npm not found on PATH")
    cmd[0] = npm
    r = subprocess.run(cmd, cwd=str(frontend_dir), capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, f"npm run build 失败:\nSTDOUT: {r.stdout[-1000:]}\nSTDERR: {r.stderr[-1000:]}"
    assert (frontend_dir / "dist" / "index.html").exists()
