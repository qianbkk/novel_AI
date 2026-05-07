"""
AI网文创作系统 V3 — Planner Agent
职责：生成完整设定包（世界观/力量体系/主角/配角/主线大纲/弧级规划）
触发时机：项目启动时运行一次
输出：setting_package.json（等待人工确认，节点①）
"""
import json
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api_client import call_llm

# ─────────────────────────────────────────────
# Planner系统提示词（可缓存前缀区域）
# ─────────────────────────────────────────────
PLANNER_SYSTEM_PROMPT = """你是一位深度理解网络文学市场的资深IP策划，专注于番茄小说平台的爆款设计。
你的任务是基于用户提供的世界观概念，设计一套完整、自洽、具有长期连载潜力的小说设定包。

【设计原则】
1. 番茄受众口味：爽感密集、主角有明确成长曲线、每隔20-30章有大爽点
2. 设定自洽性：所有规则必须在内部逻辑上一致，不能出现无法自圆其说的漏洞
3. 差异化记忆点：设定必须有至少3个「只有这本书才有」的独特元素
4. 长篇可持续性：设定要能支撑300万字不崩塌，力量体系要有足够的晋升层级
5. 人物弧光设计：主角性格缺陷是成长的燃料，不要完美无缺的开局主角

【输出格式】
严格输出JSON，不输出任何其他内容，不加Markdown代码块标记。
"""

# ─────────────────────────────────────────────
# Planner用户提示词模板
# ─────────────────────────────────────────────
def build_planner_prompt(config: dict) -> str:
    protagonist_type = config.get("protagonist_type", "待定")
    setting_concept = config.get("setting_concept", "")

    return f"""【世界观概念】
{setting_concept}

【主角定位偏好】
{protagonist_type}

【目标平台】
番茄小说，目标读者：18-35岁，喜欢爽文、系统流、都市题材

【任务】
基于以上概念，设计完整设定包。输出以下JSON结构（严格遵守，不添加注释）：

{{
  "title_candidates": ["候选书名1", "候选书名2", "候选书名3"],
  "tagline": "一句话吸引读者的简介（30字以内，含最大卖点）",
  "world_setting": {{
    "era": "当代都市（具体年份设定）",
    "hidden_world_name": "隐秘体系的正式名称",
    "hidden_world_history": "这套体系的起源和历史（200字以内）",
    "modern_survival": "为什么在现代社会还存在（逻辑自洽说明）",
    "unique_elements": ["独特元素1", "独特元素2", "独特元素3"]
  }},
  "power_system": {{
    "name": "力量体系名称",
    "currency": "人情点数",
    "levels": [
      {{"level": 1, "name": "境界名", "ability": "能力描述", "point_threshold": 0}},
      {{"level": 2, "name": "境界名", "ability": "能力描述", "point_threshold": 500}},
      {{"level": 3, "name": "境界名", "ability": "能力描述", "point_threshold": 2000}},
      {{"level": 4, "name": "境界名", "ability": "能力描述", "point_threshold": 8000}},
      {{"level": 5, "name": "境界名", "ability": "能力描述", "point_threshold": 30000}},
      {{"level": 6, "name": "境界名（终极）", "ability": "能力描述", "point_threshold": 100000}}
    ],
    "shop_items_examples": [
      {{"item": "商品名", "cost": 100, "effect": "效果描述"}},
      {{"item": "商品名", "cost": 500, "effect": "效果描述"}},
      {{"item": "商品名", "cost": 2000, "effect": "效果描述"}}
    ],
    "special_rules": ["规则1（有趣的系统限制或特性）", "规则2", "规则3"]
  }},
  "protagonist": {{
    "name": "主角全名",
    "age": 24,
    "background": "背景经历（150字以内，含触发觉醒的具体事件）",
    "personality": "性格描述（包含核心缺陷）",
    "core_flaw": "最大性格缺陷（成长弧的起点）",
    "awakening_trigger": "第1章觉醒人情债系统的具体触发情境",
    "initial_power_level": "初始境界",
    "speech_quirks": ["口头禅1", "行为习惯2", "语言特征3"],
    "goals": {{
      "surface_goal": "表面追求（可见的世俗目标）",
      "deep_goal": "深层渴望（情感/价值观层面）",
      "hidden_fear": "内心恐惧（推动成长的内驱力）"
    }}
  }},
  "key_characters": [
    {{
      "role": "角色定位（如：早期导师/主要反派/爱情线）",
      "name": "姓名",
      "arc_introduced": 1,
      "relationship_to_mc": "与主角的关系",
      "personality": "性格简述",
      "secret": "隐藏秘密（后期揭露用）",
      "speech_quirks": ["口癖1"]
    }},
    {{
      "role": "角色定位",
      "name": "姓名",
      "arc_introduced": 1,
      "relationship_to_mc": "与主角的关系",
      "personality": "性格简述",
      "secret": "隐藏秘密",
      "speech_quirks": ["口癖1"]
    }},
    {{
      "role": "角色定位",
      "name": "姓名",
      "arc_introduced": 2,
      "relationship_to_mc": "与主角的关系",
      "personality": "性格简述",
      "secret": "隐藏秘密",
      "speech_quirks": ["口癖1"]
    }}
  ],
  "main_conflict": {{
    "surface_conflict": "表面冲突（读者最先看到的）",
    "deep_conflict": "深层矛盾（支撑全书的核心张力）",
    "antagonist_force": "主要对立力量（不一定是单一反派）",
    "final_resolution_direction": "大结局方向（不是具体情节，是方向）"
  }},
  "arc_outline": [
    {{
      "arc_id": 1,
      "arc_name": "弧名称",
      "arc_goal": "本弧叙事目标",
      "estimated_chapters": 30,
      "arc_climax_description": "弧高潮的具体情节（3句话）",
      "emotion_curve": "情绪曲线描述",
      "new_characters_introduced": ["角色名"],
      "arc_ending_state": "本弧结束时的世界状态",
      "is_final_arc": false
    }},
    {{
      "arc_id": 2,
      "arc_name": "弧名称",
      "arc_goal": "本弧叙事目标",
      "estimated_chapters": 35,
      "arc_climax_description": "弧高潮",
      "emotion_curve": "情绪曲线",
      "new_characters_introduced": [],
      "arc_ending_state": "本弧结束时的世界状态",
      "is_final_arc": false
    }},
    {{
      "arc_id": 3,
      "arc_name": "弧名称",
      "arc_goal": "本弧叙事目标",
      "estimated_chapters": 40,
      "arc_climax_description": "弧高潮",
      "emotion_curve": "情绪曲线",
      "new_characters_introduced": [],
      "arc_ending_state": "本弧结束时的世界状态",
      "is_final_arc": false
    }}
  ],
  "golden_chapter_hooks": {{
    "chapter_1_opening": "第1章开头两段的具体内容方向（制造立即吸引力）",
    "chapter_1_shuang_point": "第1章第一个爽点的具体情境（1000字内必须出现）",
    "chapter_3_cliffhanger": "第3章末尾钩子的方向"
  }}
}}"""


