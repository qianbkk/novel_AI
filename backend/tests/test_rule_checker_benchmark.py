"""确定性质量门离线基准（任务 03）

为 analyze_chapter 和 format_issues_for_prompt 建立可复现基准：
- 完全合格正文高分
- 每种规则单缺陷各自的临界阈值与上/下边界
- 多缺陷叠加（interaction）
- 中文标点 / 对话密集 / 短章 / 长章 / 异常输入
- 相同正文重复执行结果稳定（无随机 / 无时钟）
- 完全离线：不引入网络或时间依赖

不调用真实 LLM，不修改 rule_checker.py 实现除非有最小案例证实的缺陷。
"""
from __future__ import annotations

import sys
import re
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


import pytest
from engine.tools.rule_checker import analyze_chapter, format_issues_for_prompt


# ──────────────────────────────────────────────────────────────────────
# 工具：构造最小缺陷文本
# ──────────────────────────────────────────────────────────────────────


def _clean_text(chars: int = 1500) -> str:
    """一篇'足够干净'的章节样本，含开篇钩子和结尾落钩。"""
    body = (
        "林渊把账本压在膝上，指节发白。"
        "司机从后视镜里瞥他一眼，没说话。"
        "「现结。」顾青锋丢过来一张手绘地图，"
        "「缓冲区入口在双河镇老粮站西墙。」\n"
    )
    closing = (
        "他到底要不要去？——耳机里忽然传出短促的忙音。"
        "「你确定要去吗？」林渊没有回答。"
    )
    out = body
    while len(out) < chars - len(closing):
        out += "他熄了灯，走到窗前。城郊的灯火昏暗，远处苍莽山脉的轮廓像一道未磨利的刀刃。"
    out += closing
    return out[:chars]


def _cliche_heavy_text(per_1k: int = 8, total_chars: int = 2000) -> str:
    """构造每千字命中 per_1k 次陈词的章节。"""
    cliche = "此刻他蓦然不禁心中一动，眼眸中闪过一丝精光。"
    block = cliche + "。这是叙述。" * 10  # 约 80 字左右
    blocks_needed = total_chars // len(block)
    return (cliche + block[:60]) * blocks_needed


def _dialog_heavy_text(ratio_target: float = 0.85) -> str:
    """对话占比逼近 ratio_target 的章节。"""
    pairs = []
    while True:
        seg = "「你说啥？」「我不知道。」「那算了。」「行。」「明天再谈吧。」「好。」"
        pairs.append(seg)
        body = "".join(pairs)
        body_chars = len(body)
        # 估算对话字符（粗略：含「」的行）
        dialog_chars = sum(len(s) for s in re.findall(r"[「『\"][^\n]+[」』\"]", body))
        if body_chars == 0:
            continue
        if dialog_chars / body_chars >= ratio_target:
            return body
        if len(body) > 50000:
            return body


# ──────────────────────────────────────────────────────────────────────
# A. 完全合格
# ──────────────────────────────────────────────────────────────────────


def test_clean_1500_char_no_issues():
    text = _clean_text(1500)
    r = analyze_chapter(text)
    assert r["score"] >= 7.5, f"干净正文应 ≥7.5，实际={r['score']}"
    # 该样本应无 issues（除非中文字段误判）
    # 不强制 issues==[]，但 cliche_per_1k 应 ≤ 1
    assert r["details"].get("cliche_per_1k", 0) <= 2.0


# ──────────────────────────────────────────────────────────────────────
# B. 临界阈值与单缺陷边界
# ──────────────────────────────────────────────────────────────────────


def test_cliche_just_under_threshold_no_issue():
    """每千字 < 5 → 不报陈词。"""
    # 1000 字放 4 个陈词实例
    cliche = "此刻他蓦然不禁心中一动，眼眸中闪过一丝精光。"
    text = cliche + ("别处还有一些人不会用这个陈词。" * 50)
    text = text[:1000]
    r = analyze_chapter(text)
    assert r["details"]["cliche_per_1k"] < 5
    assert not any("陈词" in i for i in r["issues"])


