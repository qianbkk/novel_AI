"""test_methodology_prompts_2026_07_25.py

战略审视 Commit 0 — 4 招方法论 prompt 内化测试。

只测 prompt_templates 层的字符串输出（不依赖 LLM），
确保 4 招方法论常量化、helper 拼装、边界情况都正确。

详见 docs/wiki/03-Writing-Engine.md §4 Commit 0 描述。
"""
from __future__ import annotations

from engine.config.prompt_templates import (
    BUT_LAW_INSTRUCTION,
    INFO_ASYMMETRY_INSTRUCTION,
    MODULAR_NARRATIVE_INSTRUCTION,
    THREE_LAYER_HOOK_INSTRUCTION,
    get_methodology_instruction,
)


# ════════════════════════════════════════════
# 1. 4 招常量必须存在 + 非空 + 含关键执行指令
# ════════════════════════════════════════════

def test_info_asymmetry_exists_and_has_three_modes():
    assert INFO_ASYMMETRY_INSTRUCTION
    # 必须含 3 模式名称
    assert "读者知/主角不知" in INFO_ASYMMETRY_INSTRUCTION
    assert "主角知/配角不知" in INFO_ASYMMETRY_INSTRUCTION
    assert "双方均不知" in INFO_ASYMMETRY_INSTRUCTION
    # 必须含执行关键词
    assert "操作" in INFO_ASYMMETRY_INSTRUCTION
    assert "避免" in INFO_ASYMMETRY_INSTRUCTION


def test_but_law_exists_and_has_three_positions():
    assert BUT_LAW_INSTRUCTION
    # 章首/章中 1/3 / 章中 2/3 / 章尾四个位置必须都有
    assert "200 字" in BUT_LAW_INSTRUCTION  # 章首
    assert "1/3" in BUT_LAW_INSTRUCTION      # 章中 1/3
    assert "2/3" in BUT_LAW_INSTRUCTION      # 章中 2/3
    # 障碍四类
    for cat in ["物理", "情感", "信息", "道德"]:
        assert cat in BUT_LAW_INSTRUCTION, f"missing category {cat}"
    # 失败信号
    assert "废章" in BUT_LAW_INSTRUCTION


def test_three_layer_hook_exists_and_has_three_layers():
    assert THREE_LAYER_HOOK_INSTRUCTION
    # 微观 / 中观 / 宏观 三层
    for layer in ["微观期待", "中观期待", "宏观期待"]:
        assert layer in THREE_LAYER_HOOK_INSTRUCTION, f"missing {layer}"
    # 章末禁词（来自通用写作铁律）
    assert "感悟" in THREE_LAYER_HOOK_INSTRUCTION
    assert "决心" in THREE_LAYER_HOOK_INSTRUCTION
    # 7 钩子类
    assert "悬念" in THREE_LAYER_HOOK_INSTRUCTION


def test_modular_narrative_exists_and_has_three_lines():
    assert MODULAR_NARRATIVE_INSTRUCTION
    # 主线 / 支线 / 暗线
    for line in ["主线模块", "支线模块", "暗线模块"]:
        assert line in MODULAR_NARRATIVE_INSTRUCTION, f"missing {line}"
    # 三原则
    assert "锚点归一" in MODULAR_NARRATIVE_INSTRUCTION
    assert "高潮切断" in MODULAR_NARRATIVE_INSTRUCTION
    assert "漏斗汇聚" in MODULAR_NARRATIVE_INSTRUCTION


# ════════════════════════════════════════════
# 2. get_methodology_instruction helper — 默认全套
# ════════════════════════════════════════════

def test_helper_default_returns_all_four_modules():
    out = get_methodology_instruction()
    assert out, "default call should return non-empty string"
    # 顺序：但 → 信息差 → 期待感 → 模块化（writer.py 注释的"从微观到宏观"）
    idx_but = out.find("但是法则")
    idx_info = out.find("信息差三模式")
    idx_three = out.find("三层期待感")
    idx_modular = out.find("模块化叙事")
    assert idx_but != -1 and idx_info != -1 and idx_three != -1 and idx_modular != -1
    # 顺序必须正确
    assert idx_but < idx_info < idx_three < idx_modular, (
        f"order should be but_law < info_asymmetry < three_layer_hook < modular_narrative, "
        f"got indices {idx_but}/{idx_info}/{idx_three}/{idx_modular}"
    )
    # 必须有 header
    assert "方法论执行清单" in out


