"""test_checker_evidence_2026_07_27.py

架构审视 §A3 — checker 评分 30% 权重建立在无证据的判断上。

证据（docs/drafts/architecture-roadmap-2026-07-27.md §A3）：
- engine/agents/checker.py:78-86 score_chapter 的 user_prompt 只有【章节信息+正文】，
  没有 setting/L2/上章结尾。
- 但评分维度里 consistency(15%) + plot_logic(15%) = 30% 权重压在没有证据的判断上。
- 现状是模型拿噪声当信号，猜出来的分还参与 PASS_SCORE 判定。

修法（本轮 spec）：
- score_chapter 增可选 evidence 参数：setting 原文/上章结尾/近期事件摘要/应回收伏笔。
- prompt 显式给，并要求 consistency/plot_logic 扣分必须引用具体冲突点。
- 证据缺失时明确告知"本章无前文可比对, consistency 按 setting 判" —— 不沉默。

本文件是规范（spec-as-test）。当前实现尚未支持 evidence= 参数与 Evidence 类，
因此 import 阶段就会失败 —— 这是预期的红，等实现补完即变绿。

验收点（与 §A3 一一对应）：
  A. 证据出现在 prompt 里                       → §3
  B. 冲突版本 consistency 显著低于无冲突版本     → §4
  C. 无证据时不崩、有降级提示                    → §5
  D. token 预算不失控（证据有上限）               → §6
  E. Evidence dataclass 形状正确                → §1
  F. score_chapter 新签名向后兼容                → §2
"""
from __future__ import annotations

import dataclasses
import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import pytest  # noqa: E402

# 这些符号尚不存在 —— 本文件作为 spec，实现后才会通过。
# 当前实现会在这里抛 ImportError，等 engine/agents/checker.py 加上 Evidence
# 和新签名 score_chapter 后，本文件即可全绿。
from engine.agents.checker import Evidence, score_chapter  # noqa: E402


# ─── 共享 fixture ─────────────────────────

def _make_router(json_body: str = (
    '{"dimensions": {"pacing": 6, "character_voice": 6, "plot_logic": 6,'
    ' "consistency": 6, "writing_naturalness": 6, "hook_power": 6},'
    ' "overall_score": 6.0}'
)):
    router = MagicMock()
    router.call.return_value = (json_body, 0.0)
    return router


def _base_task():
    return {"chapter_number": 5, "chapter_role": "发展",
            "shuang_description": "升级"}


def _capture_user_prompt(router):
    return router.call.call_args.kwargs["user_prompt"]


# ─────────────────────────────────────────────────────────
# §1. Evidence dataclass 形状正确
# ─────────────────────────────────────────────────────────

def test_evidence_is_a_dataclass():
    """Evidence 应该是 dataclass —— 而不是裸 dict —— 拿到 IDE 提示和类型校验。"""
    assert dataclasses.is_dataclass(Evidence)


def test_evidence_has_required_fields():
    """约定字段：4 个核心证据来源，全部可选。"""
    names = {f.name for f in dataclasses.fields(Evidence)}
    assert "setting_text" in names,            "世界书命中原文"
    assert "last_chapter_ending" in names,     "上章结尾"
    assert "recent_events" in names,           "近期事件摘要"
    assert "foreshadowing_to_resolve" in names,"本章应回收伏笔"


def test_evidence_all_fields_default_to_none():
    """所有字段都缺省 None —— 缺失证据时整体 Evidence() 也合法。"""
    e = Evidence()
    assert e.setting_text is None
    assert e.last_chapter_ending is None
    assert e.recent_events is None
    assert e.foreshadowing_to_resolve is None


