"""
Rewriter Agent V2 — P0/P1/P2三级修订 + P0自检清单
P0自检清单实现V3方案8.2节全部5项
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import call_llm

# ── P0自检清单系统提示（V3方案8.2节）──
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

def run_p0_checklist(text: str, task: dict, memory: dict) -> tuple[dict, float]:
    """运行P0自检清单，返回问题清单和成本"""
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

    resp, cost = call_llm(
        agent_name="checker_main",
        system_prompt=P0_CHECKLIST_SYSTEM,
        user_prompt=context,
        max_tokens=800,
        temperature=0.1,
    )
    resp = resp.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        result = __import__('json').loads(resp)
    except:
        result = {"rewrite_priority": ["JSON解析失败，基于原始反馈重写"], "hook_strength": "弱", "shuang_present": False}
    return result, cost

# ── P2：轻度润色 ──
P2_SYSTEM = """你是网文编辑，对章节进行轻度润色。
规则：只修改有明确问题的段落；不改情节不加内容；加强结尾钩子；输出完整章节。"""

def run_p2(text: str, feedback: str, task: dict) -> tuple[str, float]:
    out, cost = call_llm(
        "rewriter", P2_SYSTEM,
        f"【修改要求】{feedback}\n\n【原文】\n{text}\n\n输出修改后完整正文：",
        max_tokens=len(text)*2+300, temperature=0.6)
    return out, cost

# ── P1：中度重写 ──
P1_SYSTEM = """你是资深网文作者，对章节进行中度重写。
规则：保留核心情节骨架；重写弱段；大幅加强结尾钩子；对话要有人物辨识度；输出完整正文。"""

def run_p1(text: str, feedback: str, task: dict, checker_result: dict) -> tuple[str, float]:
    out, cost = call_llm(
        "rewriter", P1_SYSTEM,
        f"【任务】第{task['chapter_number']}章 | {task['chapter_goal']}\n"
        f"爽点：{task['shuang_description']}\n钩子：{task['ending_hook_description']}\n"
        f"【主要问题】{feedback}\n最弱点：{checker_result.get('weakest_point','')}\n"
        f"【原文骨架参考】\n{text}\n\n请重写：",
        max_tokens=max(3500, int(len(text)*2.5)), temperature=0.78)
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
    priority_str = "\n".join(f"  {i+1}. {p}" for i,p in enumerate(checklist.get("rewrite_priority",[])))
    all_issues = (checklist.get("timeline_issues",[]) + checklist.get("causality_issues",[]) +
                  checklist.get("state_issues",[]))
    issues_str = "\n".join(f"  - {i}" for i in all_issues[:4]) or "  无明显问题（主要是质感不足）"

    out, cost_write = call_llm(
        "rewriter", P0_SYSTEM,
        f"【必须包含的情节节点】\n"
        f"目标：{task['chapter_goal']}\n爽点（必须）：{task['shuang_description']}\n"
        f"钩子（必须）：{task['ending_hook_description']}\n出场人物：{', '.join(task.get('main_characters',[]))}\n\n"
        f"【上一章结尾衔接】{hot.get('last_chapter_ending','')[:150]}\n\n"
        f"【前版本需要避免的问题】\n{issues_str}\n\n"
        f"【重写优先级】\n{priority_str}\n\n"
        f"【字数】{task.get('target_length','2000-2200')}字\n\n"
        f"请为第{task['chapter_number']}章（主角：{mc_name}）写出全新高质量版本：",
        max_tokens=4000, temperature=0.85)
    return out, cost_check + cost_write

def run_rewriter(text, rewrite_level, feedback, task, checker_result, memory, setting_core) -> tuple[str, float]:
    print(f"  ✏️  [Rewriter] {rewrite_level}级修订...")
    if rewrite_level == "P2":
        return run_p2(text, feedback, task)
    elif rewrite_level == "P1":
        return run_p1(text, feedback, task, checker_result)
    else:
        return run_p0(text, feedback, task, memory, setting_core)
