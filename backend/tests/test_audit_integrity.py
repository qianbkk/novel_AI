"""数据完整性只读审计（任务 15）

为 audit_project.py 增加构造化 bad-data 测试：
- 重复章节号 (DB UniqueConstraint 阻挡；写测试确认阻挡生效)
- 孤儿 chapter_character（FK 约束阻挡；写测试确认约束生效）
- 空库 + 正常库不误报
- Auditor 类最小可用

约束：
- 不自动修复、删除或重写数据
- 不访问真实 backend/data
- 测试只使用临时目录（_test_db 隔离）
"""
from __future__ import annotations

import sys
import uuid as _uuid
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_BACKEND_TESTS = Path(__file__).resolve().parent
if str(_BACKEND_TESTS) not in sys.path:
    sys.path.insert(0, str(_BACKEND_TESTS))


from _test_db import isolated_test_db  # noqa: E402,F401


@pytest.fixture
def app_env(isolated_test_db):
    """提供 fresh DB；自建 engine 并启用 SQLite FK（PRAGMA foreign_keys=ON）。"""
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    # 关键：在 create_all 前导入所有 model，让 Base.metadata 知道表
    from app import models  # noqa: F401  副作用：注册所有 mapped class
    db_path = isolated_test_db
    eng = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk_pragma(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    SF = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)
    yield {"session_factory": SF, "engine": eng}
    Base.metadata.drop_all(bind=eng)


def _create_project(s, title: str = "测试项目"):
    from app.models import Project
    p = Project(
        id="p_" + _uuid.uuid4().hex[:23].ljust(23, "0"),
        title=title,
        genre="玄幻",
        config_json={"套路": "凡人修仙", "篇幅": "中篇"},
    )
    s.add(p)
    s.commit()
    return p.id


def _create_chapter(s, project_id: str, chapter_no: int,
                    title: str = "标题", content: str = "正文"):
    from app.models import Chapter
    c = Chapter(
        id="c_" + _uuid.uuid4().hex[:23].ljust(23, "0"),
        project_id=project_id,
        chapter_no=chapter_no,
        title=title,
        content=content,
    )
    s.add(c)
    s.commit()
    return c.id


def _create_character(s, project_id: str, name: str = "林尘"):
    from app.models import Character
    c = Character(
        id="ch_" + _uuid.uuid4().hex[:21].ljust(21, "0"),
        project_id=project_id,
        name=name,
        role="主角",
        detail_json={},
    )
    s.add(c)
    s.commit()
    return c.id


def _create_chapter_character(s, chapter_id: str, character_id: str):
    from app.models import ChapterCharacter
    cc = ChapterCharacter(
        id="cc_" + _uuid.uuid4().hex[:22].ljust(22, "0"),
        chapter_id=chapter_id,
        character_id=character_id,
    )
    s.add(cc)
    s.commit()
    return cc.id


# A. 重复章节号 — DB UniqueConstraint 应阻挡
class TestDuplicateChapterDbConstraint:

    def test_uniqueness_blocks_duplicates(self, app_env):
        from sqlalchemy.exc import IntegrityError
        sf = app_env["session_factory"]
        with sf() as s:
            pid = _create_project(s, "双重章节")
            _create_chapter(s, pid, 1, title="first")
            with pytest.raises(IntegrityError):
                _create_chapter(s, pid, 1, title="second")
            s.rollback()

    def test_constraint_named_correctly(self, app_env):
        from sqlalchemy import inspect
        inspector = inspect(app_env["engine"])
        uqs = inspector.get_unique_constraints("chapters")
        names = {u["name"] for u in uqs}
        assert "uq_chapters_project_chapter_no" in names


# B. 孤儿 chapter_character — DB FK 约束阻挡
class TestOrphanChapterCharacter:

    def test_orphan_blocked_by_fk(self, app_env):
        from sqlalchemy.exc import IntegrityError
        sf = app_env["session_factory"]
        with sf() as s:
            pid = _create_project(s, "FK 测试")
            cid = _create_chapter(s, pid, 1)
            with pytest.raises(IntegrityError):
                _create_chapter_character(
                    s, cid, "ghost_char_id_xxxxxxxxxxxxxxxxx")
            s.rollback()

    def test_normal_cc_has_valid_char_id(self, app_env):
        from sqlalchemy import select
        from app.models import ChapterCharacter, Character
        sf = app_env["session_factory"]
        with sf() as s:
            pid = _create_project(s, "正常")
            cid = _create_chapter(s, pid, 1)
            char_id = _create_character(s, pid, name="林尘")
            _create_chapter_character(s, cid, char_id)
        with sf() as s:
            valid = {c.id for c in s.execute(select(Character)).scalars().all()}
            ccs = s.execute(select(ChapterCharacter)).scalars().all()
            assert all(cc.character_id in valid for cc in ccs)


# C. 空 / 正常库不误报
class TestNoFalsePositives:

    def test_empty_db_no_duplicates(self, app_env):
        from sqlalchemy import select, func
        from app.models import Chapter
        with app_env["session_factory"]() as s:
            rows = s.execute(
                select(Chapter.chapter_no, func.count(Chapter.id))
                .group_by(Chapter.chapter_no)
                .having(func.count(Chapter.id) > 1)
            ).all()
            assert rows == []

    def test_one_chapter_one_char_no_orphan(self, app_env):
        from sqlalchemy import select
        from app.models import Character, ChapterCharacter
        sf = app_env["session_factory"]
        with sf() as s:
            pid = _create_project(s, "正常")
            cid = _create_chapter(s, pid, 1)
            char_id = _create_character(s, pid, name="王德顺")
            _create_chapter_character(s, cid, char_id)
        with sf() as s:
            valid = {c.id for c in s.execute(select(Character)).scalars().all()}
            orphans = [cc.character_id for cc in
                       s.execute(select(ChapterCharacter)).scalars().all()
                       if cc.character_id not in valid]
        assert orphans == []


# D. Auditor 基本结构
class TestAuditorStructure:

    def test_auditor_constructor_no_crash(self):
        from scripts.audit_project import Auditor
        a = Auditor("nonexistent_pid_for_audit_test")
        assert a.errors == []
        assert a.warnings == []

    def test_auditor_check_passes_in_default_mode(self):
        """strict=False 默认时，失败进 warnings（而非 errors）。"""
        from scripts.audit_project import Auditor
        a = Auditor("audit_x")
        a.check(True, "测试通过")
        a.check(False, "测试失败", detail="某章节有问题")
        assert any("测试通过" in p for p in a.passes)
        assert any("测试失败" in w for w in a.warnings)
        assert a.errors == []

    def test_auditor_strict_mode_promotes_fail_to_error(self):
        """strict=True 时失败进 errors。"""
        from scripts.audit_project import Auditor
        a = Auditor("audit_x", strict=True)
        a.check(False, "严格模式失败", detail="detail")
        assert any("严格模式失败" in e for e in a.errors)
        assert a.warnings == []
