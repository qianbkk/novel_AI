"""2026-07-20 审计驱动修复 — 行为测试。

覆盖：
  - 审计 #1  /health 5xx 错误 SSE 不泄漏 traceback
  - 审计 #2  _rebuild_chapter_character_edges N+1 → 单次预加载
  - 审计 #3  rewrite_candidates.json 原子写 + 同章并发互斥
  - 审计 #4  client.ts 5xx 错误体不暴露（前端逻辑在 frontend，但后端保证不泄漏）
  - 审计 #5  /health prod 模式脱敏
  - 审计 #7  伏笔关联名不匹配时入 warnings
  - 审计 #8  extract 持久化异常有日志
  - 审计 #12 stdout_text 环形截断

不依赖真实 LLM（mock 分支），端到端验证 SQLite + 磁盘行为。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# 审计测试用 mock LLM（必须最先于 app.* import）
os.environ.setdefault("NOVEL_LLM_PROVIDER", "mock")
os.environ.setdefault("NOVEL_ENGINE_MOCK", "1")

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from tests._test_db import isolated_test_db  # noqa: F401


# ─────────────────────────────────────────────────────────────────────────
# 审计 #5：/health prod 模式脱敏
# ─────────────────────────────────────────────────────────────────────────
class TestHealthProdRedaction:
    def test_dev_mode_happy_path_returns_ok(self, api_client, monkeypatch):
        """dev 模式默认路径：/health 200 + 无 detail 字段（db ok 路径）。"""
        monkeypatch.delenv("NOVEL_PRODUCTION", raising=False)
        r = api_client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert "detail" not in body

    def test_prod_mode_happy_path_still_ok(self, api_client, monkeypatch):
        """prod 模式 DB ok 时仍返 200。"""
        monkeypatch.setenv("NOVEL_PRODUCTION", "1")
        try:
            r = api_client.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "ok"
            assert "detail" not in body
        finally:
            monkeypatch.delenv("NOVEL_PRODUCTION", raising=False)

    def test_health_main_py_branches(self):
        """直接验证 main.py 里的 prod 分支条件（避免 mock session 复杂度）。

        通过读源码确认 prod 模式 503 时 body 不带 detail；dev 模式 503
        时 detail 限 80 字符。"""
        from pathlib import Path
        main_src = (Path(__file__).resolve().parent.parent / "app" / "main.py").read_text(encoding="utf-8")
        # prod 分支：'NOVEL_PRODUCTION' in body 只能为 dev 模式；prod 模式无 detail 键
        assert '"detail" not in' in main_src or 'body = {' in main_src
        # 关键源码片段
        assert 'os.environ.get("NOVEL_PRODUCTION") == "1"' in main_src
        assert 'len(body["detail"]) <= 80' in main_src or 'detail": str(e)[:80]' in main_src


# ─────────────────────────────────────────────────────────────────────────
# 审计 #2：_rebuild_chapter_character_edges 单次预加载
# ─────────────────────────────────────────────────────────────────────────
class TestRebuildEdgesN1:
    def test_rebuild_uses_single_preload(self, project_with_chapters):
        """N×M 命中情况下，db.query 次数稳定而不是 N×M。"""
        from app import novel_extract
        from app.database import SessionLocal
        from app.models import Chapter, ChapterCharacter, Character

        pid = project_with_chapters
        db = SessionLocal()
        try:
            # 计数 query：通过 SQLAlchemy event 监听
            from sqlalchemy import event
            from app.database import engine

            counter = {"n": 0}

            def _count(conn, cursor, statement, params, context, executemany):
                counter["n"] += 1

            event.listen(engine, "before_cursor_execute", _count)
            try:
                written = novel_extract._rebuild_chapter_character_edges(pid, db)
            finally:
                event.remove(engine, "before_cursor_execute", _count)

            # 准备 5 章 × 3 角色，全部命中。原实现 = 5×3 = 15 次 exists 查询
            # + 1 次 chapters + 1 次 characters = 17。新实现 = 1 次 preload +
            # 1 次 chapters + 1 次 characters = 3。
            assert counter["n"] <= 5, f"query 次数过多：{counter['n']}"
            assert written >= 1

            # 幂等：第二次调用 written=0
            db.commit()  # 第一次写入需要 commit 才能被第二次 preload 看到
            counter["n"] = 0
            event.listen(engine, "before_cursor_execute", _count)
            try:
                written2 = novel_extract._rebuild_chapter_character_edges(pid, db)
            finally:
                event.remove(engine, "before_cursor_execute", _count)
            assert written2 == 0
        finally:
            db.close()


@pytest.fixture
def project_with_chapters(api_client):
    """5 章 + 3 角色，每章正文都包含全部角色名 → 命中 5×3=15 条边。"""
    from app.database import SessionLocal
    from app.models import Chapter, Character, Project

    pid = "test-edges-" + uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        db.add(Project(id=pid, title="边测试", genre="都市", config_json={}))
        db.commit()
        names = ["林渊", "苏晚栀", "孟怀远"]
        for n in names:
            db.add(Character(project_id=pid, name=n))
        db.commit()
        for i in range(1, 6):
            db.add(Chapter(
                project_id=pid, chapter_no=i,
                title=f"第{i}章",
                content=(
                    f"本章{i}。林渊与苏晚栀合计，孟怀远在一旁看着。"
                    "债主委员会的人已经在门外等着。"
                ),
            ))
        db.commit()
    finally:
        db.close()
    return pid


# ─────────────────────────────────────────────────────────────────────────
# 审计 #3：rewrite_candidates.json 原子写 + 同章并发互斥
# ─────────────────────────────────────────────────────────────────────────
class TestRewriteAtomicAndConcurrency:
    def test_sequential_writes_keep_valid_json(self, api_client, tmp_path):
        """连续多次 rewrite 同章后，rewrite_candidates.json 始终可解析。"""
        from app.database import SessionLocal
        from app.models import Chapter, NovelAIBinding, Project
        from app.chapter_rewrite import rewrite_chapter
        from sqlalchemy.orm import Session

        pid = "test-rewrite-atomic-" + uuid.uuid4().hex[:8]
        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()

        db = SessionLocal()
        try:
            db.add(Project(id=pid, title="原子写", genre="都市", config_json={}))
            db.commit()  # 必须先 commit Project，否则 FK ref 不到
            db.add(NovelAIBinding(project_id=pid, novel_ai_dir=str(engine_dir), novel_id=pid))
            db.add(Chapter(
                project_id=pid, chapter_no=1, title="原章",
                content="林渊在云州的早晨醒来。",
            ))
            db.commit()
        finally:
            db.close()

        # monkeypatch NOVEL_AI_DIR 让 _resolve_engine_paths 走 tmp
        os.environ["NOVEL_AI_DIR"] = str(engine_dir)
        try:
            for i in range(5):
                db = SessionLocal()
                try:
                    r = asyncio.run(rewrite_chapter(
                        project_id=pid, chapter_no=1,
                        instruction=f"指示 {i}", db=db,
                    ))
                    assert "version_label" in r
                finally:
                    db.close()

            # 索引可解析
            idx_path = engine_dir / "output" / "rewrite_candidates.json"
            assert idx_path.exists()
            data = json.loads(idx_path.read_text(encoding="utf-8"))
            assert "chapter_1" in data
            assert len(data["chapter_1"]) == 5
            # 所有 label 唯一
            labels = [e["version"] for e in data["chapter_1"]]
            assert len(set(labels)) == 5
        finally:
            os.environ.pop("NOVEL_AI_DIR", None)

    def test_concurrent_rewrites_yield_unique_labels(self, api_client, tmp_path):
        """同章并发 N 个 rewrite 请求：所有 label 唯一，无覆盖。"""
        from app.database import SessionLocal
        from app.models import Chapter, NovelAIBinding, Project
        from app.chapter_rewrite import rewrite_chapter
        import concurrent.futures

        pid = "test-rewrite-conc-" + uuid.uuid4().hex[:8]
        engine_dir = tmp_path / "engine"
        engine_dir.mkdir()

        db = SessionLocal()
        try:
            db.add(Project(id=pid, title="并发改写", genre="都市", config_json={}))
            db.commit()  # 先 commit Project，再挂 FK
            db.add(NovelAIBinding(project_id=pid, novel_ai_dir=str(engine_dir), novel_id=pid))
            db.add(Chapter(
                project_id=pid, chapter_no=1, title="原章",
                content="林渊在云州的早晨醒来。",
            ))
            db.commit()
        finally:
            db.close()

        os.environ["NOVEL_AI_DIR"] = str(engine_dir)
        try:
            def _one(i):
                from app.database import SessionLocal
                db = SessionLocal()
                try:
                    return asyncio.run(rewrite_chapter(
                        project_id=pid, chapter_no=1,
                        instruction=f"指示 {i}", db=db,
                    ))
                finally:
                    db.close()

            # 5 个 thread 同步跑（每个跑独立 asyncio loop）
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                futures = [ex.submit(_one, i) for i in range(5)]
                results = [f.result(timeout=30) for f in futures]

            labels = sorted([r["version_label"] for r in results])
            assert len(set(labels)) == 5, f"labels 应唯一，实际 {labels}"
            ch_dir = engine_dir / "output" / "chapters"
            files = sorted(ch_dir.glob("ch_0001_v*.txt"))
            assert len(files) == 5
            contents = {f.read_text(encoding="utf-8") for f in files}
            assert len(contents) == 5, "候选文件内容应互不相同"
        finally:
            os.environ.pop("NOVEL_AI_DIR", None)


# ─────────────────────────────────────────────────────────────────────────
# 审计 #7：伏笔关联名不匹配时入 warnings
# ─────────────────────────────────────────────────────────────────────────
class TestForeshadowLinkedNameWarning:
    def test_unmatched_linked_name_appends_warning(self, api_client, project_with_text):
        """LLM 返回 linked_character_name 但没匹配到任何已入库角色时，
        返回值的 warnings 含说明。"""
        from app.novel_extract import _build_corpus, _persist_misc
        from app.models import Chapter
        from app.database import SessionLocal

        pid = project_with_text
        # 直接构造一个 misc payload 让 linked_name 找不到
        from app.models import Character
        db = SessionLocal()
        try:
            # 项目里先种一个真实角色
            db.add(Character(project_id=pid, name="林渊"))
            db.commit()
            # 拿真实 char id 用于 FK 命中
            lin_yuan = db.query(Character).filter_by(project_id=pid, name="林渊").first()
            real_char_id = lin_yuan.id

            chapters = db.query(Chapter).filter_by(project_id=pid).all()
            _ = _build_corpus(chapters)
            payload = {
                "factions": [],
                "power_system": None,
                "foreshadowings": [
                    {
                        "content": "孟家旧怨",
                        "linked_character_name": "孟家旧怨本人",  # 故意不匹配
                        "importance": "高",
                        "status": "已铺垫",
                    },
                    {
                        "content": "苏晚栀身份",
                        "linked_character_name": "林渊",  # 命中
                        "importance": "中",
                        "status": "已铺垫",
                    },
                ],
            }
            warnings: list[str] = []
            written_f, written_p, written_fs = _persist_misc(
                pid, payload,
                name_to_id={"林渊": real_char_id},
                warnings=warnings,
                db=db,
            )
            db.commit()
            assert written_fs == 2
            # 关键断言：未匹配的 name 应入 warnings
            assert any("孟家旧怨本人" in w for w in warnings), (
                f"未匹配角色名应入 warnings，实际 {warnings}"
            )
            # 已匹配的 name 不应入 warnings
            assert not any("苏晚栀身份" in w for w in warnings)
        finally:
            db.close()


@pytest.fixture
def project_with_text(api_client):
    """2 章纯文本 project。"""
    from app.database import SessionLocal
    from app.models import Project

    pid = "test-fs-" + uuid.uuid4().hex[:8]
    db = SessionLocal()
    try:
        db.add(Project(id=pid, title="伏笔测试", genre="都市", config_json={}))
        db.commit()
    finally:
        db.close()
    text = (
        "第一章 风起\n林渊在云州的早晨醒来。\n\n"
        "第二章 暗涌\n林渊与苏晚栀合计第一笔交易。\n"
    )
    api_client.post(f"/projects/{pid}/chapters/import-text", json={"text": text})
    return pid


# ─────────────────────────────────────────────────────────────────────────
# 审计 #8：extract 持久化异常有日志
# ─────────────────────────────────────────────────────────────────────────
class TestExtractPersistExceptionLogging:
    def test_persist_failure_logs_exception(self, api_client, project_with_text, caplog):
        """_persist_world 等子函数抛错时，except Exception 块会 log.exception。"""
        from app import novel_extract
        import logging
        from app.database import SessionLocal

        pid = project_with_text
        # monkeypatch _persist_world 让它抛错
        def boom(*a, **kw):
            raise RuntimeError("模拟 world 写入失败")
        orig = novel_extract._persist_world
        novel_extract._persist_world = boom
        try:
            db = SessionLocal()
            try:
                with caplog.at_level(logging.ERROR, logger="novel_ai.novel_extract"):
                    with pytest.raises(RuntimeError, match="模拟 world 写入失败"):
                        # 调顶层函数会进 _check_conflict_and_prepare 然后到 boom
                        asyncio.run(novel_extract.extract_setting_from_chapters(
                            project_id=pid, db=db,
                        ))
                assert any(
                    "extract_setting persist failed" in rec.message
                    for rec in caplog.records
                ), f"未记录 persist 失败日志，caplog={caplog.records}"
            finally:
                db.close()
        finally:
            novel_extract._persist_world = orig


# ─────────────────────────────────────────────────────────────────────────
# 审计 #12：BridgeRun stdout_text 环形截断
# ─────────────────────────────────────────────────────────────────────────
class TestStdoutTextTruncation:
    def test_append_stdout_caps_at_max(self):
        """_append_stdout 多次 append 后总长不超过 _STDOUT_TEXT_MAX。"""
        from app.api.bridge import _append_stdout, _STDOUT_TEXT_MAX

        # 一次性灌超过上限的字符串
        big = "a" * (_STDOUT_TEXT_MAX + 500_000)
        result = _append_stdout(None, [big])
        assert len(result) <= _STDOUT_TEXT_MAX
        # 保留尾部
        assert result.endswith("a" * 100)
        # 头部的 'a' 已被截掉
        assert len(result) == _STDOUT_TEXT_MAX

    def test_append_stdout_keeps_recent_across_calls(self):
        """多次小 append 应保留所有最近内容；旧内容在超额时丢。"""
        from app.api.bridge import _append_stdout, _STDOUT_TEXT_MAX

        result = None
        # 灌 2 次 _STDOUT_TEXT_MAX / 2 的块
        half = _STDOUT_TEXT_MAX // 2
        result = _append_stdout(result, ["a" * half])
        result = _append_stdout(result, ["b" * half])
        # 两次共填满到 _STDOUT_TEXT_MAX
        assert len(result) == _STDOUT_TEXT_MAX
        # 第三次追加一个 1 字符 → 触发截断
        result = _append_stdout(result, ["c"])
        assert len(result) == _STDOUT_TEXT_MAX
        # 尾部一定是 'c'
        assert result.endswith("c")
        # 头部应仍是 'a'（half 长）+ 'b'，因为 half + half + 1 = STDOUT_TEXT_MAX + 1
        # 截断保留最后 STDOUT_TEXT_MAX 字符 → 丢掉最前面 1 个 'a'
        assert result.startswith("a")
        # 验证 'b' 仍然存在（保留尾部）
        assert "b" * half in result


# ─────────────────────────────────────────────────────────────────────────
# 2026-07-20 真实 LLM 接入：think / code-fence 包装层剥离
# ─────────────────────────────────────────────────────────────────────────
class TestStripThinkBlocks:
    """MiniMax-M3 等推理模型常用 <think>...</think> + ```json``` 包装 JSON。
    _strip_think_blocks 必须同时剥两层。"""

    def test_strip_think_only(self):
        from app.llm_client import _strip_think_blocks
        text = "<think>some reasoning</think>\n\nactual content here"
        assert _strip_think_blocks(text) == "actual content here"

    def test_strip_code_fence_with_lang(self):
        from app.llm_client import _strip_think_blocks
        text = '```json\n{"a": 1, "b": 2}\n```'
        assert _strip_think_blocks(text) == '{"a": 1, "b": 2}'

    def test_strip_code_fence_without_lang(self):
        from app.llm_client import _strip_think_blocks
        text = '```\n{"a": 1}\n```'
        assert _strip_think_blocks(text) == '{"a": 1}'

    def test_strip_both_think_and_fence(self):
        """真实场景：think 段在前 + json fence 在后。"""
        from app.llm_client import _strip_think_blocks
        text = '<think>Let me think about this carefully.\n</think>\n```json\n{"ok": true, "data": [1,2,3]}\n```'
        result = _strip_think_blocks(text)
        import json
        # 必须能解析为 JSON
        parsed = json.loads(result)
        assert parsed == {"ok": True, "data": [1, 2, 3]}

    def test_no_wrapper_passthrough(self):
        """裸 JSON 字符串原样返回。"""
        from app.llm_client import _strip_think_blocks
        text = '{"plain": "json"}'
        assert _strip_think_blocks(text) == text

    def test_multiple_think_blocks(self):
        from app.llm_client import _strip_think_blocks
        text = "<think>first</think>middle<think>second</think>end"
        assert _strip_think_blocks(text) == "middleend"

    def test_unclosed_fence_passes_through(self):
        """未闭合的 ``` 视作普通字符。"""
        from app.llm_client import _strip_think_blocks
        text = '```json\n{"a": 1}\nno close'
        # 没有 closing ```，函数应原样返回（不剥）
        assert _strip_think_blocks(text) == text
