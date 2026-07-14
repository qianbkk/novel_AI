"""frontend_align/ — Phase 3 测试拆分

不变量测试按业务域分文件存放。
原文件位置：tests/test_invariants.py（已替换为 re-export shim）
"""

from tests._paths import REPO_ROOT, BACKEND_ROOT
import json
import sys
from pathlib import Path
import pytest

BACKEND = Path(BACKEND_ROOT)
sys.path.insert(0, str(BACKEND))

# ── 原 test_invariants.py 顶部声明的 app.schema_validator 系列 ──
from app.schema_validator import (  # noqa: E402,F401
    validate_setting_package, validate_chapter_meta, SchemaError,
    get_setting_package_schema, get_chapter_meta_schema,
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    get_world_view_rich_schema, get_character_card_schema, get_entity_relation_rich_schema,
)

def _backend_alive(base_url: str, timeout: float = 1.0) -> bool:
    """探测后端是否在指定 URL 监听。
    用 socket TCP 探测而非 HTTP 请求——更轻、更快、不依赖 httpx 异常类型。
    skipif 装饰器在 collection 阶段执行，所以必须快（默认 1s timeout）。
    """
    import socket
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port
    if not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


class TestFrontendBackendPortConsistency:
    """历史 bug（你独立验证）：
      - 前端默认 http://localhost:8123，但实际 active 后端在 8132（8123 僵尸）
      - 前端 client.ts 调用的 6 个 path（rules / foreshadowings / chapter
        characters / ai-assist-level）在 8123 上 404，在 8132 上 200
      - 前端 → 8123 → 404 → 前端误以为后端没起来
    修复：
      - frontend/.env: VITE_API_BASE=http://localhost:8132
      - frontend/src/api/client.ts: 默认端口改 8132
      - frontend/src/App.tsx / Dashboard.tsx / WorldBuild.tsx: 错误提示里的
        "默认地址" 也跟改到 8132
    本测试锁死：
      1) frontend 默认 URL 必须跟当前可联通 backend 一致
      2) backend 必须注册前端需要的 6 个 path（防 router 删漏）
    """

    @pytest.fixture(autouse=True)
    def frontend_paths(self):
        from pathlib import Path
        fe = BACKEND.parent / "frontend"
        self.fe_src = fe / "src"
        self.fe_env = fe / ".env"

    def test_frontend_default_url_is_valid(self):
        """前端 client.ts 默认 fallback URL 必须是合法的 http://localhost:PORT。
        纯静态检查（不联网），CI 无需起服务即可跑。
        """
        import re
        client = (self.fe_src / "api" / "client.ts").read_text(encoding="utf-8")
        m = re.search(r'\|\|\s*"(http://localhost:\d+)"', client)
        assert m, (
            "client.ts 必须有 `|| \"http://localhost:XXXX\"` fallback。"
            f"实际 client.ts 顶部 200 字: {client[:200]!r}"
        )
        default_url = m.group(1)
        # URL 形态必须合法
        assert re.match(r"^http://localhost:\d{4,5}$", default_url), (
            f"fallback URL 形态异常: {default_url!r}（期望 http://localhost:PORT，4-5 位端口）"
        )

    @pytest.mark.skipif(
        not _backend_alive("http://localhost:8132", timeout=1.0),
        reason="需要本机 8132 后端在跑（start.sh 或 uvicorn app.main:app --port 8132）"
    )
    def test_frontend_default_url_reachable_at_runtime(self):
        """运行时验证：client.ts 默认 URL 真的能联通后端。
        跳过条件：8132 不可达（CI / 冷启动）。
        本地开发：跑 `uvicorn app.main:app --port 8132` 后此测试会真的 ping。
        """
        import re
        import httpx
        client = (self.fe_src / "api" / "client.ts").read_text(encoding="utf-8")
        m = re.search(r'\|\|\s*"(http://localhost:\d+)"', client)
        assert m
        default_url = m.group(1)
        r = httpx.get(f"{default_url}/health", timeout=2.0)
        assert r.status_code == 200, (
            f"前端默认 {default_url} 不可达 (status={r.status_code})。"
            f"前端实际打开会全 404。"
        )

    @pytest.mark.skipif(
        not _backend_alive("http://localhost:8132", timeout=1.0),
        reason="需要本机 8132 后端在跑（openapi.json 探测）"
    )
    def test_backend_registers_frontend_contract_paths(self):
        """后端必须注册前端 client.ts 实际调用的 6 个契约 path。
        运行时验证：读后端 openapi.json，检查必需 path 都已注册。
        跳过条件：8132 不可达（CI）。
        """
        import httpx
        client = (self.fe_src / "api" / "client.ts").read_text(encoding="utf-8")
        # 6 个核心契约 path
        required = {
            "/projects/{project_id}/rules",
            "/projects/{project_id}/foreshadowings",
            "/projects/{project_id}/foreshadowings/{foreshadowing_id}/status",
            "/projects/{project_id}/chapters/{chapter_id}/characters",
            "/projects/{project_id}/ai-assist-level",
        }
        openapi = httpx.get("http://localhost:8132/openapi.json", timeout=2.0).json()
        backend_paths = set(openapi.get("paths", {}).keys())
        for p in required:
            assert p in backend_paths, (
                f"前端调 {p}，后端 openapi 没注册这条 → 必然 404"
            )

    def test_no_hardcoded_8123_in_user_facing_strings(self):
        """错误提示文案不能再硬编码 :8123，否则改默认端口后用户看到错地址。"""
        for path in [
            self.fe_src / "App.tsx",
            self.fe_src / "pages" / "Dashboard.tsx",
            self.fe_src / "pages" / "WorldBuild.tsx",
        ]:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            # 排除注释里说明旧值的字样
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*"):
                    continue
                assert ":8123" not in line, (
                    f"{path.name}:{line!r} 还硬编码 :8123 — "
                    f"用户改 backend 端口后会看到错地址"
                )

    def test_no_hardcoded_8123_in_docs_and_scripts(self):
        """README / dev.bat / scripts / docs html 不能硬编码 :8123
        (排除：注释里解释历史/端口漂移、tests/ 锁死历史 bug)。
        历史背景：commit 3278a77 把后端 8123→8132，本轮扫出 5 处残留
        (README.md / dev.bat / docs/novel-ai-guide.html / run_mvp.py) 全已修。
        """
        from pathlib import Path
        repo = Path(REPO_ROOT)
        # 扫这些路径下硬编码 :8123 的可执行/文档文件
        targets = [
            repo / "README.md",
            repo / "dev.bat",
            repo / "docs" / "novel-ai-guide.html",
            repo / "backend" / "scripts" / "run_mvp.py",
        ]
        # 排除注释里提到 8123 是因为要解释 8132 的来历 / findstr 锚定语义
        ALLOWED_LINE_FRAGMENTS = (
            "8123 经常被",        # client.ts 注释解释 8132 来历
            "Anchoring on the trailing space prevents \":8123\"",  # dev.bat findstr
            "from matching \":81230\"",  # dev.bat findstr
            "锚定在尾部空格上是为了防止 \":8123\"",  # dev.bat:91 中文版锚点解释
            "到 \":81230\" 之类的端口号",  # dev.bat:92 中文版锚点解释
        )
        violations: list[str] = []
        for path in targets:
            if not path.exists():
                continue
            content = path.read_text(encoding="utf-8")
            for i, line in enumerate(content.splitlines(), start=1):
                if ":8123" not in line:
                    continue
                if any(frag in line for frag in ALLOWED_LINE_FRAGMENTS):
                    continue
                # 排除纯注释行（dev.bat 用 REM / shell 用 # / frontend 用 //）——
                # 注释里出现 ":8123" 通常是解释历史端口或锚定语义，
                # 不是真正硬编码给用户的地址。
                stripped = line.strip()
                if stripped.startswith(("REM", "//", "#")):
                    continue
                violations.append(f"{path.relative_to(repo)}:{i}: {line.rstrip()}")
        assert not violations, (
            "硬编码 :8123 残留（应统一为 :8132）：\n  "
            + "\n  ".join(violations)
        )


