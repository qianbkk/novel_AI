"""test_talk_questions_api_consumer_2026_08_17.py

P2-15 修复验证：talk_questions 必须有 API 消费端。

历史 bug（审计发现）：
- orchestrator.py:387 在 talk 模式下把引导性问题写到 state.talk_questions。
- 全仓 grep "talk_questions" 仅在 engine/* 出现，app/* 没有任何路由读取。
- 影响：用户在 talk 模式下拿到的问题永远看不到，talk 模式事实上无意义。

修复（任务 P2-15 2026-08-17）：
- backend/app/api/worldbuild.py 新增 _load_talk_questions_from_engine 帮助函数
  （与 _load_arc_plans_from_engine 同款，从 orchestrator_state.json 读）。
- bridge.py 新增 GET /projects/{id}/bridge/orchestrator-state/talk-questions 路由
  暴露给前端（owner 校验，沿用 _owner_check）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── 1. 帮助函数能从 orchestrator_state.json 读 talk_questions ─────────────────

def test_load_talk_questions_from_engine_helper_exists():
    """app/api/worldbuild.py 必须导出 _load_talk_questions_from_engine。"""
    from app.api import worldbuild
    assert hasattr(worldbuild, "_load_talk_questions_from_engine"), (
        "worldbuild.py 必须导出 _load_talk_questions_from_engine，"
        "用于从 orchestrator_state.json 读 talk 模式引导性问题"
    )


def test_load_talk_questions_returns_state_array(tmp_path, monkeypatch):
    """helper 读到 talk_questions 数组并返。"""
    # mock NovelAIBinding：通过 monkeypatch app.database.SessionLocal
    # （helper 内部 `from ..database import SessionLocal` 必须打到这个引用）
    import app.database as app_db
    import app.api.worldbuild as wb

    # 创建 orchestrator_state.json
    # 注意：helper 期望路径 <novel_ai_dir>/output/orchestrator_state.json
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    state = {
        "arc_plans": [],
        "talk_questions": [
            {"arc_id": 1, "questions": ["主角如何觉醒金手指？", "第一个反派是谁？"]},
            {"arc_id": 2, "questions": ["第二章主要冲突是什么？"]},
        ],
    }
    state_file = out_dir / "orchestrator_state.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    class FakeBinding:
        novel_ai_dir = str(tmp_path)
    class FakeQuery:
        def filter_by(self, **kw):
            return self
        def first(self):
            return FakeBinding()
    class FakeDB:
        def query(self, *args):
            return FakeQuery()
        def close(self):
            pass

    monkeypatch.setattr(app_db, "SessionLocal", lambda: FakeDB())

    result = wb._load_talk_questions_from_engine("test_project")
    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["arc_id"] == 1
    assert "主角如何觉醒" in result[0]["questions"][0]


def test_load_talk_questions_returns_empty_when_no_state(tmp_path, monkeypatch):
    """state.json 不存在时 helper 返 []（不抛，与 _load_arc_plans 同款）。"""
    import app.database as app_db
    import app.api.worldbuild as wb

    class FakeBinding:
        novel_ai_dir = str(tmp_path)  # tmp_path 没有 output/orchestrator_state.json
    class FakeQuery:
        def filter_by(self, **kw):
            return self
        def first(self):
            return FakeBinding()
    class FakeDB:
        def query(self, *args):
            return FakeQuery()
        def close(self):
            pass

    monkeypatch.setattr(app_db, "SessionLocal", lambda: FakeDB())

    result = wb._load_talk_questions_from_engine("test_project")
    assert result == []


def test_load_talk_questions_returns_empty_when_no_binding(monkeypatch):
    """项目没有 NovelAIBinding 时 helper 返 []（不影响前端）。"""
    import app.database as app_db
    import app.api.worldbuild as wb

    class FakeQuery:
        def filter_by(self, **kw):
            return self
        def first(self):
            return None
    class FakeDB:
        def query(self, *args):
            return FakeQuery()
        def close(self):
            pass

    monkeypatch.setattr(app_db, "SessionLocal", lambda: FakeDB())

    result = wb._load_talk_questions_from_engine("test_project")
    assert result == []


# ── 2. API 路由暴露给前端 ─────────────────────────

def test_api_route_get_talk_questions_exists():
    """backend/app/api/bridge.py 必须有 GET talk-questions 路由暴露给前端。"""
    import inspect
    from app.api import bridge

    src = inspect.getsource(bridge)
    assert "talk-questions" in src or "talk_questions" in src, (
        "bridge.py 必须暴露 talk_questions 路由（GET /projects/{id}/bridge/"
        "orchestrator-state/talk-questions 或类似），让前端能消费 talk 模式问题"
    )


def test_api_route_uses_owner_check():
    """talk_questions 路由必须做 owner 校验（与既有路由一致防越权）。"""
    import inspect
    from app.api import bridge

    src = inspect.getsource(bridge)
    # 找 talk_questions 相关的路由函数（如果存在）
    # 检查它是否调了 require_owned_project / _owner_check / _current_user_or_401
    if "talk-questions" not in src and "talk_questions" not in src:
        pytest.skip("talk_questions 路由未实现，跳过 owner 校验断言")

    # 粗略检查：路由函数附近必须含 owner 校验关键词
    # （不强求哪种 auth helper，但必须有一道）
    talk_related = src[src.find("talk"):src.find("talk") + 2000] \
        if "talk" in src else ""
    assert any(kw in talk_related for kw in (
        "_owner_check", "require_owned_project", "_current_user_or_401",
    )), (
        "talk_questions 路由必须有 owner 校验，禁止越权读取其它项目问题"
    )