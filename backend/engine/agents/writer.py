"""Writer Agent — 章节正文生成

Migrated from novel_AI/agents/writer_agent.py. P2 expansion:
  - Now uses backend.engine.config.prompt_templates for genre/hook/voice
    guidance (the canonical place per CLAUDE.md rule).
  - Now uses backend.engine.memory.manager for context retrieval
    (real L2 hot/cold + L5 + style samples).
  - Prompt Cache prefix kept (Anthropic only).

P3 expansion: 字数控制接入生成路径。
  - 旧：`router.call()` 写到哪算哪，事后校验（擦屁股）
  - 新：`router.call_with_length_budget()` 写入路径截断+续写（预防）
  - 配 _truncate_at_sentence_boundary 避免硬切在字中间
"""
from __future__ import annotations
import logging
import os
import sys
from typing import Tuple

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..memory.manager import get_writer_context, maybe_update_style_samples
from ..config.prompt_templates import (
    get_genre_instruction, get_hook_guidance,
    get_character_voice_reminder, get_methodology_instruction,
    UNIVERSAL_WRITING_RULES,
)
# 简化（#45）：writer.py 之前自己实现 _call_with_budget（约 30 行重试逻辑），
# 跟 rewriter.py 几乎一样。统一抽到 engine.utils.call_with_budget_with_retry。
# 顺便去掉 writer.py 自己的 _ACTIVE_ROUTER 模块状态（跟 rewriter 对齐用
# engine.llm_router.get_active_router()）——之前 llm_router.install 已经调过
# writer.set_active_router，删了之后所有 agent 都从同一处读 active router。
from ..utils import call_with_budget_with_retry


log = logging.getLogger("novel_ai.engine.writer")


# #45 简化：去掉 writer.py 自己的 _ACTIVE_ROUTER + set_active_router + _get_router。
# 现在跟 rewriter.py / 其他 agent 一样用 engine.llm_router.get_active_router()，
# 单一来源（之前 llm_router.install 会调 writer.set_active_router，但每个 agent
# 各存一份 _ACTIVE_ROUTER 容易漂移）。
def _get_router() -> LLMRouter:
    """Bridge: 从 engine.llm_router 拿 active router；fallback 到 env-only 新实例
    （smoke test 路径，没有 DB-driven 配置）。"""
    router = get_active_router()
    if router is None:
        return LLMRouter()
    return router


def _call_with_budget(agent_name: str, system: str, user: str,
                      target_chars: int, *, temperature: float = 0.82,
                      tolerance: int = 200,
                      max_continues: int = 2) -> Tuple[str, float]:
    """Length-budget call (写入路径字数控制). 写作 agent 专用.

    #45 简化：实际逻辑已抽到 engine.utils.call_with_budget_with_retry，
    这里只是薄包装 + writer 专属 default temperature。
    """
    return call_with_budget_with_retry(
        router=_get_router(),
        agent_name=agent_name,
        system=system,
        user=user,
        target_chars=target_chars,
        temperature=temperature,
        tolerance=tolerance,
        max_continues=max_continues,
    )


# ── Prompt templates (P2: import from config.prompt_templates) ──
# Keep a local alias so writers / callers don't break if config is missing.
UNIVERSAL_WRITING_RULES_LOCAL = """\
你的文字风格：节奏紧凑、对话自然、动作流畅、爽点清晰、钩子有力。
- 不要出现"说道""他心想"等 AI 常见词
- 对话要像真人在说话，不用套话
- 动作要带感官描写（声音、触感、气息）
- 爽点之后立刻接新的钩子
- 每章结尾给读者留一个"想看下一章"的理由
"""


def _genre_instruction(genre: str) -> str:
    """P2: pull from config.prompt_templates; fall back to inline default."""
    try:
        return get_genre_instruction(genre)
    except Exception:
        log.exception("_genre_instruction fallback: get_genre_instruction raised for genre=%r", genre)
        return f"题材：{genre}。\n"


def _hook_guidance(hook_type: str) -> str:
    try:
        return get_hook_guidance(hook_type)
    except Exception:
        log.exception("_hook_guidance fallback: get_hook_guidance raised for hook_type=%r", hook_type)
        return f"结尾钩子类型：{hook_type}。请在结尾埋下一个让读者想看下一章的钩子。\n"


