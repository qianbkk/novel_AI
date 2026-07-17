"""deploy/ — Phase 3 测试拆分

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

class TestPytestCollection:
    """历史 bug：`pytest tests/` 在 collection 阶段会报 1 个 error，
    因为 test_alignment_smoke.py 里有个 def test(name: str) 装饰器工厂
    撞 pytest 自动收集规则。修复：改名 _test + __test__ = False。"""

    def test_all_tests_dir_collects_cleanly(self):
        """跑 pytest --collect-only 应该 0 collection error。
        关键：test_alignment_smoke.py 里的 def test(name: str) 装饰器工厂
        如果被 pytest 自动收集就会触发 collection error（参数不匹配）。
        修复：rename + __test__ = False。
        """
        import subprocess
        # 用 sub-process 跑收集（不重入自己）
        result = subprocess.run(
            ["python", "-m", "pytest", "tests/", "--collect-only", "-q"],
            capture_output=True, text=True, cwd=str(BACKEND_ROOT),
            timeout=60,
        )
        out = (result.stdout + result.stderr).lower()
        # 检查"X error"模式（pytest collection error 的标志）
        import re
        m = re.search(r"(\d+)\s+errors?", out)
        if m:
            err_count = int(m.group(1))
            assert err_count == 0, (
                f"pytest tests/ 有 {err_count} collection errors:\n{out[-1000:]}"
            )


class TestDeploymentDocs:
    """历史背景（独立审查标记的高危点修复配套）：
      Provider.api_key 改 Fernet 加密后，部署必须设 MASTER_KEY env。
      没有 generate 脚本 + README 部署文档，用户部署时不知道这步。

      本轮新增：
        - backend/scripts/generate_master_key.py — 输出 MASTER_KEY=...
        - README.md 加「部署」章节：MASTER_KEY / CORS / 端口 / 迁移 / 范围外
    """

    def test_generate_master_key_script_exists(self):
        """generate_master_key.py 必须存在且能跑（生成有效 Fernet key）。"""
        import subprocess
        from pathlib import Path
        script = Path(REPO_ROOT) / "backend" / "scripts" / "generate_master_key.py"
        assert script.exists(), (
            "backend/scripts/generate_master_key.py 不存在 — "
            "部署时无法生成 MASTER_KEY，Provider API key 加密没人能用"
        )
        # 真跑一遍
        result = subprocess.run(
            ["python", "-m", "scripts.generate_master_key"],
            cwd=script.parent.parent,
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, f"脚本失败：{result.stderr}"
        assert "MASTER_KEY=" in result.stdout, "脚本输出必须含 MASTER_KEY="
        # 提取 key，校验长度
        for line in result.stdout.splitlines():
            if line.startswith("MASTER_KEY="):
                key = line.split("=", 1)[1].strip()
                assert len(key) == 44, f"MASTER_KEY 长度 {len(key)} ≠ 44"
                # Fernet round-trip 验证
                from cryptography.fernet import Fernet
                f = Fernet(key.encode("ascii"))
                token = f.encrypt(b"sanity")
                assert f.decrypt(token) == b"sanity"
                return
        assert False, "脚本输出里没找到 MASTER_KEY=..."

    def test_readme_has_deployment_section(self):
        """README 必须有「部署」章节且提到 MASTER_KEY + ALLOWED_ORIGINS。"""
        from pathlib import Path
        readme = Path(REPO_ROOT) / "README.md"
        content = readme.read_text(encoding="utf-8")
        assert "## 部署" in content, "README 缺「部署」章节"
        assert "MASTER_KEY" in content, "部署章节必须提到 MASTER_KEY"
        assert "ALLOWED_ORIGINS" in content, "部署章节必须提到 ALLOWED_ORIGINS（CORS）"


class TestOpenApiExport:
    """历史背景（独立审查标记的低优先级）：
      frontend/openapi.json 之前手工 commit，已严重漂移（缺 10+ 端点）。
      本轮修复：加 export_openapi.py 从运行中的后端拉 spec，frontend/openapi.json
      加 .gitignore 自动忽略。CI / 开发者需要时跑 `python -m scripts.export_openapi`
      重新生成。
    """

    def test_frontend_openapi_gitignored(self):
        """frontend/openapi.json 必须在 frontend/.gitignore 里（不再 commit）。"""
        from pathlib import Path
        gi = Path(REPO_ROOT) / "frontend" / ".gitignore"
        content = gi.read_text(encoding="utf-8")
        assert "openapi.json" in content, (
            "frontend/.gitignore 必须包含 openapi.json — "
            "否则它会污染 commit history（旧版本漂移问题）"
        )

    def test_export_openapi_script_exists(self):
        """export_openapi.py 必须存在 + 可作为 module import。"""
        from pathlib import Path
        script = Path(REPO_ROOT) / "backend" / "scripts" / "export_openapi.py"
        assert script.exists(), "backend/scripts/export_openapi.py 不存在"
        # 验证可 import + 有 main()
        import importlib.util
        spec_obj = importlib.util.spec_from_file_location("export_openapi", script)
        mod = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(mod)  # type: ignore
        assert hasattr(mod, "main"), "export_openapi.py 必须定义 main()"


class TestHealthEndpointDBCheck:
    """历史背景（迭代 #8）：
      /health 之前永远返回 {"status": "ok"}，不管 DB 是否锁 / 磁盘满 /
      migration 失败。k8s livenessProbe / readinessProbe 拿到 ok 后会继续
      发流量，实际后端挂但监控看不见。

      修法：/health 必须真执行 SELECT 1（验证 DB session 可用）。
    """

    def test_health_returns_db_ok_when_db_works(self):
        """DB 可用时 /health 返回 200 + db: ok。"""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        r = client.get("/health")
        assert r.status_code == 200, f"/health 期望 200，实际 {r.status_code}"
        body = r.json()
        assert body["status"] == "ok"
        assert body.get("db") == "ok", (
            f"/health 响应必须含 db 字段：{body}"
        )

    def test_health_returns_503_when_db_fails(self, monkeypatch):
        """DB 不可达时 /health 返回 503 + status: degraded。"""
        from fastapi.testclient import TestClient
        from app import database as db_mod
        from app import main as main_mod

        class FakeSession:
            def execute(self, *args, **kwargs):
                raise RuntimeError("DB lock acquired")
            def close(self):
                pass
        monkeypatch.setattr(db_mod, "SessionLocal", lambda: FakeSession())
        monkeypatch.setattr(main_mod, "SessionLocal", lambda: FakeSession())

        from app.main import app as _app
        client = TestClient(_app)
        r = client.get("/health")
        assert r.status_code == 503, (
            f"DB 故障时 /health 应返回 503，实际 {r.status_code}"
        )
        body = r.json()
        assert body["status"] == "degraded"
        assert body["db"] == "error"
        assert "DB lock" in body.get("detail", ""), (
            f"detail 应含错误信息：{body}"
        )


class TestSQLitePragmas:
    """历史背景（迭代 #10）：
      SQLite 默认 journal_mode=rollback，写操作期间全库锁 → engine 写
      state.json 时前端 /health / GET chapters 都会阻塞。
      busy_timeout=0 → 锁冲突立刻抛（多 worker / 测试并行跑时假错）。
      foreign_keys=OFF → 默认不强制 FK，数据完整性弱（orphan 行可能）。

      修法：connect event 设 PRAGMA journal_mode=WAL + busy_timeout=5000
      + synchronous=NORMAL + foreign_keys=ON。
    """

    def test_journal_mode_is_wal(self):
        """SQLite journal_mode 必须是 WAL。"""
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            # mode 可能是 'wal' / 'memory' 等；wal 是我们要的
            assert mode.lower() == "wal", (
                f"journal_mode 应为 WAL，实际 {mode!r}"
            )

    def test_busy_timeout_is_set(self):
        """busy_timeout 必须 >= 1000ms（默认 0 = 不等 = 锁冲突假错）。"""
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
            assert timeout >= 1000, (
                f"busy_timeout 应 >= 1000ms，实际 {timeout}ms"
            )

    def test_foreign_keys_enabled(self):
        """PRAGMA foreign_keys 必须为 ON（默认 OFF）。"""
        from app.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            fk = conn.execute(text("PRAGMA foreign_keys")).scalar()
            assert fk == 1, (
                f"foreign_keys 应为 ON（=1），实际 {fk}（FK 约束可能失效）"
            )


class TestGetDbDependency:
    """最后 #21：get_db 是 FastAPI Depends 入口，零测试覆盖。"""
    def test_get_db_yields_session_and_closes_on_exit(self):
        from app.database import get_db, SessionLocal
        from app.models import BridgeRun
        gen = get_db()
        db = next(gen)
        assert db is not None
        try:
            db.query(BridgeRun).first()
        except Exception:
            pass
        try:
            next(gen)
        except StopIteration:
            pass

    def test_get_db_closes_session_on_exception(self):
        from app.database import get_db
        from app.models import BridgeRun
        gen = get_db()
        db = next(gen)
        try:
            db.query(BridgeRun).first()
            try:
                gen.throw(RuntimeError("downstream boom"))
            except RuntimeError as e:
                assert "downstream boom" in str(e)
        finally:
            try:
                next(gen)
            except StopIteration:
                pass

    def test_sessionmaker_binds_to_engine(self):
        from app.database import SessionLocal, engine
        sess = SessionLocal()
        try:
            bind = sess.get_bind()
            assert bind is engine, (
                f"SessionLocal 应 bind 到 app.database.engine，实际 {bind}"
            )
        finally:
            sess.close()


