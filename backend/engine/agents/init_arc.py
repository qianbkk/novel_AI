"""build_arc_state.py — 根据 setting_package.json 直接生成 orchestrator_state.json 的 arc_plans

不调 LLM，1 秒内完成。Bootstrap 的简化版，足够让 run 流程跑起来。
"""
from __future__ import annotations
import json
from pathlib import Path

from ..config.paths import CHAPTERS_DIR_STR, SETTING_PATH_STR, STATE_PATH_STR
from ..state import create_initial_state, save_state


def build_state_from_paths(
    project_id: str,
    *,
    setting_path: Path,
    state_path: Path,
    chapters_dir: Path,
    chapters_per_arc: int | None = None,
) -> dict:
    """Build arc state from explicit project-scoped paths."""
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

    # Preserve the longest contiguous sequence of formal chapter files. Bootstrap
    # may already have produced chapters 1-3; resetting to zero would make the
    # orchestrator regenerate and overwrite them.
    completed_chapter = 0
    while (chapters_dir / f"ch_{completed_chapter + 1:04d}.txt").is_file():
        completed_chapter += 1
    state["current_chapter"] = completed_chapter

    # 章节级任务保留为空，让 node_load_arc_tasks 调用 Outline Agent。
    # total_chapters_planned 是弧级规划总数；加载每弧任务时不得再次累加。
    state["chapter_task_queue"] = []
    state["total_chapters_planned"] = sum(
        int(arc.get("estimated_chapters", 0) or 0) for arc in arc_plans
    )
    state["current_task"] = None

    save_state(state, str(state_path))
    return state


def build_state_from_setting(project_id: str, chapters_per_arc: int | None = None) -> dict:
    """Read the active setting package and initialize project arc plans.

    Existing contiguous formal chapters are preserved so bootstrap output is not
    regenerated. Chapter tasks remain empty until the Outline Agent loads an arc.
    """
    return build_state_from_paths(
        project_id,
        setting_path=Path(SETTING_PATH_STR),
        state_path=Path(STATE_PATH_STR),
        chapters_dir=Path(CHAPTERS_DIR_STR),
        chapters_per_arc=chapters_per_arc,
    )


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