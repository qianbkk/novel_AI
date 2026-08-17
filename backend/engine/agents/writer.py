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
    get_early_chapter_style_guide,
    POV_LOCK_INSTRUCTION, get_pov_lock_instruction,
    UNIVERSAL_WRITING_RULES, EMOTION_CORES,
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
                      max_continues: int = 2,
                      response_format: str = "writer_json") -> Tuple[str, float]:
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
        response_format=response_format,
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


# 世界书注入预算（字符）。刻意保守：writer prompt 已经有 10+ 个约束块，
# 再堆下去 LLM 的遵守率会掉（07-Real-LLM-Testing.md §4 已观察到长 prompt
# 连输出格式都不守）。世界书只补「本章真正会用到的那几条设定原文」。
LOREBOOK_BUDGET_CHARS = 900

# P1-9（2026-08-17）：writer prompt 硬字符上限。审计 + 文档
# docs/wiki/03-Writing-Engine.md:198-200 已自承 writer prompt 7k-10k 字
# 时 LLM 守约束率塌方。6000 字高于常规 4-5k（留 buffer 给方法论 / 钩子等
# 必有指令），但低于 7k 红线。超限时 build_writer_prompt 末尾强制截断 +
# log.warning 留下信号（CLAUDE.md「失败要响亮」）。
WRITER_PROMPT_BUDGET_CHARS = 6000

# 任务 task-02：早期章节风格锚点激活阈值与黄金章节阈值
#   - 前 5 章：项目样本为空时 style_block 不会注入，但还有【黄金章节写作要点】（结构向）
#   - 第 6-20 章：项目样本仍为空时，注入题材惯例指南（题材惯例向）
#   - 第 21 章起：style_manager 已启用内部样本，正常走 style_block 路径
EARLY_STYLE_BLOCK_MAX_CHAPTER = 20


# ═══════════════════════════════════════════════════════
# v1.0 Stage F: writer prompt v2 — 4 个新 block
# ═══════════════════════════════════════════════════════
# 设计动机（docs/drafts/v1-quality-first-design.md § Stage 3）：
# 把 v1.0 前期工程的所有输出（genre_profile / theme_spine / macro_spine /
# expectation_ledger）落到 writer prompt 中，让 LLM 知道：
#   1. 写给谁（题材读者画像）
#   2. 写什么主题（恒久共性）
#   3. 本章在期待感推进中的位置（当前 arc）
#   4. 上一章/上几章留下的 show-item 必须接力（连续性）
# 不破坏 v0.5 调用方式（无 v1_* 字段时正常生成 prompt）。


def _build_genre_block(genre_profile: dict | None) -> str:
    """v1.0 Stage F Block 1：题材读者画像 block。

    含 reader_persona.primary + tone_preference + show_item_examples +
    taboo。设计师给的：'读者喜欢看的调调 / 读者会为自己脑海中的幻想买单'。
    """
    if not genre_profile:
        return ""

    parts: list[str] = ["【v1.0 题材读者画像】"]
    parts.append(f"题材：{genre_profile.get('genre', '未指定')}")

    persona = genre_profile.get("reader_persona") or {}
    if persona.get("primary"):
        parts.append(f"核心读者：{persona['primary']}")
    if persona.get("core_fantasy"):
        parts.append(f"读者幻想：{persona['core_fantasy']}")

    if genre_profile.get("tone_preference"):
        parts.append(f"调调：{genre_profile['tone_preference']}")

    examples = genre_profile.get("show_item_examples") or []
    if examples:
        parts.append("show-item 参考：" + " / ".join(str(e) for e in examples[:3]))

    taboo = genre_profile.get("taboo") or []
    if taboo:
        parts.append("禁忌：" + " | ".join(str(t) for t in taboo[:2]))

    return "\n".join(parts) + "\n"