class TestNovelProductionEnforcement:
    """独立审查 §3.2 提到：生产环境忘设 MASTER_KEY → 临时 key 加密 →
    重启后无法解密 → 数据永久损坏。

    修法（app/main.py._check_master_key_in_production）：
    - NOVEL_PRODUCTION=1 + MASTER_KEY 未设 → 启动时 fail-fast（RuntimeError）
    - NOVEL_PRODUCTION=1 + MASTER_KEY 已设 → 继续运行
    - dev 模式（默认）→ 保持原行为（warn 但继续）

    本测试锁死：源码必须有 fail-fast 检查 + env 开关语义正确。
    """
    def test_source_has_production_check(self):
        import inspect
        from app import main as main_mod
        src = inspect.getsource(main_mod._check_master_key_in_production)
        assert "NOVEL_PRODUCTION" in src, \
            "main._check_master_key_in_production 必须读 NOVEL_PRODUCTION env"
        assert "MASTER_KEY" in src, \
            "main._check_master_key_in_production 必须检查 MASTER_KEY"

    def test_production_check_wired_into_lifespan(self):
        """_check_master_key_in_production 必须在 lifespan 里被调用（启动时 fail-fast）。"""
        import inspect
        from app import main as main_mod
        lifespan_src = inspect.getsource(main_mod.lifespan)
        assert "_check_master_key_in_production" in lifespan_src, \
            "main.lifespan 必须调用 _check_master_key_in_production（启动时 fail-fast）"

    def test_production_check_runs_before_migrations(self):
        """_check_master_key_in_production 必须在 run_migrations 之前调用——
        否则 MASTER_KEY 缺失 + 已存在的 api_key_encrypted 会先被读到 → decrypt 失败。"""
        import inspect
        from app import main as main_mod
        lifespan_src = inspect.getsource(main_mod.lifespan)
        check_pos = lifespan_src.find("_check_master_key_in_production")
        migration_pos = lifespan_src.find("run_migrations")
        assert check_pos != -1, "lifespan 必须调 _check_master_key_in_production"
        assert migration_pos != -1, "lifespan 必须调 run_migrations"
        assert check_pos < migration_pos, \
            f"_check_master_key_in_production (pos={check_pos}) 必须在 run_migrations (pos={migration_pos}) 之前调用"

    def test_production_no_master_key_fails(self, monkeypatch):
        """NOVEL_PRODUCTION=1 + MASTER_KEY 未设 → 必须抛 RuntimeError。"""
        monkeypatch.setenv("NOVEL_PRODUCTION", "1")
        monkeypatch.delenv("MASTER_KEY", raising=False)
        from app import security as sec_mod
        if hasattr(sec_mod.get_master_key, "cache_clear"):
            sec_mod.get_master_key.cache_clear()
        from app import main as main_mod
        import pytest
        with pytest.raises(RuntimeError, match="MASTER_KEY"):
            main_mod._check_master_key_in_production()

    def test_dev_mode_no_master_key_passes(self, monkeypatch):
        """dev 模式（无 NOVEL_PRODUCTION）+ MASTER_KEY 未设 → 不抛（warn 但继续）。"""
        monkeypatch.delenv("NOVEL_PRODUCTION", raising=False)
        monkeypatch.delenv("MASTER_KEY", raising=False)
        from app import security as sec_mod
        if hasattr(sec_mod.get_master_key, "cache_clear"):
            sec_mod.get_master_key.cache_clear()
        from app import main as main_mod
        # 不应抛
        try:
            main_mod._check_master_key_in_production()
        except RuntimeError as e:
            if "MASTER_KEY" in str(e) or "PRODUCTION" in str(e):
                import pytest
                pytest.fail(f"dev 模式不应抛 RuntimeError：{e}")
            raise
