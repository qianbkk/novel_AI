"""backend/tests/conftest.py — pytest 共享配置

Phase D 修复：让 pytest 从任何 cwd 都能正确收集 backend/tests/ 下的测试。

核心问题：tests/invariants/test_X.py 子包用 `from tests.X import ...`
相对导入，需要 backend/ 在 sys.path。但老 conftest 不存在，pytest
自动发现无法保证 backend/ 在 sys.path 里（取决于 invocation cwd）。

修法：在 backend/tests/ 下放 conftest.py，pytest 收集时自动执行：
  1. 把 backend/ 插入 sys.path（解决 tests.X 相对导入）
  2. 暴露 REPO_ROOT / BACKEND_ROOT 给 fixture 路径测试使用
  3. 提供 `api_client`，让 API 合同测试使用隔离数据库。
"""
from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

# Establish a process-wide safety net before any app module is imported during
# collection. Tests may override DATABASE_URL locally, but the fallback must
# never be the user's working database.
_SESSION_DB = Path(tempfile.gettempdir()) / f"novel_ai_pytest_{uuid.uuid4().hex}.sqlite"
os.environ["DATABASE_URL"] = f"sqlite:///{_SESSION_DB.as_posix()}"
os.environ["NOVEL_AI_SKIP_BACKUP"] = "1"

# ── LLM / embedding 密封（必须在任何 app.* 被 import 之前执行）──
# app/config.py 的 Settings 带 `env_file=".env"`，所以从 backend/ 跑 pytest 时
# 会**自动**读到工作区的 backend/.env —— 里面是真 key + NOVEL_LLM_PROVIDER=minimax。
# 于是 llm_router.resolve_provider() 不再返回 None，call_llm_json 走真 HTTP，
# 测试变成"打真实 LLM"：结果依赖限流/额度/网络，既慢又不确定（实测 502 / 429）。
# 单个测试文件里的 `os.environ.setdefault(...)` 挡不住这件事：settings 在第一个
# import app.config 的测试模块加载时就已实例化，后来的 setdefault 不生效。
#
# 这里在 collection 最早期把 provider 钉成 mock、把所有 key 清空。既覆盖
# NOVEL_ 前缀（app.config.Settings 走这一套），也覆盖裸名（engine/llm/router.py
# 用 os.getenv("MINIMAX_API_KEY") 之类直接读）。env 变量优先级高于 .env 文件，
# 所以无论 shell 有没有 source 过 .env，测试进程看到的都是 mock。
os.environ["NOVEL_LLM_PROVIDER"] = "mock"
os.environ["NOVEL_EMBEDDING_PROVIDER"] = "mock"
for _sealed in (
    "LLM_API_KEY", "LLM_API_BASE", "LLM_MODEL",
    "DEEPSEEK_API_KEY", "KIMI_API_KEY", "MINIMAX_API_KEY",
    "EMBEDDING_API_KEY",
    "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY", "CUSTOM_API_KEY",
):
    os.environ[_sealed] = ""
    os.environ[f"NOVEL_{_sealed}"] = ""

# 把 backend/ 插入 sys.path（让 tests.invariants 等子包可被 import）
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


# 共享 fixture（任务 08 batch 3-4）
import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _session_database_schema():
    """Create the schema on the process-local database and align subprocesses."""
    from app import models  # noqa: F401
    from app.database import Base, engine

    os.environ["DATABASE_URL"] = str(engine.url)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(scope="session", autouse=True)
def _block_outbound_llm_http():
    """密封护栏：测试进程里任何真实出站 HTTP 都必须**响亮失败**，而不是静默打真 API。

    上面的 env 密封已经让 provider 回到 mock，但那是"配置正确才生效"的防线。
    这里补一道断言式防线：谁再引入一条绕过 mock 分支的真实调用（比如新 provider
    直接 new httpx.AsyncClient、或某个测试自己把 provider 改回 minimax 却忘了
    mock transport），会立刻拿到一个明确的 RuntimeError，而不是变成一个依赖网络
    的随机失败。

    只拦截**外部主机**的真实网络请求。放行两类：
      - 内存内 transport（TestClient / ASGITransport / MockTransport）
      - localhost / 127.0.0.1（tests/invariants/test_frontend_align.py 会故意
        探测本机 8132 后端，那是有意的运行时校验，不是"偷偷打真 API"）
    """
    import httpx

    real_send = httpx.AsyncClient.send
    real_send_sync = httpx.Client.send

    _LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "testserver"}

    def _is_blocked(client, request) -> bool:
        transport = getattr(client, "_transport", None)
        if not isinstance(transport, (httpx.HTTPTransport, httpx.AsyncHTTPTransport)):
            return False
        return (request.url.host or "").lower() not in _LOCAL_HOSTS

    async def guarded_send(self, request, *args, **kwargs):
        if _is_blocked(self, request):
            raise RuntimeError(
                f"测试套件禁止真实出站 HTTP：{request.method} {request.url}。"
                "LLM/embedding 调用必须走 mock 分支或被 monkeypatch。"
            )
        return await real_send(self, request, *args, **kwargs)

    def guarded_send_sync(self, request, *args, **kwargs):
        if _is_blocked(self, request):
            raise RuntimeError(
                f"测试套件禁止真实出站 HTTP：{request.method} {request.url}。"
                "LLM/embedding 调用必须走 mock 分支或被 monkeypatch。"
            )
        return real_send_sync(self, request, *args, **kwargs)

    httpx.AsyncClient.send = guarded_send
    httpx.Client.send = guarded_send_sync
    try:
        yield
    finally:
        httpx.AsyncClient.send = real_send
        httpx.Client.send = real_send_sync


def pytest_sessionfinish(session, exitstatus):
    database = sys.modules.get("app.database")
    if database is not None:
        database.engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            Path(str(_SESSION_DB) + suffix).unlink()
        except OSError:
            pass


@pytest.fixture
def api_client(isolated_test_db):
    """FastAPI TestClient + 真隔离临时 DB（任务 08 batch 3）。

    替代 ~20 处重复的：
        def client(isolated_test_db):
            from fastapi.testclient import TestClient
            from app.main import app
            from app.database import Base, engine
            Base.metadata.create_all(bind=engine)
            with TestClient(app) as c:
                yield c

    用法：
        def test_foo(api_client):
            r = api_client.get("/auth/...")
            assert r.status_code == 200

    依赖 `isolated_test_db` → 测试用临时 SQLite，**不污染真实 backend/data**。
    yield 后自动 teardown：TestClient 关闭、engine dispose、temp 文件删除。
    """
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
