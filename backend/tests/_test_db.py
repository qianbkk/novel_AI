"""测试用临时 SQLite 数据库 helper（任务 08 · 第一批）

之前 9 个测试文件各自复制粘贴这段样板：
  _tmp_db = NamedTemporaryFile(...)
  os.environ["DATABASE_URL"] = f"sqlite:///{...}.sqlite"
  os.environ["JWT_SECRET"] = "..."

本 helper 把样板集中为 `isolated_test_db` fixture，并被需要它的 conftest
自动注入。调用方不用操心 UUID、JWT 长度、settings 缓存失效。

约束：
- 单测一组进程（避免 session-scope 共享可变数据库）
- 自动清理 tmp 文件
- 不隐藏设置；只把样板缩小
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


def _make_isolated_db_url() -> str:
    """生成一个临时 sqlite 文件路径并写到 DATABASE_URL，返回该路径。"""
    _tmp = tempfile.NamedTemporaryFile(
        suffix=".sqlite", prefix="novel_test_db_", delete=False)
    _tmp.close()
    unique_path = f"{_tmp.name}.{_uuid.uuid4().hex[:6]}.sqlite"
    os.environ["DATABASE_URL"] = f"sqlite:///{unique_path}"
    os.environ["JWT_SECRET"] = os.environ.get("JWT_SECRET", _MIN_JWT_SECRET)
    # 设置 env 之后，强制重置 pydantic_settings 的缓存
    try:
        from app.config import Settings as _Settings
        import app.config as _cfg
        _cfg.settings = _Settings()
    except Exception:
        pass
    return unique_path


@pytest.fixture
def isolated_test_db():
    """提供一条临时 sqlite DB 的 URL，并通过 env 让 app.database 命中。

    使用：函数级别 fixture — 每个测试拿到独立 DB。
    自动 set env 与重置 settings；yield 后清理文件。
    """
    path = _make_isolated_db_url()
    yield path
    # 清理（容错：文件可能已被测试自己删除 / 还在用）
    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def isolated_test_db_engine(isolated_test_db):
    """在 isolated_test_db 之上再建立 SQLAlchemy engine / create_all。

    用于不通过 FastAPI TestClient 而是直接走 ORM 的场景。
    """
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    return engine
