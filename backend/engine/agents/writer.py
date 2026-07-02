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
import os
import sys
from typing import Tuple

from ..llm.router import LLMRouter
from ..memory.manager import get_writer_context, maybe_update_style_samples
from ..config.prompt_templates import (
    get_genre_instruction, get_hook_guidance,
    get_character_voice_reminder, UNIVERSAL_WRITING_RULES,
)

# Active router is set by backend.engine.graph.build_project_graph()
_ACTIVE_ROUTER: LLMRouter | None = None


def set_active_router(router: LLMRouter) -> None:
    """Wire a router into this module so call_llm goes through it."""
    global _ACTIVE_ROUTER
    _ACTIVE_ROUTER = router


def _get_router() -> LLMRouter:
    """Bridge: backend has no global api_client; the active router does the call."""
    if _ACTIVE_ROUTER is None:
        # P1 fallback: a fresh, env-only router. Works for the smoke test path
        # where there is no DB-driven config.
        return LLMRouter()
    return _ACTIVE_ROUTER


def _call_with_budget(agent_name: str, system: str, user: str,
                      target_chars: int, *, temperature: float = 0.82,
                      tolerance: int = 200,
                      max_continues: int = 2) -> Tuple[str, float]:
    """Length-budget call (写入路径字数控制). 写作 agent 专用.

    网络抖动重试：router._post_with_retry 已经有 tenacity 3 次 retry，
    但其退避是指数 1-10s（最多 30s 总耗时），如果服务端挂掉超过 30s
    仍然失败。这里加一层 agent-level retry：3 次（每次 60s 内）失败后
    再 sleep 30s 重试一轮。避免一次瞬时网络抖动就让整章 escalate。
    """
    import time as _time
    import httpx as _httpx
    last_exc: Exception | None = None
    for attempt in range(2):  # 1st try + 1 retry
        try:
            return _get_router().call_with_length_budget(
                agent_name=agent_name,
                system_prompt=system,
                user_prompt=user,
                target_chars=target_chars,
                tolerance=tolerance,
                temperature=temperature,
                max_continues=max_continues,
            )
        except (_httpx.TransportError, _httpx.HTTPStatusError, ConnectionError) as e:
            last_exc = e
            if attempt < 1:
                # 第一轮失败 → sleep 30s 再试
                # 理由：MiniMax 偶尔出现 30-60s 短暂不可用，
                # 30s sleep 是经验值（再长用户等不及）
                _time.sleep(30)
    # 两次都失败 → 抛最后一次异常，让 orchestrator 走 escalate
    raise last_exc  # type: ignore[misc]


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
        return f"题材：{genre}。\n"


def _hook_guidance(hook_type: str) -> str:
    try:
        return get_hook_guidance(hook_type)
    except Exception:
        return f"结尾钩子类型：{hook_type}。请在结尾埋下一个让读者想看下一章的钩子。\n"


def _character_voice_reminder(characters: list, setting: dict) -> str:
    try:
        return get_character_voice_reminder(characters, setting)
    except Exception:
        return ""


# Cacheable system prefix — sent to LLM but eligible for prompt cache (Anthropic only)
WRITER_CACHE_PREFIX = """\
你是一位专业的网络小说作者。
""" + UNIVERSAL_WRITING_RULES


def build_writer_prompt(task: dict, context: dict, setting: dict) -> tuple[str, str]:
    """Build (cached_system_prefix, dynamic_user_prompt)."""
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

现在开始写第{task.get('chapter_number', 0)}章正文（直接输出正文，无需标题）："""

    return system_dynamic, user_prompt


def run_writer(task: dict, memory: dict, setting_core: dict) -> tuple[str, float]:
    """Generate chapter body. Returns (text, cost_usd).

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
        context = memory if isinstance(memory, dict) else {}

    # Trigger style sample refresh (P1: no-op)
    try:
        maybe_update_style_samples(task.get("chapter_number", 0), novel_id)
    except Exception:
        pass

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
    return _call_with_budget(
        agent_name="writer",
        system=system_dynamic,
        user=user_prompt,
        target_chars=target_chars,
        temperature=0.82,
        tolerance=200,
        max_continues=2,
    )
