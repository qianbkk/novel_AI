"""Title Generator 测试 — Issue #12 修复验证。

之前 _derive_title 机械从内容首句截取，本质是正文不是标题。
修法：调 LLM 读章节内容生成真正像样的标题（4-15 字事件/冲突/转折）。

测试覆盖：
- 解析 LLM 响应：JSON / markdown fence / 裸字符串
- 标题清洗：去「第N章」前缀、去末尾标点、截断
- Fallback：内容太短 / LLM 失败
- API 端点 /projects/:id/regenerate-titles：
  - 跳过滤（only_missing）
  - 限定章节号（chapter_nos）
  - sample 模式不写库
"""
import json
import pytest

from engine.agents.title_generator import (
    generate_title_for_chapter,
    _parse_title_response,
    _sanitize_title,
    _fallback_title,
)
from app.main import app
from app.database import SessionLocal
from app.models import Project, Chapter
from fastapi.testclient import TestClient
import uuid


# ──────────────────── _parse_title_response ────────────────────


class TestParseTitleResponse:
    def test_json_strict(self):
        resp = '{"title": "U盘揭开转账证据"}'
        assert _parse_title_response(resp) == "U盘揭开转账证据"

    def test_markdown_fence_json(self):
        resp = '```json\n{"title": "查账追辖壳公司"}\n```'
        assert _parse_title_response(resp) == "查账追辖壳公司"

    def test_bare_string(self):
        resp = "陆承击碎孟浩谎言"
        assert _parse_title_response(resp) == "陆承击碎孟浩谎言"

    def test_bare_string_with_quotes(self):
        resp = '"陆承揭露王栋阴谋"'
        assert _parse_title_response(resp) == "陆承揭露王栋阴谋"

    def test_empty(self):
        assert _parse_title_response("") == ""

    def test_multiline_takes_first(self):
        resp = '{"title": "标题"}\n下面是一些解释...'
        assert _parse_title_response(resp) == "标题"


# ──────────────────── _sanitize_title ────────────────────


class TestSanitizeTitle:
    def test_strip_chapter_prefix(self):
        assert _sanitize_title("第270章 博远商贸资金外流") == "博远商贸资金外流"
    def test_strip_markdown_heading(self):
        assert _sanitize_title("# 博远商贸资金外流") == "博远商贸资金外流"
    def test_strip_trailing_punct(self):
        assert _sanitize_title("陆承揭露真相。") == "陆承揭露真相"
    def test_truncate_to_15(self):
        long = "陆承在法庭上揭露了王栋多年来的阴谋诡计并最终将他绳之以法"
        result = _sanitize_title(long)
        assert len(result) <= 15
    def test_empty(self):
        assert _sanitize_title("") == ""


# ──────────────────── _fallback_title ────────────────────


class TestFallbackTitle:
    def test_empty_content(self):
        assert _fallback_title("") == ""
    def test_pending_revision(self):
        assert _fallback_title("[待修订]\n") == "待修订章节"
    def test_first_line(self):
        content = "陆承把U盘插进电脑。\n第二行..."
        assert _fallback_title(content) == "陆承把U盘插进电脑"
    def test_skip_junk_lines(self):
        content = "【场景描述】\n陆承冲向对手。\n第三行"
        assert _fallback_title(content) == "陆承冲向对手"


# ──────────────────── generate_title_for_chapter (mocked LLM) ────────────────────


class TestGenerateForChapter:
    def test_too_short_skips_llm(self, monkeypatch):
        """内容 < 50 字（如 [待修订] 占位）→ 不调 LLM"""
        from engine.agents import title_generator as tg_module

        called = {"count": 0}

        def fake_call(self, **kwargs):
            called["count"] += 1
            return '{"title": "should not happen"}', 0.0

        class FakeRouter:
            call = fake_call

        monkeypatch.setattr(tg_module, "get_active_router", lambda: FakeRouter())
        title, cost = generate_title_for_chapter(1, "[待修订]", {})
        # 占位内容 → fallback 走 first-line extraction，会包含 [待修订]
        assert called["count"] == 0

    def test_empty_content_returns_placeholder(self):
        """空内容 → "空白章节"，让 API 知道这章没内容可生成"""
        title, cost = generate_title_for_chapter(1, "")
        assert title == "空白章节"
        assert cost == 0.0

    def test_normal_content_calls_llm(self, monkeypatch):
        """正常章节调 LLM 并解析响应"""
        from engine.agents import title_generator as tg_module

        class FakeRouter:
            def call(self, **kwargs):
                return '{"title": "U盘揭开转账证据"}', 0.002

        monkeypatch.setattr(tg_module, "get_active_router", lambda: FakeRouter())
        content = "陆承从王栋的保险柜里找到了三年的转账记录U盘，每一笔都指向离岸公司。" * 10
        title, cost = generate_title_for_chapter(270, content, {"chapter_role": "发展"})
        assert title == "U盘揭开转账证据"
        assert cost == 0.002

    def test_llm_failure_falls_back(self, monkeypatch):
        """LLM 调用失败 → fallback 不抛异常"""
        from engine.agents import title_generator as tg_module

        class FakeRouter:
            def call(self, **kwargs):
                raise RuntimeError("API down")

        monkeypatch.setattr(tg_module, "get_active_router", lambda: FakeRouter())
        content = "陆承把U盘插进电脑。\n第二段..."
        title, cost = generate_title_for_chapter(10, content)
        assert "陆承" in title  # fallback should extract first line