def _character_voice_reminder(characters: list, setting: dict) -> str:
    try:
        return get_character_voice_reminder(characters, setting)
    except Exception:
        log.exception("_character_voice_reminder fallback: get_character_voice_reminder raised")
        return ""


# Cacheable system prefix — sent to LLM but eligible for prompt cache (Anthropic only)
WRITER_CACHE_PREFIX = """\
你是一位专业的网络小说作者。
""" + UNIVERSAL_WRITING_RULES


def _world_one_liner(setting: dict) -> str:
    """世界观一句话提要（注入 system_dynamic，让 LLM 始终知道这是哪本书）。"""
    ws = setting.get("world_setting", {}) or {}
    hidden = ws.get("hidden_world_name", "")
    surface = ws.get("surface_world_name", "")
    ps = setting.get("power_system", {}) or {}
    pname = ps.get("name", "")
    if not (hidden or surface or pname):
        return ""
    return f"\n【本书世界观】表世界「{surface or '—'}」+ 里世界「{hidden or '—'}」；力量体系「{pname or '—'}」。\n"


def _build_world_block(task: dict, setting: dict) -> str:
    """把 setting_package 里的结构化世界观压成 writer 能用的【世界观速览】块。

    一期修复（2026-07-16）：之前 writer prompt 完全不含 world_setting /
    power_system / key_characters —— 世界观注入通道不存在。
    策略：不全量灌（token 爆炸），按「本章出场人物 + 力量体系 + 世界独特元素」
    做相关性压缩，预算 ~400-600 字。setting 为空时返回空串（向后兼容空壳 driver）。
    """
    parts: list[str] = []

    ws = setting.get("world_setting", {}) or {}
    hidden = ws.get("hidden_world_name", "")
    surface = ws.get("surface_world_name", "")
    history = (ws.get("hidden_world_history", "") or "")[:150]
    uniq = ws.get("unique_elements", []) or []
    if hidden or surface or uniq:
        line = f"世界：表世界「{surface}」/ 里世界「{hidden}」" if (hidden or surface) else ""
        if history:
            line += f"。{history}"
        if line:
            parts.append(line)
        if uniq:
            parts.append("独特设定：" + "；".join(str(u) for u in uniq[:4]))

    ps = setting.get("power_system", {}) or {}
    levels = ps.get("levels", []) or []
    if levels:
        lv_str = " → ".join(f"{l.get('name','?')}" for l in levels)
        parts.append(f"力量体系「{ps.get('name','')}」：{lv_str}（资源：{ps.get('currency','')}）")

    # 本章关键人物设定（吞设定修复 2026-07-20）：
    # 审计 /simplify #1 — 之前只注入 main_characters 命中的配角，导致配角完全
    # 不在 writer 视野里，LLM 写出来章节里没出现顾青锋等关键人物。
    # 现在：main_characters 全量注入；其它 key_characters 也注入（带 cap 5 控预算），
    # LLM 至少知道「这些人物存在」才能自然引用，符合「严禁吞设定」的业务约束。
    main_chars = set(task.get("main_characters", []) or [])
    key_chars = setting.get("key_characters", []) or []
    char_lines: list[str] = []
    seen_names: set[str] = set()
    # 先 main_characters（出场必注入），再去重补 key_characters 凑够 5 个
    for c in key_chars:
        cname = c.get("name", "")
        if not cname or cname in seen_names:
            continue
        quirks = "、".join(c.get("speech_quirks", [])[:2])
        bg = (c.get("background", "") or "")[:60]
        char_lines.append(
            f"  {cname}（{c.get('role','')}）：{bg}"
            + (f"｜口癖：{quirks}" if quirks else "")
        )
        seen_names.add(cname)
        if len(char_lines) >= 8:  # main + 配角 上限 8，控制 token
            break
    if char_lines:
        parts.append(
            "【关键人物（main_characters 必出场，其它配角本章可自然引用，不要凭空增删）】\n"
            + "\n".join(char_lines)
        )

    if not parts:
        return ""
    return "【世界观速览（写作时必须遵守，不得违背）】\n" + "\n".join(parts) + "\n"


