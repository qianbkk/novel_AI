"""Rewriter Agent V2 — P0/P1/P2 三级修订 + P0 自检清单
P0 自检清单实现 V3 方案 8.2 节全部 5 项。

Migrated from novel_AI/agents/rewriter_agent.py.

P4 expansion: 字数控制接入生成路径。
  - 旧：P0/P1/P2 全部 router.call()，字数要求只在 prompt 文字里
    （【字数】2000-2200字），LLM 写到哪算哪，事后校验（擦屁股）
  - 新：P0/P1/P2 全部走 _call_with_budget，按 task.target_length
    中位数强制落到目标字数（写入路径预防）
  - 与 writer.run_writer 对称：同一种预防式控制覆盖同一种生成路径
"""
from __future__ import annotations

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..utils import parse_llm_json_response


# ── P0 自检清单系统提示（V3 方案 8.2 节）──
P0_CHECKLIST_SYSTEM = """你是一位严格的网文编辑，对章节草稿做重写前的自检分析。
输出以下JSON，不加任何说明：
{
  "timeline_issues": ["时间线矛盾列表，无则空数组"],
  "foreshadow_issues": ["铺垫/悬念未处理问题，无则空数组"],
  "causality_issues": ["因果链问题，无则空数组"],
  "state_issues": ["与已知角色/世界状态的矛盾，无则空数组"],
  "knowledge_issues": ["角色用了不该知道的信息，无则空数组"],
  "hook_strength": "弱/中/强",
  "shuang_present": true或false,
  "rewrite_priority": ["最需要改进的2-3点，按优先级排序"]
}"""


# ── 字数控制 helper（P4）──
def _get_active_router_or_fallback() -> LLMRouter:
    """Bridge: rewriter 跟 writer 一样走 active router；fallback 一个 env-only 实例。"""
    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    return router


def _parse_target_chars(task: dict, default: int = 2200) -> int:
    """从 task.target_length（"2000-2200"）取中位数作为 target_chars。
    与 writer._parse_target_chars 等价逻辑；单点维护防止漂移。
    """
    target = str(task.get("target_length", f"{default-200}-{default}"))
    if "-" in target:
        try:
            lo, hi = target.split("-")
            return (int(lo) + int(hi)) // 2
        except (ValueError, TypeError):
            return default
    try:
        return int(target)
    except (ValueError, TypeError):
        return default


def _call_with_budget(agent_name: str, system: str, user: str,
                      target_chars: int, *, temperature: float,
                      tolerance: int = 200, max_continues: int = 2):
    """长度预算调用（写入路径字数控制）。rewriter 三条路径共用。"""
    return _get_active_router_or_fallback().call_with_length_budget(
        agent_name=agent_name,
        system_prompt=system,
        user_prompt=user,
        target_chars=target_chars,
        tolerance=tolerance,
        temperature=temperature,
        max_continues=max_continues,
    )


def run_p0_checklist(text: str, task: dict, memory: dict) -> tuple[dict, float]:
    """运行 P0 自检清单，返回问题清单和成本"""
    hot = memory.get("hot", {}) if "hot" in memory else memory
    context = f"""【章节任务】第{task['chapter_number']}章 | {task['chapter_goal']}
爽点预期：{task['shuang_description']}
钩子预期：{task['ending_hook_description']}

【已知状态】
主角等级：{hot.get('protagonist_level','感债者')}
活跃剧情线：{str(hot.get('active_threads',[]))[:300]}
上章结尾：{hot.get('last_chapter_ending','')[:150]}

【章节草稿（前2500字）】
{text[:2500]}"""

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    resp, cost = router.call(
        agent_name="checker_main",
        system_prompt=P0_CHECKLIST_SYSTEM,
        user_prompt=context,
        max_tokens=800,
        temperature=0.1,
    )
    result = parse_llm_json_response(
        resp,
        {"rewrite_priority": ["JSON解析失败，基于原始反馈重写"],
         "hook_strength": "弱", "shuang_present": False},
    )
    return result, cost


# ── P2：轻度润色 ──
P2_SYSTEM = """你是网文编辑，对章节进行轻度润色。
规则：只修改有明确问题的段落；不改情节不加内容；加强结尾钩子；输出完整章节。"""


