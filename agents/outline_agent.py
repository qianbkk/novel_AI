"""
Outline Agent V2 — 弧级章节任务拆解
集成：七种钩子库 / 爽点类型校验 / Prompt Cache
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import call_llm
from memory.memory_manager import get_l2
from config.prompt_templates import HOOK_TYPES, SHUANG_TYPES

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
    mc       = setting.get("protagonist", {})
    chars    = setting.get("key_characters", [])
    levels   = setting.get("power_system", {}).get("levels", [])
    hot      = memory.get("hot", memory)  # 兼容新旧schema

    char_list  = "\n".join(f"  {c['name']}（{c['role']}）" for c in chars)
    level_str  = " | ".join(f"Lv{l['level']}:{l['name']}" for l in levels)
    threads    = hot.get("active_threads", [])
    threads_str = "\n".join(f"  - {t}" for t in threads) or "  无"

    print(f"📋 [Outline] 拆解弧{arc['arc_id']}「{arc['arc_name']}」"
          f"（{arc['estimated_chapters']}章，起始Ch{start_chapter}）")

    user_prompt = f"""【弧信息】
弧{arc['arc_id']}「{arc['arc_name']}」
目标：{arc['arc_goal']}
预计章节：{arc['estimated_chapters']}章（起始：第{start_chapter}章）
高潮：{arc['arc_climax_description']}
情绪曲线：{arc['emotion_curve']}
本弧引入角色：{', '.join(arc.get('new_characters_introduced', []))}
弧结束状态：{arc['arc_ending_state']}

【主角】{mc.get('name','陆承')} | 当前等级：{hot.get('protagonist_level','感债者')} | 点数：{hot.get('protagonist_points',0)}
【力量层级】{level_str}
【可用角色】
{char_list}
【活跃剧情线（需在本弧推进或收尾）】
{threads_str}

输出JSON数组（{arc['estimated_chapters']}个任务）：
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

    resp, cost = call_llm(
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
    start = resp.find('['); end = resp.rfind(']')+1
    if start >= 0 and end > start:
        resp = resp[start:end]
    try:
        tasks = json.loads(resp)
    except json.JSONDecodeError:
        import re
        resp2 = re.sub(r',\s*}', '}', resp)
        resp2 = re.sub(r',\s*]', ']', resp2)
        tasks = json.loads(resp2)

    # 标记弧高潮
    if tasks:
        climax_idx = min(arc.get("arc_climax_chapter_offset", len(tasks)-3), len(tasks)-1)
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
