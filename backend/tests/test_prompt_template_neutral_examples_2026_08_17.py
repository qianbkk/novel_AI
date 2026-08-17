"""test_prompt_template_neutral_examples_2026_08_17.py

P0-2 修复验证：prompt 模板与 agent fallback 不再硬编码「云州」宇宙专名。

CLAUDE.md 红线：「prompt 里不得出现任何具体项目的专名（角色名/地名/世界名）」。
本文件覆盖 5 个泄漏源：
  1. engine.config.prompt_templates.HOOK_TYPES — 7 个钩子 example 都曾是专名
  2. engine.config.prompt_templates.GENRE_WRITING_INSTRUCTIONS[都市"] 禁止写法行
  3. engine.agents.title_generator.TITLE_GEN_SYSTEM_PROMPT 示例
  4. engine.agents.outline.run_outline 中的主角/等级 fallback
  5. engine.agents.rewriter 中的主角/等级 fallback
"""

from __future__ import annotations

import pytest


# 与 test_writer_prompt_no_project_leak_2026_07_26.LEAK_TOKENS 保持一致；
# 这里重复列出是为了本文件自包含（脱离其它测试也能直接跑）
LEAK_TOKENS = [
    "林渊", "苏晚栀", "孟浩", "顾青锋", "云州",
    "陆承", "贺苗", "苏云溪", "章廷", "临江市",
    "感债者", "识债者", "债主委员会", "周芸",
]


# ── 1. prompt_templates.HOOK_TYPES 7 个 example 都不得含专名 ─────────────────

@pytest.mark.parametrize("hook_name", [
    "悬念钩", "危机钩", "信息钩", "情感钩",
    "反转钩", "升级钩", "对抗钩",
])
@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_hook_types_example_no_leak(hook_name, token):
    """7 种钩子的 example 都不应含任何项目专名（CLAUDE.md 红线）。"""
    from engine.config.prompt_templates import HOOK_TYPES
    example = HOOK_TYPES[hook_name]["example"]
    assert token not in example, (
        f"HOOK_TYPES['{hook_name}']['example'] 泄漏专名: {token}"
    )


def test_hook_types_example_still_has_substantive_content():
    """修复不是把 example 全删 —— 仍需保留钩子的实质内容（不能空泛）。"""
    from engine.config.prompt_templates import HOOK_TYPES
    for name, h in HOOK_TYPES.items():
        assert len(h["example"]) >= 10, f"HOOK_TYPES['{name}']['example'] 太空泛"
        assert "。" in h["example"] or "\n" in h["example"], (
            f"HOOK_TYPES['{name}']['example'] 缺标点/换行（不像句范例）"
        )


# ── 2. 都市题材 禁止写法 行不得含专名 ─────────────────────────

@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_genre_dushi_forbidden_line_no_leak(token):
    """都市题材的「禁止写法」举例不得含具体项目专名。"""
    from engine.config.prompt_templates import GENRE_WRITING_INSTRUCTIONS
    dushi = GENRE_WRITING_INSTRUCTIONS["都市"]
    assert token not in dushi, f"都市题材写作指令泄漏专名: {token}"


def test_genre_dushi_forbidden_line_still_present():
    """修复不能把「禁止写法」整段删 —— 必须保留至少 1 条反例。"""
    from engine.config.prompt_templates import GENRE_WRITING_INSTRUCTIONS
    dushi = GENRE_WRITING_INSTRUCTIONS["都市"]
    assert "禁止写法" in dushi
    # 至少包含「心想」这类核心反例信号
    assert "心想" in dushi


# ── 3. title_generator 系统 prompt 示例不得含专名 ─────────────────────────

@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_title_generator_system_prompt_no_leak(token):
    """title_generator 的 system prompt 示例不能含项目专名。"""
    from engine.agents import title_generator as tg
    src = tg.TITLE_GEN_SYSTEM_PROMPT if hasattr(tg, "TITLE_GEN_SYSTEM_PROMPT") else ""
    if not src:
        # 退而求其次：扫整个模块源码
        import inspect
        src = inspect.getsource(tg)
    assert token not in src, f"title_generator 模块泄漏专名: {token}"


# ── 4. outline 的主角/等级 fallback 不得含专名 ─────────────────────────

@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_outline_module_no_leak(token):
    """outline 模块（包括默认 fallback 字符串）不能含项目专名。"""
    import inspect
    from engine.agents import outline
    src = inspect.getsource(outline)
    assert token not in src, f"engine.agents.outline 泄漏专名: {token}"


# ── 5. rewriter 的主角/等级 fallback 不得含专名 ─────────────────────────

@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_rewriter_module_no_leak(token):
    """rewriter 模块不能含项目专名。"""
    import inspect
    from engine.agents import rewriter
    src = inspect.getsource(rewriter)
    assert token not in src, f"engine.agents.rewriter 泄漏专名: {token}"


# ── 6. get_hook_guidance / get_genre_instruction 渲染输出不含专名 ─────────

@pytest.mark.parametrize("hook_name", [
    "悬念钩", "危机钩", "信息钩", "情感钩",
    "反转钩", "升级钩", "对抗钩",
])
@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_get_hook_guidance_renders_no_leak(hook_name, token):
    """get_hook_guidance(hook_type) 返回的字符串不能含专名。"""
    from engine.config.prompt_templates import get_hook_guidance
    out = get_hook_guidance(hook_name)
    assert token not in out, f"get_hook_guidance({hook_name!r}) 泄漏: {token}"


@pytest.mark.parametrize("genre", ["都市", "玄幻", "萌宝甜宠"])
@pytest.mark.parametrize("token", LEAK_TOKENS)
def test_get_genre_instruction_renders_no_leak(genre, token):
    """get_genre_instruction(genre) 返回的字符串不能含专名。"""
    from engine.config.prompt_templates import get_genre_instruction
    out = get_genre_instruction(genre)
    assert token not in out, f"get_genre_instruction({genre!r}) 泄漏: {token}"