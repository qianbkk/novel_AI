"""backend/tests/test_rule_checker.py — 三期规则检查器回归测试"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_clean_chapter_scores_high():
    from engine.tools.rule_checker import analyze_chapter
    text = """林渊把账本压在膝上，指节发白。司机从后视镜里瞥他一眼，没说话。
「现结。」顾青锋丢过来一张手绘地图，「缓冲区入口在双河镇老粮站西墙。」
林渊把车窗摇下一条缝。城郊的风带土腥气，远处苍莽山脉的轮廓像一道没磨利的刀刃。
「别逞强。」顾青锋补了一句。
林渊没接话，把账本合上。"""
    r = analyze_chapter(text)
    assert r["score"] >= 7.0, f"干净正文应得高分，实际={r}"
    assert r["issues"] == []


def test_cliche_density_docks_score():
    from engine.tools.rule_checker import analyze_chapter
    text = "此刻他蓦然不禁心中一动，深吸一口气，眼眸中闪过一丝精光。" * 8
    r = analyze_chapter(text)
    assert r["details"]["cliche_per_1k"] >= 5
    assert any("陈词" in i for i in r["issues"])


def test_dialog_ratio_too_low_flagged():
    from engine.tools.rule_checker import analyze_chapter
    text = "他走进屋里。屋里很暗。灯没开。他站在门口。他环顾四周。" * 20
    r = analyze_chapter(text)
    assert r["details"]["dialog_ratio"] < 0.10
    assert any("对话过少" in i for i in r["issues"])


def test_dialog_ratio_too_high_flagged():
    from engine.tools.rule_checker import analyze_chapter
    text = "「你来了」「嗯」「坐吧」「好」「喝茶吗」「来一杯」「谢了」「不客气」" * 50
    r = analyze_chapter(text)
    assert r["details"]["dialog_ratio"] > 0.70


def test_repeated_opening_penalized():
    from engine.tools.rule_checker import analyze_chapter
    opening = "林渊把账本压在膝上，指节发白"
    prev = [opening, opening]   # 连续两章同样开场
    text = opening + "。他在想今晚要不要去探那个仓库。"
    r = analyze_chapter(text, prev_openings=[opening, opening])
    assert any("雷同" in i for i in r["issues"])


def test_closing_hook_weak_flagged():
    from engine.tools.rule_checker import analyze_chapter
    # 纯描写、无问号/破折号/省略号/人称 → 应该报"结尾钩子弱"
    sentence = "夜幕降临，远山如黛，霜色渐浓。"
    text = sentence * 120   # 12 * 120 = 1440 字
    r = analyze_chapter(text)
    assert any("结尾钩子弱" in i for i in r["issues"]), r["issues"]


def test_format_issues_empty_returns_empty():
    from engine.tools.rule_checker import format_issues_for_prompt
    assert format_issues_for_prompt({"issues": [], "details": {}}) == ""


def test_format_issues_renders_chinese():
    from engine.tools.rule_checker import format_issues_for_prompt
    r = {"issues": ["陈词过密（5.2/千字）"],
         "details": {"cliche_hits": [("眼眸中", "闪过")]}}
    out = format_issues_for_prompt(r)
    assert "陈词" in out
    assert "规则层" in out


def test_checker_prompt_receives_rule_layer_feedback():
    from engine.agents.checker import score_chapter

    router = MagicMock()
    router.call.return_value = ('{"dimensions": {}, "overall_score": 6}', 0.0)
    task = {
        "chapter_number": 1,
        "chapter_role": "发展",
        "shuang_description": "",
        "_rule_feedback": "【规则层预检】\n  陈词过密\n",
    }

    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", task)

    user_prompt = router.call.call_args.kwargs["user_prompt"]
    assert "规则层预检" in user_prompt
    assert "陈词过密" in user_prompt


def test_weighted_score_accepts_decimal_strings_and_clamps_values():
    from engine.agents.checker import calculate_weighted_score

    score = calculate_weighted_score({
        "pacing": "7.5",
        "character_voice": 20,
        "plot_logic": None,
        "consistency": "bad",
        "writing_naturalness": 8,
        "hook_power": 7,
    })

    assert 1.0 <= score <= 10.0
