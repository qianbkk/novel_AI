"""test_chapter_edit_tracker_resync.py

2026-08-06 修复（核查清单 #4）：人工编辑章节 / 采纳候选后，update_chapter_content
之前只更新 DB + 章节文件 + RAG + 人物边，**完全不调 run_tracker**。L2 memory 的
character_states / inventory / active_threads / last_chapter_ending 继续指向
编辑前的事实——100 章长篇里越往后漂移越严重，下一章 writer 按"跳过了这一章"写。

新行为：
  - update_chapter_content 接受 background_tasks：传入时挂 tracker 重跑任务到
    BackgroundTasks；传入 None 时降级为同步跑（便于测试同步观察 L2 变化）
  - 返回 dict 多一个 `tracker_resync_scheduled: bool` 字段

本测试锁死：
  1. background_tasks=None 同步路径 → L2 memory 被更新（last_updated_chapter,
     character_states 跟新正文一致）
  2. background_tasks=<BG> 异步路径 → 返回 tracker_resync_scheduled=True
  3. tracker LLM 调用失败时，update_chapter_content 仍能正常返回（不抛），
     但 log error（"失败要响亮"）
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tests._paths import REPO_ROOT, BACKEND_ROOT
import sys
from pathlib import Path

BACKEND = Path(BACKEND_ROOT)
sys.path.insert(0, str(BACKEND))

# 在这里直接 monkeypatch run_tracker，避免依赖真实 LLM（行为测试用真实 LLM，
# 不变量测试只锁协议）。
import engine.agents.tracker as tracker_mod_module  # noqa: E402


def _fake_tracker_response() -> str:
    """模拟 tracker 提取的合法 JSON。run_tracker 消费的字段必须齐全。"""
    return json.dumps({
        "chapter_summary": "（测试）主角踏入云州城门，与债主议席会首次对峙。",
        "scene_location": "云州城门 / 债主议席会",
        "world_events": [
            {"description": "主角踏入云州城门", "importance": "high"},
        ],
        "character_states": {
            "林尘": {"location": "云州", "level": "感债者", "mood": "警觉"},
            "债主议席": {"location": "云州议席", "posture": "敌对"},
        },
        "active_threads": [
            {"name": "主角重生线", "status": "active"},
            {"name": "债主围剿线", "status": "escalating"},
        ],
        "inventory": [
            {"item": "债感玉佩", "owner": "林尘", "acquired_chapter": 1},
        ],
        "new_foreshadowing": [
            {"desc": "（测试）债主议席会暗示更大阴谋", "target_chapter": 50},
        ],
        "continuity_alerts": [],
        "last_chapter_ending": "（测试）林尘在议席会会场上与钟爷首次正面交锋。",
    })


def _patch_tracker_with_mock(monkeypatch, *, fail: bool = False):
    """monkeypatch tracker 的 router 让真的 run_tracker 跑通但不调真 LLM。

    思路：跟 test_engine.py:2530 同型 —— 把 get_active_router 换成 mock_router，
    mock_router.call() 返回固定 JSON；run_tracker 仍然跑真的 parse / merge /
    save_l2 逻辑，只是 LLM response 是假的。这样测试断言覆盖的是真实代码路径。
    """
    if fail:
        # tracker LLM 失败 → router.call 抛异常
        mock_router = MagicMock()
        mock_router.call.side_effect = RuntimeError("（mock）tracker LLM 调用失败")
        import engine.agents.tracker as tracker_mod
        monkeypatch.setattr(tracker_mod, "get_active_router", lambda: mock_router)
    else:
        mock_router = MagicMock()
        mock_router.call.return_value = (_fake_tracker_response(), 0.001)
        import engine.agents.tracker as tracker_mod
        monkeypatch.setattr(tracker_mod, "get_active_router", lambda: mock_router)


def _patch_l2_io(monkeypatch):
    """monkeypatch get_l2 / save_l2 走内存 dict，避免真写 L2 文件。"""
    store: dict[str, dict] = {}

    def fake_get_l2(novel_id: str) -> dict:
        if novel_id not in store:
            store[novel_id] = {
                "hot": {},
                "cold": {},
                "constraints": {},
                "meta": {"novel_id": novel_id, "last_updated_chapter": 0},
            }
        return store[novel_id]

    def fake_save_l2(novel_id: str, memory: dict) -> None:
        store[novel_id] = memory

    import engine.memory.manager as mem_mod
    monkeypatch.setattr(mem_mod, "get_l2", fake_get_l2)
    monkeypatch.setattr(mem_mod, "save_l2", fake_save_l2)
    monkeypatch.setattr(
        "app.chapter_edit.get_l2", fake_get_l2, raising=False,
    )

    return store


def _patch_chapter_rewrite(monkeypatch):
    """不需要真正的 engine 目录。让 _resolve_engine_paths 返回 tmp 路径。"""
    tmp = MagicMock()
    tmp.__truediv__.return_value = tmp  # 任何 /a/b 链都返回同一个 mock
    tmp.exists.return_value = False
    tmp.mkdir.return_value = None
    tmp.read_text.return_value = ""
    tmp.write_text = MagicMock()
    import app.chapter_rewrite as cr_mod
    monkeypatch.setattr(cr_mod, "_resolve_engine_paths",
                        lambda project_id, db: {
                            "novel_ai_dir": "/tmp/fake",
                            "output_dir": tmp,
                            "chapters_dir": tmp,
                        })


@pytest.fixture
def project_with_chapter(db_session):
    """准备一个 project + chapter 用于 update_chapter_content 测试。"""
    from app.database import SessionLocal
    from app.models import Project, Chapter
    import secrets
    db = SessionLocal()
    try:
        pid = f"test-edit-{secrets.token_hex(8)}"
        project = Project(
            id=pid,
            title="edit-tracker-test",
            genre="玄幻",
            status="ready",
            config_json={},
        )
        db.add(project)
        db.flush()
        chapter = Chapter(
            project_id=pid,
            chapter_no=1,
            title="原标题",
            content="原内容。",
            summary="原摘要",
        )
        db.add(chapter)
        db.commit()
        db.refresh(chapter)
        yield pid, chapter.id
    finally:
        try:
            db.query(Chapter).filter_by(project_id=pid).delete()
            db.query(Project).filter_by(id=pid).delete()
            db.commit()
        except Exception:
            pass
        db.close()


class TestManualEditTrackerResync:
    """人工编辑章节后 tracker 必须被重跑（核查清单 #4 修复）。"""

    def test_sync_path_updates_l2_memory(self, monkeypatch, project_with_chapter):
        """background_tasks=None → 同步降级路径，update_chapter_content 返回时
        L2 memory 已被更新（last_updated_chapter、character_states 跟新正文一致）。"""
        project_id, chapter_id = project_with_chapter
        store = _patch_l2_io(monkeypatch)
        _patch_tracker_with_mock(monkeypatch)
        _patch_chapter_rewrite(monkeypatch)

        from app.chapter_edit import update_chapter_content
        from app.database import SessionLocal
        from app.models import Chapter

        # 拿到原 hash
        db = SessionLocal()
        try:
            chapter = db.query(Chapter).filter_by(id=chapter_id).first()
            current_hash = "x" * 64  # 实际 update_chapter_content 走 hash 校验
            # 算真实 hash
            from app.chapter_edit import chapter_revision_hash
            current_hash = chapter_revision_hash(chapter.title, chapter.content or "")
        finally:
            db.close()

        import asyncio
        result = asyncio.run(
            update_chapter_content(
                project_id=project_id,
                chapter_id=chapter_id,
                title="新标题",
                content="新内容。主角踏入云州城门，与债主议席会首次对峙。",
                expected_revision_hash=current_hash,
                db=SessionLocal(),
                background_tasks=None,  # 同步降级路径
                source="manual_edit",
            )
        )

        # 锁死的关键断言 1：L2 memory 被 tracker 更新过
        mem = store[project_id]
        assert mem["meta"]["last_updated_chapter"] == 1, (
            f"L2 meta.last_updated_chapter 应是 1；实际: {mem['meta']}"
        )
        # 锁死的关键断言 2：character_states 是 tracker 注入的，不是空 dict
        assert "林尘" in mem["hot"]["character_states"], (
            f"L2 character_states 必须包含 tracker 提取的角色；实际: "
            f"{mem['hot']['character_states']}"
        )
        # 锁死的关键断言 3：last_chapter_ending 反映了 tracker 提取的内容
        # （mock 响应里给了 "（测试）林尘在议席会会场上与钟爷首次正面交锋。"）
        ending = mem["hot"].get("last_chapter_ending", "")
        assert "议席会" in ending or "首次" in ending, (
            f"last_chapter_ending 应反映 tracker 提取的内容（来自 mock 响应）；"
            f"实际: {ending!r}"
        )
        # 锁死的关键断言 4：返回里 tracker_resync_scheduled=False（同步路径）
        assert result["tracker_resync_scheduled"] is False, (
            f"同步降级路径下 tracker_resync_scheduled 应是 False；实际: "
            f"{result.get('tracker_resync_scheduled')}"
        )

    def test_async_path_schedules_background_task(self, monkeypatch, project_with_chapter):
        """background_tasks=<BG> → 返回 tracker_resync_scheduled=True，
        BackgroundTasks 已挂上 tracker resync 任务（响应后跑）。"""
        project_id, chapter_id = project_with_chapter
        _patch_l2_io(monkeypatch)
        _patch_tracker_with_mock(monkeypatch)
        _patch_chapter_rewrite(monkeypatch)

        from app.chapter_edit import update_chapter_content
        from app.database import SessionLocal
        from app.models import Chapter

        db = SessionLocal()
        try:
            chapter = db.query(Chapter).filter_by(id=chapter_id).first()
            from app.chapter_edit import chapter_revision_hash
            current_hash = chapter_revision_hash(chapter.title, chapter.content or "")
        finally:
            db.close()

        # 模拟 FastAPI BackgroundTasks
        bg_tasks: list = []
        class FakeBG:
            def add_task(self, fn, *args, **kwargs):
                bg_tasks.append((fn, args, kwargs))

        import asyncio
        result = asyncio.run(
            update_chapter_content(
                project_id=project_id,
                chapter_id=chapter_id,
                title="新标题",
                content="新内容。",
                expected_revision_hash=current_hash,
                db=SessionLocal(),
                background_tasks=FakeBG(),
                source="manual_edit",
            )
        )

        # 锁死：返回里 tracker_resync_scheduled=True
        assert result["tracker_resync_scheduled"] is True, (
            f"异步路径下 tracker_resync_scheduled 应是 True；实际: "
            f"{result.get('tracker_resync_scheduled')}"
        )
        # 锁死：BackgroundTasks 实际挂了一个任务（_run_tracker_resync + kwargs）
        assert len(bg_tasks) == 1, (
            f"BackgroundTasks 应挂 1 个任务；实际挂了 {len(bg_tasks)} 个"
        )
        fn, args, kwargs = bg_tasks[0]
        assert kwargs["project_id"] == project_id
        assert kwargs["chapter_no"] == 1
        assert kwargs["source"] == "manual_edit"

    def test_tracker_failure_does_not_break_edit(self, monkeypatch, project_with_chapter, caplog):
        """tracker LLM 失败时 update_chapter_content 仍正常返回（"失败要响亮"：
        log error 但不抛），编辑本身的成功不应该被 tracker 失败阻塞。"""
        project_id, chapter_id = project_with_chapter
        _patch_l2_io(monkeypatch)
        _patch_tracker_with_mock(monkeypatch, fail=True)
        _patch_chapter_rewrite(monkeypatch)

        from app.chapter_edit import update_chapter_content
        from app.database import SessionLocal
        from app.models import Chapter

        db = SessionLocal()
        try:
            chapter = db.query(Chapter).filter_by(id=chapter_id).first()
            from app.chapter_edit import chapter_revision_hash
            current_hash = chapter_revision_hash(chapter.title, chapter.content or "")
        finally:
            db.close()

        import asyncio
        import logging
        caplog.set_level(logging.ERROR, logger="novel_ai.chapter_edit")
        result = asyncio.run(
            update_chapter_content(
                project_id=project_id,
                chapter_id=chapter_id,
                title="新标题",
                content="新内容。",
                expected_revision_hash=current_hash,
                db=SessionLocal(),
                background_tasks=None,  # 同步降级 → 失败会真抛
                source="manual_edit",
            )
        )

        # 锁死 1：编辑仍然成功
        assert result["source"] == "manual_edit"
        assert "新标题" in result["title"]
        # 锁死 2：tracker 失败有 log（"失败要响亮"）
        # 注意：因为 background_tasks=None 走的是同步降级路径，_run_tracker_resync
        # 抛异常会被 try/except 捕获并继续，**不抛到外层** —— 这正是我们要的：
        # 编辑不能因 tracker 失败而失败。
        err_logs = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any(
            "tracker resync FAILED" in r.getMessage() for r in err_logs
        ), f"tracker 失败应有 log error；实际 logs: {[r.getMessage() for r in err_logs]}"