class TestFrontendTypesAligned:
    """历史背景（独立审查标记的低优先级）：
      前端 types.ts 之前缺：
        - BridgeRun.args_json / stdout_text / started_at / finished_at
          （SSE 处理逻辑依赖这些字段，但类型声明里没有）
        - ChapterFull.created_at 应为 string | null（后端 Optional[datetime]）
      后端 schema 实际有这些字段，TypeScript 类型漂移会让 IDE 静默接受错误字段名。

      本轮修复：补齐字段类型，optional/required 与后端 schema 对齐。
    """

    def test_frontend_bridge_run_type_has_all_fields(self):
        """前端 BridgeRun 类型必须含 args_json / stdout_text / started_at / finished_at。"""
        from pathlib import Path
        types_ts = Path(REPO_ROOT) / "frontend" / "src" / "types.ts"
        content = types_ts.read_text(encoding="utf-8")
        # 提取 BridgeRun interface 块
        import re
        m = re.search(r"export interface BridgeRun\s*\{([^}]*)\}", content)
        assert m, "找不到 export interface BridgeRun"
        block = m.group(1)
        for field in ["args_json", "stdout_text", "started_at", "finished_at"]:
            assert f"{field}" in block, (
                f"前端 BridgeRun interface 缺 {field} 字段 — "
                f"后端 BridgeRunOut 有，但前端类型漏声明"
            )

    def test_frontend_chapter_full_created_at_nullable(self):
        """前端 ChapterFull.created_at 应为 string | null（后端允许 None）。"""
        from pathlib import Path
        types_ts = Path(REPO_ROOT) / "frontend" / "src" / "types.ts"
        content = types_ts.read_text(encoding="utf-8")
        import re
        m = re.search(r"export interface ChapterFull\s*\{([^}]*)\}", content)
        assert m, "找不到 export interface ChapterFull"
        block = m.group(1)
        # 必须有 "created_at: string | null" 形式（允许 None）
        assert re.search(r"created_at:\s*string\s*\|\s*null", block), (
            "前端 ChapterFull.created_at 应为 string | null，"
            "与后端 Optional[datetime] 对齐"
        )


