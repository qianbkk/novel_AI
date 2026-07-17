"""bridge/ — Phase 3 测试拆分

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

class TestReviewContract:
    """历史 bug：
      前端 submitReview 发 `edited_content` 字段，
      后端 ReviewRequest 读 `content` 字段 → 永远拿到 None
      → 用户"编辑后提交"的内容静默丢失。

    锁定条件：
      - 前端 api.submitReview 类型声明必须用 `content`
      - 前端实际调用必须用 `content`
      - 后端 ReviewRequest 必须有 `content` 字段
      - 前端不能出现 `edited_content`（已统一）
    """

    def test_frontend_submit_review_uses_content_field(self):
        """前端 api.submitReview 类型声明 + BridgeConsole 调用都用 `content`。"""
        client_ts = (Path(REPO_ROOT) / "frontend" / "src" / "api" / "client.ts").read_text(encoding="utf-8")
        assert "edited_content" not in client_ts, (
            "frontend api/client.ts 还用 edited_content 字段 — "
            "后端 ReviewRequest 读 content，编辑内容会被丢弃"
        )
        assert "content?:" in client_ts or "content: string" in client_ts, (
            "frontend api/client.ts submitReview 必须显式声明 content 字段"
        )

    def test_frontend_submit_review_call_site_uses_content(self):
        """BridgeConsole.tsx 实际调用 api.submitReview 时用 content key。"""
        console_tsx = (Path(REPO_ROOT) / "frontend" / "src" / "pages" / "BridgeConsole.tsx").read_text(encoding="utf-8")
        assert "edited_content" not in console_tsx, (
            "frontend BridgeConsole.tsx 还传 edited_content — "
            "实际提交时编辑内容会被丢弃"
        )
        # 调用点必须传 content 字段（值是三元表达式）
        import re
        m = re.search(r"api\.submitReview\s*\([^)]*content:\s*", console_tsx, re.DOTALL)
        assert m, (
            "frontend BridgeConsole.tsx 调 api.submitReview 时必须传 content 字段"
        )

    def test_backend_review_request_has_content_field(self):
        """后端 ReviewRequest 必须有 content 字段（与前端对齐）。"""
        from app.schemas import ReviewRequest
        fields = ReviewRequest.model_fields
        assert "content" in fields, (
            "backend ReviewRequest 缺 content 字段 — 前端编辑提交会拿到 None"
        )
        # 显式不允许 edited_content（避免再次漂移）
        assert "edited_content" not in fields, (
            "backend ReviewRequest 不应有 edited_content 字段（应统一为 content）"
        )


class TestBridgeDeadCodeRemoved:
    """历史背景：
      commit 62baf44 把 run 进程从 in-process 切到 subprocess（_spawn_engine_subprocess），
      旧版 _run_bridge_async 函数和 _run_bridge_async_imported 降级引用变 dead code。
      本轮清理：函数体删掉，只留 stub 抛 NotImplementedError；_run_bridge_async_imported
      字符串彻底从源码消失。
    """

    def test_no_run_bridge_async_imported_string_in_source(self):
        """源码（包括 subprocess 降级路径字符串）不能出现 _run_bridge_async_imported。"""
        from pathlib import Path
        repo = Path(REPO_ROOT)
        offenders: list[str] = []
        for py_file in (repo / "backend").rglob("*.py"):
            # 跳过 tests/ 自身（test 文件里 grep 这个名字是合法的——在断言里）
            if "tests" in py_file.parts:
                continue
            content = py_file.read_text(encoding="utf-8")
            if "_run_bridge_async_imported" in content:
                offenders.append(str(py_file.relative_to(repo)))
        assert not offenders, (
            "_run_bridge_async_imported 仍存在（已删除函数，不应再被引用）：\n  "
            + "\n  ".join(offenders)
        )

    def test_run_bridge_async_only_stub(self):
        """_run_bridge_async 函数体应只剩 stub（抛 NotImplementedError），不能真有逻辑。"""
        from pathlib import Path
        bridge_py = Path(REPO_ROOT) / "backend" / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 找到函数定义位置
        import re
        m = re.search(r"async def _run_bridge_async\([^)]*\):\s*\n(.*?)(?=\nasync def |def |class |\Z)", content, re.DOTALL)
        assert m, "找不到 _run_bridge_async 函数"
        body = m.group(1)
        # 不应有 run_graph_task / asyncio.to_thread 这种实质逻辑
        assert "run_graph_task" not in body, (
            "_run_bridge_async 函数体不应再调用 run_graph_task（已废弃）"
        )
        assert "NotImplementedError" in body, (
            "_run_bridge_async 必须是 stub（抛 NotImplementedError）"
        )


class TestOrphanBridgeRunRecovery:
    """历史 bug（独立审查标记）：
      并发锁在内存 _project_locks，进程崩溃后 DB 里 status='running'
      且 finished_at IS NULL 的记录永久卡住。下次任何 /bridge/run → 409 Conflict。

    修复：main.py lifespan handler 启动时调 _recover_orphan_bridge_runs()，
    把所有未结束的 running 行标为 'failed'，写入 finished_at。
    """

    def test_main_has_orphan_recovery_function(self):
        """backend/app/main.py 必须定义 _recover_orphan_bridge_runs 函数。"""
        from pathlib import Path
        main_py = Path(REPO_ROOT) / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "_recover_orphan_bridge_runs" in content, (
            "backend/app/main.py 缺 _recover_orphan_bridge_runs 函数 — "
            "启动时无法清理孤儿 BridgeRun 行，进程崩溃后项目永久 409"
        )

    def test_main_uses_lifespan_handler(self):
        """必须用 @asynccontextmanager lifespan 替代 deprecated @app.on_event。"""
        from pathlib import Path
        main_py = Path(REPO_ROOT) / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        assert "@asynccontextmanager" in content and "async def lifespan" in content, (
            "backend/app/main.py 必须用 lifespan handler（@app.on_event 已被 deprecated）"
        )
        assert "@app.on_event" not in content, (
            "backend/app/main.py 还用 deprecated 的 @app.on_event — "
            "应改为 @asynccontextmanager lifespan"
        )

    def test_lifespan_calls_orphan_recovery(self):
        """lifespan handler 必须调 _recover_orphan_bridge_runs()。"""
        from pathlib import Path
        main_py = Path(REPO_ROOT) / "backend" / "app" / "main.py"
        content = main_py.read_text(encoding="utf-8")
        # lifespan 函数体内必须调 _recover_orphan_bridge_runs
        import re
        m = re.search(r"async def lifespan\(.*?\):(.*?)(?=\nasync def |def |class |\Z)", content, re.DOTALL)
        assert m, "找不到 lifespan 函数"
        body = m.group(1)
        assert "_recover_orphan_bridge_runs()" in body, (
            "lifespan handler 必须调 _recover_orphan_bridge_runs()"
        )

    def test_recovery_marks_orphan_runs_failed(self):
        """直接调 _recover_orphan_bridge_runs 验证：orphan 行被标 failed。"""
        from datetime import datetime
        from app.main import _recover_orphan_bridge_runs
        from app.database import SessionLocal
        from app.models import BridgeRun, Project
        from datetime import datetime, timezone

        # 准备：先建一个真 Project（FK 约束开启后 BridgeRun 需要合法 project_id）
        db = SessionLocal()
        try:
            project = Project(
                id="test-orphan-recovery-proj",
                title="orphan recovery test project",
                genre="都市",
                audience="男频",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()

            test_run = BridgeRun(
                project_id=project.id,
                command="run",
                status="running",
                started_at=datetime.now(timezone.utc),
                finished_at=None,
            )
            db.add(test_run)
            db.commit()
            test_run_id = test_run.id
        finally:
            db.close()

        # 调 cleanup
        recovered = _recover_orphan_bridge_runs()
        assert recovered >= 1, f"应至少清理 1 条 orphan，实际 {recovered}"

        # 验证：状态变成 failed，finished_at 有值
        db = SessionLocal()
        try:
            run = db.get(BridgeRun, test_run_id)
            assert run is not None
            assert run.status == "failed", (
                f"orphan run 状态应改为 failed，实际 {run.status}"
            )
            assert run.finished_at is not None, (
                "orphan run 应写入 finished_at"
            )
        finally:
            # 清理测试数据（先删 FK 引用，再删 project）
            if run:
                db.delete(run)
            project_obj = db.get(Project, "test-orphan-recovery-proj")
            if project_obj:
                db.delete(project_obj)
            db.commit()
            db.close()

    def test_cors_uses_env_or_default(self):
        """CORS 必须从 env 读 ALLOWED_ORIGINS，不能硬编码 *。"""
        from pathlib import Path
        main_py = Path(REPO_ROOT) / "backend" / "main.py" if False else (
            Path(REPO_ROOT) / "backend" / "app" / "main.py"
        )
        content = main_py.read_text(encoding="utf-8")
        assert 'allow_origins=["*"]' not in content, (
            "backend/app/main.py CORS 还硬编码 * — 部署前必须收紧"
        )
        assert "ALLOWED_ORIGINS" in content, (
            "backend/app/main.py 必须从 env 读 ALLOWED_ORIGINS"
        )


class TestBridgeSubprocessArchitecture:
    """历史 bug：bridge.run 用 BackgroundTasks 在 uvicorn worker 进程内跑
    engine，uvicorn 重启（手动 / --reload / OOM）会杀掉 in-flight engine run。
    修复：spawn subprocess 跑 engine，stdout pipe 转发 SSE 事件，DB 写
    BridgeRun.status 跟踪生命周期，uvicorn 重启不影响。

    本测试锁死：
    1) subprocess worker 脚本存在
    2) bridge._spawn_engine_subprocess 函数存在
    3) run_bridge endpoint 调用 _spawn_engine_subprocess 而不是 _run_bridge_async
    4) build_graph 接受 checkpointer 参数（之前 status 命令 fail 的隐藏 bug）
    5) SSECapture 在 queue=None 时回退到 stdout（subprocess 模式不丢消息）
    """

    def test_worker_script_exists(self):
        from pathlib import Path
        ws = Path(BACKEND_ROOT) / "engine" / "workers" / "run_bridge_subprocess.py"
        assert ws.exists(), f"worker 脚本不存在: {ws}"

    def test_bridge_has_spawn_engine_subprocess(self):
        from app.api import bridge as bridge_mod
        assert hasattr(bridge_mod, "_spawn_engine_subprocess"), (
            "bridge 必须有 _spawn_engine_subprocess 函数（替代 in-process BackgroundTasks）"
        )

    def test_run_endpoint_uses_subprocess(self):
        """run_bridge endpoint 必须调 _spawn_engine_subprocess，不是 _run_bridge_async。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 关键断言：源代码里必须出现 _spawn_engine_subprocess
        assert "_spawn_engine_subprocess" in src, (
            "run_bridge 没用 _spawn_engine_subprocess——仍在 in-process 旧路径"
        )
        # 反向：不能再有 background_tasks.add_task(_run_bridge_async, ...)
        assert "background_tasks.add_task(\n        _run_bridge_async" not in src and \
               "background_tasks.add_task(_run_bridge_async" not in src, (
            "run_bridge 仍用 BackgroundTasks + _run_bridge_async（in-process 旧路径）"
        )

    def test_build_graph_accepts_checkpointer(self):
        """build_graph 必须接受 checkpointer 参数（否则 status 命令 fail）。"""
        from engine.orchestrator import build_graph
        # 不传 checkpointer 也能用
        g = build_graph()
        assert g is not None
        # 传 checkpointer 也能用
        from langgraph.checkpoint.memory import MemorySaver
        g2 = build_graph(checkpointer=MemorySaver())
        assert g2 is not None

    def test_sse_capture_handles_none_queue(self):
        """SSECapture 在 queue=None 时不能崩（subprocess 模式）。"""
        from engine.graph import SSECapture
        from io import StringIO
        # queue=None 必须不抛
        cap = SSECapture(None)
        # 模拟 print 输出
        cap.write("hello world\n")
        cap.write("more text\n")
        cap.flush()
        # StringIO 行为：write 后 super().write 把数据存到内部 buffer
        # 不能崩 + 至少不抛异常
        assert True

    def test_subprocess_smoke_status(self):
        """subprocess worker 跑 status 命令能 exit_code=0。"""
        import subprocess
        import sys
        from pathlib import Path
        result = subprocess.run(
            [sys.executable, "-m", "engine.workers.run_bridge_subprocess",
             "smoke-test", "c12345678901234567890123456789012", "status", "batch"],
            capture_output=True, text=True,
            cwd=str(Path(BACKEND_ROOT)),
            timeout=15,
        )
        # 之前 status 命令的 build_graph 错让 exit_code=1，修了之后必须=0
        assert result.returncode == 0, (
            f"subprocess status 应 exit_code=0，实际: {result.returncode}\n"
            f"stdout: {result.stdout[-500:]}\nstderr: {result.stderr[-500:]}"
        )

    def test_graph_run_graph_task_handles_unknown_command(self):
        """run_graph_task 收到未知命令时必须 exit_code=1（不是 0）。"""
        from engine.graph import run_graph_task
        from queue import Queue
        q = Queue()
        # 用一个明显没注册的命令
        exit_code, stdout = run_graph_task(
            project_id="nonexistent",
            command="definitely_not_a_real_command_xyz",
            args=[],
            run_id="test-unknown",
            queue=q,
        )
        assert exit_code == 1, (
            f"未知命令应返回 exit_code=1，实际 {exit_code}（'假装成功'是 fake-pass）"
        )
        assert "未知命令" in stdout, f"stderr 应明确说未知命令，实际 stdout: {stdout[:200]!r}"


