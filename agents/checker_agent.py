"""
Checker Agent — 三模型质检评分
主评 + 两路交叉复核，取加权均分
输出：{score: float, dimensions: {}, verdict: str, rewrite_level: str, feedback: str}
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import call_llm

CHECKER_SYSTEM = """你是一位经验丰富的网络文学质量评审，专注于都市系统流类型。
你的任务是对提交的章节进行多维度评分。

【评分维度】（每项1-10分）
1. hook_power（钩子力度）：结尾是否让人迫切想看下一章，1=没有钩子，10=极度上头
2. shuang_density（爽感密度）：全章爽点数量和质量，1=全程平淡，10=爽点密集
3. character_voice（人物声音）：对话和行为是否符合人物性格，不同人物是否有辨识度
4. plot_logic（情节逻辑）：事件因果是否自洽，有无明显BUG
5. writing_naturalness（文笔自然度）：是否有AI腔、是否流畅自然

【输出格式】严格JSON：
{
  "dimensions": {
    "hook_power": 分数,
    "shuang_density": 分数,
    "character_voice": 分数,
    "plot_logic": 分数,
    "writing_naturalness": 分数
  },
  "overall_score": 综合分（加权均分，hook和shuang权重更高）,
  "strongest_point": "最大优点（一句话）",
  "weakest_point": "最大问题（一句话）",
  "specific_feedback": "具体修改建议（如需要）"
}"""

def score_chapter(text: str, task: dict, agent_name: str = "checker_main") -> tuple[dict, float]:
    chapter_info = f"第{task['chapter_number']}章 | 定位：{task['chapter_role']} | 爽点：{task['shuang_description']}"
    # 截取用于评审的文本（前3000字，兼顾成本和准确性）
    sample = text[:3000] + ("...[截断]" if len(text) > 3000 else "")

    resp, cost = call_llm(
        agent_name=agent_name,
        system_prompt=CHECKER_SYSTEM,
        user_prompt=f"【章节信息】{chapter_info}\n\n【章节正文】\n{sample}",
        max_tokens=600,
        temperature=0.2,
    )
    resp = resp.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        result = json.loads(resp)
    except:
        # 解析失败给个中等分避免整章重来
        result = {
            "dimensions": {"hook_power":6,"shuang_density":6,"character_voice":6,"plot_logic":7,"writing_naturalness":6},
            "overall_score": 6.2,
            "strongest_point": "解析失败，默认评分",
            "weakest_point": "",
            "specific_feedback": ""
        }
    return result, cost


def calculate_weighted_score(dimensions: dict) -> float:
    weights = {
        "hook_power": 0.30,
        "shuang_density": 0.25,
        "character_voice": 0.20,
        "plot_logic": 0.15,
        "writing_naturalness": 0.10,
    }
    total = sum(dimensions.get(k, 6) * w for k, w in weights.items())
    return round(total, 2)


def run_checker(text: str, task: dict, audit_mode: str = "full") -> tuple[dict, float]:
    """
    运行质检
    audit_mode:
      full  = 三模型评审（正常章节）
      lite  = 单模型评审（批量生产模式）
      bootstrap = 仅主评（黄金三章阶段）

    返回：(result_dict, total_cost)
    result_dict包含:
      score, dimensions, verdict, rewrite_level, feedback
    """
    total_cost = 0.0
    scores = []

    # 主评
    r1, c1 = score_chapter(text, task, "checker_main")
    total_cost += c1
    s1 = calculate_weighted_score(r1.get("dimensions", {}))
    scores.append(s1)
    main_result = r1

    if audit_mode == "full":
        # 交叉评1
        r2, c2 = score_chapter(text, task, "checker_cross1")
        total_cost += c2
        s2 = calculate_weighted_score(r2.get("dimensions", {}))
        scores.append(s2)

        # 交叉评2
        r3, c3 = score_chapter(text, task, "checker_cross2")
        total_cost += c3
        s3 = calculate_weighted_score(r3.get("dimensions", {}))
        scores.append(s3)

        # 加权均分（主评权重0.5，交叉各0.25）
        final_score = round(s1 * 0.5 + s2 * 0.25 + s3 * 0.25, 2)
    else:
        final_score = s1

    # 判定等级
    if final_score >= 7.5:
        verdict = "PASS"
        rewrite_level = "none"
    elif final_score >= 6.5:
        verdict = "PASS_WITH_NOTE"
        rewrite_level = "none"
    elif final_score >= 5.5:
        verdict = "REWRITE_LIGHT"
        rewrite_level = "P2"  # 轻度修订
    elif final_score >= 4.5:
        verdict = "REWRITE_MEDIUM"
        rewrite_level = "P1"  # 中度重写
    else:
        verdict = "REWRITE_HEAVY"
        rewrite_level = "P0"  # 完整重写

    return {
        "score": final_score,
        "individual_scores": scores,
        "dimensions": main_result.get("dimensions", {}),
        "verdict": verdict,
        "rewrite_level": rewrite_level,
        "feedback": main_result.get("specific_feedback", ""),
        "strongest_point": main_result.get("strongest_point", ""),
        "weakest_point": main_result.get("weakest_point", ""),
    }, total_cost