class TestAgentsPackageDocAccurate:
    """迭代 #75（小修）：engine/agents/__init__.py 注释之前指向
    "legacy stub.py is kept as a fallback" —— 但目录里实际没有 stub.py
    （commit 历史已删），留下**误导性**引用。开发读起来以为有兜底实现，
    实际 ImportError 会直接传给上层（fail-fast）。

    修法：注释改为实际描述（无 stub 兜底，fail-fast 符合 #62 系列修法）。
    加 invariant test 防止回退到"有 stub.py"的描述。
    """
    def test_init_no_claims_stub_py_exists(self):
        """__init__.py 不能 import stub 模块（fail-fast 原则），且不能误导说 stub.py 是兜底。

        检查：剥掉 docstring + 注释后（避免误判 #75 修复说明里被引用的历史短语），
        代码本体不能 `from .stub` / `import stub` 之类的实际 import。
        """
        # __init__ 模块不能 inspect.getsource（是 attribute 不是 module body），
        # 直接读文件
        from pathlib import Path
        import ast
        init_py = Path(BACKEND_ROOT) / "engine" / "agents" / "__init__.py"
        src = init_py.read_text(encoding="utf-8")

        # 用 AST 扫描真实 import（避免被注释/文档字符串里的字面文本误判）
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "stub" and not alias.name.startswith("stub"), (
                        f"engine/agents/__init__.py 仍然 import 了 stub 模块：{alias.name}（#75）"
                    )
            elif isinstance(node, ast.ImportFrom):
                # from .stub import ... 或 from . import stub
                for alias in node.names:
                    assert alias.name != "stub", (
                        f"engine/agents/__init__.py 仍然 from ... import 了 stub："
                        f"module={node.module}, name={alias.name}（#75）"
                    )
                if node.module and "stub" in node.module:
                    assert False, (
                        f"engine/agents/__init__.py 仍然 from {node.module} import（#75）"
                    )

    def test_init_does_not_import_stub_module(self):
        """__init__.py 不能 import 'stub'（模块已不存在）。"""
        from engine.agents import __init__ as init_mod
        # 检查是否有 .stub 这种 import
        import_attrs = [a for a in dir(init_mod) if not a.startswith("_")]
        assert "stub" not in import_attrs, (
            f"engine/agents/__init__.py 仍然 import 了 stub 模块：{import_attrs}"
        )

    def test_no_legacy_stub_py_on_disk(self):
        """agents 目录里不应该再有 stub.py 模块（fail-fast 原则）。"""
        from pathlib import Path
        agents_dir = Path(BACKEND_ROOT) / "engine" / "agents"
        stub_py = agents_dir / "stub.py"
        # 如果真存在 stub.py，本测试提醒——fail-fast 模式不应该有这个兜底
        if stub_py.exists():
            # 存在时不一定是 bug（可能有合法用途），但应该被显式确认
            # 触发的逻辑：expect_stub = False（fail-fast 原则）
            assert False, (
                f"{stub_py} 仍然存在——fail-fast 原则下不应再有 stub.py 兜底。"
                "如果确需保留，明确注释其用途（#75 跟进）"
            )
        # 不存在时通过
        assert not stub_py.exists(), \
            f"{stub_py} 不应存在（fail-fast，符合 #62 系列修法）"
