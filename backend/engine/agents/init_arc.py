"""build_arc_state.py — 根据 setting_package.json 直接生成 orchestrator_state.json 的 arc_plans

不调 LLM，1 秒内完成。Bootstrap 的简化版，足够让 run 流程跑起来。
"""
from __future__ import annotations
import json
from pathlib import Path

from ..config.paths import SETTING_PATH_STR, STATE_PATH_STR
from ..orchestrator import _placeholder_task
from ..state import create_initial_state, save_state


def build_state_from_setting(project_id: str, chapters_per_arc: int | None = None) -> dict:
    """读 setting_package.json → 把每个 arc 转成 ArcPlan 字典，注入 state。

    chapters_per_arc: 覆盖原 estimated_chapters（可选）。

    30 章真实 LLM 测试 (2026-07-20) 修复：
    之前 init_arc 只生成 arc_plans，chapter_task_queue 留空。
    真正填队列的是 node_load_arc_tasks → run_outline(LLM)。
    但真实 LLM 在某些 prompt 下返的 task 数 < estimated_chapters，
    导致 orchestrator 跑完 LLM 返的子集就停了，跑不到 full N 章。

    现在 init_arc 直接按 arc_plans[i].estimated_chapters 预填 placeholder
    task 队列——node_load_arc_tasks 检查到 queue 非空就跳过 outline，
    走 placeholder 链路也能写出完整 N 章。
    placeholder 字段足够 writer / normalizer / checker 消费（chapter_number,
    chapter_role, chapter_goal, main_characters, target_length, audit_mode,
    is_arc_climax 都有）。
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

    # 30 章真实 LLM 测试 (2026-07-20)：按 estimated_chapters 预填 placeholder
    # task 队列，绕过 run_outline LLM 返数偏少的失败模式。node_load_arc_tasks
    # 见 queue 非空会跳过 outline，直接进入 node_get_next_task。
    # 占位 task 的 main_characters 从 setting.key_characters 取主角 + 关键配角，
    # 这样 writer 反吞设定修复（engine/agents/writer.py:_build_world_block）能拿到
    # 真正的角色名（林渊 / 苏晚栀 / 孟浩 / 顾青锋 等），不会写成纯\"主角\"。
    setting_key_chars = setting.get("key_characters") or []
    setting_protagonist = (setting.get("protagonist") or {}).get("name", "主角")
    char_pool = [c.get("name") for c in setting_key_chars if c.get("name")] or [setting_protagonist]
    char_pool.insert(0, setting_protagonist)  # 主角永远首位
    char_pool = list(dict.fromkeys(char_pool))  # 去重保序
    task_queue: list = []
    for arc_idx, arc in enumerate(arc_plans):
        arc_len = int(arc["estimated_chapters"])
        for i in range(arc_len):
            task = _placeholder_task(arc_idx, i, arc)
            # 每 5 章轮换主要出场角色：1-5 林渊+苏晚栀，6-10 林渊+孟浩，...
            # 简化：前 3 章引入不同配角，让前期埋下人物关系；后期扩充到 4-5 个
            if i < 3:
                task["main_characters"] = [setting_protagonist, char_pool[min(i+1, len(char_pool)-1)]]
            elif i < 10:
                task["main_characters"] = [setting_protagonist, char_pool[min(1 + (i // 3), len(char_pool)-1)]]
            else:
                # 后期章节尽量多带配角
                task["main_characters"] = char_pool[:min(4, len(char_pool))]
            task_queue.append(task)
    state["chapter_task_queue"] = task_queue
    state["total_chapters_planned"] = len(task_queue)
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
    print(f"✅ 已预填 chapter_task_queue: {len(state['chapter_task_queue'])} 个 placeholder task")
    return state


if __name__ == "__main__":
    import sys
    sys.exit(0 if run_init_arc(sys.argv[1:], ".") else 1)