def build_writer_prompt(task: dict, context: dict, setting: dict) -> tuple[str, str]:
    """Build (cached_system_prefix, dynamic_user_prompt).

    修订 2026-07-16：让 LLM 输出 JSON {title, body} 而不是纯文本，
    解决 300 章实测暴露的"标题全是「第N章·发展·第N章：推进剧情」"问题。
    JSON 输出更鲁棒：避免 LLM 漂移输出 markdown fence / 多余前缀。
    """
    mc      = setting.get("protagonist", {}) or {}
    genre   = setting.get("genre", "都市")
    mc_name = mc.get("name", "主角")

    # 一期修复（根因 #1：世界观注入断线）：writer 之前只读 protagonist/genre，
    # planner 产出的 world_setting / power_system / key_characters 全部不进 prompt，
    # 300 章实测里 writer 眼中的世界只剩"主角等级"一个字符串。
    # 这里拼一个紧凑的【世界观速览】块（预算 ~400-600 字），全量丢弃 → 摘要注入。
    world_block = _build_world_block(task, setting)

    genre_instr    = _genre_instruction(genre)
    if task.get("is_final_chapter"):
        # 一期修复（复盘 P3 配套）：终章不走常规钩子指导，明确收尾要求
        hook_guidance = ("【终章要求】这是全书最后一章：收束主线冲突、交代主要人物归宿、"
                         "回应开篇。以余韵作结，禁止留下新悬念或「下一章」钩子。\n")
    else:
        hook_guidance = _hook_guidance(task.get("ending_hook_type", "悬念钩"))
    voice_reminder = _character_voice_reminder(task.get("main_characters", []) or [], setting)

    # 2026-07-25 修 bug：原代码在 f-string 内用 `{{}}`（意图是 literal {}）
    # 但 Python 解析 `setting.get('world_setting') or {{}}` 时把 `{{}}` 当 set literal
    # （含 dict 元素），触发 TypeError: unhashable type: 'dict'。
    # 修法：在 f-string 外提前算好 _surface_world_name，用 {name} 占位
    ws = setting.get("world_setting") or {}
    _surface_world_name = ws.get("surface_world_name") or "云州"

    # 4 招方法论（2026-07-25 战略审视 Commit 0）：但/但是 / 信息差 / 3 层期待感 / 模块化叙事
    # 默认全开；如果 task 标记了 "disable_methodology"（如终章/草稿模式）可降级为子集
    if task.get("is_final_chapter"):
        # 终章：方法论约束可以放宽（不需要"但是"再制造冲突，需要收束）
        methodology_block = get_methodology_instruction(["three_layer_hook"])
    else:
        methodology_block = get_methodology_instruction()

    style_samples = context.get("style_samples", []) or []
    style_block = ""
    if style_samples:
        style_block = "\n【风格参考（仅模仿语感，不抄内容）】\n"
        style_block += "\n---\n".join(str(s)[:600] for s in style_samples[:2])

    char_states = context.get("character_states", {}) or {}
    char_states_str = "\n".join(f"  {k}: {v}" for k, v in char_states.items()) or "  无"
    threads = context.get("active_threads", []) or []
    threads_str = "\n".join(f"  - {t}" for t in threads[:6]) or "  无"
    forbidden = context.get("relevant_forbidden", []) or []
    forbidden_str = "\n".join(f"  ✗ {f}" for f in forbidden) or "  无"
    foreshadow = context.get("foreshadowing_due_soon", []) or []
    foreshadow_str = "\n".join(f"  → {f}" for f in foreshadow) or "  无"
    cold_str = context.get("cold_summary", "") or ""

    # 一期修复（按需章包，参考 linshi.txt C 节）：从 worldview 里抽
    # 本章出场人物相关的小切片，避免全量塞入。世界观总览只在 system_dynamic
    # 给一句话提要，具体细节由 main_characters 切片注入。
    world_one_liner = _world_one_liner(setting)

    # 二期：从 task.foreshadowing_ops 渲染「本章伏笔工作单」
    from .foreshadow_helper import format_foreshadow_ops_for_prompt
    foreshadow_worklist = format_foreshadow_ops_for_prompt([task])

    # 二期：emotion_shift / core_conflict / plot_progression 是新字段；
    # 老 task 可能没有，做空值兼容。
    emotion_shift = task.get("emotion_shift") or "未指定"
    core_conflict = task.get("core_conflict") or "未指定"
    plot_progression = task.get("plot_progression") or "未指定"

    # 2026-07-25 战略审视 Commit 1：stakes + dilemma 渲染
    # 老 task 无此字段 → stakes_block = ''（writer prompt 长度不受影响）
    stakes_raw = task.get("stakes")
    if isinstance(stakes_raw, dict) and stakes_raw:
        if_lose = stakes_raw.get("if_lose") or []
        if_win = stakes_raw.get("if_win") or []
        stakes_block = (
            "【本章筹码 stakes】\n"
            + ("失败将失去：" + " / ".join(str(x) for x in if_lose) + "\n" if if_lose else "")
            + ("成功将获得：" + " / ".join(str(x) for x in if_win) + "\n" if if_win else "")
            + "⚠ 主角的每一个关键决策必须显式呼应上述筹码（失去 = 焦虑，获得 = 期待）。\n"
        )
    else:
        stakes_block = ""

    dilemma_raw = task.get("dilemma")
    if isinstance(dilemma_raw, dict) and dilemma_raw and "option_a" in dilemma_raw:
        opt_a = dilemma_raw.get("option_a", "")
        opt_b = dilemma_raw.get("option_b", "")
        both_cost = dilemma_raw.get("both_cost", "")
        dilemma_block = (
            "【本章两难 dilemma】\n"
            + f"选项A：{opt_a}\n"
            + f"选项B：{opt_b}\n"
            + (f"两者皆失：{both_cost}\n" if both_cost else "")
            + "⚠ 本章不必揭晓最终选择，但必须让读者**明确感知**主角面临的取舍张力。\n"
        )
    else:
        dilemma_block = ""

    system_dynamic = genre_instr + world_one_liner

    user_prompt = f"""【当前写作任务】
第{task.get('chapter_number', 0)}章 ｜ 定位：{task.get('chapter_role','')} ｜ 目标字数：{task.get('target_length','2000-2200')}字
章节目标：{task.get('chapter_goal','')}
核心冲突：{core_conflict}
情感迁移：{emotion_shift}
主线推进：{plot_progression}
是否弧高潮：{'是（全力以赴）' if task.get('is_arc_climax') else '否'}
{stakes_block}{dilemma_block}

{world_block}【主角状态】
姓名：{mc_name} ｜ 等级：{context.get('protagonist_level','凡人')} ｜ 点数：{context.get('protagonist_points',0)}
道具：{', '.join(context.get('inventory', []) or []) or '无'}
场景：{context.get('scene_location','未指定')} ｜ 时间：{context.get('time_context','未指定')}

【上章结尾】
{context.get('last_chapter_ending','（本书开篇）')}

【近期事件（5章摘要）】
{context.get('recent_events','无')}

【当前剧情线】
{threads_str}

【本章人物状态】
{char_states_str}

【爽点要求】
类型：{task.get('shuang_type','未指定') or '未指定'}
描述：{task.get('shuang_description','')}

{hook_guidance}

【本章出场人物】{', '.join(task.get('main_characters', []) or [])}

【世界观设定一致性硬约束】
- 本章必须严格使用上面【关键人物】列出的角色名（林渊 / 苏晚栀 / 孟浩 / 顾青锋 等），不得改名、合并、拆分
- 不得新增未列出的新角色名
- 表世界「{_surface_world_name}」+ 里世界设定（如力量体系 / 势力名 / 地名）必须原样复用，不要重新发明同义词
- 严禁「吞设定」：本章正面提及的关键人物 / 伏笔 / 地名，本章正文里至少要出现一次

【本章禁止事项】
{forbidden_str}

【即将到期的伏笔（请在本章埋下呼应）】
{foreshadow_str}
{foreshadow_worklist}
{voice_reminder}
{('【历史背景参考】\n' + cold_str) if cold_str else ''}
{style_block}

{methodology_block}
现在开始写第{task.get('chapter_number', 0)}章。

【输出格式】严格 JSON，不要任何 markdown fence 或额外文字：
{{"title": "本章标题（4-15字，含本章核心冲突或转折）", "body": "正文第一段...", "title_alts": ["备选标题 1", "备选标题 2"]}}

约束：
- title 必须是本章独特的事件 / 决策 / 转折（不能是「发展」「推进剧情」这种通用词）
- title 不要写「第N章」前缀
- body 直接写正文，不要任何"以下是..."等元描述
- 若 LLM 忘了 JSON 格式，我会从你的文本里兜底提取，所以内容质量优先"""

    return system_dynamic, user_prompt


