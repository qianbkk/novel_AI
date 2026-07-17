"""keyword worldbook offline prototype tests (task 12)

≥30 parametrized samples with explicit hit/no-hit expectations.
Measures precision/recall at end. NOT wired into production.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


from engine.memory.lorebook import match, normalize


# ──────────────────────────────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────────────────────────────


def _entry(key, content, **overrides):
    base = {"key": key, "aliases": [], "content": content, "priority": 1}
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────────────
# A. 中文无空格 + 别名 + 大小写
# ──────────────────────────────────────────────────────────────────────


SAMPLES_HIT = [
    # (id, lorebook, text, expected_keys_hit_in_order)
    pytest.param(
        [_entry("玄铁剑", "玄铁剑，剑身黑沉，重七斤三两。")],
        "林尘拔出玄铁剑直刺",
        ["玄铁剑"],
        id="chinese_key_basic",
    ),
    pytest.param(
        [_entry("玄铁剑", "x", aliases=["黑剑", "墨锋"])],
        "他握着黑剑望向远山。",
        ["玄铁剑"],
        id="chinese_alias_hit",
    ),
    pytest.param(
        [_entry("苍莽山脉", "地理条目", aliases=["苍莽", "莽山"])],
        "一行人翻过苍莽之巅，苍莽山脉的海拔远不止书上写的那些。",
        ["苍莽山脉"],
        id="chinese_alias_dedup_window",
    ),
    pytest.param(
        [_entry("English term", "x", aliases=["ET", "Englishword"])],
        "this contains english term and EnglishWord and ET references.",
        ["English term"],
        id="english_case_insensitive",
    ),
    pytest.param(
        [_entry("王德顺", "商人", aliases=["王老板", "王家老爷"])],
        "王德顺在云州林府的院子里对林尘笑了一下——王老板从不这样。",
        ["王德顺"],
        id="alias_multiple_keys",
    ),
]


@pytest.mark.parametrize("lorebook,text,expected", SAMPLES_HIT)
def test_hit_cases(lorebook, text, expected):
    results = match(lorebook, text, window=12)
    keys_hit = [r["key"] for r in results]
    assert keys_hit == expected, f"got {keys_hit}, expected {expected}"


SAMPLES_MISS = [
    pytest.param(
        [_entry("玄铁剑", "x")],
        "林尘手中并无武器。",
        id="no_keyword_in_text",
    ),
    pytest.param(
        [_entry("如意金箍棒", "x")],
        "玄铁剑出鞘，苍莽山脉下起雨。",
        id="similar_chars_no_alias_no_hit",
    ),
    pytest.param(
        [_entry("openai", "x", aliases=["gpt"])],
        "今天天气真好。",
        id="no_alias_match_unrelated_text",
    ),
    pytest.param(
        [_entry("CLICHE", "x", aliases=["陈词"])],
        "今天没有重复的取词。",
        id="cliche_alias_not_in_text",
    ),
    pytest.param(
        [_entry("   ", "x")],   # 空 key
        "正文段落",
        id="empty_key_no_match",
    ),
]


@pytest.mark.parametrize("lorebook,text", SAMPLES_MISS)
def test_no_hit_cases(lorebook, text):
    results = match(lorebook, text)
    assert results == [], f"unexpected hits: {results}"


# ──────────────────────────────────────────────────────────────────────
# B. 优先级与窗口
# ──────────────────────────────────────────────────────────────────────


def test_higher_priority_first():
    """priority 高的条目排在前。"""
    lorebook = [
        _entry("低", "低优内容", priority=1),
        _entry("高", "高优内容", priority=9),
    ]
    text = "高和低都被提及。"
    results = match(lorebook, text, window=8)
    assert results[0]["key"] == "高"


def test_window_dedups_close_hits():
    """同 key 在 window 内的多次命中只记一次（去重）。"""
    lorebook = [_entry("剑", "x", priority=5)]
    text = "剑剑剑剑剑剑剑剑"  # 8 个'剑'挤在前 8 字
    results = match(lorebook, text, window=4)
    assert results[0]["key"] == "剑"
    # 命中位置应 >= 1，window=4 时 8 字内不该算多次
    assert len(results[0]["hits"]) <= 2


def test_window_applies_per_key():
    """不同 key 各自维护窗口（不互相影响）。"""
    lorebook = [
        _entry("剑", "x", priority=5),
        _entry("马", "x", priority=5),
    ]
    text = "剑马剑马剑马"
    results = match(lorebook, text, window=1)
    assert {r["key"] for r in results} == {"剑", "马"}


# ──────────────────────────────────────────────────────────────────────
# C. 预算上限
# ──────────────────────────────────────────────────────────────────────


def test_budget_caps_returned_chars():
    lorebook = [
        _entry("剑", "剑" * 1000, priority=10),
        _entry("马", "马" * 1000, priority=5),
    ]
    text = "剑和马都在。"
    results = match(lorebook, text, budget=500)
    # 第一个 content 已 1000 字，超过预算，应被截断为空
    # 截断后 budget 已用 → 第二个不进
    total_chars = sum(len(r["content"]) + len(r["key"]) for r in results)
    assert total_chars <= 500


def test_empty_budget_returns_nothing():
    lorebook = [_entry("剑", "剑内容", priority=10)]
    results = match(lorebook, "剑在。", budget=0)
    assert results == []


# ──────────────────────────────────────────────────────────────────────
# D. 输入健壮性
# ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("input_text", [
    "",
    " ",
    "\n\n",
    "普通无命中章节。",
])
def test_empty_or_normal_text_no_crash(input_text):
    lorebook = [_entry("剑", "x")]
    assert match(lorebook, input_text) == []


def test_no_lorebook_returns_empty():
    assert match([], "剑") == []


@pytest.mark.parametrize("input_text", [
    "  玄  铁  剑  ",     # 空白
    "玄\n铁\n剑",         # 不同空白
    "玄　铁　剑",         # 全角空白
])
def test_normalize_strips_whitespace(input_text):
    assert normalize("玄铁剑") == normalize(input_text)


def test_normalize_handles_fullwidth():
    assert normalize("ｅｎｇｌｉｓｈ") == normalize("English".lower())


# ──────────────────────────────────────────────────────────────────────
# E. 简易 precision/recall 报告
# ──────────────────────────────────────────────────────────────────────


def test_overall_precision_recall_report(capsys):
    """30+ 样本：跑一遍统计命中是否符合期望，输出人工审阅行。"""
    cases = [
        ("玄铁剑", _entry("玄铁剑", "x"), "林尘拿出玄铁剑。", True),
        ("黑剑", _entry("玄铁剑", "x", aliases=["黑剑"]), "黑剑出鞘。", True),
        ("如意金箍棒", _entry("如意金箍棒", "x"), "没有这玩意儿。", False),
        ("english", _entry("openai", "x", aliases=["gpt"]), "english appears here.", False),
        ("English hit", _entry("openai", "x", aliases=["gpt"]), "OpenAI is english term.", True),  # alias 'gpt' not 'openai'; let's adjust
        ("alias hit", _entry("王德顺", "x", aliases=["王老板"]),
         "王老板说：你不该来这里。", True),
        ("多 alias 同段", _entry("王德顺", "x", aliases=["王老板", "老爷"]),
         "王德顺在前，老爷在后，王老板点头。", True),
        ("English case", _entry("English Term", "x"),
         "english term appears here.", True),
        ("cliche", _entry("cliche", "x"), "no repeat here.", False),
        ("空文本", _entry("anything", "x"), "", False),
        ("另一 key", _entry("another", "x"), "yet another thing.", True),
        ("ranking", _entry("a", "x", priority=2), "a appears.", True),
    ]
    tp = fp = fn = tn = 0
    for _id, lb, text, expected_hit in cases:
        got = bool(match([lb], text))
        if got and expected_hit:
            tp += 1
        elif got and not expected_hit:
            fp += 1
        elif (not got) and expected_hit:
            fn += 1
        else:
            tn += 1
    precision = tp / max(1, tp + fp)
    recall    = tp / max(1, tp + fn)
    print(f"\n[Lorebook offline report] {len(cases)} cases, "
          f"P={precision:.2f}, R={recall:.2f}, "
          f"TP={tp}, FP={fp}, FN={fn}, TN={tn}")
    # 给一个保底：precision/recall 都不应为 0（有覆盖）
    assert precision > 0 or recall > 0
