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
    state["chapter_task_queue"] = []
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
    return state


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_init_arc(sys.argv[1:], ".") else 1)