def _extract_title(raw: str, fallback_goal: str = "") -> tuple[str, str]:
    """从 writer 输出里提取 (title, body)。

    2026-07-23 simplify：4 级降级（严格 JSON / markdown fence / 「【标题】: xxx」前缀 /
    首句兜底）抽到 `utils.extract_llm_response_body` 统一处理，本函数保留为兼容
    旧调用点的薄壳，逻辑委托给 extract_llm_response_body。

    失败时用 chapter_goal 派生占位标题，避免下游报 "NoneType has no attribute"。
    """
    from ..utils import extract_llm_response_body, _first_line_as_title as _flt
    if not raw or not raw.strip():
        return _goal_to_title(fallback_goal), ""
    body, title = extract_llm_response_body(
        raw, fallback_goal=fallback_goal, fallback_title=_goal_to_title(fallback_goal),
    )
    if not title:
        title = _flt(body) if body else _goal_to_title(fallback_goal)
    return title[:50], body


def _first_line_as_title(text: str) -> str:
    """从正文首行提取一个简洁标题（去掉 markdown heading / scene label / 第N章 前缀）。

    2026-07-23 simplify：实现已抽到 `utils._first_line_as_title`，
    本函数保留为向后兼容的薄壳（import 旧调用点不破）。
    """
    from ..utils import _first_line_as_title as _flt
    return _flt(text) or "未命名章节"