def test_evidence_can_carry_text_and_lists():
    """字段类型契约：str 与 list[str] 都接受。"""
    e = Evidence(
        setting_text="等级上限为 5",
        last_chapter_ending="主角刚升到 4 级",
        recent_events=["第 3 章: 进城", "第 4 章: 结识队友"],
        foreshadowing_to_resolve=["血脉觉醒"],
    )
    assert e.setting_text == "等级上限为 5"
    assert e.recent_events == ["第 3 章: 进城", "第 4 章: 结识队友"]
    assert e.foreshadowing_to_resolve == ["血脉觉醒"]


# ─────────────────────────────────────────────────────────
# §2. score_chapter 新签名向后兼容
# ─────────────────────────────────────────────────────────

def test_score_chapter_evidence_is_keyword_only():
    """新签名约定：evidence 必须是 keyword-only，不能污染位置参数顺序。"""
    sig = inspect.signature(score_chapter)
    assert "evidence" in sig.parameters
    p = sig.parameters["evidence"]
    assert p.kind == inspect.Parameter.KEYWORD_ONLY
    assert p.default is None, "evidence 默认 None，缺省时不报错"


def test_score_chapter_callable_without_evidence():
    """旧调用方式（不传 evidence）必须仍然工作 —— 不破坏既有调用点。"""
    router = _make_router()
    task = _base_task()
    with patch("engine.agents.checker.get_active_router", return_value=router):
        result, cost = score_chapter("正文", task)
    assert isinstance(result, dict)
    assert "consistency" in result["dimensions"]


# ─────────────────────────────────────────────────────────
# §3. 证据出现在 prompt 里
# ─────────────────────────────────────────────────────────

def test_setting_text_appears_in_prompt():
    router = _make_router()
    ev = Evidence(setting_text="主角最高只能到 5 级")
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    assert "主角最高只能到 5 级" in _capture_user_prompt(router)


def test_last_chapter_ending_appears_in_prompt():
    router = _make_router()
    ev = Evidence(last_chapter_ending="他望着远方的灯火，攥紧了拳头。")
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    assert "他望着远方的灯火，攥紧了拳头。" in _capture_user_prompt(router)


def test_recent_events_appear_in_prompt():
    router = _make_router()
    ev = Evidence(recent_events=["第 3 章: 进城", "第 4 章: 结识队友"])
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    prompt = _capture_user_prompt(router)
    assert "第 3 章" in prompt and "第 4 章" in prompt


def test_foreshadowing_to_resolve_appears_in_prompt():
    router = _make_router()
    ev = Evidence(foreshadowing_to_resolve=["血脉觉醒", "失踪的师父"])
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    prompt = _capture_user_prompt(router)
    assert "血脉觉醒" in prompt
    assert "失踪的师父" in prompt


def test_prompt_has_explicit_evidence_section_header():
    """证据必须在 prompt 里有专门段头，不能和正文混在一起。"""
    router = _make_router()
    ev = Evidence(setting_text="X", last_chapter_ending="Y")
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    prompt = _capture_user_prompt(router)
    # 段头存在即可；具体字眼（【证据】/【前情】/【背景与约束】）实现时可调
    assert ("【证据】" in prompt
            or "【前情】" in prompt
            or "【背景与约束】" in prompt
            or "evidence" in prompt.lower()), \
        f"prompt 没有专门的证据段头：{prompt!r}"


def test_prompt_asks_for_specific_conflict_citation():
    """扣分必须引用具体冲突点 —— 模型不能继续打哑谜。"""
    router = _make_router()
    ev = Evidence(setting_text="X")
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    prompt = _capture_user_prompt(router)
    # 提示语具体字眼实现时可调；但"引用冲突点"的意思要落到 prompt 上
    assert ("引用" in prompt
            or "具体冲突" in prompt
            or "指出" in prompt), \
        f"prompt 没要求 consistency/plot_logic 扣分时引用冲突：{prompt!r}"


# ─────────────────────────────────────────────────────────
# §4. 冲突版本 → consistency 显著更低（语义层 mock）
# ─────────────────────────────────────────────────────────