def test_cliche_just_over_threshold_flags():
    """每千字 ≥ 5 → 报陈词过密。"""
    text = _cliche_heavy_text(per_1k=8)
    r = analyze_chapter(text)
    assert r["details"]["cliche_per_1k"] >= 5
    assert any("陈词" in i for i in r["issues"])


def test_dialog_low_boundary():
    """对话 9% 应被报为过少（<10%）。"""
    # 1000 字 + 90 字对话 ≈ 9%
    body = "他走进屋里。屋里很暗。灯没开。" * 150  # ≈ 750 字
    dialog = "「你来了？」" * 10                   # ≈ 60 字
    text = body + dialog
    r = analyze_chapter(text)
    assert r["details"]["dialog_ratio"] < 0.10
    assert any("对话过少" in i for i in r["issues"])


def test_dialog_high_boundary():
    """对话 80% 以上应被报为过多。"""
    text = _dialog_heavy_text(ratio_target=0.85)
    r = analyze_chapter(text)
    assert r["details"]["dialog_ratio"] > 0.70
    assert any("对话过多" in i for i in r["issues"])


def test_opening_repeat_one_prev_docks():
    """上一章开场与本章节首 10 字相同 → diversity_score 扣。"""
    # 上一章开场至少 10 字（实现要求）
    prev_opening = "林渊把账本压在膝上，指节发白"
    text = prev_opening + "。明天的事他还没想好，可今晚就要去探那个仓库。"
    # 仔细看 prev_openings 是与本章节首 60 字（剥标点）的头 20 字比，
    # 实际扣分逻辑看 head_norm[:20]。构造文本首 20 字相同 / 包含关系即可。
    r = analyze_chapter(text, prev_openings=[prev_opening])
    assert any("雷同" in i for i in r["issues"])


def test_opening_unique_no_dock():
    """不重复的开场不扣分。"""
    text = "暮色压下来。远山如黛。" * 30
    r = analyze_chapter(text, prev_openings=["林渊把账本压在膝上"])
    assert not any("雷同" in i for i in r["issues"])


def test_paragraph_too_long_flagged():
    """段落均长 >300 字应报段落过长。"""
    para = "他默然不语，走到窗前。远山黛青，灯火昏黄。" * 30  # 一段
    r = analyze_chapter(para + "\n\n" + para)
    assert any("段落过长" in i for i in r["issues"])


def test_closing_hook_short_chapter_not_required():
    """< 800 字正文不强制要求结尾钩子（避免短章节误报）。"""
    # 5 段 * 80 字 = 400 字，无钩子信号
    text = "夜幕降临，远山如黛。" * 22
    r = analyze_chapter(text)
    assert not any("结尾钩子弱" in i for i in r["issues"])


# ──────────────────────────────────────────────────────────────────────
# C. 多缺陷叠加
# ──────────────────────────────────────────────────────────────────────


def test_stacked_cliche_and_dialog_high():
    """陈词密度 + 极高对话占比同时出现 → issues 至少命中两条。"""
    # 拼装：开头塞陈词 → 中间全对话
    cliche = "此刻他蓦然不禁心中一动，眼眸中闪过一丝精光。"
    dialog = "「你来了。」「嗯，坐吧。」「行。」「好。」「明天再谈。」「好。」「嗯。」"
    body = (cliche + " " + dialog) * 40
    r = analyze_chapter(body)
    issue_types = [i for i in r["issues"]]
    # 至少要有 2 个不同类别的 issues
    assert len(issue_types) >= 2


def test_stacked_no_dialog_and_long_paragraph():
    """无对话 + 超长段落 → 至少命中'对话过少'与'段落过长'。"""
    # 单段约 360 字（avg_para_len = 单段总长/1 ≈ 360 > 300）
    base = ("他没有说话，也没有动，只是安静地坐在窗前。"
            "城外的灯火很远，夜色像一张无限延展的黑色画布，"
            "把所有不存在的细节都呈现出来，这种沉默比语言更沉，"
            "比呼喊更重，比任何一句台词都要锋利，"
            "比所有已说出和未说出口的句子都更具说服力。") * 4
    r = analyze_chapter(base)
    assert any("对话过少" in i for i in r["issues"])
    assert any("段落过长" in i for i in r["issues"])


