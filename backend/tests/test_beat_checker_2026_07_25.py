"""test_beat_checker_2026_07_25.py

战略审视 Commit 4 — beat_checker 离线工具回归测试。

不依赖 LLM，纯字符串/JSON 操作 + tempfile 模拟 <novel_ai_dir>。

详见 docs/wiki/03-Writing-Engine.md §1 M4 (扮猪吃虎/打脸节拍校验)。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from engine.tools.beat_checker import (
    check_emotion_diversity,
    check_face_slap_three_stage,
    check_hook_present,
    check_upgrade_loop,
    load_chapter_metas,
    print_report,
    run_all_checks,
    save_report,
)


def _meta(chapter_no: int, **fields) -> dict:
    """构造一个 ch_NNNN_meta.json 的内容。"""
    base = {
        "chapter_no": chapter_no,
        "chapter_role": "发展",
        "shuang_type": None,
        "ending_hook_type": "悬念钩",
        "emotion_core": "压抑",
        "emotion_intensity": 3,
        "foreshadowing_ops": [],
    }
    base.update(fields)
    return base


def _write_chapter(tdir: Path, chapter_no: int, **fields):
    """在临时目录里写 ch_NNNN_meta.json。"""
    chapters_dir = tdir / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    path = chapters_dir / f"ch_{chapter_no:04d}_meta.json"
    path.write_text(json.dumps(_meta(chapter_no, **fields), ensure_ascii=False), encoding="utf-8")


# ─── load_chapter_metas ─────────────────────────

def test_load_chapter_metas_returns_empty_for_missing_dir():
    with TemporaryDirectory() as tmp:
        result = load_chapter_metas(tmp)
        assert result == []


def test_load_chapter_metas_sorted_by_chapter_no():
    with TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        _write_chapter(tdir, 5)
        _write_chapter(tdir, 3)
        _write_chapter(tdir, 1)
        _write_chapter(tdir, 4)
        _write_chapter(tdir, 2)
        result = load_chapter_metas(tmp)
        assert [m["chapter_no"] for m in result] == [1, 2, 3, 4, 5]


def test_load_chapter_metas_skips_corrupt_json():
    with TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        _write_chapter(tdir, 1)
        # 写一个损坏的 meta
        chapters_dir = tdir / "output" / "chapters"
        (chapters_dir / "ch_0002_meta.json").write_text("{not valid json", encoding="utf-8")
        _write_chapter(tdir, 3)
        result = load_chapter_metas(tmp)
        # 损坏的 ch_0002 被跳过,只留下 1 和 3
        assert [m["chapter_no"] for m in result] == [1, 3]


# ─── check_face_slap_three_stage ─────────────────────────

def test_face_slap_three_stage_complete_pattern():
    """完整三阶段(铺垫 → 打脸 → 碾压)应在最近 N 章找到。"""
    metas = [
        _meta(1, chapter_role="铺垫", shuang_type="碾压"),
        _meta(2, chapter_role="发展", shuang_type="打脸"),
        _meta(3, chapter_role="发展", shuang_type="碾压"),
    ]
    result = check_face_slap_three_stage(metas, window=10)
    assert result["status"] == "GREEN"
    assert "完整三阶段" in result["reason"]


def test_face_slap_three_stage_missing_pressure():
    """缺中间阶段(只有铺垫和碾压,无打脸)。"""
    metas = [
        _meta(1, chapter_role="铺垫", shuang_type="碾压"),
        _meta(2, chapter_role="发展", shuang_type="升级"),  # 不是打脸/碾压
        _meta(3, chapter_role="发展", shuang_type="碾压"),
    ]
    result = check_face_slap_three_stage(metas, window=10)
    assert result["status"] == "RED"


def test_face_slap_three_stage_too_few_chapters():
    """章节数 < 3 → YELLOW（无法验证）。"""
    metas = [_meta(1), _meta(2)]
    result = check_face_slap_three_stage(metas, window=10)
    assert result["status"] == "YELLOW"


# ─── check_upgrade_loop ─────────────────────────

def test_upgrade_loop_complete_pattern():
    """完整升级循环(升级 → 反杀 + 新伏笔)。"""
    metas = [
        _meta(1, shuang_type="打脸"),
        _meta(2, shuang_type="升级"),
        _meta(3, shuang_type="碾压", foreshadowing_ops=[
            {"op": "plant", "desc": "新危机种子", "target_chapter": 10}
        ]),
    ]
    result = check_upgrade_loop(metas, window=10)
    assert result["status"] == "GREEN"


def test_upgrade_loop_missing_plant():
    """升级 → 反杀但无新伏笔 plant。"""
    metas = [
        _meta(1, shuang_type="升级"),
        _meta(2, shuang_type="碾压", foreshadowing_ops=[]),
    ]
    result = check_upgrade_loop(metas, window=10)
    assert result["status"] == "RED"


def test_upgrade_loop_missing_reverse_kill():
    """升级 → 不是反杀(下一章是升级或别的)。"""
    metas = [
        _meta(1, shuang_type="升级"),
        _meta(2, shuang_type="揭秘"),  # 不是碾压/打脸/救场
    ]
    result = check_upgrade_loop(metas, window=10)
    assert result["status"] == "RED"


# ─── check_emotion_diversity ─────────────────────────

def test_emotion_diversity_all_distinct():
    """5 章 5 种 emotion → GREEN。"""
    metas = [
        _meta(1, emotion_core="憋屈"),
        _meta(2, emotion_core="压抑"),
        _meta(3, emotion_core="爽快"),
        _meta(4, emotion_core="震惊"),
        _meta(5, emotion_core="燃"),
    ]
    result = check_emotion_diversity(metas, window=5)
    assert result["status"] == "GREEN"
    assert "5 种" in result["reason"]


def test_emotion_diversity_repetitive():
    """5 章 2 种 emotion(连续同情绪疲劳) → RED。"""
    metas = [
        _meta(1, emotion_core="压抑"),
        _meta(2, emotion_core="压抑"),
        _meta(3, emotion_core="压抑"),
        _meta(4, emotion_core="燃"),
        _meta(5, emotion_core="压抑"),
    ]
    result = check_emotion_diversity(metas, window=5)
    assert result["status"] == "RED"
    assert "唯一值仅 2 种" in result["details"][0]


def test_emotion_diversity_too_few_emotion_fields():
    """< 3 章有 emotion 字段 → YELLOW。"""
    metas = [
        _meta(1, emotion_core=""),  # 空 → 不计入
        _meta(2, emotion_core=""),
        _meta(3, emotion_core="压抑"),  # 仅 1 个有效
    ]
    result = check_emotion_diversity(metas, window=5)
    assert result["status"] == "YELLOW"


# ─── check_hook_present ─────────────────────────

def test_hook_present_all_valid():
    metas = [_meta(i, ending_hook_type=hook) for i, hook in enumerate(
        ["悬念钩", "危机钩", "信息钩", "情感钩", "反转钩", "升级钩", "对抗钩"],
        start=1,
    )]
    result = check_hook_present(metas)
    assert result["status"] == "GREEN"


def test_hook_present_invalid_type():
    metas = [
        _meta(1, ending_hook_type="悬念钩"),
        _meta(2, ending_hook_type="bad_hook"),  # 越界
    ]
    result = check_hook_present(metas)
    assert result["status"] == "YELLOW"


# ─── run_all_checks 汇总 ─────────────────────────

def test_run_all_checks_overall_status_aggregation():
    """4 个子检查的状态按 RED > YELLOW > GREEN 聚合。"""
    with TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        # 写 10 章,情绪多样但缺三阶段
        for i in range(1, 11):
            _write_chapter(tdir, i, emotion_core=["憋屈", "压抑", "爽快", "震惊", "燃"][i % 5])
        report = run_all_checks(tmp, window=10)
        # emotion_diversity 应 GREEN;face_slap_three_stage 应 RED(无完整三阶段)
        assert report["overall_status"] in ("RED", "YELLOW")
        assert "checks" in report
        assert len(report["checks"]) == 4


def test_run_all_checks_all_green():
    """完整合规 10 章应 GREEN。"""
    with TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        # 三阶段
        _write_chapter(tdir, 1, chapter_role="铺垫", shuang_type="碾压")
        _write_chapter(tdir, 2, shuang_type="打脸")
        _write_chapter(tdir, 3, shuang_type="碾压")
        # 升级循环
        _write_chapter(tdir, 4, shuang_type="升级")
        _write_chapter(tdir, 5, shuang_type="碾压", foreshadowing_ops=[
            {"op": "plant", "desc": "新危机", "target_chapter": 8}
        ])
        # 情绪多样
        _write_chapter(tdir, 6, emotion_core="甜蜜")
        _write_chapter(tdir, 7, emotion_core="震惊")
        _write_chapter(tdir, 8, emotion_core="爽快")
        _write_chapter(tdir, 9, emotion_core="虐心")
        _write_chapter(tdir, 10, emotion_core="燃")
        report = run_all_checks(tmp, window=10)
        assert report["overall_status"] == "GREEN", report


# ─── save_report ─────────────────────────

def test_save_report_writes_atomic_json():
    with TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        for i in range(1, 4):
            _write_chapter(tdir, i)
        report = run_all_checks(tmp, window=10)
        path = save_report(report, output_dir=str(tdir / "output"))
        assert Path(path).exists()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        assert "checks" in data
        assert data["total_chapters_loaded"] == 3


# ─── print_report (只检查不抛异常) ─────────────────────────

def test_print_report_does_not_raise(capsys):
    with TemporaryDirectory() as tmp:
        tdir = Path(tmp)
        for i in range(1, 4):
            _write_chapter(tdir, i)
        report = run_all_checks(tmp, window=10)
        print_report(report)  # 不应抛异常
        captured = capsys.readouterr()
        assert "节拍校验报告" in captured.out
        assert "🔴" in captured.out or "🟡" in captured.out or "🟢" in captured.out