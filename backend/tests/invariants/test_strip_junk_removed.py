"""strip-junk-headers 端点删除 (security-2026-07-13 #5)

锁定：
  - POST /projects/{id}/bridge/strip-junk-headers 端点已从 FastAPI 路由表移除
  - 返回 404 / 405 而不是 200（防止有人误以为还在工作）
  - scripts/strip_chapter_headers.py 模块本身仍可 import（CLI 用）
"""


class TestStripJunkEndpointRemoved:
    """路由表不应再有 strip-junk-headers。"""

    def test_endpoint_not_registered(self, db_bootstrap):
        from app.main import app
        routes = {r.path for r in app.routes if hasattr(r, "path")}
        matching = [r for r in routes if "strip-junk" in r.lower()]
        assert not matching, \
            f"strip-junk-headers 端点应已删除，实际路由: {matching}"

    def test_post_returns_404(self, db_bootstrap):
        """Posting 到已删除端点应 404 (FastAPI default for unknown route)."""
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        # 任意 project_id；端点不存在所以 path 不会被路由解析
        r = client.post(
            "/projects/whatever/bridge/strip-junk-headers",
            json={},
        )
        # FastAPI 对未知路径默认 404；不应是 200 或 405
        assert r.status_code in (404, 405), \
            f"已删除端点应 404/405，实际 {r.status_code}: {r.text[:200]}"

    def test_strip_chapter_headers_script_still_importable(self):
        """CLI 脚本本身保留（只是不能走 HTTP 端点调用）。"""
        import importlib
        mod = importlib.import_module("scripts.strip_chapter_headers")
        assert hasattr(mod, "main") or hasattr(mod, "strip_junk_first_lines"), \
            "scripts.strip_chapter_headers 模块结构应保留"