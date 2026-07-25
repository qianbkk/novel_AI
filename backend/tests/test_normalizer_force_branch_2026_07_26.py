"""test_normalizer_force_branch_2026_07_26.py

审计修 Medium#1 回归测试。

历史问题:normalizer.run_normalizer 在 dialogue_count >= FORCE_THRESHOLD 且
needs_llm=False 时走 LLM 替换分支,但:
1. dialogue_replace_prompt 用 .format(cnt=) 叠加在已含 f-string 插值(含污染样本)
   的字符串上 —— 污染样本若含裸 { / } 会抛 KeyError/ValueError。
2. 该 FORCE+LLM 分支完全无测试覆盖。

本测试覆盖:
- 污染样本含 { 时不崩(pure f-string 修法正确)
- 污染样本含 } 时不崩
- 污染样本含 JSON-like 字面量时不崩
- FORCE 分支确实触发 router(用 monkeypatch 在 normalizer 模块上替换 LLMRouter)
- 返回文本/费用/issue 字段齐全
- needs_llm=True 时不二次调 LLM
"""
from __future__ import annotations

import pytest

from engine.agents import normalizer as norm_mod
from engine.agents.normalizer import (
    DIALOGUE_FORCE_THRESHOLD,
    DIALOGUE_WARNING_THRESHOLD,
    run_normalizer,
)


class _MockRtr:
    """简易 mock router:所有 call 返回 ("改写后文本", 0.05)。

    call_args 列表记录所有调用,供断言使用。
    """
    def __init__(self):
        self.call_args_list: list[dict] = []

    def call(self, **kwargs):
        self.call_args_list.append(kwargs)
        return ("改写后文本", 0.05)


def _patch_normalizer_router(monkeypatch, mock):
    """在 4 个名字位置同时 patch,确保 second_pass_llm 和 router_call_for_dialogue 都生效。

    normalizer 内部两处 router 引用:
    1. second_pass_llm:
       - `from ..llm.router import LLMRouter`(模块级,已绑定 → patch norm_mod.LLMRouter)
       - `from ..llm_router import get_active_router`(模块级,已绑定 → patch norm_mod.get_active_router)
    2. router_call_for_dialogue:
       - 同上,但 import 在函数内,每次调用重新解析 → patch 源模块
         engine.llm.router.LLMRouter 和 engine.llm_router.get_active_router
    """
    monkeypatch.setattr(norm_mod, "LLMRouter", lambda: mock)
    monkeypatch.setattr(norm_mod, "get_active_router", lambda: None)
    monkeypatch.setattr("engine.llm.router.LLMRouter", lambda: mock)
    monkeypatch.setattr("engine.llm_router.get_active_router", lambda: None)


@pytest.fixture
def mocked_router(monkeypatch):
    """把 normalizer 模块内的 LLMRouter 替换为 mock,记录所有 call。"""
    mock = _MockRtr()
    _patch_normalizer_router(monkeypatch, mock)
    return mock


def _build_force_text(n: int) -> str:
    """构造 n 行"X 说道"对话,触发 FORCE 阈值。"""
    return "\n".join(f"角色{i+1}说道：'第{i+1}句'。" for i in range(n))


# ─── 1. 不崩测试(pure f-string 修法验证) ─────────────────────────


def test_force_branch_does_not_crash_on_braces_in_samples(mocked_router):
    """污染样本含 { / } 时不抛 KeyError/ValueError(审计修 Medium#1 主目标)。"""
    text = _build_force_text(DIALOGUE_FORCE_THRESHOLD)
    text += '\n角色99说：\'包含 {{ "key": "value" }} 的对话\''
    task = {"target_length": "2000"}
    clean_text, issues, cost = run_normalizer(text, task)
    assert isinstance(clean_text, str)
    assert isinstance(issues, list)
    assert cost > 0


def test_force_branch_handles_json_like_samples(mocked_router):
    """污染样本含 JSON-like 字面量时不崩。"""
    text = _build_force_text(DIALOGUE_FORCE_THRESHOLD)
    text += '\n甲说：\'{"foo": "bar", "nested": {"k": 1}}\''
    task = {"target_length": "2000"}
    clean_text, issues, cost = run_normalizer(text, task)
    assert isinstance(clean_text, str)


def test_force_branch_handles_template_like_samples(mocked_router):
    """污染样本含 Python f-string 模板语法({var})时不崩。"""
    text = _build_force_text(DIALOGUE_FORCE_THRESHOLD)
    text += '\n乙说：\'调用 {name} 函数,参数 {arg[0]}\''
    task = {"target_length": "2000"}
    clean_text, issues, cost = run_normalizer(text, task)
    assert isinstance(clean_text, str)


