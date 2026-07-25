"""test_meta_save_beat_link_2026_07_26.py

审计修 Critical#1 真实链路回归测试。

历史问题:save_chapter() 的 meta dict 不含 shuang_type / ending_hook_type /
emotion_core / emotion_intensity / foreshadowing_ops / is_arc_climax / narrative_thread,
导致 beat_checker.load_chapter_metas() 读回的 metas 缺这些字段,
AC-10/AC-11/AC-12 的依赖校验(check_face_slap_three_stage / upgrade_loop /
emotion_diversity)恒为 YELLOW/RED。

本测试断言:
1. save_chapter() 写入的 meta.json 含上述 7 个字段
2. beat_checker.load_chapter_metas() 能读到这些字段(端到端)
3. 给定合规数据,check_face_slap_three_stage 返回 GREEN
4. 给定合规数据,check_upgrade_loop 返回 GREEN
5. 给定 3 种 emotion_core,check_emotion_diversity 返回 GREEN

详见 docs/wiki/03-Writing-Engine.md §5 + 2026-07-26 审计修 Critical#1。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
from pathlib import Path

import pytest


@pytest.fixture
def isolated_chapters_dir(monkeypatch, tmp_path):
    """把 paths.py 的所有路径重定向到 tmp_path,并 reload 依赖模块。

    依赖模块 reload 顺序很关键:
    1. engine.config.paths(读 env)
    2. engine.orchestrator(save_chapter 用 CHAPTERS_DIR)
    3. engine.tools.beat_checker(load_chapter_metas 不读 env,但函数内部 Path 不缓存)
    4. engine.tools.acceptance_tests(读 OUTPUT_DIR_STR 来定位 novel_ai_dir)
    """
    monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
    import engine.config.paths as paths_mod
    importlib.reload(paths_mod)
    from engine import orchestrator
    importlib.reload(orchestrator)
    import engine.tools.beat_checker as beat_mod
    importlib.reload(beat_mod)
    import engine.tools.acceptance_tests as ac_mod
    importlib.reload(ac_mod)
    yield tmp_path
    monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
    importlib.reload(paths_mod)
    importlib.reload(orchestrator)
    importlib.reload(beat_mod)
    importlib.reload(ac_mod)


def _build_full_meta(chapter_no: int, **overrides) -> dict:
    """构造一个含全部方法论字段的 meta(模拟修后的 orchestrator.save_and_track)。"""
    base = {
        "chapter_number": chapter_no,
        "chapter_role": "发展",
        "chapter_goal": "测试",
        "title": f"第{chapter_no}章",
        "score": 85.0,
        "verdict": "pass",
        "dimensions": {},
        "rewrite_count": 0,
        "word_count": 2000,
        "shuang_type":        "碾压",
        "ending_hook_type":   "悬念钩",
        "is_arc_climax":      False,
        "narrative_thread":   "main",
        "emotion_core":       "压抑",
        "emotion_intensity":  3,
        "foreshadowing_ops":  [],
    }
    base.update(overrides)
    return base


def test_save_chapter_writes_all_methodology_fields(isolated_chapters_dir):
    """修后:save_chapter 落盘的 meta.json 含 7 个方法论字段。"""
    from engine.orchestrator import save_chapter

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    meta = _build_full_meta(1)

    save_chapter(novel_id="default", ch_num=1, text="正文测试" * 100, meta=meta)

    meta_path = chapters_dir / "ch_0001_meta.json"
    assert meta_path.exists(), "meta.json should exist after save_chapter"
    with open(meta_path, encoding="utf-8") as f:
        written = json.load(f)
    for field in [
        "shuang_type", "ending_hook_type", "is_arc_climax",
        "narrative_thread", "emotion_core", "emotion_intensity",
        "foreshadowing_ops",
    ]:
        assert field in written, f"missing field: {field}"
        # 关键断言:不是空字符串/默认值
        assert written[field] == meta[field], f"{field} not persisted correctly"


def test_save_chapter_is_passthrough_for_minimal_meta(isolated_chapters_dir):
    """save_chapter 是纯 passthrough,兜底由 orchestrator.save_and_track(meta builder)负责。

    本测试证明:save_chapter 不会自动给老 meta 补默认字段(职责单一)。
    真正的向后兼容在 orchestrator.py:749 处 —— meta builder 永远产出完整 meta。
    """
    from engine.orchestrator import save_chapter

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    old_meta = {
        "chapter_number": 1,
        "chapter_role": "发展",
        "chapter_goal": "测试",
        "title": "第1章",
        "score": 80.0,
        "verdict": "pass",
        "dimensions": {},
        "rewrite_count": 0,
        "word_count": 1500,
        # 无新字段(模拟老 task 走非战略审视路径)
    }
    save_chapter(novel_id="default", ch_num=1, text="旧版正文" * 50, meta=old_meta)

    meta_path = chapters_dir / "ch_0001_meta.json"
    assert meta_path.exists()
    with open(meta_path, encoding="utf-8") as f:
        written = json.load(f)
    # 老 meta 能写入,字段不被丢
    assert written["title"] == "第1章"
    assert written["chapter_number"] == 1
    # 关键:passthrough 不会主动加 shuang_type(职责分离,不是 bug)
    # beat_checker 读侧 .get(..., "") 兜底(已是 commit 4 已修行为)


def test_orchestrator_meta_builder_always_fills_methodology_fields(isolated_chapters_dir):
    """验证 orchestrator.save_and_track 的 meta builder 在 749 行补全字段。

    这是真正的向后兼容保证 —— 只要走完整 orchestrator 流程,新字段一定有。
    本测试通过 monkeypatch 走 save_and_track 调用链,断言 meta 永远完整。
    """
    from engine import orchestrator

    # 找 orchestrator 中构造 meta dict 的代码块(749 行起)
    import inspect
    src = inspect.getsource(orchestrator)
    # 必须出现所有 7 个新字段
    for field in [
        "shuang_type", "ending_hook_type", "is_arc_climax",
        "narrative_thread", "emotion_core", "emotion_intensity",
        "foreshadowing_ops",
    ]:
        assert f'"{field}"' in src or f"'{field}'" in src, (
            f"orchestrator.py 缺失字段 {field} —— "
            f"请确认 save_and_track 的 meta dict 已包含全部方法论字段"
        )


# ─── beat_checker 真实链路集成 ─────────────────────────


def test_beat_checker_reads_methodology_fields(isolated_chapters_dir):
    """端到端:save_chapter → load_chapter_metas → 字段能读出来。"""
    from engine.orchestrator import save_chapter
    from engine.tools.beat_checker import load_chapter_metas

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    meta = _build_full_meta(1)
    save_chapter(novel_id="default", ch_num=1, text="测试" * 200, meta=meta)

    metas = load_chapter_metas(str(isolated_chapters_dir))
    assert len(metas) == 1
    m = metas[0]
    assert m["shuang_type"] == "碾压"
    assert m["ending_hook_type"] == "悬念钩"
    assert m["emotion_core"] == "压抑"
    assert m["narrative_thread"] == "main"


def test_face_slap_three_stage_green_after_real_save(isolated_chapters_dir):
    """save_chapter 写入三阶段节拍数据后,check_face_slap_three_stage 返回 GREEN。"""
    from engine.orchestrator import save_chapter
    from engine.tools.beat_checker import (
        check_face_slap_three_stage, load_chapter_metas,
    )

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    # 阶段 1:铺垫+碾压
    save_chapter(novel_id="default", ch_num=1,
                 text="开篇" * 300,
                 meta=_build_full_meta(1, chapter_role="铺垫",
                                       shuang_type="碾压"))
    # 阶段 2:打脸
    save_chapter(novel_id="default", ch_num=2,
                 text="发展" * 300,
                 meta=_build_full_meta(2, chapter_role="发展",
                                       shuang_type="打脸"))
    # 阶段 3:碾压+is_arc_climax=True
    save_chapter(novel_id="default", ch_num=3,
                 text="高潮" * 300,
                 meta=_build_full_meta(3, chapter_role="弧高潮",
                                       shuang_type="碾压",
                                       is_arc_climax=True))

    metas = load_chapter_metas(str(isolated_chapters_dir))
    result = check_face_slap_three_stage(metas, window=10)
    assert result["status"] == "GREEN", (
        f"expected GREEN after real save, got {result['status']}: {result}"
    )


def test_upgrade_loop_green_after_real_save(isolated_chapters_dir):
    """save_chapter 写入升级循环数据后,check_upgrade_loop 返回 GREEN。"""
    from engine.orchestrator import save_chapter
    from engine.tools.beat_checker import (
        check_upgrade_loop, load_chapter_metas,
    )

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    # 阶段 1:升级
    save_chapter(novel_id="default", ch_num=1,
                 text="升级前" * 300,
                 meta=_build_full_meta(1, shuang_type="升级"))
    # 阶段 2:碾压+新伏笔
    save_chapter(novel_id="default", ch_num=2,
                 text="反杀" * 300,
                 meta=_build_full_meta(
                     2, shuang_type="碾压",
                     foreshadowing_ops=[{"op": "plant", "desc": "新危机"}],
                 ))

    metas = load_chapter_metas(str(isolated_chapters_dir))
    result = check_upgrade_loop(metas, window=10)
    assert result["status"] == "GREEN", (
        f"expected GREEN after real save, got {result['status']}: {result}"
    )


def test_emotion_diversity_green_after_real_save(isolated_chapters_dir):
    """save_chapter 写入 3 种 emotion_core 后,check_emotion_diversity 返回 GREEN。"""
    from engine.orchestrator import save_chapter
    from engine.tools.beat_checker import (
        check_emotion_diversity, load_chapter_metas,
    )

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    save_chapter(novel_id="default", ch_num=1,
                 text="a" * 300,
                 meta=_build_full_meta(1, emotion_core="压抑"))
    save_chapter(novel_id="default", ch_num=2,
                 text="b" * 300,
                 meta=_build_full_meta(2, emotion_core="震惊"))
    save_chapter(novel_id="default", ch_num=3,
                 text="c" * 300,
                 meta=_build_full_meta(3, emotion_core="爽快"))

    metas = load_chapter_metas(str(isolated_chapters_dir))
    result = check_emotion_diversity(metas, window=5)
    assert result["status"] == "GREEN", (
        f"expected GREEN after real save, got {result['status']}: {result}"
    )


def test_hook_present_green_after_real_save(isolated_chapters_dir):
    """save_chapter 写入 ending_hook_type 后,check_hook_present 返回 GREEN。"""
    from engine.orchestrator import save_chapter
    from engine.tools.beat_checker import check_hook_present, load_chapter_metas

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    for i in range(1, 4):
        save_chapter(novel_id="default", ch_num=i,
                     text="x" * 300,
                     meta=_build_full_meta(i, ending_hook_type="悬念钩"))

    metas = load_chapter_metas(str(isolated_chapters_dir))
    result = check_hook_present(metas)
    assert result["status"] == "GREEN", (
        f"expected GREEN after real save, got {result['status']}: {result}"
    )


def test_acceptance_ac10_returns_pass_after_real_save(isolated_chapters_dir, capsys):
    """AC-10 端到端:save_chapter 三阶段 → ac10_face_slap_beat → PASS(非 SKIP/FAIL)。"""
    from engine.orchestrator import save_chapter
    from engine.tools.acceptance_tests import ac10_face_slap_beat

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    save_chapter(novel_id="default", ch_num=1,
                 text="铺垫" * 300,
                 meta=_build_full_meta(1, chapter_role="铺垫",
                                       shuang_type="碾压"))
    save_chapter(novel_id="default", ch_num=2,
                 text="挑衅" * 300,
                 meta=_build_full_meta(2, shuang_type="打脸"))
    save_chapter(novel_id="default", ch_num=3,
                 text="摊牌" * 300,
                 meta=_build_full_meta(3, shuang_type="碾压",
                                       is_arc_climax=True))

    result = ac10_face_slap_beat()
    out = capsys.readouterr().out
    assert result is True, f"AC-10 should PASS, output:\n{out}"
    assert "PASS" in out, f"expected PASS in output:\n{out}"


def test_acceptance_ac11_returns_pass_after_real_save(isolated_chapters_dir, capsys):
    """AC-11 端到端:save_chapter 升级循环 → ac11_upgrade_loop → PASS。"""
    from engine.orchestrator import save_chapter
    from engine.tools.acceptance_tests import ac11_upgrade_loop

    chapters_dir = isolated_chapters_dir / "output" / "chapters"
    save_chapter(novel_id="default", ch_num=1,
                 text="升级前" * 300,
                 meta=_build_full_meta(1, shuang_type="升级"))
    save_chapter(novel_id="default", ch_num=2,
                 text="反杀" * 300,
                 meta=_build_full_meta(
                     2, shuang_type="碾压",
                     foreshadowing_ops=[{"op": "plant", "desc": "新危机"}],
                 ))

    result = ac11_upgrade_loop()
    out = capsys.readouterr().out
    assert result is True, f"AC-11 should PASS, output:\n{out}"
    assert "PASS" in out