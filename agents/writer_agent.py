"""
Writer Agent V2 — 章节正文生成
集成：prompt_templates / memory_manager按需检索 / Prompt Cache / 风格样本
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_client import call_llm
from memory.memory_manager import get_writer_context, maybe_update_style_samples
from config.prompt_templates import (
    get_genre_instruction, get_hook_guidance, get_character_voice_reminder,
    UNIVERSAL_WRITING_RULES
)

# ── 可缓存的系统提示前缀（慢变，适合Prompt Cache）──
WRITER_CACHE_PREFIX = """你是一位专业的网络小说作者，擅长都市系统流爽文写作。
你的文字风格：节奏紧凑、对话自然、动作流畅、爽点清晰、钩子有力。
""" + UNIVERSAL_WRITING_RULES

def build_writer_prompt(task: dict, context: dict, setting: dict) -> tuple[str, str]:
    """
    构造Writer提示词，返回 (cached_system_prefix, dynamic_user_prompt)
    cached_system_prefix: 放入system的可缓存部分
    dynamic_user_prompt:  每次不同的章节具体要求
    """
    mc        = setting.get("protagonist", {})
    genre     = setting.get("genre", "都市")
    mc_name   = mc.get("name", "陆承")

    # 动态部分：题材指令 + 角色口癖提醒 + 钩子指导
    genre_instr   = get_genre_instruction(genre)
    hook_guidance = get_hook_guidance(task.get("ending_hook_type", "悬念钩"))
    voice_reminder = get_character_voice_reminder(task.get("main_characters", []), setting)

    # 风格样本（来自memory_manager）
    style_samples = context.get("style_samples", [])
    style_src     = context.get("style_samples_source", "external")
    style_block   = ""
    if style_samples:
        style_block = f"\n【风格参考（{style_src}样本，模仿语感节奏，不抄内容）】\n"
        style_block += "\n---\n".join(s[:600] for s in style_samples[:2])

    # 按需检索的上下文（~1500 tokens）
    char_states_str = "\n".join(f"  {k}: {v}" for k, v in context.get("character_states", {}).items()) or "  无"
    threads_str     = "\n".join(f"  - {t}" for t in context.get("active_threads", [])[:6]) or "  无"
    forbidden_str   = "\n".join(f"  ✗ {f}" for f in context.get("relevant_forbidden", [])) or "  无"
    foreshadow_str  = "\n".join(f"  → {f}" for f in context.get("foreshadowing_due_soon", [])) or "  无"
    cold_str        = context.get("cold_summary", "")

    system_dynamic = genre_instr + (voice_reminder or "")

    user_prompt = f"""【当前写作任务】
第{task['chapter_number']}章 ｜ 定位：{task['chapter_role']} ｜ 目标字数：{task.get('target_length','2000-2200')}字
章节目标：{task['chapter_goal']}
是否弧高潮：{'是（全力以赴）' if task.get('is_arc_climax') else '否'}

【主角状态】
姓名：{mc_name} ｜ 等级：{context.get('protagonist_level','感债者')} ｜ 点数：{context.get('protagonist_points',0)}
道具：{', '.join(context.get('inventory',[])) or '无'}
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
类型：{task.get('shuang_type','未指定')}
描述：{task['shuang_description']}

【{hook_guidance}】

【本章出场人物】{', '.join(task.get('main_characters',[]))}

【本章禁止事项】
{forbidden_str}

【即将到期的伏笔（请在本章埋下呼应）】
{foreshadow_str}
{('【历史背景参考】\n' + cold_str) if cold_str else ''}
{style_block}

现在开始写第{task['chapter_number']}章正文（直接输出正文，无需标题）："""

    return system_dynamic, user_prompt


def run_writer(task: dict, memory: dict, setting_core: dict) -> tuple[str, float]:
    """
    生成章节正文
    memory 参数兼容旧接口（dict），内部自动转为新格式
    """
    novel_id = setting_core.get("novel_id", "renqingzhai_v1")

    # 优先使用新版memory_manager的按需检索
    try:
        context = get_writer_context(novel_id, task)
    except Exception:
        # 兜底：直接使用传入的memory dict
        context = memory if isinstance(memory, dict) else {}

    # 触发风格样本更新（第20章/每30章）
    maybe_update_style_samples(task.get("chapter_number", 0), novel_id)

    system_dynamic, user_prompt = build_writer_prompt(task, context, setting_core)

    target = task.get("target_length", "2000-2200")
    max_words = int(target.split("-")[-1]) if "-" in str(target) else int(target)
    max_tokens = max(3000, int(max_words * 2.2))

    text, cost = call_llm(
        agent_name="writer",
        system_prompt=system_dynamic,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=0.82,
        use_cache=True,
        cached_system=WRITER_CACHE_PREFIX,
    )
    return text, cost