def _build_theme_block(theme_spine: dict | None) -> str:
    """v1.0 Stage F Block 2：共性主题 block。

    含 theme_statement + resonance_anchors + expectation_arc.description。
    用户指导核心：'恒久的共性主题，能引起读者共鸣'。
    """
    if not theme_spine:
        return ""

    parts: list[str] = ["【v1.0 共性主题】"]
    if theme_spine.get("theme_statement"):
        parts.append(f"本书主题：{theme_spine['theme_statement']}")

    anchors = theme_spine.get("resonance_anchors") or []
    if anchors:
        parts.append("共鸣维度：" + " / ".join(str(a) for a in anchors))

    arc = theme_spine.get("expectation_arc") or {}
    if arc.get("description"):
        parts.append(f"期待感弧：{arc['description']}")

    return "\n".join(parts) + "\n"


def _build_expectation_block(macro_spine: dict | None, *, chapter_number: int) -> str:
    """v1.0 Stage F Block 3：期待感推进 block。

    按 chapter_number 找所属 arc，渲染 arc.name + theme_focus +
    expectation_progress。让 writer 知道'本章在期待感推进中的位置'。
    """
    if not macro_spine:
        return ""

    arcs = macro_spine.get("arcs") or []
    current = None
    for a in arcs:
        try:
            start = int(a.get("start_chapter", 0))
            end = int(a.get("end_chapter", 0))
        except (TypeError, ValueError):
            continue
        if start <= chapter_number <= end:
            current = a
            break

    parts: list[str] = ["【v1.0 当前期待感位置】"]
    if current:
        parts.append(
            f"本章属于 arc{current.get('arc_id', '?')} "
            f"「{current.get('name', '未命名')}」"
            f"（第 {current.get('start_chapter')} - {current.get('end_chapter')} 章）"
        )
        if current.get("theme_focus"):
            parts.append(f"弧主题焦点：{current['theme_focus']}")
        if current.get("expectation_progress"):
            parts.append(f"期待感推进：{current['expectation_progress']}")
        if current.get("main_conflict"):
            parts.append(f"本弧核心冲突：{current['main_conflict']}")
        if current.get("tone"):
            parts.append(f"调性：{current['tone']}")
    else:
        parts.append(f"本章（第 {chapter_number} 章）未归属任何 arc（请检查 macro_spine）")

    return "\n".join(parts) + "\n"


def _build_showitem_block(expectation_ledger: list | None, *, chapter_number: int) -> str:
    """v1.0 Stage F Block 4：show-item 接力 block。

    从 expectation_ledger 取最近 3 章的 show_item_used，提示 writer
    '本章应延续这些物件/动作，构成 show-item chain'。
    这是 show-don't-tell 落到 writer prompt 的物理机制。
    """
    if not expectation_ledger:
        return ""

    recent: list[dict] = []
    for entry in expectation_ledger:
        try:
            ch = int(entry.get("chapter_number", 0))
        except (TypeError, ValueError):
            continue
        if ch < chapter_number:
            recent.append(entry)
    recent = recent[-3:]  # 最近 3 章

    if not recent:
        return ""

    parts: list[str] = ["【v1.0 show-item 接力（前几章留下的物件/动作）】"]
    seen: set[str] = set()
    for entry in recent:
        ch = entry.get("chapter_number")
        items = entry.get("show_item_used") or []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                parts.append(f"第 {ch} 章 → {item}")

    if len(parts) == 1:
        # 没收集到任何 item
        return ""

    parts.append(
        "\n（本章应在合适处让上述物件/动作再次出现，构成读者'对这部小说的牵挂'"
        "——不要全部出现，至少 1 个能在本章找到呼应）"
    )
    return "\n".join(parts) + "\n"