def run_p2(text: str, feedback: str, task: dict) -> tuple[str, float]:
    """P2 轻度润色。字数控制：按 task.target_length 中位数（与 writer 对称）。"""
    target_chars = _parse_target_chars(task)
    out, cost = _call_with_budget(
        agent_name="rewriter",
        system=P2_SYSTEM,
        user=f"【修改要求】{feedback}\n\n【原文】\n{text}\n\n输出修改后完整正文：",
        target_chars=target_chars,
        temperature=0.6,
        tolerance=200,
        max_continues=2,
    )
    return out, cost


# ── P1：中度重写 ──
P1_SYSTEM = """你是资深网文作者，对章节进行中度重写。
规则：保留核心情节骨架；重写弱段；大幅加强结尾钩子；对话要有人物辨识度；输出完整正文。"""


def run_p1(text: str, feedback: str, task: dict, checker_result: dict) -> tuple[str, float]:
    """P1 中度重写。字数控制：按 task.target_length 中位数（与 writer 对称）。"""
    target_chars = _parse_target_chars(task)
    out, cost = _call_with_budget(
        agent_name="rewriter",
        system=P1_SYSTEM,
        user=(
            f"【任务】第{task['chapter_number']}章 | {task['chapter_goal']}\n"
            f"爽点：{task['shuang_description']}\n钩子：{task['ending_hook_description']}\n"
            f"【主要问题】{feedback}\n最弱点：{checker_result.get('weakest_point','')}\n"
            f"【原文骨架参考】\n{text}\n\n请重写："
        ),
        target_chars=target_chars,
        temperature=0.78,
        tolerance=200,
        max_continues=2,
    )
    return out, cost


# ── P0：完整重写（含自检清单驱动）──
P0_SYSTEM = """你是资深网文作者，对章节进行完整重写。
这是该章的第二次创作，前版本不达标。
规则：不参考原文具体写法，只保留必须发生的情节节点；从头构建更好的版本；结尾钩子是成败关键；直接输出正文。"""


def run_p0(text: str, feedback: str, task: dict, memory: dict, setting_core: dict) -> tuple[str, float]:
    # 先运行自检清单
    checklist, cost_check = run_p0_checklist(text, task, memory)
    hot = memory.get("hot", {}) if "hot" in memory else memory
    mc_name = setting_core.get("protagonist", {}).get("name", "陆承")

    # 整合自检结果到重写指令
    priority_str = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(checklist.get("rewrite_priority", [])))
    all_issues = (checklist.get("timeline_issues", []) + checklist.get("causality_issues", []) +
                  checklist.get("state_issues", []))
    issues_str = "\n".join(f"  - {i}" for i in all_issues[:4]) or "  无明显问题（主要是质感不足）"

    # 主重写：写入路径 length-budget call（替代原 router.call()）
    target_chars = _parse_target_chars(task)
    out, cost_write = _call_with_budget(
        agent_name="rewriter",
        system=P0_SYSTEM,
        user=(
            f"【必须包含的情节节点】\n"
            f"目标：{task['chapter_goal']}\n爽点（必须）：{task['shuang_description']}\n"
            f"钩子（必须）：{task['ending_hook_description']}\n出场人物：{', '.join(task.get('main_characters',[]))}\n\n"
            f"【上一章结尾衔接】{hot.get('last_chapter_ending','')[:150]}\n\n"
            f"【前版本需要避免的问题】\n{issues_str}\n\n"
            f"【重写优先级】\n{priority_str}\n\n"
            f"【字数】{task.get('target_length','2000-2200')}字\n\n"
            f"请为第{task['chapter_number']}章（主角：{mc_name}）写出全新高质量版本："
        ),
        target_chars=target_chars,
        temperature=0.85,
        tolerance=200,
        max_continues=2,
    )
    return out, cost_check + cost_write


def run_rewriter(text, rewrite_level, feedback, task, checker_result, memory, setting_core) -> tuple[str, float]:
    """P0/P1/P2 三级修订调度入口。"""
    print(f"  ✏️  [Rewriter] {rewrite_level}级修订...")
    if rewrite_level == "P2":
        return run_p2(text, feedback, task)
    elif rewrite_level == "P1":
        return run_p1(text, feedback, task, checker_result)
    else:
        return run_p0(text, feedback, task, memory, setting_core)