def _goal_to_title(goal: str) -> str:
    """从 chapter_goal 派生标题。goal 为空时返回「未命名章节」。"""
    if not goal or not goal.strip():
        return "未命名章节"
    s = goal.strip()
    # 去掉「第N章」前缀
    import re as _re
    s = _re.sub(r"^第\d+[章卷][\s::：]*", "", s)
    return s[:30] if len(s) <= 30 else s[:27] + "…"


def run_writer(task: dict, memory: dict, setting_core: dict) -> tuple[str, str, float]:
    """Generate chapter body + title. Returns (text, title, cost_usd).

    修订 2026-07-16：3 元组返回，让 orchestrator 把 title 写进 meta.json，
    chapter_import 从 meta.title 派生数据库的 Chapter.title，
    修复「章节标题全是 placeholder」的 bug。

    P3: 字数控制已接入生成路径（不再是事后校验）。
    - 从 task.target_length（如 "2000-2200"）取中位数作为 target_chars
    - 用 call_with_length_budget 而非 call：写入路径 truncate + 续写
    - 截断时优先停在「。」「！」「？」处（_truncate_at_sentence_boundary）
    """
    novel_id = setting_core.get("novel_id", "default")

    # P1: in-memory context; P2: real L2 retrieval
    try:
        context = get_writer_context(novel_id, task)
    except Exception:
        log.exception("_build_system_and_user fallback: get_writer_context raised for novel=%s", novel_id)
        context = memory if isinstance(memory, dict) else {}

    # Trigger style sample refresh (P1: no-op)
    try:
        maybe_update_style_samples(task.get("chapter_number", 0), novel_id)
    except Exception:
        # style sample 是 no-op 装饰，失败不应阻断主流程，但仍要 log
        log.warning("maybe_update_style_samples failed (non-critical)", exc_info=True)

    system_dynamic, user_prompt = build_writer_prompt(task, context, setting_core)

    # 解析 target_length → target_chars（取范围中位数）
    target = str(task.get("target_length", "2000-2200"))
    if "-" in target:
        try:
            lo, hi = target.split("-")
            target_chars = (int(lo) + int(hi)) // 2
        except (ValueError, TypeError):
            target_chars = 2200
    else:
        target_chars = int(target) if target.isdigit() else 2200

    # 写入路径 length-budget call（替代原 router.call()）
    raw_text, cost = _call_with_budget(
        agent_name="writer",
        system=system_dynamic,
        user=user_prompt,
        target_chars=target_chars,
        temperature=0.82,
        tolerance=200,
        max_continues=2,
    )

    # 提取 title（JSON / markdown fence / 标题前缀 / 首句 4 级降级）
    title, body = _extract_title(raw_text, fallback_goal=task.get("chapter_goal", ""))
    return body, title, cost
