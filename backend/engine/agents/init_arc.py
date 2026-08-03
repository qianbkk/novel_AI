"""build_arc_state.py — 根据 setting_package.json 直接生成 orchestrator_state.json 的 arc_plans

不调 LLM，1 秒内完成。Bootstrap 的简化版，足够让 run 流程跑起来。
"""
from __future__ import annotations
import json
from pathlib import Path

from ..config.paths import SETTING_PATH_STR, STATE_PATH_STR
from ..state import create_initial_state, save_state


def build_state_from_setting(project_id: str, chapters_per_arc: int | None = None) -> dict:
    """读 setting_package.json → 把每个 arc 转成 ArcPlan 字典，注入 state。

    chapters_per_arc: 覆盖原 estimated_chapters（可选）。

    init_arc 只负责建立弧级计划，章节任务必须由 node_load_arc_tasks 调用
    Outline Agent 生成。曾经为规避模型少返回任务而预填 placeholder 队列，
    会让 node_load_arc_tasks 直接短路，导致真实长篇没有 arc_*_tasks.json，
    且 shuang_type / emotion_core / narrative_thread / foreshadowing_ops 从源头
    全为空。现在数量契约由 Outline Agent 的分批生成与强校验负责；不足时
    显式失败，不再以低信息占位任务换取表面的章节数量。
    """
    setting_path = Path(SETTING_PATH_STR)
    if not setting_path.exists():
        raise FileNotFoundError(f"setting_package.json 不存在：{setting_path}")
    # 迭代 #42: 之前直接 json.loads — 如果 setting_package.json 损坏
    # （半写、编码错），原始 JSONDecodeError / UnicodeDecodeError 透出
    # 抛 RuntimeError → 前端看到几百行 traceback。同 pull_setting_package
    # (迭代 #35) 同型问题，同修法。
    try:
        setting = json.loads(setting_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError(
            f"setting_package.json 损坏（{type(e).__name__}）：{e}。"
            f"请重新跑 POST /bridge/run command=planner 重新生成。"
        ) from e

    state = create_initial_state(
        novel_id=project_id,
        title=(setting.get("title_candidates") or ["未命名"])[0],
        platform=setting.get("platform", "fanqie"),
        genre=setting.get("genre", "玄幻"),
        setting_concept=setting.get("tagline", ""),
        budget_limit_usd=setting.get("budget_limit_usd", 500.0),
    )

    # 把 setting 里的弧转为 ArcPlan
    arc_plans = []
    for a in setting.get("arc_outline", []):
        plans_chapters = chapters_per_arc or a.get("estimated_chapters", 35)
        arc_plans.append({
            "arc_id": a.get("arc_id", len(arc_plans) + 1),
            "arc_name": a.get("arc_name", f"第{len(arc_plans)+1}弧"),
            "arc_goal": a.get("arc_goal", ""),
            "estimated_chapters": plans_chapters,
            "arc_climax_description": a.get("arc_climax_description", ""),
            "arc_climax_chapter_offset": a.get("arc_climax_chapter_offset", plans_chapters - 5),
            "emotion_curve": a.get("emotion_curve", ""),
            "new_characters_introduced": a.get("new_characters_introduced", []),
            "arc_ending_state": a.get("arc_ending_state", ""),
            "is_final_arc": a.get("is_final_arc", False),
        })

    state["arc_plans"] = arc_plans
    state["total_arcs_planned"] = len(arc_plans)
    state["current_phase"] = "writing"
    state["current_chapter"] = 0

    # 章节级任务保留为空，让 node_load_arc_tasks 调用 Outline Agent。
    # total_chapters_planned 是弧级规划总数；加载每弧任务时不得再次累加。
    state["chapter_task_queue"] = []
    state["total_chapters_planned"] = sum(
        int(arc.get("estimated_chapters", 0) or 0) for arc in arc_plans
    )
    state["current_task"] = None

    save_state(state, STATE_PATH_STR)
    return state


def run_init_arc(args, output_dir: str) -> dict:
    """init_arc 命令主入口。"""
    # 约定：args[0] = project_id（调用方传）
    project_id = args[0] if args else "default"
    chapters_per_arc = int(args[1]) if len(args) >= 2 else None
    state = build_state_from_setting(project_id, chapters_per_arc)
    print(f"✅ 已初始化 arc_plans: {len(state['arc_plans'])} 弧")
    for a in state["arc_plans"]:
        print(f"   弧 {a['arc_id']} 「{a['arc_name']}」: {a['estimated_chapters']} 章")
    print("✅ 章节任务队列待 Outline Agent 按弧生成")
    return state


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_init_arc(sys.argv[1:], ".") else 1)