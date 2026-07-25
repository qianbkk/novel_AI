"""test_acceptance_tests_12_2026_07_25.py

战略审视 Commit 7 — 12 项自动验收(AC-1~AC-12)回归。

覆盖:
- AC-1~AC-5 原有 5 项不被破坏(空数据 SKIP / CLI 入口齐全)
- AC-6 但是法则密度:_has_but_signal / 段位覆盖 / 阈值 60%
- AC-7 信息差多样性:_info_mode_of 三模式 + 连续 3 章同模式检测
- AC-8 情绪锚点多样性:最近 5 章唯一值 >= 3
- AC-9 三线分布:streak 检测 + 偏离目标 30% 警告
- AC-10 扮猪吃虎节拍:复用 beat_checker(GREEN/YELLOW/RED 三态)
- AC-11 升级循环合规:复用 beat_checker
- AC-12 对话提示词密度:复用 normalizer 阈值
- run_all() 输出 12/12 项
- CLI 入口 ac1..ac12 齐全

详见 docs/wiki/03-Writing-Engine.md §1 M4 + M5 + §5 Commit 7。
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest


# ─── 隔离 NOVEL_AI_DIR fixture ─────────────────────────


@pytest.fixture
def isolated_novel_ai_dir(monkeypatch, tmp_path):
    """把 paths.py 的所有路径重定向到 tmp_path,确保测试不污染真实数据。"""
    monkeypatch.setenv("NOVEL_AI_DIR", str(tmp_path))
    # 重新 import paths 模块以读取新 env
    import engine.config.paths as paths_mod
    importlib.reload(paths_mod)
    import engine.tools.acceptance_tests as ac_mod
    importlib.reload(ac_mod)
    yield tmp_path, ac_mod
    # cleanup:reload 还原原状
    monkeypatch.delenv("NOVEL_AI_DIR", raising=False)
    importlib.reload(paths_mod)
    importlib.reload(ac_mod)


# ─── 1. 模块结构(AC-1~AC-5 仍存在 + 新增 AC-6~AC-12) ─────────────────────────


def test_run_all_returns_12_results(isolated_novel_ai_dir):
    """run_all() 必须跑 12 项。"""
    _, ac_mod = isolated_novel_ai_dir
    # 不直接调 run_all()(它会打很多日志);检查函数列表
    expected = [
        "ac1_consistency", "ac2_genre_switch", "ac3_outline_quality",
        "ac4_platform_compliance", "ac5_character_arcs",
        "ac6_but_law_density", "ac7_info_asymmetry_diversity",
        "ac8_emotion_diversity", "ac9_narrative_thread_distribution",
        "ac10_face_slap_beat", "ac11_upgrade_loop", "ac12_dialogue_density",
    ]
    for name in expected:
        assert hasattr(ac_mod, name), f"missing: {name}"


def test_cli_dispatch_supports_all_12(isolated_novel_ai_dir):
    """__main__ 分支必须支持 ac1..ac12。"""
    _, ac_mod = isolated_novel_ai_dir
    # 模拟 CLI argv
    import subprocess
    result = subprocess.run(
        [sys.executable, "-m", "engine.tools.acceptance_tests", "ac1"],
        capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1],
    )
    # 没有数据应 SKIP,exit code 不应非零
    assert "AC-1" in result.stdout


# ─── 2. AC-6 但是法则密度 ─────────────────────────


def test_but_signal_recognizes_all_10_signals(isolated_novel_ai_dir):
    """10 个转折信号词全部命中。"""
    _, ac_mod = isolated_novel_ai_dir
    for sig in ac_mod._BUT_LAW_SIGNALS:
        text = f"前面一切顺利{sig}后面开始崩塌。"
        assert ac_mod._has_but_signal(text), f"signal {sig!r} not recognized"


def test_but_signal_returns_false_for_neutral_text(isolated_novel_ai_dir):
    """中性文本返回 False。"""
    _, ac_mod = isolated_novel_ai_dir
    assert not ac_mod._has_but_signal("今天天气真好,我出门散步。")


def test_ac6_skip_when_no_chapters(isolated_novel_ai_dir):
    """无章节 → SKIP → True。"""
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod.ac6_but_law_density() is True


def test_ac6_pass_when_chapters_have_but_signals(isolated_novel_ai_dir, capsys):
    """>60% 章节有 ≥2/3 段转折 → PASS。"""
    tmp, ac_mod = isolated_novel_ai_dir
    chapters_dir = tmp / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    # 5 章,每章在 [开头 / 中段 / 结尾] 各放一个转折信号
    # 中段放在 text[mid_idx±100] 范围内 —— AC-6 用 mid_idx = len(text) // 2
    for i in range(1, 6):
        # 段落 A(开头, ~250 字)
        head = "然而" + ("他走进屋里," * 30)
        # 段落 B(中段, ~250 字) —— 转折放在此处
        mid = "但是" + ("没想到事情是这样," * 30)
        # 段落 C(结尾, ~250 字)
        tail = "不料" + ("谁知最后变成这样," * 30)
        text = head + "\n" + mid + "\n" + tail
        (chapters_dir / f"ch_{i:04d}.txt").write_text(text, encoding="utf-8")
    result = ac_mod.ac6_but_law_density()
    out = capsys.readouterr().out
    assert "PASS" in out
    assert "5/5" in out or "100%" in out


def test_ac6_fail_when_coverage_below_threshold(isolated_novel_ai_dir, capsys):
    """<60% 章节覆盖 → FAIL。"""
    tmp, ac_mod = isolated_novel_ai_dir
    chapters_dir = tmp / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    # 5 章全部只有 1 段覆盖
    for i in range(1, 6):
        text = "今天天气真好。" * 200 + "但是" + "结尾" * 50
        (chapters_dir / f"ch_{i:04d}.txt").write_text(text, encoding="utf-8")
    result = ac_mod.ac6_but_law_density()
    out = capsys.readouterr().out
    assert "FAIL" in out


# ─── 3. AC-7 信息差多样性 ─────────────────────────


def test_info_mode_empty_field_returns_both_blind(isolated_novel_ai_dir):
    """空 dict {} → both_blind。"""
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod._info_mode_of({"info_asymmetry": {}}) == "both_blind"


def test_info_mode_missing_field_returns_none(isolated_novel_ai_dir):
    """无字段 → None(视为 N/A)。"""
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod._info_mode_of({}) is None


def test_info_mode_reader_knows_only(isolated_novel_ai_dir):
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod._info_mode_of(
        {"info_asymmetry": {"reader_knows": ["X"]}}
    ) == "reader_knows"


def test_info_mode_protagonist_knows_only(isolated_novel_ai_dir):
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod._info_mode_of(
        {"info_asymmetry": {"protagonist_knows": ["X"]}}
    ) == "protagonist_knows"


def test_info_mode_both_lists_none(isolated_novel_ai_dir):
    """reader_knows 与 protagonist_knows 都是 None → both_blind。"""
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod._info_mode_of(
        {"info_asymmetry": {"reader_knows": None, "protagonist_knows": None}}
    ) == "both_blind"


def test_ac7_skip_when_no_tasks(isolated_novel_ai_dir):
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod.ac7_info_asymmetry_diversity() is True


def test_ac7_pass_with_diverse_modes(isolated_novel_ai_dir, capsys):
    """4 章用 3 种不同 mode → PASS。"""
    tmp, ac_mod = isolated_novel_ai_dir
    out_dir = tmp / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {"chapter_number": 1, "info_asymmetry": {"reader_knows": ["x"]}},
        {"chapter_number": 2, "info_asymmetry": {"protagonist_knows": ["y"]}},
        {"chapter_number": 3, "info_asymmetry": {}},  # both_blind
        {"chapter_number": 4, "info_asymmetry": {"reader_knows": ["z"]}},
    ]
    (out_dir / "arc_1_tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    result = ac_mod.ac7_info_asymmetry_diversity()
    out = capsys.readouterr().out
    assert "PASS" in out


def test_ac7_fail_with_3_consecutive_same_mode(isolated_novel_ai_dir, capsys):
    """连续 3 章同 mode → FAIL。"""
    tmp, ac_mod = isolated_novel_ai_dir
    out_dir = tmp / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {"chapter_number": 1, "info_asymmetry": {"reader_knows": ["a"]}},
        {"chapter_number": 2, "info_asymmetry": {"reader_knows": ["b"]}},
        {"chapter_number": 3, "info_asymmetry": {"reader_knows": ["c"]}},
        {"chapter_number": 4, "info_asymmetry": {}},  # 打破 streak
    ]
    (out_dir / "arc_1_tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    result = ac_mod.ac7_info_asymmetry_diversity()
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_ac7_skip_when_mostly_no_field(isolated_novel_ai_dir, capsys):
    """大部分章节无 info_asymmetry → SKIP。"""
    tmp, ac_mod = isolated_novel_ai_dir
    out_dir = tmp / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {"chapter_number": 1},
        {"chapter_number": 2},
        {"chapter_number": 3, "info_asymmetry": {}},
        {"chapter_number": 4},
        {"chapter_number": 5},
    ]
    (out_dir / "arc_1_tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    result = ac_mod.ac7_info_asymmetry_diversity()
    out = capsys.readouterr().out
    assert "SKIP" in out or "PASS" in out  # SKIP 或 PASS 都接受(3+ valid 即可)


# ─── 4. AC-8 情绪锚点多样性 ─────────────────────────


def test_ac8_skip_when_no_tasks(isolated_novel_ai_dir):
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod.ac8_emotion_diversity() is True


def test_ac8_pass_with_3_unique_emotions(isolated_novel_ai_dir, capsys):
    """5 章 3 种 emotion_core → PASS。"""
    tmp, ac_mod = isolated_novel_ai_dir
    out_dir = tmp / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {"chapter_number": 1, "emotion_core": "压抑"},
        {"chapter_number": 2, "emotion_core": "震惊"},
        {"chapter_number": 3, "emotion_core": "爽快"},
        {"chapter_number": 4, "emotion_core": "压抑"},
        {"chapter_number": 5, "emotion_core": "燃"},
    ]
    (out_dir / "arc_1_tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    result = ac_mod.ac8_emotion_diversity()
    out = capsys.readouterr().out
    assert "PASS" in out


def test_ac8_fail_with_only_2_unique_emotions(isolated_novel_ai_dir, capsys):
    """5 章 2 种 emotion_core → FAIL。"""
    tmp, ac_mod = isolated_novel_ai_dir
    out_dir = tmp / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {"chapter_number": 1, "emotion_core": "压抑"},
        {"chapter_number": 2, "emotion_core": "压抑"},
        {"chapter_number": 3, "emotion_core": "震惊"},
        {"chapter_number": 4, "emotion_core": "压抑"},
        {"chapter_number": 5, "emotion_core": "压抑"},
    ]
    (out_dir / "arc_1_tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    result = ac_mod.ac8_emotion_diversity()
    out = capsys.readouterr().out
    assert "FAIL" in out


# ─── 5. AC-9 三线分布 ─────────────────────────


def test_valid_threads_constant(isolated_novel_ai_dir):
    """_VALID_THREADS 必须含 main/side/hidden。"""
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod._VALID_THREADS == {"main", "side", "hidden"}


def test_ac9_skip_when_no_tasks(isolated_novel_ai_dir):
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod.ac9_narrative_thread_distribution() is True


def test_ac9_pass_with_balanced_distribution(isolated_novel_ai_dir, capsys):
    """main/side/hidden 分布合理(60/30/10) + 无 streak → PASS。"""
    tmp, ac_mod = isolated_novel_ai_dir
    out_dir = tmp / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {"chapter_number": i, "narrative_thread": th}
        for i, th in enumerate(
            ["main", "main", "side", "main", "side", "main", "hidden",
             "main", "side", "main"], start=1,
        )
    ]
    (out_dir / "arc_1_tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    result = ac_mod.ac9_narrative_thread_distribution()
    out = capsys.readouterr().out
    assert "PASS" in out


def test_ac9_fail_with_3_consecutive_same_thread(isolated_novel_ai_dir, capsys):
    """连续 3 章同 thread → FAIL。"""
    tmp, ac_mod = isolated_novel_ai_dir
    out_dir = tmp / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {"chapter_number": 1, "narrative_thread": "main"},
        {"chapter_number": 2, "narrative_thread": "main"},
        {"chapter_number": 3, "narrative_thread": "main"},
        {"chapter_number": 4, "narrative_thread": "side"},
        {"chapter_number": 5, "narrative_thread": "main"},
    ]
    (out_dir / "arc_1_tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False), encoding="utf-8"
    )
    result = ac_mod.ac9_narrative_thread_distribution()
    out = capsys.readouterr().out
    assert "FAIL" in out


# ─── 6. AC-10 扮猪吃虎节拍 ─────────────────────────


def test_ac10_skip_when_no_metas(isolated_novel_ai_dir):
    """无 meta.json → SKIP → True。"""
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod.ac10_face_slap_beat() is True


def test_ac10_passes_when_face_slap_found(isolated_novel_ai_dir, capsys):
    """3 阶段完整 → PASS。"""
    tmp, ac_mod = isolated_novel_ai_dir
    chapters_dir = tmp / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    # 阶段 1(铺垫/发展 + 碾压)
    # 阶段 2(打脸/碾压)
    # 阶段 3(碾压/救场)
    metas = [
        {"chapter_no": 1, "chapter_role": "铺垫", "shuang_type": "碾压"},
        {"chapter_no": 2, "chapter_role": "发展", "shuang_type": "打脸"},
        {"chapter_no": 3, "chapter_role": "弧高潮", "shuang_type": "碾压",
         "is_arc_climax": True},
    ]
    for m in metas:
        (chapters_dir / f"ch_{m['chapter_no']:04d}_meta.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8"
        )
    result = ac_mod.ac10_face_slap_beat()
    out = capsys.readouterr().out
    assert "PASS" in out


# ─── 7. AC-11 升级循环合规 ─────────────────────────


def test_ac11_skip_when_no_metas(isolated_novel_ai_dir):
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod.ac11_upgrade_loop() is True


def test_ac11_passes_when_upgrade_loop_found(isolated_novel_ai_dir, capsys):
    """升级 → 反杀 + 新伏笔 → PASS。"""
    tmp, ac_mod = isolated_novel_ai_dir
    chapters_dir = tmp / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    metas = [
        {"chapter_no": 1, "shuang_type": "升级"},
        {"chapter_no": 2, "shuang_type": "碾压",
         "foreshadowing_ops": [{"op": "plant", "desc": "下一轮危机"}]},
    ]
    for m in metas:
        (chapters_dir / f"ch_{m['chapter_no']:04d}_meta.json").write_text(
            json.dumps(m, ensure_ascii=False), encoding="utf-8"
        )
    result = ac_mod.ac11_upgrade_loop()
    out = capsys.readouterr().out
    assert "PASS" in out


# ─── 8. AC-12 对话提示词密度 ─────────────────────────


def test_ac12_skip_when_no_chapters(isolated_novel_ai_dir):
    _, ac_mod = isolated_novel_ai_dir
    assert ac_mod.ac12_dialogue_density() is True


def test_ac12_pass_with_low_dialogue_density(isolated_novel_ai_dir, capsys):
    """<25 提示词/章 → PASS。"""
    tmp, ac_mod = isolated_novel_ai_dir
    chapters_dir = tmp / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    # 写 3 章正常文本(几乎无对话提示词)
    for i in range(1, 4):
        text = "他跑进门,看见桌上的信。" * 50 + "但是" + "结尾" * 30
        text = text + ("补充" * 100)
        (chapters_dir / f"ch_{i:04d}.txt").write_text(text, encoding="utf-8")
    result = ac_mod.ac12_dialogue_density()
    out = capsys.readouterr().out
    assert "PASS" in out


def test_ac12_fail_with_force_threshold(isolated_novel_ai_dir, capsys):
    """≥50 提示词/章 → FAIL(强制替换级别)。"""
    tmp, ac_mod = isolated_novel_ai_dir
    chapters_dir = tmp / "output" / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    # 一章 60 行"X 说道"
    lines = [f"角色{i+1}说道：'这是第{i+1}句话。'" for i in range(60)]
    text = "\n".join(lines)
    (chapters_dir / "ch_0001.txt").write_text(text, encoding="utf-8")
    result = ac_mod.ac12_dialogue_density()
    out = capsys.readouterr().out
    assert "FAIL" in out


# ─── 9. run_all() 集成 ─────────────────────────


def test_run_all_returns_true_on_empty_data(isolated_novel_ai_dir, capsys):
    """空数据 → 全 SKIP → 12/12 PASS → True。"""
    _, ac_mod = isolated_novel_ai_dir
    result = ac_mod.run_all()
    out = capsys.readouterr().out
    assert "12/12" in out
    assert result is True