# ──────────────────── API 端点 ────────────────────


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def test_project_with_chapters(db):
    """创建测试项目 + 5 章。"""
    pid = "test-titlegen-" + uuid.uuid4().hex[:8]
    db.add(Project(id=pid, title="测试标题生成", genre="玄幻",
                   audience="男频", config_json={}))
    db.commit()
    chapters_data = [
        (1, "陆承把U盘里的转账记录按时间排序，每一笔都指向王栋实际控制的离岸公司。"),
        (2, "[待修订]\n"),  # 占位章节
        (3, "周芸坐在茶楼靠窗的位置，面前是一杯没喝过的碧螺春。"),
        (4, "法庭上，王栋的律师试图把证据链切碎，但陆承手里还有U盘。"),
        (5, ""),  # 空内容
    ]
    for no, content in chapters_data:
        db.add(Chapter(
            id=f"ch-{pid}-{no}",
            project_id=pid,
            chapter_no=no,
            title=f"第{no}章",  # 占位标题
            content=content,
        ))
    db.commit()
    yield pid, chapters_data
    db.query(Chapter).filter_by(project_id=pid).delete()
    db.query(Project).filter_by(id=pid).delete()
    db.commit()


class TestRegenerateTitlesAPI:
    def test_only_missing_filters_chapters(self, client, test_project_with_chapters):
        """only_missing=True 跳过已有像样标题的。"""
        pid, _ = test_project_with_chapters
        # 我们的 fixture 都设了占位 title「第N章」，should_miss=True 时应该处理全部
        r = client.post(f"/projects/{pid}/regenerate-titles",
                        json={"only_missing": True, "sample": True})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["processed"] == 5
        # 占位章节 (#2, #5) 仍然会处理（没有像样标题）
        processed_nos = [c["chapter_no"] for c in data["changes"]]
        assert 1 in processed_nos
        assert 5 in processed_nos

    def test_chapter_nos_filter(self, client, test_project_with_chapters):
        pid, _ = test_project_with_chapters
        r = client.post(f"/projects/{pid}/regenerate-titles",
                        json={"chapter_nos": [1, 3], "only_missing": False, "sample": True})
        assert r.status_code == 200
        data = r.json()
        assert data["processed"] == 2
        processed_nos = sorted(c["chapter_no"] for c in data["changes"])
        assert processed_nos == [1, 3]

    def test_sample_does_not_write_db(self, client, test_project_with_chapters, db):
        """sample=True 时 DB 不被修改。"""
        pid, _ = test_project_with_chapters
        r = client.post(f"/projects/{pid}/regenerate-titles",
                        json={"only_missing": False, "sample": True})
        assert r.status_code == 200
        # DB 还是占位 title「第N章」
        ch1 = db.query(Chapter).filter_by(project_id=pid, chapter_no=1).first()
        assert ch1.title == "第1章"

    def test_write_mode_updates_db(self, client, test_project_with_chapters, db, monkeypatch):
        """sample=False 时 DB 应被更新。"""
        from engine.agents import title_generator as tg_module

        def fake_gen(chapter_no, content, meta):
            return f"标题-{chapter_no}", 0.001

        monkeypatch.setattr(tg_module, "generate_title_for_chapter", fake_gen)

        pid, _ = test_project_with_chapters
        r = client.post(f"/projects/{pid}/regenerate-titles",
                        json={"only_missing": False, "sample": False})
        assert r.status_code == 200
        data = r.json()
        assert data["updated"] > 0
        # DB 应被更新
        ch1 = db.query(Chapter).filter_by(project_id=pid, chapter_no=1).first()
        assert "标题-1" in ch1.title

    def test_limit_truncates(self, client, test_project_with_chapters):
        pid, _ = test_project_with_chapters
        r = client.post(f"/projects/{pid}/regenerate-titles",
                        json={"limit": 2, "only_missing": False, "sample": True})
        assert r.status_code == 200
        data = r.json()
        assert data["processed"] == 2