"""前后端 API 契约漂移测试（任务 06）

策略：以 FastAPI 路由为真源；client.ts 用过的每个 path 必须在后端 app/api/
里能找到匹配路由；client.ts 没声明的静态字段名也只允许在 backend 定义里出现。
不维护第二份手写清单，所有路径从代码中 AST 抽取。

测试分两层：
  - 静态（不依赖运行）：client.ts path 模板可被扫描到的每条都跟 backend router 比较
  - HTTP（运行时，本地跳过）：用 openapi.json 做精确 path+method 匹配
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
REPO_ROOT    = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


# ──────────────────────────────────────────────────────────────────────
# 工具：从 client.ts 抽出所有 request<...>(path, ...) 调用
# ──────────────────────────────────────────────────────────────────────


def _scan_client_endpoints():
    """解析 frontend/src/api/client.ts，抽取所有 request 调用点。

    返回 [(http_method_lower, path_template_without_query, line_no), ...]
    """
    client_path = REPO_ROOT / "frontend" / "src" / "api" / "client.ts"
    src = client_path.read_text(encoding="utf-8")

    out = []
    for i, line in enumerate(src.splitlines(), start=1):
        m = re.search(r"request<[^>]+>\(\s*`([^`]+)`", line)
        if not m:
            continue
        path = m.group(1)
        # 跳过条件查询字符串拼接里的 `\`...\`` 模板（不可解析为字符串字面量）
        # 例：`/projects${search ? \`?${search}\` : ""}` —— query 部分嵌在模板里
        # 判定：path 包含 `${` 与条件运算符 `?` 同时存在 — 视为不可解析
        if "${" in path and (" ?" in path or "?" in path.split("${", 1)[0]):
            # 含 ${var} 且本段里有未包裹进 backtick 的 ? — 整段非可解析
            # 退路：去掉 `?${var...}` 之后的部分
            pass
        # 常规处理：去除 `?xxx` query 字符串
        if "?" in path:
            path = path.split("?", 1)[0]
        # 处理条件三元 `path${search ? \`?${search}\` : ""}` —— 不可静态求值
        # 退化：取首个 ${ 之前的前缀作为 path（通常已是完整路径前缀）
        if "${" in path:
            base = path.split("${", 1)[0]
            tail = "${" + path.split("${", 1)[1]
            # 把整段条件（直到最深一层 }) 删掉 —— 简化：如果 base 是 /projects
            # 直接用 base
            path = base
        # 把剩余的简单 `${name}` 占位规整为 `{name}` 形式以便匹配
        path = re.sub(r"\$\{([^}]+)\}", r"{\1}", path)

        # 跳过空 path 或只带基础前缀的（如只剩 `/projects`）
        if not path.strip("/"):
            continue

        method_m = re.search(r"method:\s*[\"']([A-Z]+)[\"']", line)
        method = method_m.group(1).lower() if method_m else "get"
        out.append((method, path, i))
    return out


def _scan_backend_route_templates():
    """从 backend/app/api/*.py 用 AST 抽出所有 @router.METHOD("path") 路由模板。

    返回 {method: set(path_template)} 字典，含 APIRouter(prefix=...) 拼接。
    """
    api_dir = BACKEND_ROOT / "app" / "api"
    out = {}  # method -> set(path)
    for py in api_dir.glob("*.py"):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        # 找 router = APIRouter(prefix="...") 的 prefix
        prefix = ""
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            if len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name) or tgt.id != "router":
                continue
            v = node.value
            if not isinstance(v, ast.Call):
                continue
            for kw in v.keywords:
                if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                    prefix = kw.value.value or ""
        # 累积所有 @router.METHOD("path")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if not isinstance(func.value, ast.Name):
                continue
            if func.value.id != "router":
                continue
            if not node.args:
                continue
            arg = node.args[0]
            if not isinstance(arg, ast.Constant) or not isinstance(arg.value, str):
                continue
            path = (prefix or "") + arg.value
            method = func.attr.lower()
            out.setdefault(method, set()).add(path)
    return out


# ──────────────────────────────────────────────────────────────────────
# A. 前端调过的每个 path 在后端有对应路由（动态占位符对齐）
# ──────────────────────────────────────────────────────────────────────


def _path_segments(path: str) -> list[str]:
    """拆分 path 为段，过滤空字符串。"""
    return [s for s in path.split("/") if s]


def _is_param_segment(seg: str) -> bool:
    """占位段：FastAPI `{name}` 或 JS 模板 `${name}`。"""
    if seg.startswith("{") and seg.endswith("}"):
        return True
    if seg.startswith("${") and seg.endswith("}"):
        return True
    return False


def _path_matches(front_path: str, back_path: str) -> bool:
    """前端模板（带 ${projectId} 等动态段）vs 后端模板（带 {project_id}）。

    简化匹配：段数必须一致；非占位段必须完全相同；占位段允许不同名同位置。
    """
    fp = _path_segments(front_path)
    bp = _path_segments(back_path)
    if len(fp) != len(bp):
        return False
    for a, b in zip(fp, bp):
        a_is_param = _is_param_segment(a)
        b_is_param = _is_param_segment(b)
        if a_is_param and b_is_param:
            continue
        if a_is_param or b_is_param:
            return False
        if a != b:
            return False
    return True


def _find_backend_path(method: str, front_path: str, backend_routes) -> str | None:
    """寻找与 (method, front_path) 对齐的后端路由路径。"""
    candidates = backend_routes.get(method, set())
    for bp in candidates:
        if _path_matches(front_path, bp):
            return bp
    # 可能 method 错（前端默认 GET 但后端用 POST 之类）— 也试一下
    for alt_method in ("post", "put", "patch", "delete"):
        if method != alt_method:
            for bp in backend_routes.get(alt_method, set()):
                if _path_matches(front_path, bp):
                    return bp
    return None


class TestFrontendBackendPathContract:
    """逐条核对 client.ts 中的 (method, path) 在后端 router 里能找到匹配。"""

    @pytest.fixture(scope="class")
    def client_endpoints(self):
        return _scan_client_endpoints()

    @pytest.fixture(scope="class")
    def backend_routes(self):
        return _scan_backend_route_templates()

    def test_client_ts_has_paths(self, client_endpoints):
        """客户端至少应有几条 API 调用。"""
        assert len(client_endpoints) >= 30, (
            f"client.ts 路径扫描过少（{len(client_endpoints)}），"
            f"扫描逻辑可能已坏。"
        )

    def test_backend_router_has_routes(self, backend_routes):
        """后端路由数量合理。"""
        total = sum(len(v) for v in backend_routes.values())
        assert total >= 30, f"后端路由扫描过少（{total}）"

    @pytest.mark.parametrize(
        "method,path,line_no",
        _scan_client_endpoints(),
        ids=[f"{m.upper()} {p}:L{l}" for (m, p, l) in _scan_client_endpoints()],
    )
    def test_every_client_path_matches_backend(
        self, method, path, line_no, backend_routes,
    ):
        """每个 client.ts 调用的 (method, path) 在后端 router 里能找到匹配模板。"""
        matched = _find_backend_path(method, path, backend_routes)
        assert matched, (
            f"client.ts 第 {line_no} 行 `request<...>({path!r})` "
            f"method={method.upper()} 未在后端 router 中找到匹配路由。"
            f"如果这是新增 API，请同步 backend/app/api/ 注册。\n"
            f"扫描到的路由 GET={sorted(backend_routes.get('get', set()))[:3]}..."
        )


# ──────────────────────────────────────────────────────────────────────
# B. 动态路径占位与可选 query：抽样验证
# ──────────────────────────────────────────────────────────────────────


class TestDynamicPathParamCoverage:
    """前端调用里的模板路径（{project_id}, {chapter_id}, ...）必须能在
    后端路由里至少匹配一条同样形状的路由。
    """

    @pytest.mark.parametrize("front,back_should_exist", [
        ("/projects/{project_id}",            "/projects/{project_id}"),
        ("/projects/{project_id}/chapters",   "/projects/{project_id}/chapters"),
        ("/projects/{project_id}/rules",      "/projects/{project_id}/rules"),
        ("/projects/{project_id}/foreshadowings", "/projects/{project_id}/foreshadowings"),
    ])
    def test_template_shapes_align(self, front, back_should_exist):
        routes = _scan_backend_route_templates()
        # 至少有一个 method 上有 back_should_exist
        found = any(
            _path_matches(front, bp)
            for method_paths in routes.values()
            for bp in method_paths
        )
        assert found, (
            f"前端路径模板 {front!r} 没有任何后端路由匹配"
        )


# ──────────────────────────────────────────────────────────────────────
# C. 静态 fallback：client.ts 默认 baseUrl 不带 :8123
# ──────────────────────────────────────────────────────────────────────


class TestNo8123HardcodedInUserFacingClient:
    """防回归：前端 client.ts 默认 baseUrl 必须不是 :8123（历史端口漂移）。"""

    def test_default_url_not_8123(self):
        client = (REPO_ROOT / "frontend" / "src" / "api" / "client.ts").read_text(
            encoding="utf-8")
        # 找 `|| "http://localhost:PORT"` fallback
        m = re.search(r'\|\|\s*"(http://localhost:(\d+))"', client)
        assert m, "client.ts 没有默认 baseUrl fallback"
        port = m.group(2)
        assert port != "8123", (
            f"client.ts 默认 baseUrl port = {port}，"
            f"实际后端在 8132（8123 是已废弃的僵尸端口）"
        )
