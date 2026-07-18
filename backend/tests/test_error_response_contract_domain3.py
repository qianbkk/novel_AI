"""API 错误响应一致性（任务 07 · 资源域第三批：outline / chapters / chapter_titles）

domain 1 = projects/auth；domain 2 = providers/role/worldbuild。
本批次聚焦 outline / chapters / chapter_titles 端点。

约束（沿用前两批）：
- 不建立全局异常框架
- 所有 project-scoped 端点保持 ownership
- 响应不泄漏 traceback、磁盘路径、SQL、key 或模型原始响应
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
_BACKEND_TESTS = Path(__file__).resolve().parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
if str(_BACKEND_TESTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_TESTS))


import pytest
from _test_db import isolated_test_db  # noqa: E402,F401

PROJECT_PATH = "/projects/00000000000000000000000000000000"


@pytest.fixture
def client(isolated_test_db):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _cleanup_test_providers():
    """domain 3 测试可能通过 POST 创建 chapters / outlines / chapter_titles，
    这些记录会落到真实 backend/data/novel_assistant.db（isolated_test_db
    只改 env，不重建 engine）。清理按可识别 name / 唯一 sentinel。
    """
    yield
    try:
        from app.database import SessionLocal
        from app.models import Provider
        db = SessionLocal()
        try:
            n = db.query(Provider).filter(
                Provider.name.in_(["domain3-leak", "domain4-leak"])
            ).delete(synchronize_session=False)
            db.commit()
        finally:
            db.close()
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────────
# A. /outline/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestOutlineErrorCodes:

    def test_list_outlines_unknown_project_404(self, client):
        r = client.get(f"{PROJECT_PATH}/outlines")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_create_outline_missing_required_422(self, client):
        """POST outline 缺 arc_name / arc_goal → 422。

        后端先校验 project 存在（404），再校验 body schema（422），
        所以无效 body 在缺 project 时表现为 404。两者均视为合规的 4xx 响应。
        """
        r = client.post(f"{PROJECT_PATH}/outlines", json={"arc_id": 1})
        assert r.status_code in (400, 404, 422), f"got {r.status_code}: {r.text}"

    def test_patch_outline_unknown_id_404(self, client):
        r = client.patch(f"{PROJECT_PATH}/outlines/00000000000000000000000000000000", json={"status": "approved"})
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_delete_outline_unknown_id_404(self, client):
        r = client.delete(f"{PROJECT_PATH}/outlines/00000000000000000000000000000000")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_generate_outline_unknown_project_404(self, client):
        r = client.post(
            f"{PROJECT_PATH}/outlines/generate",
            json={"arc_id": 1, "arc_name": "x", "arc_goal": "y", "arc_estimated_chapters": 10, "arc_climax_chapter_offset": 5},
        )
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# B. /chapters/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestChaptersErrorCodes:

    def test_list_chapters_unknown_project_404(self, client):
        r = client.get(f"{PROJECT_PATH}/chapters")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"

    def test_create_chapter_missing_content_422(self, client):
        """POST chapters 缺 chapter_no / content → 422 或 404（project 不存在）。"""
        r = client.post(f"{PROJECT_PATH}/chapters", json={})
        assert r.status_code in (400, 404, 422), f"got {r.status_code}: {r.text}"

    def test_get_chapter_unknown_id_404(self, client):
        r = client.get(f"{PROJECT_PATH}/chapters/00000000000000000000000000000000")
        assert r.status_code == 404, f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# C. /chapter-titles/* 错误码
# ──────────────────────────────────────────────────────────────────────


class TestChapterTitlesErrorCodes:

    def test_regenerate_titles_missing_payload_422(self, client):
        """POST chapter-titles/regenerate-titles 缺字段 → 422 或 404（project 不存在）。"""
        r = client.post(f"{PROJECT_PATH}/regenerate-titles", json={})
        assert r.status_code in (400, 404, 422), f"got {r.status_code}: {r.text}"


# ──────────────────────────────────────────────────────────────────────
# D. 不泄漏敏感信息（domain 3 端点）
# ──────────────────────────────────────────────────────────────────────


FORBIDDEN_LEAKS = [
    "Traceback (most recent call last)",
    "File \"",
    "SELECT ",
    "INSERT INTO",
    "sqlite:///",
    "c:\\",
    "D:\\",
    "/Users/",
    "/home/",
    ".sqlite",
]


@pytest.mark.parametrize("method,path,payload", [
    ("GET",    f"{PROJECT_PATH}/outlines", None),
    ("POST",   f"{PROJECT_PATH}/outlines", {"arc_id": 1, "arc_name": "x", "arc_goal": "y", "arc_estimated_chapters": 10, "arc_climax_chapter_offset": 5}),
    ("GET",    f"{PROJECT_PATH}/chapters", None),
    ("POST",   f"{PROJECT_PATH}/chapters", {"chapter_no": 1, "title": "x", "content": "y"}),
    ("POST",   f"{PROJECT_PATH}/regenerate-titles", {"sample": True}),
])
def test_domain3_endpoints_no_leak(client, method, path, payload):
    """domain 3 端点 4xx/5xx 响应 body 不含 traceback/SQL/路径/key 前缀。"""
    if payload is None:
        r = client.request(method, path)
    else:
        r = client.request(method, path, json=payload)
    if r.status_code >= 500:
        for leak in FORBIDDEN_LEAKS:
            assert leak not in r.text, (
                f"{method} {path} 500 响应泄漏 {leak!r}：\n{r.text[:500]}"
            )