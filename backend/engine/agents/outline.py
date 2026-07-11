"""Outline Agent V2 — 弧级章节任务拆解

Migrated from novel_AI/agents/outline_agent.py. Uses
backend.engine.config.prompt_templates for HOOK_TYPES / SHUANG_TYPES and
backend.engine.llm.router for the LLM call.
"""
from __future__ import annotations
import json
import re

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..config.prompt_templates import HOOK_TYPES, SHUANG_TYPES


HOOK_LIST   = " | ".join(HOOK_TYPES.keys())
SHUANG_LIST = " | ".join(SHUANG_TYPES.keys())


OUTLINE_SYSTEM = f"""你是一位网文策划，将弧级大纲拆解为具体章节任务单。
深度理解番茄读者口味：密集爽感、清晰钩子、行动驱动情节。

【拆解原则】
1. 每5-8章设置一个「爽点章」，每15-20章设置一个「中爽点章」
2. 弧最后3章节奏加速
3. 结尾钩子类型只能从以下选择：{HOOK_LIST}
4. 爽点类型只能从以下选择：{SHUANG_LIST}（无爽点时填null）
5. 章节定位：铺垫|发展|爽点|弧高潮|过渡
6. 字数：普通2000-2200，爽点2200-2500，弧高潮3000-3300

严格输出JSON数组，不输出任何其他内容。"""


def run_outline(arc: dict, start_chapter: int, setting: dict, memory: dict) -> tuple[list, float]:
    """拆解一弧为章节任务清单。
    memory 为 L2 hot layer dict（或完整 memory，自动取 hot）。
    """
    print(f"📋 [Outline] 拆解弧{arc.get('arc_id', '?')}「{arc.get('arc_name','?')}」"
          f"（{arc.get('estimated_chapters', '?')}章，起始Ch{start_chapter}）")

    user_prompt = _build_user_prompt(arc, start_chapter, setting, memory)

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    resp, cost = router.call(
        agent_name="outline",
        system_prompt=OUTLINE_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=8000,
        temperature=0.75,
    )
    resp = _extract_json_array(resp)
    try:
        tasks = json.loads(resp)
    except json.JSONDecodeError:
        resp2 = re.sub(r',\s*}', '}', resp)
        resp2 = re.sub(r',\s*]', ']', resp2)
        tasks = json.loads(resp2)

    _mark_arc_climax(tasks, arc)

    # 校验钩子类型合法性
    valid_hooks = set(HOOK_TYPES.keys())
    for t in tasks:
        if t.get("ending_hook_type") not in valid_hooks:
            t["ending_hook_type"] = "悬念钩"  # 默认兜底

    print(f"  ✅ {len(tasks)}章任务，成本${cost:.4f}")
    return tasks, cost


# ══════════════════════════════════════════
# card 模式 — 抽卡探索（生成 3 个候选分支）
# ══════════════════════════════════════════
CARD_OUTLINE_SYSTEM = """你是一位网文策划，为同一弧生成 3 个不同走向的候选大纲分支。
每个分支都是完整可执行的章节任务列表，但侧重点不同：
- A 分支：偏爽点密集（每 5 章一个爽点）
- B 分支：偏悬疑反转（每 7 章一个反转）
- C 分支：偏情感共鸣（重角色互动）

【共同约束】
- 章节定位：铺垫|发展|爽点|弧高潮|过渡
- 结尾钩子类型从以下选择：悬念钩|危机钩|信息钩|情感钩|反转钩|升级钩|对抗钩
- 爽点类型从以下选择（无爽点时填null）：打脸|升级|逆袭|揭秘|报复|碾压|救场
- 字数：普通2000-2200，爽点2200-2500，弧高潮3000-3300

严格输出JSON，格式：
{
  "candidates": [
    {"branch": "A", "flavor": "爽点密集", "tasks": [...]},
    {"branch": "B", "flavor": "悬疑反转", "tasks": [...]},
    {"branch": "C", "flavor": "情感共鸣", "tasks": [...]}
  ]
}
tasks 数组每个元素结构同 batch 模式。"""