def _build_early_style_block(task: dict, setting: dict) -> str:
    """当 chapter <= 20 且 style_samples 为空时，注入题材惯例指南。

    任务 task-02：style_manager 在 chapter >= 20 之前不启用内部高分样本，
    style_samples 主要来自外部 .txt 或 bootstrap 锚点。如果项目尚未提供
    外部样本 / bootstrap 也还没跑（绝大多数新项目第一次启动），style_samples
    是空 → writer 完全靠默认模板，没有任何"这一类网文应该长什么样"的具体指导。

    这里的早期风格指南按 platform/genre 差异化，给一段通用但具体的题材惯例。
    约束（CLAUDE.md）：模板不能含任何项目专名。{ch} 占位由 caller 渲染成具体章号。
    """
    if not isinstance(task, dict):
        return ""
    ch_num = task.get("chapter_number", 999)
    if not isinstance(ch_num, int) or ch_num > EARLY_STYLE_BLOCK_MAX_CHAPTER:
        return ""

    # 第 1-5 章已经由【黄金章节写作要点】覆盖结构向指导（保留旧契约）。
    # 第 6-20 章补题材惯例（不重复【黄金章节写作要点】的内容，避免 prompt 膨胀）。
    if ch_num <= 5:
        return ""

    platform = setting.get("platform", "fanqie") if isinstance(setting, dict) else "fanqie"
    genre    = setting.get("genre", "都市") if isinstance(setting, dict) else "都市"
    guide = get_early_chapter_style_guide(platform, genre)
    if not guide:
        return ""
    # {ch} 占位渲染成具体章号
    try:
        return guide.format(ch=ch_num)
    except (KeyError, IndexError):
        # 占位拼错兜底：去掉占位符直接给原文
        return guide.replace("{ch}", str(ch_num))

# 任务 task-01：RAG 检索注入预算（与 lorebook 同一量级）。
# orchestrator 写到 task["_rag_context"] 的每条 block 单独截断到这个字符上限，
# 整块总长再受 RAG_BUDGET_CHARS 控制——双层预算防御 prompt 膨胀。
RAG_BLOCK_BUDGET_CHARS = 300   # 单块最长渲染字符
RAG_TOTAL_BUDGET_CHARS = 900   # 整块总长（与 LOREBOOK_BUDGET_CHARS 对齐）


def _build_rag_block(task: dict) -> str:
    """把 orchestrator 写到 task["_rag_context"] 的 RAG 命中块渲染成 prompt 段。

    设计要点（任务 task-01）：
    - 受 RAG_TOTAL_BUDGET_CHARS 总预算 + RAG_BLOCK_BUDGET_CHARS 单块预算双层控制，
      防 prompt 膨胀（writer prompt 已经够长，长尾会让 LLM 连输出格式都不守）。
    - 没有命中（task 没有 _rag_context / 是空 list / 全空 text）→ 返回空串，
      跟 lorebook 一致：增强项缺失不阻断、不留空标题。
    - 每条块都标 chapter_no + 相似度，让 LLM 知道"哪一章的什么剧情相关"——
      直接套章节号就足以避免"凭空发明的旧剧情"被误认作既成事实。
    - 不复述 RAG 文本里的专名（prompt 是 setting 渲染的，文本本身可能含专名），
      这是 RAG 的固有特性：检索结果是历史章节原文，必然带项目专名。这是按用途
      注入的，不算跨项目污染（CLAUDE.md 红线是"prompt 模板里写死专名"，
      不是"运行时检索出来的内容里有专名"）。
    """
    rag_chunks = (task.get("_rag_context") or []) if isinstance(task, dict) else []
    if not rag_chunks:
        return ""
    lines: list[str] = []
    used = 0
    for c in rag_chunks:
        if not isinstance(c, dict):
            continue
        text = (c.get("text") or "").strip()
        if not text:
            continue
        # 单块硬截（防单 chunk 自身过长把整块预算吃光）
        if len(text) > RAG_BLOCK_BUDGET_CHARS:
            text = text[:RAG_BLOCK_BUDGET_CHARS].rstrip() + "…"
        ch_no = c.get("chapter_no", "?")
        sim = c.get("similarity", 0.0)
        try:
            sim_label = f"{float(sim):.2f}"
        except (TypeError, ValueError):
            sim_label = "n/a"
        line = f"  · [第{ch_no}章 | 相似度{sim_label}] {text}"
        # 整块总预算：到顶就停（不强行塞）
        if used + len(line) > RAG_TOTAL_BUDGET_CHARS:
            if not lines:
                # 第一块就超：硬截到剩余预算，避免 0 命中
                tail_budget = max(0, RAG_TOTAL_BUDGET_CHARS - used)
                if tail_budget > 0:
                    line = line[:tail_budget].rstrip() + "…"
                    lines.append(line)
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return ""
    return (
        "\n【相关历史剧情（按相似度命中，仅参考语感和事件关联，禁止直抄）】\n"
        + "\n".join(lines) + "\n"
    )