def run_planner(config: dict, output_dir: str) -> dict:
    """
    运行Planner Agent，生成设定包
    返回设定包dict，并保存到文件
    """
    print("🧠 [Planner] 正在生成设定包...")
    print(f"   题材：{config.get('genre')}")
    print(f"   主角定位：{config.get('protagonist_type', '待定')}")

    user_prompt = build_planner_prompt(config)

    response_text, cost = call_llm(
        agent_name="planner",
        system_prompt=PLANNER_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=6000,
        temperature=0.8,
    )

    # 清理可能的Markdown包裹
    text = response_text.strip()
    if text.startswith("```"):
        lines = text.split('\n')
        text = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])

    try:
        setting_package = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"⚠️  JSON解析失败，尝试修复... ({e})")
        # 尝试找到JSON范围
        start = text.find('{')
        end = text.rfind('}') + 1
        setting_package = json.loads(text[start:end])

    # 保存设定包
    output_path = os.path.join(output_dir, "setting_package.json")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(setting_package, f, ensure_ascii=False, indent=2)

    print(f"✅ [Planner] 设定包生成完成，成本：${cost:.4f}")
    print(f"   保存至：{output_path}")
    print(f"\n📋 候选书名：")
    for i, title in enumerate(setting_package.get("title_candidates", []), 1):
        print(f"   {i}. {title}")
    print(f"\n💡 一句话简介：{setting_package.get('tagline', '')}")

    return setting_package


if __name__ == "__main__":
    # 测试运行
    import sys
    sys.path.insert(0, '/home/claude/novel_system')

    # 加载.env（如果存在）
    env_file = '/home/claude/novel_system/.env'
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()

    with open('/home/claude/novel_system/config/novel_config.json', encoding='utf-8') as f:
        config = json.load(f)

    result = run_planner(config, '/home/claude/novel_system/output')
    print("\n设定包预览（前500字）：")
    print(json.dumps(result, ensure_ascii=False, indent=2)[:500])
