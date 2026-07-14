"""直接调 run_orchestrator 批量生成章节（P5 验证）

跳过 bridge / worldbuild / bootstrap 完整流程，直接构造 state.arc_plans
和 state.chapter_task_queue 调 run_orchestrator。简化但保留了核心：
  - audit_mode 控制（full/lite/bootstrap/draft）
  - escalation → memory_gap 标记（P1）
  - consecutive_low_score 自适应审核（P3）
  - arc_end plan_vs_actual diff 观测（P4）
  - 跨存储对账报告（P2）

环境：
    ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
    ANTHROPIC_AUTH_TOKEN=<MiniMax token>
    NOVEL_AUDIT_MODE=lite（推荐）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")


def _build_state(args) -> dict:
    """构造一个最小 OrchestratorState —— 含 1 个 arc、N 个 chapter_task_queue。

    arc 结构：使用 _placeholder_task helper（已在 orchestrator.py 验证可用）。
    """
    from engine.state import create_initial_state
    from engine.orchestrator import _placeholder_task

    state = create_initial_state(
        novel_id=args.novel_id,
        title=args.title,
        platform=args.platform,
        genre=args.genre,
        setting_concept=args.concept,
        budget_limit_usd=args.budget,
    )

    # 1 个 arc + N 个 chapter task
    arc_plan = {
        "arc_id": 1,
        "arc_name": f"{args.title}（主线）",
        "arc_goal": args.concept,
        "estimated_chapters": args.chapters,
        "arc_climax_description": "主角觉醒，获得传承，开始修真之路",
        "arc_climax_chapter_offset": max(1, args.chapters // 2),
        "emotion_curve": "上升",
        "new_characters_introduced": ["主角"],
        "arc_ending_state": "主角完成觉醒，准备出发",
        "is_final_arc": True,
    }
    state["arc_plans"] = [arc_plan]
    state["total_arcs_planned"] = 1
    state["current_arc"] = 0

    # 关键：直接预填 chapter_task_queue，node_load_arc_tasks 看到非空会跳过
    state["chapter_task_queue"] = [
        _placeholder_task(0, i, arc_plan) for i in range(args.chapters)
    ]
    state["total_chapters_planned"] = args.chapters

    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="直接模式批量生成章节（P5）")
    parser.add_argument("--novel-id", required=True)
    parser.add_argument("--title", default="测试长篇")
    parser.add_argument("--genre", default="玄幻")
    parser.add_argument("--platform", default="fanqie")
    parser.add_argument("--concept", default="少年觉醒，获得上古传承，开始修真之路")
    parser.add_argument("--chapters", type=int, default=10)
    parser.add_argument("--audit-mode", default="lite",
                        choices=["full", "lite", "bootstrap", "draft"])
    parser.add_argument("--budget", type=float, default=500.0)
    parser.add_argument("--resume", action="store_true",
                        help="从已有 state 恢复（断点续跑）")
    args = parser.parse_args()

    os.environ["NOVEL_AUDIT_MODE"] = args.audit_mode

    print(f"📖 批量生成（直接模式）：{args.title}")
    print(f"   novel_id = {args.novel_id}")
    print(f"   目标章数 = {args.chapters}")
    print(f"   audit_mode = {args.audit_mode}")
    print(f"   ANTHROPIC_BASE_URL = {os.environ.get('ANTHROPIC_BASE_URL','<unset>')}")

    # 强制 router 把所有 agent 都路由到 MiniMax（覆盖 deepseek + anthropic 默认）
    from engine.llm.router import MODEL_ROUTES_DEFAULT
    n_agents = 0
    for agent_name in MODEL_ROUTES_DEFAULT:
        provider, _ = MODEL_ROUTES_DEFAULT[agent_name]
        if provider in ("anthropic", "deepseek"):
            MODEL_ROUTES_DEFAULT[agent_name] = ("anthropic", "MiniMax-M3")
            n_agents += 1
    print(f"🔧 {n_agents} 个 agent 切到 MiniMax-M3（覆盖 anthropic + deepseek 默认）")

    # 设一个 dummy DEEPSEEK_API_KEY 防止 router 启动时报「未配置」警告
    os.environ.setdefault("DEEPSEEK_API_KEY", "sk-dummy-not-used-because-we-rerouted-all-to-minimax")

    # 构造或恢复 state
    state_path = BACKEND / "data" / f"{args.novel_id}_state.json"
    if args.resume and state_path.exists():
        from engine.state import load_state
        try:
            saved = load_state(str(state_path))
            if saved.get("novel_id") == args.novel_id:
                state = saved
                done = state.get("current_chapter", 0)
                print(f"♻️  恢复 state：已完成 {done} 章（预算 ${state.get('budget_used_usd', 0):.4f}）")
            else:
                state = _build_state(args)
        except Exception as e:
            print(f"⚠️  恢复失败（{e}），用全新 state")
            state = _build_state(args)
    else:
        state = _build_state(args)

    # 跑 orchestrator
    from engine.orchestrator import run_orchestrator
    from engine.state import save_state

    started = time.time()
    try:
        final = run_orchestrator(state, max_chapters=args.chapters)
    except KeyboardInterrupt:
        print("\n⏸  用户中断，保存 state...")
        final = state
    elapsed = time.time() - started

    save_state(final, str(state_path))

    # 摘要
    print(f"\n{'='*60}")
    print(f"📊 生成摘要")
    print(f"   完成章数: {final.get('current_chapter', 0)}")
    print(f"   预算消耗: ${final.get('budget_used_usd', 0):.4f} / ${args.budget:.2f}")
    print(f"   总耗时: {elapsed:.1f}s ({elapsed / max(1, final.get('current_chapter', 1)):.1f}s / 章)")
    print(f"   错误数: {len(final.get('error_log', []))}")
    print(f"   人工待处理: {len(final.get('human_pending', []))}")
    print(f"   记忆缺口 (memory_gaps): {len(final.get('memory_gaps', []))}")
    qh = final.get("quality_history", [])
    if qh:
        print(f"   评分区间: min={min(qh):.1f} max={max(qh):.1f} avg={sum(qh)/len(qh):.2f}")
        last10 = qh[-10:]
        print(f"   最近 10 章分数: {[f'{s:.1f}' for s in last10]}")
    print(f"   State 存档: {state_path}")
    print(f"{'='*60}")

    # 跨存储对账（P2）
    print("\n[跨存储对账 P2]")
    from scripts.reconcile_storage import reconcile
    rc = reconcile(novel_id=args.novel_id, strict=False)
    return rc


if __name__ == "__main__":
    sys.exit(main())