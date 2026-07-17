"""Checker Agent — 三模型质检评分

主评 + 两路交叉复核，取加权均分。`full` 模式调 3 个模型；
`lite` / `bootstrap` 模式只调主评。

Migrated from novel_AI/agents/checker_agent.py.
"""
from __future__ import annotations

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..utils import parse_llm_json_response, truncate_preserving_ends


CHECKER_SYSTEM = """你是一位经验丰富的网络文学质量评审，专注于都市系统流类型。
你的任务是对提交的章节进行多维度评分，对应用户给出的及格线定义：
"快节奏下剧情和节奏最重要，主要人物特色鲜明，文风有个性、不能八股文，
全文连贯、前后设定逻辑一致，细节不出错"。

【评分维度】（每项1-10分）
1. pacing（节奏）：剧情推进是否快慢得当，铺垫/发展/爽点/高潮是否符合章节定位，
   是否有明显拖沓或空转。权重 25%
2. character_voice（人物声音）：主要人物对话和行为是否符合其性格/口癖/职业，
   不同人物是否有辨识度，是否"千人一面"。权重 20%
3. plot_logic（情节逻辑）：事件因果是否自洽，有无明显 BUG，与前文是否矛盾。权重 15%
4. consistency（设定一致性）：力量体系/世界观/人物关系/前文事实是否一致，
   是否引入新设定却与已有设定冲突。权重 15%
5. writing_naturalness（文笔自然度）：是否有 AI 腔/陈词（嘴角勾起一抹弧度、
   眼眸中闪过精光、深吸一口气等），是否流畅自然。权重 15%
6. hook_power（钩子力度）：结尾是否让人迫切想看下一章。权重 10%

【输出格式】严格JSON：
{
  "dimensions": {
    "pacing": 分数,
    "character_voice": 分数,
    "plot_logic": 分数,
    "consistency": 分数,
    "writing_naturalness": 分数,
    "hook_power": 分数
  },
  "overall_score": 综合分（加权均分）,
  "strongest_point": "最大优点（一句话）",
  "weakest_point": "最大问题（一句话）",
  "specific_feedback": "具体修改建议（如需要）"
}"""


def score_chapter(text: str, task: dict, agent_name: str = "checker_main") -> tuple[dict, float]:
    """单模型打分。

    Phase 5 fix #5：把质检采样从「固定 3000 字截断」改成「保留开头 + 结尾」，
    因为弧高潮章节目标字数就是 3000-3300 字，原来的硬截断恰好把权重最高
    的"结尾钩子"段切掉，质检打分对高潮章节显著失准。

    新策略（避免 token 爆掉的同时保住结尾钩子）：
      - 总长 ≤ 4000：原样送（普通/弧高潮章节几乎都 ≤ 4000）
      - 总长 > 4000：保留头 2000 + 尾 2000，中间用「...」占位 → 仍能读到结尾钩子

    适用于 arc-climax 章节（target=3000-3300）以及任何 < 4000 的篇幅。
    """
    chapter_info = f"第{task['chapter_number']}章 | 定位：{task['chapter_role']} | 爽点：{task['shuang_description']}"

    # Phase 5: 保留头 + 尾，保住结尾钩子而不是硬截前 3000 字
    # Phase 9 simplify: 抽出到 utils.truncate_preserving_ends，跟 tracker 复用同一处实现
    sample = truncate_preserving_ends(text, head_chars=2000, tail_chars=2000)
    rule_feedback = task.get("_rule_feedback", "") or ""

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    resp, cost = router.call(
        agent_name=agent_name,
        system_prompt=CHECKER_SYSTEM,
        user_prompt=(
            f"【章节信息】{chapter_info}\n\n"
            f"{rule_feedback}"
            f"【章节正文】\n{sample}"
        ),
        max_tokens=600,
        temperature=0.2,
    )
    # Phase 9 simplify: 删除冗余 fence stripping —— parse_llm_json_response 内部已经剥。
    # 之前的 resp.strip().lstrip("```json")... 既 broken 又冗余：
    # lstrip("```json") 当 str.chars 模式会逐字符扫，把 j/s/o/n 等字母也算剥离集，
    # 实际让 fence 仍残留。parse_llm_json_response 自己处理且已测试覆盖。
    default = {
        "dimensions": {"pacing": 6, "character_voice": 6,
                       "plot_logic": 7, "consistency": 6,
                       "writing_naturalness": 6, "hook_power": 6},
        "overall_score": 6.2,
        "strongest_point": "解析失败，默认评分",
        "weakest_point": "",
        "specific_feedback": "",
    }
    result = parse_llm_json_response(resp, default)
    return result, cost


def calculate_weighted_score(dimensions: dict) -> float:
    """三期重排：维度 + 权重对齐用户优先级
    pacing 25% + character_voice 20% + plot_logic 15% + consistency 15%
    + writing_naturalness 15% + hook_power 10%。
    兼容老字段（hook_power + shuang_density）：把 shuang_density 合并进 pacing，
    old_dim 字段不足时按当前 schema 字段回退。"""
    weights = {
        "pacing": 0.25,
        "character_voice": 0.20,
        "plot_logic": 0.15,
        "consistency": 0.15,
        "writing_naturalness": 0.15,
        "hook_power": 0.10,
    }
    # 兼容旧 schema：把 shuang_density 按 6 分兜底加权合并
    dims = dict(dimensions or {})
    if "pacing" not in dims and "shuang_density" in dims:
        dims["pacing"] = max(1, min(10, int(dims["shuang_density"])))
    if "consistency" not in dims and "plot_logic" in dims:
        # 旧 plot_logic 包含一致性含义，不复制（独立给 consistency 默认 6）
        dims.setdefault("consistency", 6)

    def normalized_score(value) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 6.0
        return max(1.0, min(10.0, score))

    total = sum(normalized_score(dims.get(k, 6)) * w for k, w in weights.items())
    return round(total, 2)


def run_checker(text: str, task: dict, audit_mode: str = "full") -> tuple[dict, float]:
    """运行质检
    audit_mode:
      full       = 三模型评审（正常章节）
      lite       = 单模型评审（批量生产模式）
      bootstrap  = 仅主评（黄金三章阶段）

    返回：(result_dict, total_cost)
    result_dict: {score, individual_scores, dimensions, verdict, rewrite_level,
                  feedback, strongest_point, weakest_point}
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
        # 交叉评 1
        r2, c2 = score_chapter(text, task, "checker_cross1")
        total_cost += c2
        s2 = calculate_weighted_score(r2.get("dimensions", {}))
        scores.append(s2)

        # 交叉评 2
        r3, c3 = score_chapter(text, task, "checker_cross2")
        total_cost += c3
        s3 = calculate_weighted_score(r3.get("dimensions", {}))
        scores.append(s3)

        # 加权均分（主评 0.5，交叉各 0.25）
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
        rewrite_level = "P2"
    elif final_score >= 4.5:
        verdict = "REWRITE_MEDIUM"
        rewrite_level = "P1"
    else:
        verdict = "REWRITE_HEAVY"
        rewrite_level = "P0"

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