# 固定一段设定 + 一段上章结尾 + 一条伏笔
SETTING = "主角的等级上限为 5 级（魔法协会公会明文规定）"
LAST_ENDING = "主角刚升到 4 级，离上限只差一步。"
FORE = ["血脉觉醒"]

# 同一对章节，区别只在"是否与 setting 冲突"
CHAPTER_CONFLICT = (
    "他在篝火旁凝聚魔力，强行冲破了体内的禁制。一夜之间，他激活了第 6 级法印，"
    "周身金色符文流转，连远在王都的长老都感应到了这股不属于人间的气息。"
)

CHAPTER_NO_CONFLICT = (
    "他在篝火旁练习第 4 级法印——土墙术。一面厚实的岩壁在众人面前缓缓升起，"
    "队长拍拍他的肩：'不错，再稳一稳节奏。'"
)


def _keyword_router():
    """动态 mock：根据 user_prompt 的语义关键词判定是否有冲突。

    真 LLM 的语义层校验无法在测试里复现，但 prompt 注入 evidence 后，"冲突
    文本"和"setting 文本"会同时出现在 user_prompt 里 —— 关键词规则是
    真实 LLM 也会触发的同一个信号：只要 prompt 里同时有这两段，模型就
    应该扣 consistency/plot_logic 分。这里把"扣分"近似成关键词触发。
    """
    router = MagicMock()

    def fake_call(*args, **kwargs):
        prompt = kwargs.get("user_prompt", "")
        if "第 6 级" in prompt and "等级上限为 5" in prompt:
            # 冲突被识别 → consistency/plot_logic 显著低
            body = (
                '{"dimensions": {"pacing": 7, "character_voice": 7,'
                ' "plot_logic": 3, "consistency": 4,'
                ' "writing_naturalness": 7, "hook_power": 7},'
                ' "overall_score": 5.5,'
                ' "strongest_point": "节奏快",'
                ' "weakest_point": "突破等级上限违反设定",'
                ' "specific_feedback": "冲突：第 6 级 vs 等级上限为 5 级"}'
            )
        else:
            body = (
                '{"dimensions": {"pacing": 7, "character_voice": 7,'
                ' "plot_logic": 8, "consistency": 8,'
                ' "writing_naturalness": 7, "hook_power": 7},'
                ' "overall_score": 7.4,'
                ' "strongest_point": "节奏稳",'
                ' "weakest_point": "无",'
                ' "specific_feedback": "无冲突"}'
            )
        return body, 0.001

    router.call.side_effect = fake_call
    return router


def test_consistency_drops_when_chapter_conflicts_with_setting():
    """核心验收点：与设定冲突的版本，consistency 显著低于无冲突版本。

    同一证据、同一个 mock router；只换 chapter 内容 → 只有当 evidence 真
    被送进 prompt、且 mock 能从 prompt 看到冲突时，这个差异才会出现。
    """
    router = _keyword_router()
    ev = Evidence(setting_text=SETTING, last_chapter_ending=LAST_ENDING,
                  foreshadowing_to_resolve=FORE)

    with patch("engine.agents.checker.get_active_router", return_value=router):
        result_conflict, _ = score_chapter(CHAPTER_CONFLICT, _base_task(),
                                          evidence=ev)
        result_clean, _ = score_chapter(CHAPTER_NO_CONFLICT, _base_task(),
                                        evidence=ev)

    c_conflict = result_conflict["dimensions"]["consistency"]
    c_clean = result_clean["dimensions"]["consistency"]
    assert c_conflict < c_clean, \
        f"冲突版本 consistency({c_conflict}) 应低于无冲突({c_clean})"
    # 真实差距应 ≥ 2 分（语义差异不是噪声）
    assert c_clean - c_conflict >= 2, \
        f"consistency 差距 {c_clean - c_conflict} 过小，evidence 可能没真进 prompt"
    # plot_logic 同理 —— 一致性扣分不是单点的
    p_conflict = result_conflict["dimensions"]["plot_logic"]
    p_clean = result_clean["dimensions"]["plot_logic"]
    assert p_conflict < p_clean