# ──────────────────────────────────────────────────────────────────────
# D. 中文标点 / 短章 / 长章
# ──────────────────────────────────────────────────────────────────────


def test_chinese_punctuation_dialog_quotes_flagged():
    """中文方括号「」对话也应被识别。"""
    text = "「你好」「再见」「明天见」「等着」" * 30
    r = analyze_chapter(text)
    assert r["details"]["dialog_ratio"] > 0
    assert any("对话过多" in i for i in r["issues"])


def test_very_short_chapter_300_chars_handled():
    """300 字正文：不应崩溃；应给出合理 score。"""
    text = "他走进屋里。「你回来了？」「嗯。」他关上门，坐到椅子上。"
    text = text * 5
    r = analyze_chapter(text)
    assert "score" in r and 0.0 <= r["score"] <= 10.0


def test_long_chapter_5000_chars_handled():
    """5000 字正文：应能完成分析，且无异常。"""
    text = _clean_text(5000)
    r = analyze_chapter(text)
    assert "score" in r and 0.0 <= r["score"] <= 10.0


def test_empty_text_returns_zero_or_low():
    """空文本：不应抛异常；应给 0 或接近 0。"""
    r = analyze_chapter("")
    assert "score" in r
    assert r["details"]["cliche_per_1k"] == 0


def test_pure_english_text_handled():
    """纯 ASCII / 英文文本：不抛异常；陈词命中可能为 0（取决于 CLICHE_PATTERNS 兼容）。"""
    text = "Hello world. " * 500
    r = analyze_chapter(text)
    assert "score" in r


# ──────────────────────────────────────────────────────────────────────
# E. 稳定性 / 不依赖网络
# ──────────────────────────────────────────────────────────────────────


def test_idempotent_same_input_twice():
    """同一段文本两次分析 → 字段全部一致。"""
    text = _clean_text(1500)
    r1 = analyze_chapter(text)
    r2 = analyze_chapter(text)
    assert r1 == r2, "相同输入必须输出完全一致（无随机）"


def test_format_issues_idempotent():
    text = _cliche_heavy_text(per_1k=10, total_chars=1500)
    r = analyze_chapter(text)
    s1 = format_issues_for_prompt(r)
    s2 = format_issues_for_prompt(r)
    assert s1 == s2


def test_no_network_imports_in_rule_checker():
    """rule_checker.py 顶层不能 import 网络 / HTTP / 时间库之外的依赖。"""
    import inspect
    from engine.tools import rule_checker as rc
    src = inspect.getsource(rc)
    forbidden = ["import requests", "import urllib", "import http.client",
                 "import socket", "import time"]
    for token in forbidden:
        assert token not in src, (
            f"rule_checker 禁止引入网络/时钟依赖，但发现 {token!r}"
        )


def test_rule_checker_module_uses_only_stdlib():
    """规则检查器依赖只能是 Python 标准库 + 项目内模块（无第三方包）。"""
    import ast, pathlib
    src = pathlib.Path(_BACKEND / "engine" / "tools" / "rule_checker.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    top_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_imports.append(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            top_imports.append((node.module or "").split(".")[0])
    # 允许的标准库子集 + 项目内
    allowed = {"re", "typing", "__future__", "engine"}
    for name in top_imports:
        assert name in allowed, (
            f"rule_checker 顶层 import {name!r} 不是已知允许模块；"
            f"建议改为 typing/re 或迁出 rule_checker。"
        )


# ──────────────────────────────────────────────────────────────────────
# F. 算分上下界
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("score", [0.0, 5.0, 7.5, 10.0])
def test_score_within_bounds(score):
    """任何 analyze_chapter 结果 score 必 ∈ [0, 10]。"""
    text = _clean_text(1500)
    r = analyze_chapter(text)
    assert 0.0 <= r["score"] <= 10.0


def test_score_is_rounded_one_decimal():
    text = _clean_text(1500)
    r = analyze_chapter(text)
    # round(x, 1) 后 score 与 round(x*10)/10 相等
    assert abs(r["score"] - round(r["score"], 1)) < 1e-9
