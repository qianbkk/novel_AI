"""test_save_chapter_idempotency_2026_08_17.py

P0-3 修复验证：save_chapter 必须对已完成章节幂等。

CLAUDE.md 红线：「不得覆盖已完成章节」。

历史 bug（审计发现）：
- save_chapter 用 open("w") 覆盖写 ch_NNNN.txt
- 进程在 save_chapter 完成之后、save_state 之前崩溃，
  重启时 current_chapter 仍为 N-1，node_load_arc_tasks 重建任务，
  新 LLM 输出覆盖已完成章节。
- 用户 50 章进度可能回退到第 N-1 章，情节/角色/伏笔全损。

修复：save_chapter 入口检查 ch_NNNN.txt 是否已存在：
  - 不存在 → 正常写入
  - 已存在 + meta._overwrite=True → 显式覆盖（合法逃生通道）
  - 已存在 + 没有 _overwrite → log.warning + 跳过（保护已完成章节）
  - meta 文件同理（不修改已存在的 meta，避免数据不一致）
"""

from __future__ import annotations

import logging
import json
from pathlib import Path

import pytest


# ── 共用 fixture ─────────────────────────────────────

@pytest.fixture
def chapters_dir(tmp_path, monkeypatch):
    """把 orchestrator.CHAPTERS_DIR 重定向到 tmp_path，避免污染真实输出。"""
    import engine.orchestrator as orch
    monkeypatch.setattr(orch, "CHAPTERS_DIR", tmp_path)
    # OUTPUT_DIR 也得指向 tmp 父目录（save_chapter 不依赖 OUTPUT_DIR，
    # 但 CHAPTERS_DIR 必须有 mkdir 能力）
    return tmp_path


def _seed_chapter(dirpath: Path, ch_num: int, text: str, meta: dict | None = None) -> Path:
    """预先放一份已完成的 ch_NNNN.txt（模拟已写入的章节）。"""
    chapter_file = dirpath / f"ch_{ch_num:04d}.txt"
    chapter_file.write_text(text, encoding="utf-8")
    if meta is not None:
        meta_file = dirpath / f"ch_{ch_num:04d}_meta.json"
        meta_file.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return chapter_file


# ── 1. 已存在章节不被覆盖 ─────────────────────────

def test_save_chapter_does_not_overwrite_existing(chapters_dir, caplog):
    """ch_NNNN.txt 已存在时，再次 save_chapter 不能覆盖原文。"""
    from engine.orchestrator import save_chapter

    # 预先放一份"已确认通过"的章节
    original_text = "这是第 3 章已通过 checker 的原文（2000 字真实文本）" * 100
    _seed_chapter(chapters_dir, 3, original_text)

    # 模拟崩溃重启后的二次 save：LLM 重写了一份新文本想覆盖
    new_text = "这是 LLM 重写的第 3 章（与原文完全不同）" * 100
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.orchestrator")

    save_chapter("default", 3, new_text, {"score": 7.5, "chapter_goal": "覆盖测试"})

    # 原文必须完整保留
    chapter_file = chapters_dir / "ch_0003.txt"
    assert chapter_file.read_text(encoding="utf-8") == original_text, (
        "已存在的 ch_NNNN.txt 被覆盖，违反 CLAUDE.md「不得覆盖已完成章节」红线"
    )


def test_save_chapter_does_not_overwrite_meta_when_text_skipped(chapters_dir):
    """text 被跳过时，meta 文件也不应被改写（保证 ch_NNNN.txt 与 meta 一致）。"""
    from engine.orchestrator import save_chapter

    original_meta = {"score": 7.5, "checker_verdict": "PASS", "stable_id": "abc123"}
    _seed_chapter(chapters_dir, 3, "原文", meta=original_meta)

    new_meta = {"score": 8.5, "checker_verdict": "FAIL", "stable_id": "xyz999"}
    save_chapter("default", 3, "新文本", new_meta)

    meta_file = chapters_dir / "ch_0003_meta.json"
    loaded = json.loads(meta_file.read_text(encoding="utf-8"))
    assert loaded == original_meta, (
        f"已存在 ch_NNNN.txt 时 meta 被改写：原 {original_meta}，新 {loaded}"
    )