def test_force_branch_handles_unclosed_brace_in_samples(mocked_router):
    """污染样本含未闭合 { 时不崩。"""
    text = _build_force_text(DIALOGUE_FORCE_THRESHOLD)
    text += '\n丙说：\'没闭合的 { 符号\''
    task = {"target_length": "2000"}
    clean_text, issues, cost = run_normalizer(text, task)
    assert isinstance(clean_text, str)


# ─── 2. FORCE 分支确实触发 LLM(mock 验证) ─────────────────────────


def test_force_branch_calls_router(mocked_router):
    """≥FORCE 阈值 + needs_llm=False → 应触发 LLM。"""
    text = _build_force_text(DIALOGUE_FORCE_THRESHOLD)
    task = {"target_length": "2000", "audit_mode": "full"}

    clean_text, issues, cost = run_normalizer(text, task)

    # router.call 至少被调用 1 次(对话癌替换)
    assert len(mocked_router.call_args_list) >= 1, "FORCE branch should call router"
    # cost 应包含 LLM 调用费用(0.05 from fixture)
    assert cost >= 0.05
    # issues 应含"强制替换"信号
    force_issues = [i for i in issues if "强制替换" in i]
    assert len(force_issues) >= 1


def test_warning_branch_does_not_call_router(mocked_router):
    """WARNING 但 < FORCE → 不触发 router(节省 LLM 预算)。"""
    text = _build_force_text(DIALOGUE_WARNING_THRESHOLD)  # 25 行
    task = {"target_length": "2000", "audit_mode": "full"}

    clean_text, issues, cost = run_normalizer(text, task)

    # WARNING < FORCE → router 不应被调用
    assert len(mocked_router.call_args_list) == 0, (
        "WARNING-only should not call router"
    )
    warning_issues = [i for i in issues if "预警" in i]
    assert len(warning_issues) >= 1
    assert cost == 0.0


def test_no_dialogue_branch_skips_both_issues(mocked_router):
    """无对话污染 → 既不报 issue 也不调 router。"""
    text = "今天天气真好。我出门散步。" * 50  # 无对话提示词
    task = {"target_length": "2000", "audit_mode": "full"}
    clean_text, issues, cost = run_normalizer(text, task)
    dialogue_issues = [i for i in issues if "对话癌" in i]
    assert len(dialogue_issues) == 0
    assert len(mocked_router.call_args_list) == 0


# ─── 3. needs_llm=True 跳过 FORCE 分支 ─────────────────────────


def test_force_branch_skipped_when_needs_llm_already_true(mocked_router):
    """needs_llm=True 时(已 second_pass_llm)不再二次走对话癌 LLM。

    关键不变量:即使输入文本触发 needs_llm=True,也不会第二次调 LLM。
    """
    # 注入 AI 腔让 needs_llm=True(避免对话癌 FORCE 分支跑)
    text = (
        "既然如此,我们继续。" * 20  # AI 词 → 触发 needs_llm
        + _build_force_text(5)  # 只有 5 行对话(不超 FORCE)
    )
    task = {"target_length": "2000", "audit_mode": "full"}

    clean_text, issues, cost = run_normalizer(text, task)

    # 关键断言:LLM 只被调 1 次(needs_llm=True 阻止第二次对话癌调用)
    assert len(mocked_router.call_args_list) == 1, (
        f"LLM should be called once (second_pass only), "
        f"got {len(mocked_router.call_args_list)} calls"
    )
    assert cost > 0
    force_issues = [i for i in issues if "强制替换" in i]
    assert len(force_issues) == 0


# ─── 4. dialogue_replace_prompt 内容验证 ─────────────────────────


def test_dialogue_replace_prompt_contains_all_4_strategies(mocked_router):
    """FORCE 分支的 system_prompt 必须含 4 种替换策略 + 真实 count + 无残留 {cnt}。"""
    text = _build_force_text(DIALOGUE_FORCE_THRESHOLD)
    task = {"target_length": "2000"}

    run_normalizer(text, task)

    # 取最后一次 router.call 的 system_prompt(应该是 FORCE 分支的)
    assert len(mocked_router.call_args_list) >= 1
    last_call = mocked_router.call_args_list[-1]
    sys_p = last_call.get("system_prompt", "")

    assert "动作卡位" in sys_p
    assert "神态神韵" in sys_p
    assert "情境穿插" in sys_p
    assert "语感辨识" in sys_p
    # 关键:不残留 {cnt} 占位符(.format 失败的痕迹)
    assert "{cnt}" not in sys_p
    # count 必须真实填入
    assert f"count={DIALOGUE_FORCE_THRESHOLD}" in sys_p