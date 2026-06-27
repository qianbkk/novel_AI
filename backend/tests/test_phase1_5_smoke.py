"""Phase 1.5 收尾排雷 — 5 项 smoke test.

跑法: cd backend && python -m tests.test_phase1_5_smoke

依赖: TestClient（同步），SQLite DB 落 backend/data/novel.db（项目默认），
SqliteSaver 落 backend/data/checkpoints.sqlite。无需真 API Key。
"""
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.database import SessionLocal
from app.models import RoleAssignment, BridgeRun, Project, NovelAIBinding, WorldSetting
from app.api.bridge import _get_project_lock


def _seed_project_and_binding(db, project_id: str) -> None:
    """建一个 project + NovelAIBinding + 一个空的 WorldSetting，
    让 POST /bridge/run 能通过 _worldbuild_done / _get_project_and_binding 校验。
    """
    p = Project(
        id=project_id,
        title="smoke-test",
        genre="玄幻",
        config_json={},
        status="ready",  # 绕过 worldbuild 校验
    )
    db.merge(p)
    db.merge(WorldSetting(project_id=project_id))
    db.merge(NovelAIBinding(
        project_id=project_id,
        novel_ai_dir=str(Path(__file__).resolve().parents[2] / "novel_AI"),
        novel_id=project_id,
    ))
    db.commit()


def smoke_1_cold_start() -> None:
    """1/5: 冷启动 + role_assignments 15 行"""
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    db = SessionLocal()
    try:
        n = db.query(RoleAssignment).count()
    finally:
        db.close()
    assert n == 15, f"role_assignments 应恰好 15 行，实际 {n}"
    print("[1/5] cold-start OK")


def smoke_2_sse_end_to_end(project_id: str) -> None:
    """2/5: SSE 端到端 — 触发 status 命令 + 拉 stream + 验证 log/done + done 带 exit_code

    注: 用 'status' 而非 'test' 命令 — system_test 自身有 3 个 pre-existing 失败
    (novel_AI/ 源码 bug，本 spec 不动 novel_AI/)，但 status 也能走完完整 SSE 流程。
    """
    db = SessionLocal()
    try:
        _seed_project_and_binding(db, project_id)
    finally:
        db.close()

    client = TestClient(app)
    r = client.post(
        f"/projects/{project_id}/bridge/run",
        json={"command": "status", "args": []},
    )
    assert r.status_code == 200, f"POST /bridge/run 返回 {r.status_code}: {r.text}"
    run = r.json()
    run_id = run["id"]

    import json
    events = []
    with client.stream("GET", f"/projects/{project_id}/bridge/stream?run_id={run_id}") as resp:
        for line in resp.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
            if events and events[-1].get("event") == "done":
                break

    types = {e.get("event") for e in events}
    assert "log" in types, f"SSE 流中缺 'log' 事件，实际: {types}"
    assert "done" in types, f"SSE 流中缺 'done' 事件，实际: {types}"
    done_evt = [e for e in events if e.get("event") == "done"][-1]
    assert "exit_code" in done_evt, f"done 事件应透传 exit_code，实际: {done_evt}"
    assert done_evt["exit_code"] == 0, f"status 命令应 exit 0，实际: {done_evt}"
    print(f"[2/5] SSE OK (events={len(events)}, types={sorted(types)}, exit_code={done_evt['exit_code']})")


def smoke_3_concurrency_mutex(project_id: str) -> None:
    """3/5: 并发互斥 — 两层防线
    1) asyncio.Lock 单测：同一 project_id 锁互斥
    2) SQL 兜底：直接插一行 BridgeRun(status='running')，POST 应返 409

    注: 不测端到端 "POST → 等 running → 再 POST" 因为 TestClient + BackgroundTasks
    在响应返回前任务就跑完了，BridgeRun 永远不出现 running 状态。uvicorn 模式下
    这个场景会真的发生（任务异步执行），但用 TestClient 模拟不出来。
    """
    # 1) asyncio.Lock 单测
    lock = _get_project_lock(project_id)
    assert lock.locked() is False, "新 lock 应未锁"
    async def hold_lock():
        async with lock:
            assert lock.locked() is True
    import asyncio
    asyncio.run(hold_lock())
    assert lock.locked() is False, "async with 退出后锁应释放"

    # 2) SQL 兜底：插一行 running 的 BridgeRun，POST 应返 409
    db = SessionLocal()
    try:
        _seed_project_and_binding(db, project_id)
        fake_run = BridgeRun(
            project_id=project_id,
            command="test",
            args_json=[],
            status="running",
        )
        db.add(fake_run)
        db.commit()
    finally:
        db.close()

    client = TestClient(app)
    r = client.post(
        f"/projects/{project_id}/bridge/run",
        json={"command": "status", "args": []},
    )
    assert r.status_code == 409, f"有 running BridgeRun 时 POST 应 409，实际 {r.status_code}: {r.text}"

    # 清理
    db = SessionLocal()
    try:
        db.query(BridgeRun).filter_by(project_id=project_id).delete()
        db.commit()
    finally:
        db.close()

    print("[3/5] concurrency OK (asyncio.Lock mutual exclusion + SQL 409 fallback)")


def smoke_4_checkpoints_path() -> None:
    """4/5: checkpoints.sqlite 落在 backend/data/，不在 cwd"""
    expected = Path(__file__).resolve().parents[1] / "data" / "checkpoints.sqlite"
    assert expected.exists(), f"checkpoints.sqlite 应在 {expected}，实际不在"
    stray = Path("checkpoints.sqlite")
    assert not stray.exists(), f"cwd 下不应有 stray checkpoints.sqlite ({stray.resolve()})"
    print(f"[4/5] checkpoints path OK ({expected})")


def smoke_5_frontend_build() -> None:
    """5/5: 前端 build 通过 — 走 subprocess 调 npm run build

    Windows 上 subprocess 找不到 npm 时（PATH 不含 npm 安装目录），用 which 找
    不到就 hardcode 几个常见路径，最后兜底用 shell=True 让 cmd.exe 解析。
    """
    import subprocess, shutil, os
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    cmd = ["npm", "run", "build"]
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if npm is None:
        # 常见 Windows 安装路径兜底
        for p in (
            r"D:\AI\Node.js\npm.cmd",
            r"C:\Program Files\nodejs\npm.cmd",
            r"C:\Program Files (x86)\nodejs\npm.cmd",
        ):
            if os.path.exists(p):
                npm = p
                break
    if npm:
        cmd[0] = npm
        r = subprocess.run(cmd, cwd=str(frontend_dir), capture_output=True, text=True, timeout=180)
    else:
        # 真的找不到，shell=True 让 cmd.exe 找
        r = subprocess.run("npm run build", cwd=str(frontend_dir), capture_output=True, text=True,
                           timeout=180, shell=True)
    assert r.returncode == 0, f"npm run build 失败 (exit {r.returncode}):\nSTDOUT:\n{r.stdout[-1000:]}\nSTDERR:\n{r.stderr[-1000:]}"
    dist = frontend_dir / "dist" / "index.html"
    assert dist.exists(), f"{dist} 应存在"
    print(f"[5/5] frontend build OK ({dist})")


if __name__ == "__main__":
    smoke_1_cold_start()

    pid_sse = "smoke-test-sse-1"
    smoke_2_sse_end_to_end(pid_sse)

    pid_mutex = "smoke-test-mutex-1"
    smoke_3_concurrency_mutex(pid_mutex)

    smoke_4_checkpoints_path()
    smoke_5_frontend_build()
    print("\nAll 5 smokes passed.")