def _build_lorebook_block(task: dict, context: dict, setting: dict) -> str:
    """按本章实际内容触发的设定原文注入。

    2026-07-26：engine 的写作上下文原本只有 L2 摘要 + 人物状态子串匹配，
    没有任何按需检索 —— `memory/lorebook.py` 写好且带测试，却从未接线。
    检索层缺失是长篇设定漂移的主因（同类项目普遍是「摘要 + 检索 + 图谱」）。

    触发查询用「本章要写什么 + 最近发生了什么」拼成，而不是拿整本设定去灌：
    命中的才注入，且总量受 LOREBOOK_BUDGET_CHARS 限制。
    """
    try:
        from ..memory.lorebook import build_lorebook_from_setting, match as _lore_match
        entries = build_lorebook_from_setting(setting)
        if not entries:
            return ""
        query = "\n".join(str(x) for x in [
            task.get("chapter_goal", ""),
            task.get("core_conflict", ""),
            task.get("plot_progression", ""),
            task.get("shuang_description", ""),
            " ".join(task.get("main_characters") or []),
            context.get("recent_events", ""),
            context.get("last_chapter_ending", ""),
            " ".join(str(t) for t in (context.get("active_threads") or [])),
        ] if x)
        hits = _lore_match(entries, query, budget=LOREBOOK_BUDGET_CHARS)
        if not hits:
            return ""
        lines = "\n".join(f"  · {h['key']}：{h['content']}" for h in hits)
        return (
            "\n【本章相关设定（原文，必须与之一致；未列出的设定不要自行发明）】\n"
            + lines + "\n"
        )
    except Exception:
        # 世界书是增强项，失败不该阻断写作 —— 但要留下信号，不静默吞掉
        log.exception("_build_lorebook_block failed; 本章降级为无世界书注入")
        return ""


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
    # 世界书按需检索（2026-07-26 接线）：世界观速览给总貌，世界书给本章要用到的原文
    lorebook_block = _build_lorebook_block(task, context, setting)
    # RAG 向量检索（任务 task-01）：命中"过去章节里与本章相关的剧情块"。
    # 渲染在 lorebook 之后、主角状态之前——先给"过去剧情里有什么"，再给
    # "现在的状态是什么"，避免 LLM 看到状态后忽略历史参照。
    rag_block = _build_rag_block(task)

    genre_instr    = _genre_instruction(genre)
    if task.get("is_final_chapter"):
        # 一期修复（复盘 P3 配套）：终章不走常规钩子指导，明确收尾要求
        hook_guidance = ("【终章要求】这是全书最后一章：收束主线冲突、交代主要人物归宿、"
                         "回应开篇。以余韵作结，禁止留下新悬念或「下一章」钩子。\n")
    else:
        hook_guidance = _hook_guidance(task.get("ending_hook_type", "悬念钩"))
    voice_reminder = _character_voice_reminder(task.get("main_characters", []) or [], setting)

    # P2-12（2026-08-17）：过滤已死角色（防"角色已死又出现"）。
    # writer 没有 DB 访问，由 orchestrator 在调用前把 status=dead / missing
    # 的角色名写到 context["dead_characters"]（set/list）。此处过滤后渲染
    # 【本章出场人物】行，避免 LLM 把死人塞进 prompt 复活。
    _dead_set = set(context.get("dead_characters", []) or [])
    _live_main_chars = [
        n for n in (task.get("main_characters", []) or []) if n not in _dead_set
    ]
    if _live_main_chars:
        _dead_filtered_main_chars_line = (
            f"【本章出场人物】{', '.join(_live_main_chars)}"
        )
    else:
        _dead_filtered_main_chars_line = (
            "【本章出场人物】（无 — main_characters 全部已死/失踪，请 orchestrator 复核）"
        )

    # 2026-07-25 修 bug：原代码在 f-string 内用 `{{}}`（意图是 literal {}）
    # 但 Python 解析 `setting.get('world_setting') or {{}}` 时把 `{{}}` 当 set literal
    # （含 dict 元素），触发 TypeError: unhashable type: 'dict'。
    # 修法：在 f-string 外提前算好 _surface_world_name，用 {name} 占位
    #
    # 2026-07-26 修跨项目污染：这里的 fallback 原本是 "云州"，而下面的
    # 【世界观设定一致性硬约束】写死了测试项目的角色名（林渊 / 苏晚栀 / 孟浩 /
    # 顾青锋）。任何其它题材的项目都会收到「必须使用林渊」这条硬指令 —— 与同一
    # 个 prompt 里的【关键人物】块直接矛盾，诱导 LLM 串味写出别的书的专名。
    # 现在世界名与角色名全部按 setting 动态渲染，缺失时降级为不提名字的通用约束。
    ws = setting.get("world_setting") or {}
    _surface_world_name = ws.get("surface_world_name") or ""

    roster: list[str] = []
    for _name in ([mc_name] + list(task.get("main_characters") or [])
                  + [(_c or {}).get("name") for _c in (setting.get("key_characters") or [])]):
        if _name and _name not in roster:
            roster.append(str(_name))
    roster_str = " / ".join(roster[:8])

    _consistency_lines: list[str] = []
    if roster_str:
        _consistency_lines.append(
            f"- 本章必须严格使用上面【关键人物】列出的角色名（{roster_str}），不得改名、合并、拆分"
        )
    _consistency_lines.append("- 不得新增未列出的新角色名")
    if _surface_world_name:
        _consistency_lines.append(
            f"- 表世界「{_surface_world_name}」+ 里世界设定（如力量体系 / 势力名 / 地名）"
            "必须原样复用，不要重新发明同义词"
        )
    else:
        _consistency_lines.append(
            "- 世界观设定（力量体系 / 势力名 / 地名）必须原样复用上文给出的名称，不要重新发明同义词"
        )
    _consistency_lines.append(
        "- 严禁「吞设定」：本章正面提及的关键人物 / 伏笔 / 地名，本章正文里至少要出现一次"
    )
    world_consistency_block = "【世界观设定一致性硬约束】\n" + "\n".join(_consistency_lines)

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
    # 章节 task 自带的 setting_constraints / forbidden_actions 是 outline 的硬契约。
    # 旧实现只读取 memory context.relevant_forbidden，导致真实模型完全看不到
    # 「本章不得直接读取尸内记忆」等规划禁令，能在单元测试全绿时写出越界正文。
    task_constraints = task.get("setting_constraints", []) or []
    task_forbidden = task.get("forbidden_actions", []) or []
    memory_forbidden = context.get("relevant_forbidden", []) or []
    forbidden = list(dict.fromkeys(
        str(item).strip()
        for item in [*task_forbidden, *memory_forbidden]
        if str(item).strip()
    ))
    forbidden_str = "\n".join(f"  ✗ {f}" for f in forbidden) or "  无"
    task_constraints_str = (
        "\n".join(f"  ✓ {item}" for item in task_constraints if str(item).strip())
        or "  无"
    )
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

    # 2026-07-25 战略审视 Commit 2：三线 + 信息差 + 锚点归一渲染
    thread = task.get("narrative_thread") or "main"
    thread_label = {"main": "主线推进", "side": "支线铺陈", "hidden": "暗线埋笔"}.get(thread, "主线推进")
    thread_block = (
        f"【本章叙事线 narrative_thread={thread}】{thread_label}\n"
        + "⚠ 本章所有情节必须明确属于这条线 —— 主线/支线/暗线不能混着来。\n"
    )

    asym_raw = task.get("info_asymmetry")
    if isinstance(asym_raw, dict) and asym_raw:
        reader_knows = asym_raw.get("reader_knows") or []
        protagonist_knows = asym_raw.get("protagonist_knows") or []
        reveals = asym_raw.get("reveals_at_chapter")
        info_block = "【本章信息差 info_asymmetry】\n"
        if reader_knows:
            info_block += "读者已知（但主角不知）：" + " / ".join(str(x) for x in reader_knows) + "\n"
        if protagonist_knows:
            info_block += "主角已知（但读者暂不知）：" + " / ".join(str(x) for x in protagonist_knows) + "\n"
        if reveals:
            info_block += f"本章要揭示的秘密：{reveals} 章后揭晓\n"
        info_block += "⚠ 利用信息差制造张力 —— 读者知道的比主角多 = 焦虑，反之 = 期待。\n"
    else:
        info_block = ""

    anchor = task.get("anchor_to")
    anchor_block = (
        f"【本章锚点 anchor_to=arc{anchor}】本章所有线索都服务于这条主线弧。\n"
        if anchor else ""
    )

    # 2026-07-25 战略审视 Commit 3：情绪锚点渲染
    emotion_core_raw = task.get("emotion_core")
    if isinstance(emotion_core_raw, str) and emotion_core_raw in EMOTION_CORES:
        emotion_core = emotion_core_raw
    else:
        emotion_core = "压抑"  # 默认兜底（无值/越界/None 都走这里）
    emotion_intensity = task.get("emotion_intensity")
    if not isinstance(emotion_intensity, int) or not 1 <= emotion_intensity <= 5:
        emotion_intensity = 3  # 默认中等
    emotion_desc = EMOTION_CORES.get(emotion_core, "")
    intensity_label = ["", "轻微", "低", "中等", "高", "爆点"][emotion_intensity]
    emotion_block = (
        f"【本章情绪锚点 emotion={emotion_core}×{emotion_intensity}({intensity_label})】\n"
        + f"情绪核心：{emotion_desc}\n"
        + "⚠ 本章所有情节都要明确指向这个情绪。\n"
        + "⚠ 避免连续 3 章同 emotion_core(情绪疲劳→读者弃书)。\n"
    )

    system_dynamic = genre_instr + world_one_liner

    # v1.0 Stage F：组装 4 个新 block（genre / theme / expectation / show-item）
    # 注入位置：【当前写作任务】之前，让 writer 先'知道读者是谁/写什么主题'，
    # 再进入具体任务指令。
    v1_genre_block = _build_genre_block(setting.get("v1_genre_profile"))
    v1_theme_block = _build_theme_block(setting.get("v1_theme_spine"))
    v1_expectation_block = _build_expectation_block(
        setting.get("v1_macro_spine"),
        chapter_number=task.get("chapter_number", 0),
    )
    v1_showitem_block = _build_showitem_block(
        context.get("v1_expectation_ledger"),
        chapter_number=task.get("chapter_number", 0),
    )
    v1_blocks = v1_genre_block + v1_theme_block + v1_expectation_block + v1_showitem_block

    user_prompt = f"""{v1_blocks}【当前写作任务】
第{task.get('chapter_number', 0)}章 ｜ 定位：{task.get('chapter_role','')} ｜ 目标字数：{task.get('target_length','2000-2200')}字
章节目标：{task.get('chapter_goal','')}
核心冲突：{core_conflict}
情感迁移：{emotion_shift}
主线推进：{plot_progression}
是否弧高潮：{'是（全力以赴）' if task.get('is_arc_climax') else '否'}
{stakes_block}{dilemma_block}{thread_block}{info_block}{anchor_block}{emotion_block}

{world_block}{lorebook_block}{rag_block}【主角状态】
姓名：{mc_name} ｜ 等级：{context.get('protagonist_level','凡人')} ｜ 点数：{context.get('protagonist_points',0)}
道具：{', '.join(context.get('inventory', []) or []) or '无'}
场景：{context.get('scene_location','未指定')} ｜ 时间：{context.get('time_context','未指定')}

【上章结尾】
{context.get('last_chapter_ending','（本书开篇）')}

【近期事件（最近10章摘要）】
{context.get('recent_events','无')}

【当前剧情线】
{threads_str}

【本章人物状态】
{char_states_str}

【爽点要求】
类型：{task.get('shuang_type','未指定') or '未指定'}
描述：{task.get('shuang_description','')}

{hook_guidance}

{_dead_filtered_main_chars_line}

{world_consistency_block}

【本章设定约束（outline 硬契约，正文必须明确遵守）】
{task_constraints_str}

【本章禁止事项（outline 硬契约，任何爽点或钩子都不得突破）】
{forbidden_str}

【即将到期的伏笔（请在本章埋下呼应）】
{foreshadow_str}
{foreshadow_worklist}
{voice_reminder}
{('【历史背景参考】\n' + cold_str) if cold_str else ''}
{style_block}

{methodology_block}

{get_pov_lock_instruction(mc_name)}
现在开始写第{task.get('chapter_number', 0)}章。

【输出格式】严格 JSON，不要任何 markdown fence 或额外文字：
{{"title": "本章标题（4-15字，含本章核心冲突或转折）", "body": "正文第一段...", "title_alts": ["备选标题 1", "备选标题 2"]}}

约束：
- title 必须是本章独特的事件 / 决策 / 转折（不能是「发展」「推进剧情」这种通用词）
- title 不要写「第N章」前缀
- body 直接写正文，不要任何"以下是..."等元描述
- 若 LLM 忘了 JSON 格式，我会从你的文本里兜底提取，所以内容质量优先"""

    # 任务 task-02：早期章节风格锚点（第 6-20 章 + 项目风格样本为空时）。
    # 与黄金三章的结构向指南互补，这里给题材惯例。
    # 没注入场景：style_samples 非空 → style_block 已经接管；ch > 20 → style_manager
    # 启用内部样本；两者都不是 → 这里填这段题材惯例。
    _style_samples_empty = not (context.get("style_samples") or [])
    _early_style_block = _build_early_style_block(task, setting)
    if _early_style_block and _style_samples_empty:
        user_prompt += "\n" + _early_style_block

    # 黄金章节专属提示：第1-5章项目风格样本稀少，补充网文早期章节结构要点
    _ch_num = task.get("chapter_number", 999) if isinstance(task, dict) else 999
    if _ch_num <= 5:
        _hook_type = task.get("ending_hook_type", "") if isinstance(task, dict) else ""
        user_prompt += (
            f"\n【黄金章节写作要点（第{_ch_num}章，前5章专属）】\n"
            "1. 开篇前200字内建立明确冲突、悬念或目标，禁止纯环境铺垫或回忆。\n"
            "2. 每个对话段落后跟1-2句人物内心反应或动作，推动节奏而非停顿。\n"
            f"3. 章末钩子类型「{_hook_type or '信息钩'}」：最后一段必须让读者想翻页，"
            "用新信息揭露、情绪转折或直接威胁实现。\n"
            "4. 全章节奏：每800-1000字安排一个小反转或冲突升级，不允许连续平铺。\n"
            "5. 禁止大段说教、世界观说明书、或超过3行的独白。"
        )

    # P1-9（2026-08-17）：writer prompt 硬字符上限。
    # 审计 + docs/wiki/03-Writing-Engine.md:198-200 已自承：
    # recent_summaries 5→10 + RAG 900 + lorebook 900 + world 600 + style_samples
    # 4500 + methodology 4 招 3500 堆叠 → 7k-10k 字，LLM 长 prompt 守约束率塌方
    # （漏章节字数 / POV 锁 / ending_hook_type）。硬上限 + 超限 warning 防止
    # 静默污染下游。预算选 6000 字：略高于常规 4-5k 留 buffer，但低于 7k 红线。
    if len(user_prompt) > WRITER_PROMPT_BUDGET_CHARS:
        logging.getLogger("novel_ai.engine.agents.writer").warning(
            "writer prompt overflow budget: %d > %d (chapter %d)；"
            "强制截断到预算长度（优先保留核心指令，砍末尾 methodology）",
            len(user_prompt), WRITER_PROMPT_BUDGET_CHARS,
            task.get("chapter_number", 0) if isinstance(task, dict) else 0,
        )
        user_prompt = user_prompt[:WRITER_PROMPT_BUDGET_CHARS]

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
