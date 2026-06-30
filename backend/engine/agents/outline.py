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
    mc      = setting.get("protagonist", {}) or {}
    chars   = setting.get("key_characters", [])
    levels  = setting.get("power_system", {}).get("levels", [])
    hot     = memory.get("hot", memory)  # 兼容新旧 schema

    char_list  = "\n".join(f"  {c['name']}（{c['role']}）" for c in chars)
    level_str  = " | ".join(f"Lv{l['level']}:{l['name']}" for l in levels)
    threads    = hot.get("active_threads", [])
    threads_str = "\n".join(f"  - {t}" for t in threads) or "  无"

    print(f"📋 [Outline] 拆解弧{arc.get('arc_id', '?')}「{arc.get('arc_name','?')}」"
          f"（{arc.get('estimated_chapters', '?')}章，起始Ch{start_chapter}）")

    user_prompt = f"""【弧信息】
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
    resp = resp.strip()
    if resp.startswith("```"):
        lines = resp.split("\n")
        resp = "\n".join(lines[1:])
        if resp.strip().endswith("```"):
            resp = resp.strip()[:-3].strip()
    start = resp.find('['); end = resp.rfind(']') + 1
    if start >= 0 and end > start:
        resp = resp[start:end]
    try:
        tasks = json.loads(resp)
    except json.JSONDecodeError:
        resp2 = re.sub(r',\s*}', '}', resp)
        resp2 = re.sub(r',\s*]', ']', resp2)
        tasks = json.loads(resp2)

    # 标记弧高潮
    if tasks:
        climax_idx = min(arc.get("arc_climax_chapter_offset", len(tasks) - 3), len(tasks) - 1)
        tasks[climax_idx]["is_arc_climax"]  = True
        tasks[climax_idx]["target_length"]  = "3000-3300"
        tasks[climax_idx]["audit_mode"]     = "full"
        tasks[climax_idx]["chapter_role"]   = "弧高潮"

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

    P3 阶段：调一次 LLM 拿到 3 个候选；第一个候选的 tasks 被默认采纳进
    chapter_task_queue，另外 2 个作为 outline_candidates 留给前端三选一。
    """
    # 复用 batch 模式作为 A 分支（保证一致性），另外 B/C 用 LLM 生成不同 flavor
    batch_tasks, batch_cost = run_outline(arc, start_chapter, setting, memory)

    # P3 stub：实际实现需要给 LLM 发 3 次不同 prompt。这里 mock 出 3 个分支
    candidates = [
        {
            "branch": "A",
            "flavor": "爽点密集",
            "tasks": batch_tasks,
        },
        {
            "branch": "B",
            "flavor": "悬疑反转",
            "tasks": batch_tasks,  # P3 stub: 实际应该是 LLM 重新生成的悬念版
        },
        {
            "branch": "C",
            "flavor": "情感共鸣",
            "tasks": batch_tasks,  # P3 stub: 实际应该是 LLM 重新生成的情感版
        },
    ]
    return candidates, batch_cost


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