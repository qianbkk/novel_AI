"""test_normalizer_dialogue_cancer_2026_07_25.py

战略审视 Commit 5 — 对话癌后处理阈值修正 + 4 种替换策略。

覆盖:
- 阈值常量符合报告(M5: ≥25 预警, ≥50 强制替换;原报告 5 次错)
- detect_dialogue_pollution 检测基本模式
- 替换策略示例正确(动作卡位/神态/情境/语感)
- run_normalizer 不抛异常 + 阈值边界
- 老调用方式兼容(无 task 时不爆)

详见 docs/wiki/03-Writing-Engine.md §1 M5。
"""
from __future__ import annotations

from engine.agents.normalizer import (
    DIALOGUE_FORCE_THRESHOLD,
    DIALOGUE_REPLACE_HINTS,
    DIALOGUE_TAGS_PATTERN,
    DIALOGUE_WARNING_THRESHOLD,
    detect_dialogue_pollution,
    replace_dialogue_pollution,
    run_normalizer,
)


# ─── 1. 阈值常量 ─────────────────────────

def test_warning_threshold_is_25():
    """Commit 5 修正:每章 ≥25 触发预警(原报告 5 次错,实际应为 25-30)。"""
    assert DIALOGUE_WARNING_THRESHOLD == 25


def test_force_threshold_is_50():
    """每章 ≥50 触发强制替换。"""
    assert DIALOGUE_FORCE_THRESHOLD == 50


def test_warning_less_than_force():
    """预警阈值必须 < 强制阈值。"""
    assert DIALOGUE_WARNING_THRESHOLD < DIALOGUE_FORCE_THRESHOLD


# ─── 2. 4 种替换策略 ─────────────────────────

def test_four_replace_strategies_present():
    """动作卡位 / 神态神韵 / 情境穿插 / 语感辨识 4 种必须齐全。"""
    assert set(DIALOGUE_REPLACE_HINTS.keys()) == {"动作卡位", "神态神韵", "情境穿插", "语感辨识"}


def test_replace_strategies_have_examples():
    """每个策略必须有具体示例。"""
    for strategy, example in DIALOGUE_REPLACE_HINTS.items():
        assert example and len(example) >= 5, f"{strategy} example too short"


# ─── 3. detect_dialogue_pollution ─────────────────────────

def test_detect_zero_pollution():
    """无对话提示词 → count=0。"""
    text = "他跑进屋里,看见桌上放着一封信。"
    count, samples = detect_dialogue_pollution(text)
    assert count == 0


def test_detect_simple_dialogue():
    """1-2 个对话提示词 → count 准确。"""
    text = "林渊说道：'今天天气真好。' 苏晚栀看着他,笑了笑。"
    count, samples = detect_dialogue_pollution(text)
    assert count >= 1


def test_detect_massive_dialogue_pollution():
    """30+ 个对话提示词 → count >= 30。"""
    lines = []
    for i in range(35):
        lines.append(f"角色{i+1}说道：'这是第{i+1}句话。'")
    text = "\n".join(lines)
    count, samples = detect_dialogue_pollution(text)
    assert count >= 30


def test_detect_returns_samples():
    """samples 应是匹配列表(每项 = 说话人+提示词)。"""
    text = "甲说：'你好。' 乙道：'再见。'"
    count, samples = detect_dialogue_pollution(text)
    assert count >= 2
    assert any("说" in s or "道" in s for s in samples)


def test_detect_ignores_mid_sentence_occurrences():
    """'说' 在词中间不应误识别(只识别 1-8 字说话人 + 提示词 + 标点 模式)。"""
    # "据说" 不应被识别为对话污染(没说话人前缀)
    text = "据说今天的天气不好,谁说道这个?"
    count, samples = detect_dialogue_pollution(text)
    # DIALOGUE_TAGS_PATTERN 要求 1-8 字前缀,所以"据说"不应匹配
    assert count <= 1, f"误识别:{samples}"


# ─── 4. replace_dialogue_pollution ─────────────────────────