def test_save_chapter_logs_warning_when_skipping(chapters_dir, caplog):
    """已存在章节被跳过时，必须 log.warning（CLAUDE.md「失败要响亮」）。"""
    from engine.orchestrator import save_chapter

    _seed_chapter(chapters_dir, 5, "已存在的第 5 章原文")
    caplog.set_level(logging.WARNING, logger="novel_ai.engine.orchestrator")

    save_chapter("default", 5, "新文本", {})

    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("skip" in r.getMessage().lower() or "skip" in r.getMessage().lower() or
               "已存在" in r.getMessage() or "覆盖" in r.getMessage()
               for r in warnings), (
        f"跳过覆盖时必须 log.warning（响亮），实际: "
        f"{[r.getMessage() for r in warnings]}"
    )


# ── 2. 新章节正常写入 ─────────────────────────

def test_save_chapter_creates_new_chapter(chapters_dir):
    """ch_NNNN.txt 不存在时，正常创建。"""
    from engine.orchestrator import save_chapter

    save_chapter("default", 1, "第 1 章新内容" * 50, {"score": 7.0})

    chapter_file = chapters_dir / "ch_0001.txt"
    assert chapter_file.exists()
    content = chapter_file.read_text(encoding="utf-8")
    assert "第 1 章新内容" in content


def test_save_chapter_creates_meta_for_new_chapter(chapters_dir):
    """新章节的 meta 必须写盘（与 text 同步）。"""
    from engine.orchestrator import save_chapter

    meta = {"score": 7.2, "chapter_goal": "开局"}
    save_chapter("default", 1, "content", meta)

    meta_file = chapters_dir / "ch_0001_meta.json"
    assert meta_file.exists()
    loaded = json.loads(meta_file.read_text(encoding="utf-8"))
    assert loaded == meta


# ── 3. 显式 _overwrite 逃生通道 ─────────────────────────

def test_save_chapter_with_overwrite_flag_overwrites(chapters_dir):
    """meta._overwrite=True 时显式允许覆盖（合法逃生通道，例如手动修订后重写）。"""
    from engine.orchestrator import save_chapter

    _seed_chapter(chapters_dir, 7, "原文")
    new_text = "用户显式允许覆盖的新文本"
    save_chapter("default", 7, new_text, {"_overwrite": True})

    chapter_file = chapters_dir / "ch_0007.txt"
    assert chapter_file.read_text(encoding="utf-8") == new_text


def test_save_chapter_with_overwrite_flag_overwrites_meta(chapters_dir):
    """_overwrite=True 时 meta 也一并刷新（与新 text 保持一致）。"""
    from engine.orchestrator import save_chapter

    _seed_chapter(chapters_dir, 7, "原文", meta={"old": True})
    save_chapter("default", 7, "新文本", {"_overwrite": True, "new": True})

    meta_file = chapters_dir / "ch_0007_meta.json"
    loaded = json.loads(meta_file.read_text(encoding="utf-8"))
    assert loaded == {"_overwrite": True, "new": True}


# ── 4. 多次调用相同 ch_num 的稳定性 ─────────────────────────

def test_save_chapter_repeated_calls_keep_first_version(chapters_dir):
    """模拟崩溃重启 3 次：每次都重写同一章，最终保留第 1 次的原文。"""
    from engine.orchestrator import save_chapter

    versions = [
        ("第 1 次写的原文（第 1 版）" * 30, {"version": 1}),
        ("第 2 次崩溃后写的（第 2 版）" * 30, {"version": 2}),
        ("第 3 次崩溃后写的（第 3 版）" * 30, {"version": 3}),
    ]
    for text, meta in versions:
        save_chapter("default", 10, text, meta)

    chapter_file = chapters_dir / "ch_0010.txt"
    assert "第 1 次写的原文" in chapter_file.read_text(encoding="utf-8"), (
        f"3 次调用后首版未保留，文件: {chapter_file.read_text(encoding='utf-8')[:80]!r}"
    )
    meta_file = chapters_dir / "ch_0010_meta.json"
    assert json.loads(meta_file.read_text(encoding="utf-8"))["version"] == 1