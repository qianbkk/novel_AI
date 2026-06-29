"""Writer Agent — 章节正文生成

Migrated from novel_AI/agents/writer_agent.py. P1 simplification:
  - No dependency on novel_AI/config/prompt_templates.py — genre/hook
    guidance are inlined as a single UNIVERSAL_WRITING_RULES constant.
  - No dependency on novel_AI/memory/memory_manager.py — context
    is read from backend.engine.memory.stub.get_writer_context(),
    which returns a flat in-memory dict.
  - Prompt Cache prefix kept (Anthropic only).
  - P2 will swap in prompt_templates.py + real memory_manager.
"""
from __future__ import annotations
import os
import sys
from typing import Tuple

from ..llm.router import LLMRouter
from ..memory.stub import get_writer_context, maybe_update_style_samples

# Active router is set by backend.engine.graph.build_project_graph()
_ACTIVE_ROUTER: LLMRouter | None = None


def set_active_router(router: LLMRouter) -> None:
    """Wire a router into this module so call_llm goes through it."""
    global _ACTIVE_ROUTER
    _ACTIVE_ROUTER = router


def _call_llm(agent_name: str, system: str, user: str, max_tokens: int,
              temperature: float, *, use_cache: bool = False,
              cached_system: str | None = None) -> Tuple[str, float]:
    """Bridge: backend has no global api_client; the active router does the call."""
    if _ACTIVE_ROUTER is None:
        # P1 fallback: a fresh, env-only router. Works for the smoke test path
        # where there is no DB-driven config.
        router = LLMRouter()
    else:
        router = _ACTIVE_ROUTER
    return router.call(
        agent_name=agent_name,
        system_prompt=system,
        user_prompt=user,
        max_tokens=max_tokens,
        temperature=temperature,
        use_cache=use_cache,
        cached_system=cached_system,
    )


# ── Prompt templates (P1 inlined; P2 move to prompts/writer.py) ──
UNIVERSAL_WRITING_RULES = """\
你的文字风格：节奏紧凑、对话自然、动作流畅、爽点清晰、钩子有力。
- 不要出现"说道""他心想"等 AI 常见词
- 对话要像真人在说话，不用套话
- 动作要带感官描写（声音、触感、气息）
- 爽点之后立刻接新的钩子
- 每章结尾给读者留一个"想看下一章"的理由
"""


# Genre-specific quick guidance (P1 minimal; P2 expand from novel_AI/config/prompt_templates.py)
_GENRE_GUIDANCE = {
    "玄幻": "修炼等级推进、灵根/法宝/丹药体系、宗门与散修之争。",
    "仙侠": "渡劫、飞升、灵山/魔界、师徒/道侣。",
    "都市": "职场/商场/家族/校园、现实规则束缚下的逆袭。",
    "科幻": "技术逻辑自洽、未来社会结构、AI/星际/时间线。",
    "历史": "史实细节、官制/兵制/礼仪、权谋。",
    "言情": "情感递进、误会与解开、关系成长。",
    "悬疑": "线索铺陈与回收、视角错位、留白。",
    "武侠": "江湖门派、武功修炼、侠义抉择。",
    "奇幻": "种族/魔法/世界规则、冒险旅程。",
    "末世": "生存压力、资源争夺、人性考验。",
    "游戏": "系统/技能树/副本/数值。",
    "军事": "战术/编制/装备/政治。",
}


def _genre_instruction(genre: str) -> str:
    g = _GENRE_GUIDANCE.get(genre, _GENRE_GUIDANCE["都市"])
    return f"题材：{genre}。{g}\n"


def _hook_guidance(hook_type: str) -> str:
    return f"结尾钩子类型：{hook_type}。请在结尾埋下一个让读者想看下一章的钩子。\n"


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

【{hook_guidance}】

【本章出场人物】{', '.join(task.get('main_characters', []) or [])}

【本章禁止事项】
{forbidden_str}

【即将到期的伏笔（请在本章埋下呼应）】
{foreshadow_str}
{('【历史背景参考】\n' + cold_str) if cold_str else ''}
{style_block}

现在开始写第{task.get('chapter_number', 0)}章正文（直接输出正文，无需标题）："""

    return system_dynamic, user_prompt


def run_writer(task: dict, memory: dict, setting_core: dict) -> tuple[str, float]:
    """Generate chapter body. Returns (text, cost_usd)."""
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

    target = str(task.get("target_length", "2000-2200"))
    max_words = int(target.split("-")[-1]) if "-" in target else (int(target) if target.isdigit() else 2200)
    max_tokens = max(3000, int(max_words * 2.2))

    return _call_llm(
        agent_name="writer",
        system=system_dynamic,
        user=user_prompt,
        max_tokens=max_tokens,
        temperature=0.82,
        use_cache=True,
        cached_system=WRITER_CACHE_PREFIX,
    )