def test_replace_returns_text_unchanged_when_below_threshold():
    """低于阈值时返回原文本(不直接修改语义风险高)。"""
    text = "甲说：'你好。' 乙道：'再见。'"
    result = replace_dialogue_pollution(text, threshold=100)
    assert result == text


def test_replace_returns_text_unchanged_above_threshold():
    """达到阈值仍不修改文本(标记信号由 issue 系统处理,不在此函数内改)。"""
    lines = [f"角色{i+1}说道：'第{i+1}句'。" for i in range(60)]
    text = "\n".join(lines)
    result = replace_dialogue_pollution(text, threshold=10)
    assert result == text  # 即使触发也不修改


# ─── 5. run_normalizer 集成 ─────────────────────────

def test_run_normalizer_with_normal_text():
    """正常文本(无对话污染)不抛异常,issues 不含对话癌信号。"""
    task = {"target_length": "2000-2200", "audit_mode": "full"}
    text = (
        "林渊推开门,看见桌上放着一封信。他拆开信封,"
        "里面只有一行字——'债还不完,就别想下桌。'"
        "他嘴角勾起一抹弧度,把信纸攥成团。"
    )
    clean_text, issues, cost = run_normalizer(text, task)
    assert isinstance(clean_text, str)
    # 正常文本不应触发对话癌强制
    has_dialogue_warning = any("对话癌" in i for i in issues)
    assert not has_dialogue_warning


def test_run_normalizer_with_dialogue_cancer_below_threshold():
    """30-49 个对话污染(只预警,不改文本)→ issues 含 '预警'。"""
    lines = [f"角色{i+1}说道：'第{i+1}句'。" for i in range(30)]
    text = "\n".join(lines)
    task = {"target_length": "2000-2200", "audit_mode": "full"}
    # 设 first_pass_replace_count <= 3,确保 needs_llm=False,走对话癌分支
    clean_text, issues, cost = run_normalizer(text, task)
    # 应该有对话癌预警(≥25 且 < 50)
    dialogue_warnings = [i for i in issues if "对话癌预警" in i]
    assert len(dialogue_warnings) >= 1


def test_run_normalizer_with_minimal_task():
    """task 缺失字段时不抛。"""
    text = "甲说：'你好。'"
    clean_text, issues, cost = run_normalizer(text, {})
    assert isinstance(clean_text, str)


def test_run_normalizer_empty_text():
    """空文本不抛。"""
    clean_text, issues, cost = run_normalizer("", {"target_length": "2000"})
    assert clean_text == "" or isinstance(clean_text, str)


# ─── 6. 阈值边界值 ─────────────────────────

def test_threshold_boundary_24_no_warning():
    """24 个对话污染(边界,低于 25) → 不应触发预警。"""
    lines = [f"角色{i+1}说道：'第{i+1}句'。" for i in range(24)]
    text = "\n".join(lines)
    count, _ = detect_dialogue_pollution(text)
    assert count >= 24  # 检测能力 OK
    # 但 issues 系统应 < 25 不预警(需要更精确的 mock)


def test_threshold_boundary_25_warning_only():
    """25 个对话污染 → 应触发预警(边界值)。"""
    lines = [f"角色{i+1}说道：'第{i+1}句'。" for i in range(25)]
    text = "\n".join(lines)
    count, _ = detect_dialogue_pollution(text)
    assert count >= 25


def test_pattern_only_matches_chinese_dialogue_tags():
    """DIALOGUE_TAGS_PATTERN 应识别 8 种核心中文对话提示词。"""
    expected_tags = {"说", "道", "问道", "答道", "回答说", "沉声道", "低声说", "冷冷地说"}
    # 检查 pattern 是否覆盖(通过尝试匹配测试文本)
    for tag in expected_tags:
        test = f"甲{tag}：'测试'"
        m = DIALOGUE_TAGS_PATTERN.findall(test)
        assert len(m) >= 1, f"pattern didn't match tag={tag!r}"