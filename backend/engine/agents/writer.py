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
    get_character_voice_reminder, UNIVERSAL_WRITING_RULES,
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


def build_writer_prompt(task: dict, context: dict, setting: dict) -> tuple[str, str]:
    """Build (cached_system_prefix, dynamic_user_prompt).

    修订 2026-07-16：让 LLM 输出 JSON {title, body} 而不是纯文本，
    解决 300 章实测暴露的"标题全是「第N章·发展·第N章：推进剧情」"问题。
    JSON 输出更鲁棒：避免 LLM 漂移输出 markdown fence / 多余前缀。
    """
    mc      = setting.get("protagonist", {}) or {}
    genre   = setting.get("genre", "都市")
    mc_name = mc.get("name", "主角")

    genre_instr    = _genre_instruction(genre)
    hook_guidance  = _hook_guidance(task.get("ending_hook_type", "悬念钩"))
    voice_reminder = _character_voice_reminder(task.get("main_characters", []) or [], setting)

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

    system_dynamic = genre_instr

    user_prompt = f"""【当前写作任务】
第{task.get('chapter_number', 0)}章 ｜ 定位：{task.get('chapter_role','')} ｜ 目标字数：{task.get('target_length','2000-2200')}字
章节目标：{task.get('chapter_goal','')}
是否弧高潮：{'是（全力以赴）' if task.get('is_arc_climax') else '否'}

【主角状态】
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

【本章禁止事项】
{forbidden_str}

【即将到期的伏笔（请在本章埋下呼应）】
{foreshadow_str}
{voice_reminder}
{('【历史背景参考】\n' + cold_str) if cold_str else ''}
{style_block}

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

    三级降级，最大限度容忍 LLM 漂移：
    1. 严格 JSON 解析（首选）
    2. markdown fence 包着的 JSON
    3. 「【标题】: xxx」前缀 + 正文
    4. 正文首句压缩成标题（兜底）

    失败时用 chapter_goal 派生占位标题，避免下游报 "NoneType has no attribute"。
    """
    import json as _json
    import re as _re

    if not raw or not raw.strip():
        return _goal_to_title(fallback_goal), ""

    text = raw.strip()

    # 1) 尝试直接 JSON 解析
    try:
        d = _json.loads(text)
        if isinstance(d, dict):
            title = (d.get("title") or "").strip()
            body = (d.get("body") or "").strip()
            if title and body:
                return title[:50], body
            if title and not body:
                # 给了 title 但没 body → title 用 JSON 的，body 用原文本
                return title[:50], text
            if body and not title:
                # 给了 body 但没给 title（或者 LLM 给了空 title） → 用正文首句
                return _first_line_as_title(body), body
    except _json.JSONDecodeError:
        pass

    # 2) markdown fence 包裹的 JSON
    fence = _re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, _re.DOTALL)
    if fence:
        try:
            d = _json.loads(fence.group(1))
            title = (d.get("title") or "").strip()
            body = (d.get("body") or "").strip()
            if body:
                return (title or _first_line_as_title(body))[:50], body
        except _json.JSONDecodeError:
            pass

    # 3) "【标题】: xxx" 前缀（兼容半角/全角冒号）
    m = _re.match(r"【标题】\s*[:：]\s*(.+?)(?:\n|$)", text)
    if m:
        title = m.group(1).strip()[:50]
        body = text[m.end():].strip()
        if body:
            return title, body
    # 也支持 "标题: xxx"（无书名号）
    m = _re.match(r"^标题\s*[:：]\s*(.+?)(?:\n|$)", text)
    if m:
        title = m.group(1).strip()[:50]
        body = text[m.end():].strip()
        if body:
            return title, body

    # 4) 兜底：用正文首句作为标题
    return _first_line_as_title(text), text


def _first_line_as_title(text: str) -> str:
    """从正文首行提取一个简洁标题（去掉 markdown heading / scene label / 第N章 前缀）。"""
    import re as _re
    for line in text.splitlines():
        s = line.strip()
        # 跳过空行 / 太短的纯符号行（如 "----" / "***"）
        if not s or len(s) <= 1:
            continue
        if s in ("---", "***", "===", "___", "----", "****", "####"):
            continue
        # 跳过 markdown heading
        s = _re.sub(r"^#{1,6}\s+", "", s)
        # 跳过「第N章 标题」这种自身带章节号的
        s = _re.sub(r"^第\d+[章卷]\s*", "", s)
        # 跳过 scene label 【xxx】
        if s.startswith("【") and s.endswith("】") and len(s) <= 30:
            continue
        # 截断到第一个句号/问号/感叹号
        s = _re.split(r"[。！？!?]", s)[0].strip()
        if not s:
            continue
        return s[:30]
    return "未命名章节"


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