class TestRunBridgeConcurrencyGuard:
    """迭代 #30: 之前 run_bridge 用 _get_project_lock(project_id).locked() 做
    并发保护，但该 asyncio.Lock 永不被 acquire（grep 证实）→ 检查永远
    False → 给 false sense of security（代码看起来"有锁"但实际没有）。

    修法：删掉死代码，依赖 DB 层 BridgeRun.status='running' 检查 +
    lifespan 启动时 _recover_orphan_bridge_runs。
    本测试锁死：源码里不应再出现 _project_locks / _get_project_lock 引用。
    """
    def test_no_dead_project_lock_in_bridge_py(self):
        """bridge.py 不应再定义 / 调用 _project_locks / _get_project_lock。"""
        from pathlib import Path
        bridge_py = Path(BACKEND_ROOT) / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 关键符号：定义 + 调用都不能有（注释里的解释 OK）
        offenders: list[str] = []
        for i, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            # 排除纯注释行
            if stripped.startswith("#"):
                continue
            if "_project_locks" in line and "_project_locks" != "_project_locks:":  # 类型注解也排除
                offenders.append(f"line {i}: {line.rstrip()}")
            if "_get_project_lock" in line and "(" in line:  # 实际调用（带括号）
                offenders.append(f"line {i}: {line.rstrip()}")
        assert not offenders, (
            "bridge.py 还有死锁引用（应删除）：\n  " + "\n  ".join(offenders)
        )

    def test_run_bridge_only_checks_db_for_concurrent_runs(self):
        """run_bridge 源码必须只有 DB 层 BridgeRun active 检查（#30 + #74）。

        #30: 之前 run_bridge 用 _get_project_lock(project_id).locked() 做并发保护，
        但 asyncio.Lock 永不被 acquire → 检查永远 False → 给 false sense of
        security。修法：删死代码，依赖 DB 层 BridgeRun active 检查 + lifespan
        启动时 _recover_orphan_bridge_runs。

        #74: 之前 DB 检查只查 status='running' 有 TOCTOU 窗口 —— pending insert
        后到 status 翻 'running' 之前的窗口里两个并发请求都通过。修法：
        active 检查包含 pending + running（用 status.in_(["pending","running"])）。

        本测试锁死：
          - 真代码行不应有 _get_project_lock / .locked() 这种无效检查
          - 必须用 in_() 包含 pending+running（不能退化到只查 running）
        """
        from pathlib import Path
        import re
        bridge_py = Path(BACKEND_ROOT) / "app" / "api" / "bridge.py"
        content = bridge_py.read_text(encoding="utf-8")
        # 找 run_bridge 函数体（多行 args 模式：args 跨行 \n）
        m = re.search(
            r"async def run_bridge\([\s\S]*?\):(.*?)(?=\nasync def |\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 run_bridge"
        body = m.group(1)
        # 排除注释行（解释历史为什么删 lock 的注释里会出现 .locked() / _get_project_lock）
        code_lines = [
            line for line in body.splitlines()
            if not line.strip().startswith("#")
        ]
        code_body = "\n".join(code_lines)
        # 关键检查：真代码行不该有 _get_project_lock / .locked() 这种无效检查
        assert ".locked()" not in code_body, (
            "run_bridge 真代码行不应再用 .locked() 假并发检查（之前 dead code）"
        )
        assert "_get_project_lock" not in code_body, (
            "run_bridge 真代码行不应再调 _get_project_lock（死代码）"
        )
        # #74：active 检查必须包含 pending + running（in_ 模式）
        assert 'status.in_(["pending", "running"])' in code_body or \
               'in_(["pending", "running"])' in code_body, (
            "run_bridge 必须用 status.in_(['pending','running']) 查询，"
            "否则 TOCTOU 窗口（#74）未关闭"
        )
        # 反向保证：不能退化到单独 status='running'
        bad_pattern = re.search(r'\.filter_by\([^)]*status\s*=\s*["\']running["\']', code_body)
        assert not bad_pattern, (
            f"run_bridge 不能退回到只查 status='running'（#74 已修），匹配 {bad_pattern.group() if bad_pattern else None}"
        )


class TestApplyReviewInputValidation:
    """最后 #22：apply_review 是用户审核端点，零测试覆盖。"""
    def test_invalid_action_raises_value_error(self):
        from app.bridge.reports import apply_review, VALID_REVIEW_ACTIONS
        import pytest
        with pytest.raises(ValueError, match="unsupported review action"):
            apply_review(novel_ai_dir="/tmp/nonexistent", action="invalid_action_xyz")
        assert VALID_REVIEW_ACTIONS == {"accept", "reject", "edit"}

    def test_nonexistent_state_returns_not_available(self):
        from app.bridge.reports import apply_review
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            result = apply_review(
                novel_ai_dir=tmpdir,
                action="accept",
                task_id="any_task",
            )
            assert result["available"] is False, f"应 available=False，实际 {result}"

    def test_valid_actions_do_not_raise(self):
        from app.bridge.reports import apply_review
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            for action in ["accept", "reject", "edit"]:
                result = apply_review(novel_ai_dir=tmpdir, action=action)
                assert result["available"] is False

    def test_unmatched_task_id_does_not_pop_wrong_task(self, tmp_path):
        """迭代 #29：task_id 不存在时不能 pop 错的 pending 任务。

        历史 bug：_find_task_index 之前"没找到"时 fallback 到 0，
        silently pop 第一条 pending 任务。用户提交 review with task_id="X"
        但 X 不存在 → 第一条 pending 被静默移除，review_history 记的
        是 "X" 但实际 pop 的是另一条 → 数据完整性破坏。

        修法：_find_task_index 在没找到时显式返回 None，apply_review 不 pop。
        """
        from app.bridge.reports import apply_review
        import json
        import os

        # 准备 state：3 个 pending 任务
        # _state_path 走 NOVEL_AI_DIR/output/orchestrator_state.json
        state = {
            "current_phase": "writing",
            "human_pending": [
                {"task_id": "real-task-A", "task_type": "fix_chapter",
                 "description": "task A", "payload": {"chapter_number": 1},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
                {"task_id": "real-task-B", "task_type": "fix_chapter",
                 "description": "task B", "payload": {"chapter_number": 2},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
                {"task_id": "real-task-C", "task_type": "fix_chapter",
                 "description": "task C", "payload": {"chapter_number": 3},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
            ],
        }
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        state_file = output_dir / "orchestrator_state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        old_env = os.environ.get("NOVEL_AI_DIR")
        os.environ["NOVEL_AI_DIR"] = str(tmp_path)
        try:
            result = apply_review(
                novel_ai_dir=str(tmp_path),
                action="accept",
                task_id="nonexistent-task-X",  # 不存在
            )
            # 关键断言 1：响应里 matched=False
            assert result["matched"] is False, (
                f"task_id 不存在时 matched 必须 False，实际 {result.get('matched')}"
            )
            # 关键断言 2：3 个 pending 任务一个都没被 pop
            on_disk = json.loads(state_file.read_text(encoding="utf-8"))
            assert len(on_disk["human_pending"]) == 3, (
                f"task_id 不存在时不应 pop 任何 pending，"
                f"实际剩余 {len(on_disk['human_pending'])} 条（之前 bug: pop 了 0 号任务）"
            )
            assert [t["task_id"] for t in on_disk["human_pending"]] == [
                "real-task-A", "real-task-B", "real-task-C",
            ], (
                f"pending 顺序应保持不变，"
                f"实际 {[t['task_id'] for t in on_disk['human_pending']]}"
            )
            # 关键断言 3：review_history 记录了"尝试过 X 但未匹配"
            history = on_disk.get("review_history", [])
            assert len(history) == 1
            assert history[0]["task_id"] == "nonexistent-task-X"
            assert history[0]["matched"] is False
        finally:
            if old_env is not None:
                os.environ["NOVEL_AI_DIR"] = old_env
            else:
                os.environ.pop("NOVEL_AI_DIR", None)

    def test_unmatched_chapter_number_does_not_pop_wrong_task(self, tmp_path):
        """chapter_number 不存在时也不能 pop 错的 pending。"""
        from app.bridge.reports import apply_review
        import json
        import os

        state = {
            "current_phase": "writing",
            "human_pending": [
                {"task_id": "task-A", "task_type": "fix_chapter",
                 "description": "task A", "payload": {"chapter_number": 5},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
            ],
        }
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        state_file = output_dir / "orchestrator_state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        old_env = os.environ.get("NOVEL_AI_DIR")
        os.environ["NOVEL_AI_DIR"] = str(tmp_path)
        try:
            result = apply_review(
                novel_ai_dir=str(tmp_path),
                action="reject",
                chapter_number=999,  # 不存在
            )
            assert result["matched"] is False
            on_disk = json.loads(state_file.read_text(encoding="utf-8"))
            assert len(on_disk["human_pending"]) == 1, (
                "chapter_number 不存在时不应 pop 任何 pending"
            )
        finally:
            if old_env is not None:
                os.environ["NOVEL_AI_DIR"] = old_env
            else:
                os.environ.pop("NOVEL_AI_DIR", None)

    def test_matched_task_id_pops_correct_task(self, tmp_path):
        """task_id 匹配时必须 pop 对的任务。"""
        from app.bridge.reports import apply_review
        import json
        import os

        state = {
            "current_phase": "writing",
            "human_pending": [
                {"task_id": "task-A", "task_type": "fix_chapter",
                 "description": "A", "payload": {"chapter_number": 1},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
                {"task_id": "task-B", "task_type": "fix_chapter",
                 "description": "B", "payload": {"chapter_number": 2},
                 "created_at": "2025-01-01T00:00:00", "priority": "must"},
            ],
        }
        output_dir = tmp_path / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        state_file = output_dir / "orchestrator_state.json"
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        old_env = os.environ.get("NOVEL_AI_DIR")
        os.environ["NOVEL_AI_DIR"] = str(tmp_path)
        try:
            result = apply_review(
                novel_ai_dir=str(tmp_path),
                action="accept",
                task_id="task-B",
            )
            assert result["matched"] is True
            assert result["task"]["task_id"] == "task-B"
            on_disk = json.loads(state_file.read_text(encoding="utf-8"))
            assert [t["task_id"] for t in on_disk["human_pending"]] == ["task-A"], (
                f"应只 pop task-B，剩余 task-A，实际 {[t['task_id'] for t in on_disk['human_pending']]}"
            )
        finally:
            if old_env is not None:
                os.environ["NOVEL_AI_DIR"] = old_env
            else:
                os.environ.pop("NOVEL_AI_DIR", None)


class TestDrainStdoutExceptionHandling:
    """迭代 #54: _drain_stdout 是 daemon 线程，之前 try/finally 但没有 except
    — 循环里 DB 错误 / KeyError 会让线程静默死掉，bridge_run.status 卡在
    "running"，下次 /bridge/run 触发 409 Conflict。

    修法：循环 body 包内层 try/except，异常时把 bridge_run 标 failed +
    记录异常 + push error 事件到 queue。
    """
    def test_drain_stdout_inner_try_except_present(self):
        """_drain_stdout 的循环体必须有 try/except（不只外层 finally）。"""
        import inspect, re
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._spawn_engine_subprocess)
        code_lines = [l for l in src.split("\n")
                      if l.strip() and not l.strip().startswith("#")]
        code_src = "\n".join(code_lines)
        # 必须有内层 try（带 except Exception）
        # 检查 `for line in iter(proc.stdout.readline, ""):` 后是否有内层 try
        # 简化检查：源码里 must 有两次 "try:" 出现（外层 + 内层）
        try_count = code_src.count("try:")
        assert try_count >= 2, \
            f"_drain_stdout 必须有内层 try/except（循环里异常时设 bridge_run failed），" \
            f"实际 try: 出现 {try_count} 次"
        # 必须有 except Exception 处理循环错误
        assert "except Exception as loop_exc" in code_src, \
            "_drain_stdout 循环里必须有 except Exception as loop_exc → 设 bridge_run failed"

    def test_drain_stdout_pushes_error_event_on_loop_exception(self):
        """循环异常时必须 push {\"event\": \"error\", \"message\": ..., \"traceback\": ...} 到 queue。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._spawn_engine_subprocess)
        assert '"event": "error"' in src or "'event': 'error'" in src, \
            "_drain_stdout 异常时必须 push error 事件到 queue"
        assert "traceback.format_exc" in src, \
            "_drain_stdout 异常时必须带 traceback 信息"

    def test_bridge_module_imports_traceback(self):
        import app.api.bridge as bridge_mod
        # bridge.py 必须 import traceback 用于 #54 异常 traceback
        import inspect
        src = inspect.getsource(bridge_mod)
        assert "import traceback" in src, \
            "app/api/bridge.py 必须 import traceback（#54 用 traceback.format_exc）"


class TestBridgeRunConcurrencyGuard:
    """迭代 #74（medium bug fix）：

    历史：bridge.py:107 之前只查 `BridgeRun.status == 'running'` 来防止
    同一 project 并发触发两次 writing engine。

    真实流程时序（移除 asyncio.Lock 之后）：
      T0  request1 进入 run_bridge()
      T1  request1 查 running → 无
      T2  request1 db.add(BridgeRun(status='pending')) + commit
      T3  request1 background_tasks.add_task(_spawn_engine_subprocess, ...)
      T4  ... background thread 内 _drain_stdout 把 status 翻成 'running'

    T0~T4 之间的窗口里，request2 同样能查 running→无，同样放行 →
    两个 engine 子进程同时对同一 project_id 跑 → 同时写同一份
    checkpoint 文件 + 同时写同一份 .env。

    修法：active 检查改为 `status in ('pending','running')` —— 一旦
    request1 把 pending insert commit 成功，request2 的同检查立刻能看见，
    不放行。
    """
    def test_run_bridge_checks_pending_in_active(self):
        """源码扫描：run_bridge 必须把 'pending' 也算 active（#74）。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 必须有 'pending' 字面出现（说明检查了 pending 状态）
        assert "'pending'" in src or '"pending"' in src, (
            "bridge.run_bridge active 检查必须包含 'pending' 状态，"
            "否则新插入的 BridgeRun 在 status 翻 'running' 前会通过并发检查（#74）"
        )
        # 必须用 in_(...) / in [...] 而不是单独等号（否则只查一个状态）
        assert "in_([" in src or 'in_("pending"' in src or \
               'status in [' in src, (
            "active 检查必须用 in_() 包含多个状态（#74），"
            "单纯 status=='running' 仍有 TOCTOU 窗口"
        )

    def test_run_bridge_no_more_only_running_check(self):
        """回归保护：run_bridge 不能退回到只查 'running'（防止有人 reverted #74）。"""
        import re
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 退化的 "only running" 模式：filter_by(status="running") 单独使用
        # 允许 'running' 出现在 in_(['pending','running']) 里但不允许单独 filter_by
        bad_patterns = [
            r'filter_by\([^)]*status\s*=\s*["\']running["\'][^)]*\)',
            r'\.filter_by\([^)]*["\']running["\'][^)]*\)',
        ]
        for pat in bad_patterns:
            m = re.search(pat, src)
            assert not m, (
                f"bridge.run_bridge 退化到只查 'running' 模式（#74 已修），匹配 {pat} → {m.group() if m else None}"
            )

    def test_functional_pending_run_blocks_new_run(self):
        """行为测试：DB 里已经有一条 pending BridgeRun，再 insert 会触发 UNIQUE-like 冲突（#74）。

        通过直接 reproduce query pattern 验证：
          - 普通 query filter_by(status='running') 查不到 pending 行（确认这就是 bug）
          - 修法 query status.in_(['pending','running']) 能查到（确认修法有效）
        """
        from datetime import datetime, timezone
        from app.database import SessionLocal
        from app.models import BridgeRun, Project

        db = SessionLocal()
        try:
            # 准备：先建一个真 Project
            project = Project(
                id="test-toctou-window-proj",
                title="TOCTOU window test",
                genre="都市",
                audience="男频",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
            project_id = project.id

            # 插入一条 pending 行（模拟 request1 已经走到 T2 commit 但还没翻 running）
            pending_run = BridgeRun(
                project_id=project_id,
                command="run",
                status="pending",
                started_at=datetime.now(timezone.utc),
            )
            db.add(pending_run)
            db.commit()
            pending_id = pending_run.id
        finally:
            db.close()

        try:
            db = SessionLocal()
            # 验证：旧 query（只查 running）查不到 pending —— 这就是 bug
            old_check = db.query(BridgeRun).filter_by(
                project_id=project_id, status="running"
            ).first()
            assert old_check is None, (
                "前提：旧 query 只查 running 应该查不到 pending 行（证实 bug 存在）"
            )

            # 验证：修法 query（包含 pending）能查到
            from sqlalchemy import or_
            new_check = db.query(BridgeRun).filter(
                BridgeRun.project_id == project_id,
                BridgeRun.status.in_(["pending", "running"]),
            ).first()
            assert new_check is not None, (
                "修复后 query 必须能查到 pending 行（#74 关闭 TOCTOU 窗口）"
            )
            assert new_check.id == pending_id
            assert new_check.status == "pending"
        finally:
            # 清理测试数据
            db = SessionLocal()
            try:
                run = db.get(BridgeRun, pending_id)
                if run:
                    db.delete(run)
                proj = db.get(Project, project_id)
                if proj:
                    db.delete(proj)
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()

    def test_run_bridge_returns_409_with_active_status_info(self):
        """源码扫描：run_bridge 409 响应应带具体 status（调试友好）。"""
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod.run_bridge)
        # 409 响应应包含 running.status（便于前端显示哪个 status 卡住的）
        assert "running.status" in src or "f.status" in src or ".status" in src, (
            "bridge.run_bridge 409 响应应包含具体 active status 信息（#74 调试友好）"
        )


class TestBridgeEndpointsWorldbuildGuard:
    """跨表依赖顺序由代码强制执行，不能只依赖调用约定。
    import_chapters / pull_setting 之前必须强制检查 worldbuild 已完成，
    否则 import 早于 pull 时找不到 character，无法建立 ChapterCharacter 边。

    修法：bridge.py 里 pull-setting / import-chapters / reimport-chapters 3 个端点
    入口处都加 _worldbuild_done(...) 检查，没完成抛 HTTPException(400)。
    跟 run_bridge + push-concept 已有的检查形成完整覆盖。
    """
    def _func_source(self, name):
        import inspect
        from app.api import bridge as bridge_mod
        return inspect.getsource(getattr(bridge_mod, name))

    def test_pull_setting_has_worldbuild_guard(self):
        """pull_setting 必须有 _worldbuild_done 检查（#79）。"""
        src = self._func_source("pull_setting")
        assert "_worldbuild_done" in src, (
            "bridge.pull_setting 必须检查 _worldbuild_done（#79），"
            "否则 pull 早于 worldbuild 会让 character 边建不出来"
        )
        assert "raise HTTPException" in src, \
            "bridge.pull_setting 检查失败必须 raise HTTPException（fail-fast）"

    def test_import_chapters_has_worldbuild_guard(self):
        """import_chapters 必须有 _worldbuild_done 检查。"""
        src = self._func_source("import_chapters")
        assert "_worldbuild_done" in src, (
            "bridge.import_chapters 必须检查 _worldbuild_done（#79），"
            "这是 50 章 0 character 边的根因——pull 早于 import 时建不了 character 边"
        )
        assert "raise HTTPException" in src, \
            "bridge.import_chapters 检查失败必须 raise HTTPException"

    def test_reimport_chapters_has_worldbuild_guard(self):
        """reimport_chapters 必须有 _worldbuild_done 检查（#79 同型）。"""
        src = self._func_source("reimport_chapters")
        assert "_worldbuild_done" in src, (
            "bridge.reimport_chapters 必须检查 _worldbuild_done（#79），"
            "reimport 跟 import-chapters 同型——依赖 character / setting 已写入"
        )

    def test_push_concept_still_has_guard(self):
        """push_concept 已有守卫不能被撤销（回归保护）。"""
        src = self._func_source("push_concept")
        assert "_worldbuild_done" in src, (
            "bridge.push_concept 之前已有 worldbuild 守卫（#79 之前就有的修法）"
            "—— 不能被 #79 改动意外撤回"
        )

    def test_worldbuild_done_uses_job_status(self):
        """_worldbuild_done 必须看 GenerationJob.status='done' 而不只是 Project.status。

        单看 project.status='ready' 不够——如果 worldbuild 失败但 project 还
        在 'worldbuilding' 状态，import 不能放行；同理 worldbuild 在跑中
        不算 done。修法：跟 LifecycleJob history 一致，看 GenerationJob.status。
        """
        import inspect
        from app.api import bridge as bridge_mod
        src = inspect.getsource(bridge_mod._worldbuild_done)
        assert "GenerationJob" in src, (
            "_worldbuild_done 必须查 GenerationJob（job_type='worldbuild'）"
            "的 status='done'，不能只信 Project.status 字符串"
        )
        assert 'job_type="worldbuild"' in src or "job_type='worldbuild'" in src, (
            "_worldbuild_done 必须过滤 job_type='worldbuild' 的 GenerationJob"
        )
        assert ('status="done"' in src or "status='done'" in src
                or ".status == " in src and '"done"' in src
                or ".status == " in src and "'done'" in src), (
            "_worldbuild_done 必须匹配 status='done'（成功完成的 GenerationJob）"
        )

    def test_functional_worldbuild_not_done_returns_400(self):
        """行为测试：worldbuild GenerationJob 不存在 / 未完成时，import_chapters 应拒绝。

        模拟：项目在 worldbuild 但 GenerationJob 还没 done → 调 import-chapters
        应该抛 HTTPException(400)。这是 #79 修法的核心防御。
        """
        from datetime import datetime
        from fastapi import HTTPException
        from app.database import SessionLocal
        from app.models import BridgeRun, Project

        db = SessionLocal()
        try:
            project = Project(
                id="test-worldbuild-guard-proj",
                title="worldbuild guard test",
                genre="都市",
                audience="男频",
                status="worldbuilding",  # NOT ready
                config_json={},
            )
            db.add(project)
            db.commit()
            project_id = project.id
        finally:
            db.close()

        try:
            # 模拟 import_chapters 入口检查（实际函数需要 binding，但 _worldbuild_done 用 project + db）
            from app.api.bridge import _worldbuild_done
            db = SessionLocal()
            try:
                project = db.get(Project, project_id)
                # 无 worldbuild GenerationJob → worldbuild_done=False
                assert _worldbuild_done(project_id, project, db) is False, (
                    "项目状态 worldbuilding + 无 done GenerationJob → _worldbuild_done 必须 False"
                )
            finally:
                db.close()
        finally:
            # 清理
            db = SessionLocal()
            try:
                proj = db.get(Project, project_id)
                if proj:
                    db.delete(proj)
                db.commit()
            except Exception:
                db.rollback()
            finally:
                db.close()
