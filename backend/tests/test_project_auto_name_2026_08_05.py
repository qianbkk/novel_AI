"""test_project_auto_name_2026_08_05.py

2026-08-05 清单 P05 修复：create_project 缺 title 时真调 LLM 取名，不再留空 title。

四条覆盖：
  1. 显式 title → 不应调 LLM
  2. 空 title + LLM 正常返 → 用 LLM 返回值
  3. 空 title + LLM 抛错 → fallback
  4. 空 title + LLM 返空 → fallback
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def _payload(title="", genre="科幻", main_conflict="寻找失落的舰队"):
    from app.schemas import ProjectCreate
    return ProjectCreate(
        title=title,
        genre=genre,
        audience="男频·青年向",
        config_json={"main_conflict": main_conflict, "tropes": ["系统流"]},
    )


class _FakeDb:
    """最小 Session 替身：add/commit/refresh 落到实例字典上。"""

    def __init__(self):
        self.saved = {}

    def add(self, p):
        self.saved["p"] = p

    def commit(self):
        pass

    def refresh(self, p):
        pass


def _run_create(monkeypatch, payload, *, auto_name_return=None, auto_name_raise=None):
    """替换 _ai_auto_name 让测试可控；其它副作用走最小桩。"""
    import asyncio
    from app.api.projects import create_project

    db = _FakeDb()
    monkeypatch.setattr(
        "app.api.projects.require_owned_project",
        lambda *a, **kw: MagicMock(),
    )

    async def fake_auto_name(payload):
        if auto_name_raise is not None:
            raise auto_name_raise
        return auto_name_return or ""

    monkeypatch.setattr("app.api.projects._ai_auto_name", fake_auto_name)
    asyncio.run(create_project(payload=payload, request=MagicMock(), db=db))
    return db.saved["p"]


def test_create_with_explicit_title_does_not_call_llm(monkeypatch):
    """用户传了 title → _ai_auto_name 不应被调，原样入库。"""
    called = {"count": 0}

    def boom(*a, **kw):
        called["count"] += 1
        raise AssertionError("explicit title 不应触发 _ai_auto_name")

    import asyncio
    from app.api.projects import create_project

    db = _FakeDb()
    monkeypatch.setattr(
        "app.api.projects.require_owned_project",
        lambda *a, **kw: MagicMock(),
    )
    monkeypatch.setattr("app.api.projects._ai_auto_name", boom)

    p = _payload(title="星海余烬")
    asyncio.run(create_project(payload=p, request=MagicMock(), db=db))
    assert called["count"] == 0
    assert db.saved["p"].title == "星海余烬"


def test_create_with_empty_title_uses_llm_result(monkeypatch):
    """title 空时 _ai_auto_name 被调，返回值进 DB。"""
    project = _run_create(
        monkeypatch,
        _payload(title=""),
        auto_name_return="失落舰队归来",
    )
    assert project.title == "失落舰队归来", (
        f"LLM 返回的书名应进 DB；actual={project.title!r}"
    )


def test_create_with_empty_title_and_llm_failure_falls_back(monkeypatch):
    """LLM 真抛错时 fallback 未命名-{hex}。"""
    project = _run_create(
        monkeypatch,
        _payload(title=""),
        auto_name_raise=RuntimeError("mock network error"),
    )
    assert project.title, "fallback 必须非空"
    assert re.match(r"^未命名项目-[0-9a-f]{6}$", project.title), (
        f"fallback 形如 '未命名项目-xxxxxx'；actual={project.title!r}"
    )


def test_create_with_empty_title_and_llm_empty_return_falls_back(monkeypatch):
    """LLM 返空字符串时也走 fallback（防 mock_payload={title:""} 漏过）。"""
    project = _run_create(monkeypatch, _payload(title=""), auto_name_return="")
    assert project.title
    assert re.match(r"^未命名项目-[0-9a-f]{6}$", project.title), (
        f"LLM 返空时应 fallback；actual={project.title!r}"
    )
