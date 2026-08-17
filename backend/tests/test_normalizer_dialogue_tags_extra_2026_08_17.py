"""test_normalizer_dialogue_tags_extra_2026_08_17.py

P3-19 修复验证：normalizer DIALOGUE_TAGS_PATTERN 必须覆盖高频对话提示词。

历史 bug（审计发现）：
- normalizer.DIALOGUE_TAGS_PATTERN 词表漏了「喊道/喝道/笑曰/喃喃道」
  等网文高频对话提示词（[1]、]的对话密度）。
- 影响：满篇「他喊道」「她喝道」+「喃喃道」的章节 → normalizer 不识别
  → 第一遍 regex 替换不动 → 不触发 second_pass_llm → AI 腔对话癌落盘。

修复（任务 P3-19 2026-08-17）：
- 在 DIALOGUE_TAGS_PATTERN 词表追加：喊道 / 喝道 / 笑曰 / 喃喃道
  （按需可加：低声道 / 高声道 / 沉声道（已含）/ 轻声道）
"""

from __future__ import annotations

import pytest


# ── 1. DIALOGUE_TAGS_PATTERN 必须识别漏掉的高频词 ─────────────────

@pytest.mark.parametrize("tag", [
    "喊道", "喝道", "笑曰", "喃喃道",
])
def test_dialogue_tags_pattern_catches_extra_tags(tag):
    """DIALOGUE_TAGS_PATTERN 必须能匹配每个高频对话提示词（CLAUDE.md
    「以通过测试为目的放宽断言」反向：补齐词表是修 bug，不是放宽）。"""
    from engine.agents.normalizer import DIALOGUE_TAGS_PATTERN

    text = f"他{tag}：'我要走了。'"
    matches = DIALOGUE_TAGS_PATTERN.findall(text)
    assert any(m[1] == tag for m in matches), (
        f"DIALOGUE_TAGS_PATTERN 必须能匹配「{tag}」（网文高频对话提示词），"
        f"regex 应在词表追加该关键词；实际匹配数: {len(matches)}"
    )


# ── 2. detect_dialogue_pollution 必须能识别补齐的标签 ─────────────────

@pytest.mark.parametrize("tag", [
    "喊道", "喝道", "笑曰", "喃喃道",
])
def test_detect_dialogue_pollution_catches_extra_tags(tag):
    """detect_dialogue_pollution 必须能检测出「喊的词」，否则 normalizer
    后续步骤拿不到这个 tag → 不触发 second_pass_llm → AI 腔落盘。"""
    from engine.agents.normalizer import detect_dialogue_pollution

    text = (
        f"主角{tag}：「我要离开这里。」\n"
        f"旁边一人{tag}：「等等我。」\n"
        f"另一个{tag}：「快跑！」"
    )
    count, _matches = detect_dialogue_pollution(text)
    # 三次「喊的词」必须全被识别
    assert count >= 3, (
        f"detect_dialogue_pollution 必须识别「{tag}」≥ 3 次，实际: {count}, "
        f"matches: {_matches[:3]}"
    )


# ── 3. 既有词表仍能工作（不能误删）））

@pytest.mark.parametrize("tag", [
    "说", "道", "问道", "答道", "沉声道", "低声说", "回答说",
])
def test_existing_dialogue_tags_still_work(tag):
    """回归测试：DIALOGUE_TAGS_PATTERN 既有词表不能误删（CLAUDE.md
    「不以通过测试为目的删除...断言」）。"""
    from engine.agents.normalizer import DIALOGUE_TAGS_PATTERN

    text = f"他{tag}：「走了。」"
    matches = DIALOGUE_TAGS_PATTERN.findall(text)
    assert any(m[1] == tag for m in matches), (
        f"DIALOGUE_TAGS_PATTERN 既有「{tag}」必须保留（不能误删）"
    )