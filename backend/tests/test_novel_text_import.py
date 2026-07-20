"""已有小说文本导入：切章 + API 落库。

目标链路（goal 2026-07-19 授权）：用户把整本已有小说粘贴/上传为纯文本 →
确定性按「第N章/第N回/Chapter N」标题行切分 → 复用 add_chapter 落库
（embedding + 人物标记 + 重复度检测）→ 后续再做大纲/人物/世界观反向提取。

切分器是纯函数（app.novel_import.split_novel_text），单独测；
API 测试用 api_client + 隔离 DB。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest

from tests._test_db import isolated_test_db  # noqa: F401


# ─────────────────────────────────────────────
# 纯切分器
# ─────────────────────────────────────────────
class TestSplitNovelText:
    def test_basic_three_chapters(self):
        from app.novel_import import split_novel_text
        text = (
            "第一章 风雪夜归人\n夜里下了雪。他推门进来。\n\n"
            "第二章 旧identity\n第二天早上，他发现了线索。\n\n"
            "第三章 决裂\n最后他们打了起来。\n"
        )
        parts = split_novel_text(text)
        assert [p["chapter_no"] for p in parts] == [1, 2, 3]
        assert "风雪夜归人" in parts[0]["title"]
        assert "他推门进来" in parts[0]["content"]
        assert "决裂" in parts[2]["title"]
        # 标题行本身不应重复出现在正文里
        assert "第一章" not in parts[0]["content"]

    def test_chinese_numerals_and_hui(self):
        """「第十二章」「第一百零三回」这类中文数字标题必须识别为切分点。"""
        from app.novel_import import split_novel_text
        text = (
            "第十二章 山雨欲来\n正文A。\n\n"
            "第一百零三回 大闹天宫\n正文B。\n"
        )
        parts = split_novel_text(text)
        assert len(parts) == 2
        assert "山雨欲来" in parts[0]["title"]
        assert "大闹天宫" in parts[1]["title"]

    def test_sequential_renumber_ignores_source_numbers(self):
        """章号确定性重编号（分卷小说每卷重新计数 → 直接用原号会撞
        UniqueConstraint）；原始标题保留在 title 里。"""
        from app.novel_import import split_novel_text
        text = (
            "第一章 卷一开局\n正文。\n\n"
            "第一章 卷二重新计数\n正文。\n"
        )
        parts = split_novel_text(text, start_chapter_no=5)
        assert [p["chapter_no"] for p in parts] == [5, 6]

    def test_volume_heading_not_a_chapter(self):
        """「第一卷 xxx」是卷标记，不能切出一个空章节。"""
        from app.novel_import import split_novel_text
        text = (
            "第一卷 风起云涌\n\n"
            "第一章 开端\n正文A。\n\n"
            "第二章 发展\n正文B。\n"
        )
        parts = split_novel_text(text)
        assert len(parts) == 2
        assert all("卷" not in p["title"] or "开端" in p["title"] or "发展" in p["title"]
                   for p in parts)

    def test_inline_mention_not_split(self):
        """正文段落里提到「第三章的内容」不能被误切。"""
        from app.novel_import import split_novel_text
        text = (
            "第一章 开端\n他回忆起第三章的内容，那是很久以前写的了，"
            "当时第三章还没有名字，只是一段草稿而已，写得非常长。\n\n"
            "第二章 发展\n正文B。\n"
        )
        parts = split_novel_text(text)
        assert len(parts) == 2

    def test_no_headings_returns_single_chapter(self):
        from app.novel_import import split_novel_text
        text = "这是一整段没有任何章节标记的文本。" * 20
        parts = split_novel_text(text)
        assert len(parts) == 1
        assert parts[0]["chapter_no"] == 1
        assert parts[0]["content"].strip()

    def test_long_preface_becomes_prologue_chapter(self):
        """第一个章节标题前的长前言（≥200字）应作为「楔子」保留，
        不能静默丢掉用户的内容。"""
        from app.novel_import import split_novel_text
        preface = "楔子正文，很久很久以前发生的事。" * 20  # > 200 字
        text = preface + "\n\n第一章 开端\n正文A。\n"
        parts = split_novel_text(text)
        assert len(parts) == 2
        assert "楔子" in parts[0]["title"]
        assert "很久很久以前" in parts[0]["content"]

    def test_short_junk_preface_dropped(self):
        """标题前只有书名/作者两行之类的短内容 → 不单独成章。"""
        from app.novel_import import split_novel_text
        text = "《某某小说》\n作者：某人\n\n第一章 开端\n正文A。\n"
        parts = split_novel_text(text)
        assert len(parts) == 1
        assert "开端" in parts[0]["title"]

    def test_english_chapter_heading(self):
        from app.novel_import import split_novel_text
        text = "Chapter 1 The Beginning\nbody A.\n\nChapter 2\nbody B.\n"
        parts = split_novel_text(text)
        assert len(parts) == 2


# ─────────────────────────────────────────────
# API：POST /projects/{pid}/chapters/import-text
# ─────────────────────────────────────────────
@pytest.fixture
def project_id(api_client):
    from app.database import SessionLocal
    from app.models import Project
    pid = "test-import-" + uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        db.add(Project(id=pid, title="导入测试", genre="都市", config_json={}))
        db.commit()
    finally:
        db.close()
    return pid


class TestImportTextApi:
    def test_import_and_idempotent_rerun(self, api_client, project_id):
        text = (
            "第一章 开端\n正文A，足够长的正文内容在这里。\n\n"
            "第二章 发展\n正文B，足够长的正文内容在这里。\n"
        )
        r = api_client.post(
            f"/projects/{project_id}/chapters/import-text",
            json={"text": text},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_parsed"] == 2
        assert len(body["imported"]) == 2
        assert body["skipped"] == []

        # 落库校验
        listed = api_client.get(f"/projects/{project_id}/chapters").json()
        assert [c["chapter_no"] for c in listed] == [1, 2]
        assert "开端" in listed[0]["title"]

        # 第二次导入同文本 → 全部 skip，不覆盖
        r2 = api_client.post(
            f"/projects/{project_id}/chapters/import-text",
            json={"text": text},
        )
        assert r2.status_code == 200
        body2 = r2.json()
        assert body2["imported"] == []
        assert sorted(body2["skipped"]) == [1, 2]

    def test_import_empty_text_400(self, api_client, project_id):
        r = api_client.post(
            f"/projects/{project_id}/chapters/import-text",
            json={"text": "   "},
        )
        assert r.status_code == 400

    def test_import_unknown_project_404(self, api_client):
        r = api_client.post(
            "/projects/no-such-project/chapters/import-text",
            json={"text": "第一章 x\n正文。"},
        )
        assert r.status_code == 404

    def test_start_chapter_no_appends_after_existing(self, api_client, project_id):
        """已有 1-2 章后再导入续篇：start_chapter_no=3 → 落 3、4 章。"""
        first = "第一章 A\n正文一。\n\n第二章 B\n正文二。\n"
        api_client.post(
            f"/projects/{project_id}/chapters/import-text", json={"text": first},
        )
        more = "第一章 续篇甲\n正文三。\n\n第二章 续篇乙\n正文四。\n"
        r = api_client.post(
            f"/projects/{project_id}/chapters/import-text",
            json={"text": more, "start_chapter_no": 3},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["imported"]) == 2
        listed = api_client.get(f"/projects/{project_id}/chapters").json()
        assert [c["chapter_no"] for c in listed] == [1, 2, 3, 4]
