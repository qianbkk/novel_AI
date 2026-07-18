"""测试用临时 SQLite 数据库 helper（任务 08 · root fix）

历史：
  - batch 1（commit ba9c58b）：只设 DATABASE_URL env，**但 app.database.engine 在模块级
    缓存（`engine = create_engine(settings.database_url, ...)`）**，导致 isolated_test_db
    实际无效 → 测试写入真实 backend/data/novel_assistant.db 污染 rotate_master_key 等
    不变量测试。已用 autouse cleanup fixture（commit 22158bb）封堵表面问题。
  - 本次 root fix：用 task 15 test_audit_integrity 验证过的"自建 engine + PRAGMA +
    替换 app.database.SessionLocal"模式，让 isolated_test_db 真隔离。
    **不修改 app/database.py**（命中 CLAUDE.md 停止条件）。

约束：
- 单测一组进程（避免 session-scope 共享可变数据库）
- 自动清理 tmp 文件
- 不修改 app 模块源码，只在测试 setup/teardown 替换 SessionLocal
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid as _uuid
from pathlib import Path

import pytest

# 兜底：导入 backend（让 conftest 没插 path 的极端情况也能工作）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


_MIN_JWT_SECRET = "test-secret-for-pytest-only-this-must-be-at-least-32-chars-long-12345"


# 已知直接 `from app.database import SessionLocal` 的模块（不是用 get_db 间接调用）。
# 它们的模块命名空间里 SessionLocal 是独立绑定，所以必须显式 patch。
_MODULES_WITH_DIRECT_SESSIONLOCAL = (
    "app.main",
    "app.auth",
    "app.api.bridge",
    "app.api.worldbuild",
    "app.api.auth",
)


def _make_temp_sqlite_path() -> str:
    """生成一个临时 sqlite 文件路径（不写文件）。"""
    _tmp = tempfile.NamedTemporaryFile(
        suffix=".sqlite", prefix="novel_test_db_", delete=False)
    _tmp.close()
    unique_path = f"{_tmp.name}.{_uuid.uuid4().hex[:6]}.sqlite"
    try:
        os.unlink(unique_path)
    except OSError:
        pass
    return unique_path


def _build_isolated_engine_and_session(path: str):
    """构造指向临时 sqlite 的 engine + SessionLocal + Base.metadata。

    与 task 15 test_audit_integrity 同模式：自建 engine，PRAGMA + 显式 import models。
    """
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    url = f"sqlite:///{path}"
    new_engine = create_engine(url, connect_args={"check_same_thread": False})

    @event.listens_for(new_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    NewSessionLocal = sessionmaker(bind=new_engine, autoflush=False, autocommit=False)

    # 显式 import models → 把表注册到 Base.metadata
    from app.database import Base
    from app import models  # noqa: F401  -- side effect: 注册表到 Base.metadata
    Base.metadata.create_all(bind=new_engine)

    return new_engine, NewSessionLocal


def _patch_sessionlocal_in_known_modules(NewSessionLocal):
    """把 5 个已知 `from app.database import SessionLocal` 模块的本地绑定也替换。

    原因：`from X import Y` 在导入时绑定 Y 的引用到本地命名空间；后续修改 `X.Y`
    不会影响已绑定的本地引用。所以必须显式 patch 每个模块的 SessionLocal 属性。
    """
    patched = []
    for mod_name in _MODULES_WITH_DIRECT_SESSIONLOCAL:
        mod = sys.modules.get(mod_name)
        if mod is None:
            # 模块未 import（测试未触发相关路由）→ 跳过
            continue
        original = getattr(mod, "SessionLocal", None)
        if original is None or original is NewSessionLocal:
            continue
        mod.SessionLocal = NewSessionLocal
        patched.append((mod_name, original))
    return patched


def _restore_sessionlocal(patched):
    for mod_name, original in patched:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            mod.SessionLocal = original


@pytest.fixture
def isolated_test_db():
    """提供一条真隔离的临时 sqlite DB（任务 08 root fix）。

    用法：
      def test_foo(isolated_test_db):
          path = isolated_test_db  # 临时 DB 路径
          # 测试代码正常 import app.* / 用 TestClient(app)，自动命中临时 DB

    实现：
      1. 生成 temp 文件路径
      2. 自建 SQLAlchemy engine 指向 temp DB
      3. monkey-patch app.database.engine 和 app.database.SessionLocal 指向新实例
      4. 同时 patch 5 个已知直接 import SessionLocal 的模块的本地绑定
      5. 设 DATABASE_URL env（少数代码直接读 os.environ）
      6. yield 路径
      7. teardown：恢复全部引用，删除 temp 文件
    """
    from app.config import Settings
    import app.config as _cfg
    import app.database as _db

    path = _make_temp_sqlite_path()

    original_engine = _db.engine
    original_session_local = _db.SessionLocal
    original_settings = _cfg.settings

    new_engine, NewSessionLocal = _build_isolated_engine_and_session(path)

    # 替换 module 引用（覆盖 get_db / Depends 等大多数代码路径）
    _db.engine = new_engine
    _db.SessionLocal = NewSessionLocal

    # 替换已知直接 import SessionLocal 的模块的本地绑定
    patched = _patch_sessionlocal_in_known_modules(NewSessionLocal)

    # 设 env + 重置 settings 缓存
    os.environ["DATABASE_URL"] = f"sqlite:///{path}"
    os.environ["JWT_SECRET"] = os.environ.get("JWT_SECRET", _MIN_JWT_SECRET)
    try:
        _cfg.settings = Settings()
    except Exception:
        pass

    yield path

    # ─── teardown ───
    try:
        new_engine.dispose()
    except Exception:
        pass
    _db.engine = original_engine
    _db.SessionLocal = original_session_local
    _restore_sessionlocal(patched)
    try:
        _cfg.settings = original_settings
    except Exception:
        pass
    for ext in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(path + ext)
        except OSError:
            pass


@pytest.fixture
def isolated_test_db_engine(isolated_test_db):
    """在 isolated_test_db 之上返回真隔离的 SQLAlchemy engine。"""
    import app.database as _db
    return _db.engine