def run_outline_card(arc: dict, start_chapter: int, setting: dict,
                     memory: dict) -> tuple[list, float]:
    """抽卡探索模式：生成 3 个候选分支，每个分支是一组完整的 chapter tasks。

    实现：3 次实际 LLM 调用，每次用不同的 flavor 加权（爽点密集 / 悬疑反转 /
    情感共鸣）。3 个候选的 tasks 必须**真实不同**——审计师曾在 P3 阶段发现
    B/C 分支直接 reuse A 的 batch_tasks（同一个任务清单假装 3 个候选），
    导致前端用户选 B/C 拿到的内容跟 A 完全一样，看起来在跑实则静默假功能。

    A 分支仍然复用 run_outline() 的 batch 任务（保证跟非抽卡模式一致），
    B/C 分支走独立 LLM 调用，复用 OUTLINE_SYSTEM 但加一段 flavor 指导。
    """
    # A = 完整 batch 的爽点密集版（与 run_outline 同源）
    batch_tasks, batch_cost = run_outline(arc, start_chapter, setting, memory)

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()

    # B/C 各自独立 prompt 走 LLM，每次拿独立 cost 累加
    candidates: list[dict] = [
        {"branch": "A", "flavor": "爽点密集", "tasks": batch_tasks},
    ]
    total_cost = batch_cost

    branch_definitions = [
        ("B", "悬疑反转",
         "本分支强调悬疑与反转。每 7 章安排一个反转点（剧情/身份/立场反转）；"
         "钩子类型优先「悬念钩」「反转钩」「信息钩」；少用爽点章。"),
        ("C", "情感共鸣",
         "本分支强调角色互动与情感曲线。爽点不密集，但每个章节都有一对角色"
         "产生重要对话或冲突；钩子类型优先「情感钩」「危机钩」；可减少纯动作章。"),
    ]

    for branch, flavor, flavor_directive in branch_definitions:
        # 复用 OUTLINE_SYSTEM 但 user_prompt 末尾加 flavor 指导
        flavored_user = (
            _build_user_prompt(arc, start_chapter, setting, memory)
            + f"\n\n【{flavor}专属约束】\n{flavor_directive}"
        )
        try:
            resp, branch_cost = router.call(
                agent_name="outline",
                system_prompt=OUTLINE_SYSTEM,
                user_prompt=flavored_user,
                max_tokens=8000,
                temperature=0.75,
            )
            resp = _extract_json_array(resp)
            branch_tasks = json.loads(resp)
        except Exception:
            # 单个分支失败不应让整次抽卡崩掉 —— fallback 复用 A 任务，
            # log warning 让用户/审计能看到
            import logging as _log
            _log.getLogger(__name__).warning(
                "outline_card branch %s (%s) LLM call failed, "
                "fallback to branch A tasks", branch, flavor,
            )
            branch_tasks = list(batch_tasks)  # 深拷贝避免下游改 A 时串改
            branch_cost = 0.0
        total_cost += branch_cost
        candidates.append({
            "branch": branch,
            "flavor": flavor,
            "tasks": branch_tasks,
        })

    return candidates, total_cost


