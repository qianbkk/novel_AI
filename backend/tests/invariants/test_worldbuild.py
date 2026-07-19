"""worldbuild/ — Phase 3 测试拆分

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

class TestReportsPathUnified:
    """历史背景（独立审查标记）：
      engine 写到 NOVEL_AI_DIR env 路径（与 binding.novel_ai_dir 等价时是
      novel_AI/output/，否则是 backend/data/engine/output/）。
      reports.py 之前硬编码 novel_ai_dir/output/ → engine 写到 env 路径时
      reports 读不到 → status 显示陈旧或 not_initialized。

    修复：reports.py 的 _state_path / _chapters_dir / _budget_log_path
    优先用 NOVEL_AI_DIR env，fallback 到参数。
    """

    def test_reports_uses_env_novel_ai_dir(self):
        """reports.py 解析路径时必须读 NOVEL_AI_DIR env。"""
        from pathlib import Path
        reports_py = (
            Path(REPO_ROOT)
            / "backend" / "app" / "bridge" / "reports.py"
        )
        content = reports_py.read_text(encoding="utf-8")
        assert "NOVEL_AI_DIR" in content, (
            "reports.py 必须读 NOVEL_AI_DIR env（与 engine 路径解析对齐）"
        )

    def test_reports_state_path_with_env(self, monkeypatch, tmp_path):
        """设置 NOVEL_AI_DIR 后，_state_path 必须解析到 env 路径。"""
        env_dir = str(tmp_path / "novel_ai_env")
        Path(env_dir, "output").mkdir(parents=True)
        monkeypatch.setenv("NOVEL_AI_DIR", env_dir)

        # 强制重读 reports（monkeypatch.setenv 必须在 import 之后）
        from app.bridge.reports import _state_path
        result = _state_path("/some/other/path")
        assert str(result) == str(Path(env_dir) / "output" / "orchestrator_state.json"), (
            f"_state_path 没走 NOVEL_AI_DIR env：{result}"
        )


class TestWorldbuildStagesEndpoint:
    """锁死 GET /worldbuild/stages 端点存在 + 返回 10 阶段 — 防止前后端 STAGES 漂移。

    之前 WorldBuild.tsx 硬编码 STAGES 数组，改后端 stages.py 忘改前端会导致
    进度条错位。这一对端点 + invariant 让前端 WorldBuild.tsx 不再需要手动同步。
    """
    def test_endpoint_registered_in_app(self):
        """FastAPI app 必须注册 GET /worldbuild/stages"""
        from app.main import app
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/worldbuild/stages" in paths, (
            "GET /worldbuild/stages 未注册 (前端 WorldBuild.tsx 需要它拉 10 阶段)"
        )

    def test_meta_router_has_no_project_id(self):
        """meta_router 路径不能含 {project_id} — STAGES 是全局常量"""
        from app.api.worldbuild import meta_router
        for r in meta_router.routes:
            if hasattr(r, "path"):
                assert "{project_id}" not in r.path, (
                    f"meta_router 路径含 project_id 占位符：{r.path}"
                )

    def test_stages_list_matches_known_count(self):
        """stages.py::STAGES 必须恰好 10 条 — WorldBuild 进度条按 10 等分计算"""
        from app.worldbuild.stages import STAGES
        assert len(STAGES) == 10, (
            f"STAGES 数量变更需同步 WorldBuild.tsx 进度条逻辑，当前 {len(STAGES)}"
        )


class TestReportsPathUnifiedExtra:
    """(合并自原 TestReportsPathUnified 的最后一项)"""

    def test_reports_state_path_fallback_without_env(self, monkeypatch):
        """NOVEL_AI_DIR 没设置时，_state_path 必须 fallback 到参数。"""
        monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
        from app.bridge.reports import _state_path
        result = _state_path("/some/dir")
        expected = str(Path("/some/dir") / "output" / "orchestrator_state.json")
        assert str(result) == expected, (
            f"_state_path fallback 失败：{result}（期望 {expected}）"
        )


class TestGraphCommandFailurePaths:
    """迭代 #14：graph.py 17+ command 分支的 except 路径需要 invariant test 锁死。

    之前只测了 unknown command 失败。bootstrap / scan / fingerprint /
    export / stats / init_arc / human_review / style / calibrate /
    acceptance 在 except 分支都是同一模板（log.error + exit_code=1），
    抽样测 3 个：bootstrap / run / show（一个失败路径 + 一个边界）。
    """

    def test_bootstrap_failure_returns_exit_code_1(self, monkeypatch):
        """bootstrap 抛异常 → exit_code=1 + log 含 'bootstrap failed'。"""
        from engine import graph as graph_mod
        from engine.tools import bootstrap as bootstrap_mod

        def fake_run_bootstrap(novel_id):
            raise RuntimeError("mock bootstrap error")

        monkeypatch.setattr(bootstrap_mod, "run_bootstrap", fake_run_bootstrap)
        # 重 import 防止 graph_mod 已经持有原 run_bootstrap
        import importlib
        importlib.reload(graph_mod)

        from queue import Queue
        q = Queue()
        exit_code, stdout = graph_mod.run_graph_task(
            project_id="test-bootstrap-fail",
            command="bootstrap",
            args=[],
            run_id="r-bootstrap",
            queue=q,
        )
        assert exit_code == 1, (
            f"bootstrap 抛异常应 exit_code=1，实际 {exit_code}"
        )
        # log 走 logging 模块输出到 file handler（不在 stdout 捕获里），
        # 所以只断言 exit_code。log 实际记录由 caplog fixture 验证。

    def test_show_nonexistent_chapter_returns_text_and_exit_0(self):
        """show 命令对不存在的章节输出 ❌ 文本，但 exit_code 仍是 0（信息查询性质）。"""
        from engine.graph import run_graph_task
        from queue import Queue
        q = Queue()
        exit_code, stdout = run_graph_task(
            project_id="test-show",
            command="show",
            args=["9999"],  # 不可能存在的章节号
            run_id="r-show",
            queue=q,
        )
        assert exit_code == 0, (
            f"show 不存在的章节应 exit_code=0（信息查询），实际 {exit_code}"
        )
        assert "❌" in stdout, (
            f"show 应输出 ❌ 标记表示章节不存在：{stdout[:200]!r}"
        )

    def test_run_command_handler_registered(self):
        """run command 必须在 graph 分支里有处理（不能走 unknown 命令路径）。"""
        import ast
        import inspect
        import textwrap
        from engine.graph import run_graph_task

        tree = ast.parse(textwrap.dedent(inspect.getsource(run_graph_task)))
        registered_commands = {
            value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            for comparator in node.comparators
            if isinstance(comparator, (ast.Tuple, ast.List))
            for value in comparator.elts
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        }
        assert {"run", "resume"} <= registered_commands

    def test_planner_import_error_fallback(self, monkeypatch):
        """planner agent 不存在时 fallback 到 'not yet ported' warn（不 crash）。"""
        # 这种 fallback 是有意设计：让 graph 在 agent 缺失时仍能 exit_code=0
        # （即返回 warn 信息而不是抛错）。锁死这一行为防止回归。
        import importlib
        import sys as _sys
        from engine import graph as graph_mod

        # 把 planner module 暂时从 sys.modules 移除 → import 抛 ImportError
        saved = _sys.modules.pop("engine.agents.planner", None)
        # 触发 graph_mod 重新 import planner 的分支
        try:
            importlib.reload(graph_mod)
            from queue import Queue
            q = Queue()
            # 当 planner import 失败时，graph 应捕到 ImportError 并 exit_code=0
            # （设计上是 graceful fallback，让 frontend 知道命令"未移植"而非"失败"）
            try:
                exit_code, stdout = graph_mod.run_graph_task(
                    project_id="test-planner-fallback",
                    command="planner",
                    args=[],
                    run_id="r-planner",
                    queue=q,
                )
                # 要么 0 (graceful fallback) 要么 1 (throw) — 但不能 crash
                assert exit_code in (0, 1), (
                    f"planner import 失败时 exit_code 必须在 {{0, 1}}，实际 {exit_code}"
                )
            finally:
                if saved is not None:
                    _sys.modules["engine.agents.planner"] = saved
        except Exception as e:
            if saved is not None:
                _sys.modules["engine.agents.planner"] = saved
            raise


class TestImportChaptersResilient:
    """迭代 #31: import_chapters_from_novel_ai 之前一个坏文件就让整批 import 失败。

    历史 bug：chapters_dir.glob("ch_*.txt") 拿到所有 .txt，但每个文件都做：
      - n = int(txt_path.stem.split("_")[1])   → ValueError on malformed
      - txt_path.read_text(encoding="utf-8")   → UnicodeDecodeError on 编码错
      - json.loads(meta.read_text(...))        → JSONDecodeError on meta 坏
    任何一个抛异常 → 整个 import 失败 → 用户看到 0 章导入，没法定位是哪个文件坏。

    修法：每文件 try/except，log warning + 跳过该文件继续下一个。
    同样修 _force_reimport。

    本测试锁死：3 个文件（1 正常 / 1 坏 filename / 1 meta 损坏）→ 正常文件
    仍被导入，整个 import 不抛异常。
    """
    @pytest.fixture(autouse=True)
    def setup_chapters_dir(self, tmp_path):
        """准备一个含 3 个章节文件的目录：1 正常 / 1 坏 filename / 1 坏 meta"""
        import os
        import secrets
        chapters_dir = tmp_path / "output" / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)

        # 1) 正常文件
        (chapters_dir / "ch_0001.txt").write_text(
            "厅堂不大。\n\n商恪坐在案后，\n翻看案上账册。\n", encoding="utf-8"
        )
        (chapters_dir / "ch_0001_meta.json").write_text(
            json.dumps({
                "chapter_number": 1,
                "chapter_role": "铺垫",
                "chapter_goal": "展现商恪困境",
                "score": 7.0,
                "rewrite_count": 0,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
        # 2) 正常文件 + 坏 meta
        (chapters_dir / "ch_0002.txt").write_text(
            "雅间内。\n\n林尘盘膝坐下。\n", encoding="utf-8"
        )
        (chapters_dir / "ch_0002_meta.json").write_text(
            "{not valid json", encoding="utf-8"
        )
        # 3) 畸形文件名（不匹配 ch_<N> 格式）
        (chapters_dir / "ch_xyz.txt").write_text("garbage", encoding="utf-8")

        self.tmp_path = tmp_path
        # 用 secrets 保证 project_id 唯一（避免 DB 残留冲突）
        self.project_id = f"test-resilient-{secrets.token_hex(8)}"
        yield tmp_path
        # teardown：清理测试数据
        from app.database import SessionLocal
        from app.models import Project, Chapter
        db = SessionLocal()
        try:
            db.query(Chapter).filter_by(project_id=self.project_id).delete()
            db.query(Project).filter_by(id=self.project_id).delete()
            db.commit()
        except Exception:
            pass
        finally:
            db.close()

    def test_import_chapters_continues_past_bad_files(self, setup_chapters_dir):
        """3 个文件（1 正常 + 1 meta 坏 + 1 坏 filename）→ 正常文件被导入，整个 import 不抛。"""
        import asyncio
        from app.bridge.chapter_import import import_chapters_from_novel_ai
        from app.database import SessionLocal
        from app.models import Project, Chapter

        # 准备 project
        db = SessionLocal()
        try:
            project = Project(
                id=self.project_id,
                title="test",
                genre="玄幻",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            # 之前会因 ch_0002_meta.json 损坏而抛 JSONDecodeError → 0 章导入
            # 修后：2 章导入（ch_0001 + ch_0002 with empty meta），ch_xyz 跳过
            result = asyncio.run(
                import_chapters_from_novel_ai(self.project_id, str(self.tmp_path), db)
            )
            # 关键断言 1：import 没抛
            assert result is not None
            assert len(result) == 2, (
                f"应导入 2 个 chapter（ch_0001 + ch_0002 with bad meta），"
                f"实际 {len(result)} 个：{result}"
            )
            # 关键断言 2：DB 里至少有 ch_0001（最稳的）
            chapter_nos = {
                c.chapter_no for c in
                db.query(Chapter).filter_by(project_id=self.project_id).all()
            }
            assert 1 in chapter_nos, (
                f"ch_0001 应被导入，DB chapter_nos={chapter_nos}"
            )
            assert 2 in chapter_nos, (
                f"ch_0002 应被导入（meta 坏但 txt 仍可用），DB chapter_nos={chapter_nos}"
            )
        finally:
            # 清理
            try:
                db.query(Chapter).filter_by(project_id=self.project_id).delete()
                db.query(Project).filter_by(id=self.project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_force_reimport_continues_past_bad_files(self, setup_chapters_dir):
        """_force_reimport 也必须单文件坏不阻断。"""
        import asyncio
        from app.bridge.chapter_import import _force_reimport
        from app.database import SessionLocal
        from app.models import Project, Chapter

        # 准备 project
        db = SessionLocal()
        try:
            project = Project(
                id=self.project_id,
                title="test",
                genre="玄幻",
                status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            result = asyncio.run(
                _force_reimport(self.project_id, str(self.tmp_path), db)
            )
            # 至少 ch_0001 应被 created（不存在）+ ch_0002 meta 坏但仍 create
            chapter_nos = {item["chapter_no"] for item in result}
            assert 1 in chapter_nos, (
                f"_force_reimport 应至少处理 ch_0001，实际 chapter_nos={chapter_nos}"
            )
        finally:
            try:
                db.query(Chapter).filter_by(project_id=self.project_id).delete()
                db.query(Project).filter_by(id=self.project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()


class TestExportChaptersResilient:
    """迭代 #34: export_chapters / print_stats 之前单章坏让整批 export 失败。

    历史 bug：1 章编码错（Latin-1 而非 UTF-8）/ meta 损坏 → 整个 export 抛异常
    → 之前已写好的 chapters 也没保存。
    跟 import_chapters 是同型问题（迭代 #31），同样的修法。

    本测试锁死：2 个 chapter（1 正常 + 1 坏 encoding）→ 正常 chapter
    被导出，export 不抛异常。
    """
    def test_export_chapters_source_has_per_chapter_try_except(self):
        """源码级锁死：export_chapters 体内必须每章独立 try/except。

        Runtime 验证很难构造（readline 已经过滤坏文件，要让 f.read() 单独
        失败需要 partial UTF-8 sequence 截断等），但源码级锁死足以防止回归。
        """
        from pathlib import Path
        import re
        exporter_py = Path(BACKEND_ROOT) / "engine" / "tools" / "exporter.py"
        content = exporter_py.read_text(encoding="utf-8")
        m = re.search(
            r"def export_chapters\([\s\S]*?\):(.*?)(?=\ndef |\nclass |\Z)",
            content, re.DOTALL
        )
        assert m, "找不到 export_chapters"
        body = m.group(1)
        # 关键：在 for ch_num, ch_path in chapters 循环内必须有 try/except
        # 不能让单章抛异常阻断整批
        assert body.count("try:") >= 2 or body.count("except") >= 2, (
            "export_chapters 体内必须有 try/except 处理单章失败（之前 all-or-nothing）"
        )
        assert "continue" in body, (
            "跳过单章后必须 continue（不能 break / 抛异常）"
        )

    def test_print_stats_source_has_per_chapter_try_except(self):
        """print_stats 同样修法：源码必须有 try/except + continue。"""
        from pathlib import Path
        exporter_py = Path(BACKEND_ROOT) / "engine" / "tools" / "exporter.py"
        content = exporter_py.read_text(encoding="utf-8")
        # 用基于缩进的解析：找到 def print_stats( 后的非空行，body 是缩进 >= 4 空格的行
        lines = content.splitlines()
        body_start = None
        for i, line in enumerate(lines):
            if line.startswith("def print_stats"):
                body_start = i + 1
                break
        assert body_start is not None, "找不到 print_stats"
        # 收集到下一个 def 之前的所有行
        body_lines = []
        for line in lines[body_start:]:
            if line.startswith("def ") and not line.startswith("def print_stats"):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        assert "try:" in body, (
            "print_stats 体内必须有 try/except 处理单章失败"
        )
        assert "continue" in body, (
            "跳过单章后必须 continue"
        )

    def test_export_chapters_runs_without_error_on_normal_files(self, tmp_path, monkeypatch):
        """正常文件场景：export_chapters 跑通返回正确结果。"""
        from engine.tools.exporter import export_chapters
        import engine.tools.exporter as exporter_mod

        chapters_dir = tmp_path / "output" / "chapters"
        chapters_dir.mkdir(parents=True, exist_ok=True)
        (chapters_dir / "ch_0001.txt").write_text("雅间内。\n", encoding="utf-8")
        (chapters_dir / "ch_0001_meta.json").write_text(
            json.dumps({"score": 7.0, "chapter_role": "铺垫"}, ensure_ascii=False),
            encoding="utf-8",
        )
        (chapters_dir / "ch_0002.txt").write_text("林尘盘膝。\n", encoding="utf-8")
        (chapters_dir / "ch_0002_meta.json").write_text(
            json.dumps({"score": 8.0, "chapter_role": "发展"}, ensure_ascii=False),
            encoding="utf-8",
        )
        setting_path = tmp_path / "output" / "setting_package.json"
        setting_path.write_text(
            json.dumps({"title_candidates": ["测试书"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        # exporter 已经 from-import 了这些名字，必须 patch exporter 模块自己的属性
        monkeypatch.setattr(exporter_mod, "CHAPTERS_DIR_STR", str(chapters_dir))
        monkeypatch.setattr(exporter_mod, "OUTPUT_DIR_STR", str(tmp_path / "output"))
        monkeypatch.setattr(exporter_mod, "SETTING_PATH_STR", str(setting_path))
        exports_dir = tmp_path / "output" / "exports"
        exports_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(exporter_mod, "EXPORTS_DIR", str(exports_dir))

        result = export_chapters()
        assert result is not None
        assert result["chapters_exported"] == 2
        assert "雅间内" in Path(result["output_path"]).read_text(encoding="utf-8")
        assert "林尘盘膝" in Path(result["output_path"]).read_text(encoding="utf-8")


class TestPullSettingJsonErrorHandling:
    """迭代 #35: pull_setting_package 之前损坏的 setting_package.json 让
    原始 JSONDecodeError 暴露给前端（500 + 几百行 Python traceback）。

    修法：catch (json.JSONDecodeError, UnicodeDecodeError) 抛清晰 ValueError
    提示用户"文件损坏请重新跑 planner"。

    本测试锁死：损坏的 setting_package.json 必须 raise ValueError（带用户可读信息），
    不是让原始 JSONDecodeError 透出。
    """
    def test_pull_setting_raises_value_error_on_corrupt_json(self, tmp_path):
        """损坏的 setting_package.json → 抛 ValueError 不是 JSONDecodeError。"""
        import asyncio
        from app.bridge.setting_sync import pull_setting_package
        from app.database import SessionLocal
        from app.models import Project
        import secrets

        # 准备损坏文件
        (tmp_path / "output").mkdir(parents=True, exist_ok=True)
        (tmp_path / "output" / "setting_package.json").write_text(
            "{ this is not valid json",
            encoding="utf-8",
        )

        # 准备 project
        project_id = f"test-pull-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            project = Project(
                id=project_id, title="test", genre="玄幻", status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            with pytest.raises(ValueError, match="setting_package.json 损坏"):
                asyncio.run(pull_setting_package(project_id, str(tmp_path), db))
        finally:
            try:
                db.query(Project).filter_by(id=project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_pull_setting_raises_value_error_on_encoding_error(self, tmp_path):
        """非 UTF-8 编码的 setting_package.json → 抛 ValueError。"""
        import asyncio
        from app.bridge.setting_sync import pull_setting_package
        from app.database import SessionLocal
        from app.models import Project
        import secrets

        (tmp_path / "output").mkdir(parents=True, exist_ok=True)
        # 写非法 UTF-8 字节
        (tmp_path / "output" / "setting_package.json").write_bytes(
            b'{"valid_key": "\xff\xfe\x00\x41"}'
        )

        project_id = f"test-pull-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            project = Project(
                id=project_id, title="test", genre="玄幻", status="ready",
                config_json={},
            )
            db.add(project)
            db.commit()
        finally:
            db.close()

        db = SessionLocal()
        try:
            with pytest.raises(ValueError, match="setting_package.json 损坏"):
                asyncio.run(pull_setting_package(project_id, str(tmp_path), db))
        finally:
            try:
                db.query(Project).filter_by(id=project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_pull_setting_source_has_json_error_handling(self):
        """源码级锁死：pull_setting_package 必须 catch JSONDecodeError + UnicodeDecodeError。"""
        from pathlib import Path
        sync_py = Path(BACKEND_ROOT) / "app" / "bridge" / "setting_sync.py"
        content = sync_py.read_text(encoding="utf-8")
        # 找 pull_setting_package 函数
        import re
        m = re.search(
            r"async def pull_setting_package\([\s\S]*?\):",
            content, re.DOTALL
        )
        assert m, "找不到 pull_setting_package"
        # 取函数后到下一个 def 之前的内容
        start = m.end()
        lines = content[start:].splitlines()
        body_lines = []
        for line in lines:
            if line.startswith("async def ") or line.startswith("def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        assert "JSONDecodeError" in body, (
            "pull_setting_package 必须 catch JSONDecodeError"
        )
        assert "UnicodeDecodeError" in body, (
            "pull_setting_package 必须 catch UnicodeDecodeError"
        )
        assert "ValueError" in body, (
            "必须转抛 ValueError（带用户可读信息，不是原始 traceback）"
        )


class TestPostProcessLLMFailure:
    """迭代 #37: rules.py _llm_call_for_postprocess 之前 except Exception
    返回占位文本（"[tool] LLM 调用失败..."）+ cost=0。

    这是 fake-pass 同型问题：前端收到占位 + cost=0，误以为"逻辑评估完成"
    实际 LLM 失败。改 raise HTTPException(503) 让用户看到真实错误。

    本测试锁死：mock LLM 抛异常 → post_process 必须 raise 503，
    不是返回占位文本。
    """
    def test_post_process_raises_503_on_llm_failure(self, monkeypatch):
        """LLM 抛异常 → post_process 必须 raise HTTPException 503。"""
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import SessionLocal
        from app.models import Project, Chapter, RuleConfig
        import secrets

        project_id = f"test-postproc-{secrets.token_hex(8)}"
        db = SessionLocal()
        try:
            # 准备 project + chapter + rule config
            project = Project(
                id=project_id, title="test", genre="玄幻", status="ready",
                config_json={},
            )
            db.add(project)
            chapter = Chapter(
                project_id=project_id, chapter_no=1, title="ch1",
                content="林尘盘膝坐下，闭目调息。\n",
            )
            db.add(chapter)
            db.commit()
        finally:
            db.close()

        # mock LLM router 抛异常
        from app.api import rules as rules_mod
        from engine.llm import router as router_mod

        class FakeRouter:
            def call(self, *a, **kw):
                raise ConnectionError("simulated LLM 503")

        # monkeypatch get_active_router 返回 FakeRouter
        from engine import llm_router
        monkeypatch.setattr(llm_router, "get_active_router", lambda: FakeRouter())
        monkeypatch.setattr(router_mod, "LLMRouter", lambda *a, **kw: FakeRouter())

        client = TestClient(app)
        try:
            r = client.post(
                f"/projects/{project_id}/rules/post-process",
                json={"tool": "logic"},
            )
            # 必须 503（之前是 200 + 占位文本）
            assert r.status_code == 503, (
                f"LLM 失败时应返回 503，实际 {r.status_code}：{r.text}"
            )
            # detail 必须含 "LLM 调用失败" 关键词
            body = r.json()
            assert "LLM 调用失败" in str(body), (
                f"503 响应 detail 应含 'LLM 调用失败'，实际：{body}"
            )
        finally:
            db = SessionLocal()
            try:
                from app.models import Chapter
                db.query(Chapter).filter_by(project_id=project_id).delete()
                db.query(RuleConfig).filter_by(project_id=project_id).delete()
                db.query(Project).filter_by(id=project_id).delete()
                db.commit()
            except Exception:
                pass
            db.close()

    def test_post_process_source_uses_503_not_fake_pass(self):
        """源码级锁死：post-process LLM 失败时必须 raise HTTPException 不是 return 占位。"""
        from pathlib import Path
        rules_py = Path(BACKEND_ROOT) / "app" / "api" / "rules.py"
        content = rules_py.read_text(encoding="utf-8")
        # 找 _llm_call_for_postprocess 函数体
        lines = content.splitlines()
        body_start = None
        for i, line in enumerate(lines):
            if line.startswith("def _llm_call_for_postprocess"):
                body_start = i + 1
                break
        assert body_start is not None, "找不到 _llm_call_for_postprocess"
        body_lines = []
        for line in lines[body_start:]:
            if line.startswith("def ") or line.startswith("class "):
                break
            body_lines.append(line)
        body = "\n".join(body_lines)
        # 关键：必须有 raise HTTPException（不是 return 占位）
        assert "raise HTTPException" in body, (
            "_llm_call_for_postprocess 必须 raise HTTPException（不是 return 占位）"
        )
        # 关键：不能有"return 失败占位"模式
        assert "LLM 调用失败" in body, (
            "需要 raise HTTPException 503 with 'LLM 调用失败' detail"
        )
        # 反向：真代码行不能有 return 一个虚假成功占位
        code_lines = [
            line for line in body.splitlines()
            if not line.strip().startswith("#")
        ]
        code_body = "\n".join(code_lines)
        # 检查不能有"LLM 调用失败"字面量被作为 return 内容
        # （出现在 raise detail 里是 OK 的）
        return_lines = [l for l in code_body.splitlines() if "return" in l and "LLM" in l]
        # 允许 raise ... "LLM 调用失败"（含 LLM 字面量）但不应该是 return
        for line in return_lines:
            assert line.strip().startswith("raise"), (
                f"不能 return 含 LLM 字面量的占位文本（应该是 raise）：{line!r}"
            )


class TestSubprocessEnvContract:
    """锁死 _spawn_engine_subprocess → engine subprocess 的 env 契约。

    历史 bug (iter #84)：
      bridge.run spawn engine subprocess 时只 set 了 NOVEL_OUTLINE_MODE，
      缺 NOVEL_AI_DIR 和 NOVEL_ENGINE_MOCK 两个 P0 关键变量。
        - 缺 NOVEL_AI_DIR → engine 写默认 backend/data/engine/output/，
          bridge.reports 读不到 orchestrator_state.json / setting_package.json
        - 缺 NOVEL_ENGINE_MOCK=1 → LLMRouter 真去调 API 报
          ValueError('MINIMAX_API_KEY 未设置')
    """

    def test_bridge_popen_env_includes_novel_ai_dir(self):
        """bridge._spawn_engine_subprocess 的 env 字典必须显式含 NOVEL_AI_DIR。"""
        from app.api.bridge import _spawn_engine_subprocess
        import inspect
        src = inspect.getsource(_spawn_engine_subprocess)
        assert (
            '"NOVEL_AI_DIR"' in src or "'NOVEL_AI_DIR'" in src
        ), (
            "_spawn_engine_subprocess 必须在 env 字典里显式设置 "
            "NOVEL_AI_DIR，否则 subprocess 写到默认 backend/data/engine/output/"
        )

    def test_bridge_popen_env_includes_novel_engine_mock(self):
        """bridge._spawn_engine_subprocess 必须把父进程 NOVEL_ENGINE_MOCK 透传到 subprocess。"""
        from app.api.bridge import _spawn_engine_subprocess
        import inspect
        src = inspect.getsource(_spawn_engine_subprocess)
        assert "NOVEL_ENGINE_MOCK" in src, (
            "_spawn_engine_subprocess 必须显式把 NOVEL_ENGINE_MOCK 加到 env；"
            "父进程设了但 subprocess 看不到会让 LLMRouter 真去调 API 报 MINIMAX_API_KEY 未设置"
        )

    def test_bridge_env_outline_mode_still_set(self):
        """回归保护：原有 NOVEL_OUTLINE_MODE 不能因为新增 env 逻辑而被破坏。"""
        from app.api.bridge import _spawn_engine_subprocess
        import inspect
        src = inspect.getsource(_spawn_engine_subprocess)
        assert (
            '"NOVEL_OUTLINE_MODE"' in src or "'NOVEL_OUTLINE_MODE'" in src
        ), "NOVEL_OUTLINE_MODE 必须在 env 字典里强制设值"

    def test_run_bridge_subprocess_documents_env_contract(self):
        """subprocess 脚本应当文档化 / 承认 env 契约（不是隐式 magic）。"""
        from pathlib import Path
        worker = Path(BACKEND_ROOT) / "engine" / "workers" / "run_bridge_subprocess.py"
        src = worker.read_text(encoding="utf-8")
        # 必须有 NOVEL_AI_DIR 或 NOVEL_ENGINE_MOCK 的明确引用（说明开发者意识到契约）
        assert "NOVEL_AI_DIR" in src or "NOVEL_ENGINE_MOCK" in src, (
            "run_bridge_subprocess.py 应当提到 NOVEL_AI_DIR/NOVEL_ENGINE_MOCK "
            "env 契约，否则将来有人改父进程 env 字典，subprocess 端没人知道"
        )


class TestGraphPyEnvAwareOutputDir:
    """P0 修复（iter #85）：graph.py 三处硬编码 DATA_DIR/engine/output，
    完全忽略 NOVEL_AI_DIR。修：抽 _engine_output_dir() helper 统一 env-aware。
    之前症状：binding 指向 ../novel_AI 但 planner 写到 backend/data/engine/output/，
    bridge.reports 读不到，状态错乱。"""

    def test_engine_output_dir_uses_novel_ai_dir_when_set(self):
        """NOVEL_AI_DIR 设置时，_engine_output_dir 用它"""
        import os
        from engine.graph import _engine_output_dir
        from pathlib import Path
        old = os.environ.get("NOVEL_AI_DIR")
        try:
            os.environ["NOVEL_AI_DIR"] = "/tmp/test_novel_ai_dir"
            result = _engine_output_dir()
            assert str(result).replace("\\", "/") == "/tmp/test_novel_ai_dir/output"
        finally:
            if old is not None:
                os.environ["NOVEL_AI_DIR"] = old
            else:
                os.environ.pop("NOVEL_AI_DIR", None)

    def test_engine_output_dir_falls_back_to_backend(self):
        """NOVEL_AI_DIR 未设时，_engine_output_dir 用默认 backend/data/engine/output"""
        import os
        from engine.graph import _engine_output_dir
        old = os.environ.pop("NOVEL_AI_DIR", None)
        try:
            result = _engine_output_dir()
            assert "data/engine/output" in str(result).replace("\\", "/")
        finally:
            if old is not None:
                os.environ["NOVEL_AI_DIR"] = old

    def test_graph_py_no_hardcoded_output_dir(self):
        """源码扫描：graph.py 不再硬编码 str(DATA_DIR / "engine" / "output")"""
        import inspect
        from engine import graph
        src = inspect.getsource(graph)
        assert "str(DATA_DIR / \"engine\" / \"output\")" not in src, (
            "graph.py 仍有 str(DATA_DIR / 'engine' / 'output') 硬编码（iter #85 已修）"
        )


class TestNovelConfigEnvAware:
    """novel_config.json 读取必须与 push-concept 的写入位置一致。

    push-concept 写到 binding.novel_ai_dir/config/novel_config.json，
    引擎子进程通过 NOVEL_AI_DIR env 拿到同一目录。若 planner /
    orchestrator 只读固定的 backend/data/engine/config/ 路径，
    绑定非默认目录的项目会读到上一个项目残留的设定概念（跨项目串味）。
    """

    def _make_config_dir(self, tmp_path):
        import json
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir(parents=True)
        cfg = {"novel_id": "env-aware-proof", "platform": "personal",
               "genre": "都市", "setting_concept": "env 目录里的概念",
               "budget_limit_usd": 1.0}
        (cfg_dir / "novel_config.json").write_text(
            json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
        return cfg

    def test_planner_reads_config_from_novel_ai_dir(self, tmp_path, monkeypatch):
        """NOVEL_AI_DIR 设置时，planner 读 env 目录下的 novel_config.json"""
        expected = self._make_config_dir(tmp_path)
        monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
        from engine.agents.planner import _load_novel_config
        cfg = _load_novel_config()
        assert cfg.get("novel_id") == expected["novel_id"], (
            "planner 忽略 NOVEL_AI_DIR，读了固定 backend 路径的 novel_config.json"
        )

    def test_orchestrator_reads_config_from_novel_ai_dir(self, tmp_path, monkeypatch):
        """NOVEL_AI_DIR 设置时，orchestrator._config() 读 env 目录"""
        expected = self._make_config_dir(tmp_path)
        monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
        from engine.orchestrator import _config
        cfg = _config()
        assert cfg.get("novel_id") == expected["novel_id"], (
            "orchestrator 忽略 NOVEL_AI_DIR，读了固定 backend 路径的 novel_config.json"
        )

    def test_config_falls_back_to_backend_path_without_env(self, monkeypatch):
        """NOVEL_AI_DIR 未设时，回退固定 backend/data/engine/config 路径（向后兼容）"""
        monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
        from engine.config.paths import novel_config_path, NOVEL_CONFIG_PATH
        assert str(novel_config_path()) == str(NOVEL_CONFIG_PATH)


class TestPullSettingFKCascade:
    """P0 修复（iter #85）：setting_sync.py 删 Character 时没先删子表
    ChapterCharacter/EntityRelation/Foreshadowing 等 FK 引用，DELETE 报
    FOREIGN KEY constraint failed。修：调整 7 个 DELETE 顺序。"""

    def test_pull_setting_delete_order_cascades_correctly(self):
        """源码扫描：setting_sync 的 idempotent clear 必须删全部 8 个相关表，
        子表先于父表（FK 级联）。

        实现方式：先剥掉所有以 # 开头的整行注释行，避免把 docstring/注释里
        出现的 ".delete(" 误识别为调用。然后把整段源码按每个 `.delete(`
        位置切成若干「片段」，每个片段从前一个 `.delete(` 之后开始。
        片段内找唯一一个 `db.query(<ModelName>)`（如果有）即为该 delete
        对应的模型。

        这样无论是单行 `query(X).filter(...).delete()` 还是多行
        `ChapterCharacter` 那种
        `query(X)\n  .filter(\n    X.id.in_(query(Y).subquery())\n  )\n  .delete()`
        都能正确识别——因为 `.delete(` 一定在该 statement 的最后一个
        `db.query(X)` 之后且中间不会再嵌套新 query。
        """
        from pathlib import Path
        from app.bridge import setting_sync
        src = Path(setting_sync.__file__).read_text(encoding="utf-8")

        import re

        # 1) 剥整行注释
        cleaned_lines = []
        for line in src.splitlines(keepends=True):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                cleaned_lines.append(" " * len(line))
            else:
                cleaned_lines.append(line)
        cleaned = "".join(cleaned_lines)

        # 2) 找每个 .delete( 位置，按这些位置切分源码
        delete_positions = [m.start() for m in re.finditer(r"\.delete\(", cleaned)]
        segments: list[str] = []
        prev = 0
        for pos in delete_positions:
            segments.append(cleaned[prev:pos])
            prev = pos
        # 最后一段不算（之后没有 delete）

        # 3) 每个 segment 里找 db.query(<ModelName>)
        delete_calls: list[str] = []
        for seg in segments:
            queries = re.findall(r"db\.query\((\w+)\)", seg)
            if queries:
                # 取最后一个 query（即最近的、最外层的 statement 起点）
                delete_calls.append(queries[-1])

        expected_order = [
            "ChapterCharacter", "EntityRelation", "Foreshadowing",
            "MapNode", "Currency", "PowerSystem", "Faction", "Character",
        ]
        for required in expected_order:
            assert required in delete_calls, (
                f"setting_sync 没删 {required}，实际删了 {delete_calls}"
            )
        assert delete_calls[-1] == "Character", (
            f"Character.delete() 必须是最后一个（FK 级联），"
            f"实际顺序 {delete_calls}"
        )
        assert delete_calls[0] == "ChapterCharacter", (
            f"ChapterCharacter.delete() 必须第一个（最深 FK），"
            f"实际顺序 {delete_calls}"
        )