def test_no_evidence_means_router_cannot_see_the_conflict():
    """证据缺失时，即便 chapter 与设定冲突，prompt 也接不到 setting 文本，
    mock 因此无法识别冲突 → consistency 与无冲突版本无差异。

    这验证了 §A3 现状的"沉默副作用"：没有 evidence 喂给 prompt，模型就
    拿不到信号 —— 同一个 mock、同一段冲突文本，结论完全翻转。
    """
    router = _keyword_router()

    with patch("engine.agents.checker.get_active_router", return_value=router):
        result_conflict, _ = score_chapter(CHAPTER_CONFLICT, _base_task(),
                                          evidence=None)
        result_clean, _ = score_chapter(CHAPTER_NO_CONFLICT, _base_task(),
                                        evidence=None)

    # 证据缺失 → mock 都拿不到 setting 文本 → 一致判定"无冲突"
    assert result_conflict["dimensions"]["consistency"] == \
           result_clean["dimensions"]["consistency"]


# ─────────────────────────────────────────────────────────
# §5. 无证据不崩、有降级提示
# ─────────────────────────────────────────────────────────

def test_no_evidence_does_not_raise():
    router = _make_router()
    with patch("engine.agents.checker.get_active_router", return_value=router):
        # 不传 evidence → 缺省 None
        result, cost = score_chapter("正文", _base_task())
    assert isinstance(result, dict)


def test_no_evidence_adds_degradation_hint_in_prompt():
    """证据缺失时必须显式告知模型，而不是沉默。

    沉默会让模型自由发挥 —— 现状就是这种"猜分"。修法是把"无前文可比对，
    consistency 按 setting 判"写进 prompt，明确告诉模型降级口径。
    """
    router = _make_router()
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=None)
    prompt = _capture_user_prompt(router)
    # 提示语具体字眼实现时可调，但"无前文" + "按 setting 判"两个意思都要覆盖
    has_no_context_phrase = any(
        kw in prompt
        for kw in ("无前文", "无前情", "无证据", "无前文可比对", "无前情可比对"))
    assert has_no_context_phrase, f"缺证据时应有降级提示，prompt={prompt!r}"
    has_setting_phrase = "setting" in prompt.lower() or "设定" in prompt
    assert has_setting_phrase, \
        f"缺证据时应提示 consistency 按 setting 判，prompt={prompt!r}"


# ─────────────────────────────────────────────────────────
# §6. 证据有 token 上限（防止 3-模型交叉评审 token 爆掉）
# ─────────────────────────────────────────────────────────

def test_oversized_evidence_field_is_capped():
    """任一字段被塞超大文本时，prompt 整体不能被打穿。"""
    router = _make_router()
    huge = "X" * 50_000  # 5 万字 —— 足够让 prompt 炸掉
    ev = Evidence(setting_text=huge, last_chapter_ending=huge)
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    # 30k 字符上限给实现留余量（章节正文 ≤ 4k + 章节信息 + 证据 + 留白）
    assert len(_capture_user_prompt(router)) < 30_000, \
        f"prompt 失控：{len(_capture_user_prompt(router))} 字符"


def test_all_evidence_fields_combined_is_capped():
    """所有字段同时被打满，prompt 仍受控。"""
    router = _make_router()
    ev = Evidence(
        setting_text="S" * 10_000,
        last_chapter_ending="E" * 10_000,
        recent_events=["E" * 10_000],
        foreshadowing_to_resolve=["F" * 10_000],
    )
    with patch("engine.agents.checker.get_active_router", return_value=router):
        score_chapter("正文", _base_task(), evidence=ev)
    # 4 字段各 10k + 正文 ~4k + 章节信息 ≈ 44k；裁剪后必须 < 20k
    assert len(_capture_user_prompt(router)) < 20_000, \
        f"prompt 失控：{len(_capture_user_prompt(router))} 字符"