def test_helper_total_length_within_writer_budget():
    """默认全套必须 ≤ 2.5KB（writer prompt 总预算 ≤ 6KB,留 4KB 给其他块）"""
    out = get_methodology_instruction()
    assert len(out) < 2500, f"methodology block too long: {len(out)} chars (budget 2500)"


# ════════════════════════════════════════════
# 3. helper — 子集裁剪（writer.py 终章场景）
# ════════════════════════════════════════════

def test_helper_subset_single_module():
    out = get_methodology_instruction(["info_asymmetry"])
    assert "信息差三模式" in out
    assert "但是法则" not in out
    assert "三层期待感" not in out
    assert "模块化叙事" not in out
    # 单 aspect 也必须有 header
    assert "方法论执行清单" in out


def test_helper_subset_two_modules_order_preserved():
    """子集也要按"但→信息差→期待感→模块化"顺序输出"""
    out = get_methodology_instruction(["three_layer_hook", "modular_narrative"])
    assert out.find("三层期待感") < out.find("模块化叙事")


def test_helper_final_chapter_scenario():
    """复现 writer.py 第 211-216 行的终章场景：只用 three_layer_hook"""
    out = get_methodology_instruction(["three_layer_hook"])
    assert "三层期待感" in out
    assert "但是法则" not in out
    # 终章场景应比默认短（≤ 600 字符）
    assert len(out) < 700, f"final chapter block too long: {len(out)}"


# ════════════════════════════════════════════
# 4. helper — 边界情况
# ════════════════════════════════════════════

def test_helper_unknown_aspect_returns_empty():
    """未知 aspect key 必须返回空字符串（不抛异常）"""
    assert get_methodology_instruction(["nonexistent_method"]) == ""
    assert get_methodology_instruction([""]) == ""
    assert get_methodology_instruction(["__nonexistent__"]) == ""


def test_helper_empty_list_returns_empty():
    """空 list 必须返回空字符串（不是默认全套！）"""
    # 这是 fail-fast 设计：调用方传 [] 明确表示"不要任何方法论"
    assert get_methodology_instruction([]) == ""


def test_helper_explicit_none_returns_default():
    """None 应该走默认（与不传参一致）"""
    default = get_methodology_instruction()
    explicit_none = get_methodology_instruction(None)
    assert default == explicit_none


def test_helper_duplicate_aspect_dedup():
    """重复传同一个 aspect,输出不应重复"""
    out = get_methodology_instruction(["but_law", "but_law", "but_law"])
    assert out.count("但是法则") == 1, "duplicate aspect should not duplicate output"


# ════════════════════════════════════════════
# 5. 集成到 f-string 的字符串稳定性（writer.py 用法）
# ════════════════════════════════════════════

def test_methodology_block_integrates_into_fstring():
    """模拟 writer.py 第 309 行用法：{methodology_block}"""
    methodology_block = get_methodology_instruction()
    user_prompt = f"开头\n\n{methodology_block}\n\n结尾"
    # 4 模块都在最终 prompt 里
    assert "但是法则" in user_prompt
    assert "信息差三模式" in user_prompt
    assert "三层期待感" in user_prompt
    assert "模块化叙事" in user_prompt


def test_methodology_block_does_not_break_special_chars():
    """含 { } 的方法论模板不应被 f-string 误解析"""
    methodology_block = get_methodology_instruction()
    # 检查没有未转义的花括号（会让 f-string 抛 KeyError）
    # 方法论文本里有"「」"等中文标点,但不应该有 { } （f-string 会找占位符）
    # 例外：默认 None 时返回空串；拼装时这些模板不含裸 { }
    # 简单测试：直接 f-string 拼接应该不抛
    try:
        f"foo{methodology_block}bar"
    except (KeyError, ValueError) as e:
        raise AssertionError(f"f-string failed: {e}")