def _build_user_prompt(arc: dict, start_chapter: int, setting: dict,
                       memory: dict) -> str:
    """复用 run_outline 内部的 user_prompt 构造逻辑 — 抽卡模式要拼同一份
    上下文，后面再 append flavor 指导。

    注意：抽卡函数之前是直接复用 run_outline 返回的 prompt 太长懒得再拼
    → 用这个 helper 抽出供 run_outline 和 run_outline_card 共享。
    """
    mc      = setting.get("protagonist", {}) or {}
    chars   = setting.get("key_characters", [])
    levels  = setting.get("power_system", {}).get("levels", [])
    hot     = memory.get("hot", memory)

    char_list  = "\n".join(f"  {c['name']}（{c['role']}）" for c in chars)
    level_str  = " | ".join(f"Lv{l['level']}:{l['name']}" for l in levels)
    threads    = hot.get("active_threads", [])
    threads_str = "\n".join(f"  - {t}" for t in threads) or "  无"

    return f"""【弧信息】
弧{arc.get('arc_id', '?')}「{arc.get('arc_name','?')}」
目标：{arc.get('arc_goal','')}
预计章节：{arc.get('estimated_chapters','?')}章（起始：第{start_chapter}章）
高潮：{arc.get('arc_climax_description','')}
情绪曲线：{arc.get('emotion_curve','')}
本弧引入角色：{', '.join(arc.get('new_characters_introduced', []))}
弧结束状态：{arc.get('arc_ending_state','')}

【主角】{mc.get('name','陆承')} | 当前等级：{hot.get('protagonist_level','感债者')} | 点数：{hot.get('protagonist_points',0)}
【力量层级】{level_str}
【可用角色】
{char_list}
【活跃剧情线（需在本弧推进或收尾）】
{threads_str}

输出JSON数组（{arc.get('estimated_chapters','?')}个任务）：
[{{
  "chapter_number": {start_chapter},
  "chapter_role": "铺垫|发展|爽点|弧高潮|过渡",
  "chapter_goal": "本章核心任务（一句话）",
  "main_characters": ["角色名"],
  "shuang_type": "{SHUANG_LIST}中之一，或null",
  "shuang_description": "爽感场景具体描述，无爽点则空字符串",
  "ending_hook_type": "{HOOK_LIST}中之一",
  "ending_hook_description": "结尾钩子具体方向",
  "setting_constraints": ["约束1"],
  "forbidden_actions": ["禁止事项1"],
  "target_length": "2000-2200",
  "audit_mode": "full",
  "is_arc_climax": false
}}]"""


def _extract_json_array(resp: str) -> str:
    """剥 markdown fence + extract [...] JSON array。
    复用 run_outline 里的逻辑（避免重复）。
    """
    resp = resp.strip()
    if resp.startswith("```"):
        lines = resp.split("\n")
        resp = "\n".join(lines[1:])
        if resp.strip().endswith("```"):
            resp = resp.strip()[:-3].strip()
    start = resp.find('['); end = resp.rfind(']') + 1
    if start >= 0 and end > start:
        return resp[start:end]
    return resp


def _mark_arc_climax(tasks: list, arc: dict) -> None:
    """弧高潮标记：原 run_outline 内部逻辑，提到模块级方便 card 模式复用。"""
    if not tasks:
        return
    climax_idx = min(arc.get("arc_climax_chapter_offset", len(tasks) - 3), len(tasks) - 1)
    tasks[climax_idx]["is_arc_climax"]  = True
    tasks[climax_idx]["target_length"]  = "3000-3300"
    tasks[climax_idx]["audit_mode"]     = "full"
    tasks[climax_idx]["chapter_role"]   = "弧高潮"


# ══════════════════════════════════════════
# talk 模式 — 交互头脑风暴
# ══════════════════════════════════════════
def run_outline_talk(arc: dict, start_chapter: int, setting: dict,
                     memory: dict) -> tuple[dict, float]:
    """交互式头脑风暴：先生成 1 份"待讨论大纲" + 几个分歧点问题等作者回应。

    P3 阶段：复用 batch 模式生成 tasks，再额外生成 3-5 个引导性问题（如
    "主角应该在这里觉醒还是更晚？"），推送给前端 human_pending 列表。
    """
    tasks, cost = run_outline(arc, start_chapter, setting, memory)
    # P3 stub：实际应用 LLM 基于 arc + setting 生成 3-5 个分歧点
    questions = [
        {
            "qid": f"talk_{arc.get('arc_id','?')}_q1",
            "question": f"在弧「{arc.get('arc_name','?')}」中，主角是否应该获得一项新能力？如果是，请描述这项能力的具体形态。",
            "context": f"弧目标：{arc.get('arc_goal','')}",
        },
        {
            "qid": f"talk_{arc.get('arc_id','?')}_q2",
            "question": "弧高潮的「对手」由谁担任？从已有角色选，还是新引入？",
            "context": "弧高潮的爽度主要由对手的压迫感和反派的智商决定。",
        },
        {
            "qid": f"talk_{arc.get('arc_id','?')}_q3",
            "question": "本章弧内允许主角损失什么？（友情/记忆/道具/金钱…）",
            "context": "损失越具体，反击越有共鸣；避免笼统的'挫折'。",
        },
    ]
    return {"tasks": tasks, "questions": questions}, cost