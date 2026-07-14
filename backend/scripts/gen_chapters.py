"""批量章节生成驱动（P5 验证）

直接调 run_orchestrator 跑 N 章，跳过 bridge/server 中间层，便于
长时间批量生成测试。

用法：
    cd backend
    python -m scripts.gen_chapters \\
        --novel-id gen_300_$(date +%s) \\
        --title "测试长篇" \\
        --genre "玄幻" \\
        --concept "少年觉醒，闯荡修真世界" \\
        --chapters 50

环境：
    ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
    ANTHROPIC_AUTH_TOKEN=<MiniMax token>
    NOVEL_AUDIT_MODE=lite（推荐，省 ~70% LLM 成本）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 让 backend 在 path
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

# 在 import engine.* 前确保 MiniMax 环境变量已加载
os.environ.setdefault("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")


def main() -> int:
    parser = argparse.ArgumentParser(description="批量生成章节（P5 验证）")
    parser.add_argument("--novel-id", required=True, help="novel_id 唯一标识（区分不同 run）")
    parser.add_argument("--title", default="测试长篇")
    parser.add_argument("--genre", default="玄幻")
    parser.add_argument("--platform", default="fanqie", choices=["fanqie", "qidian", "personal"])
    parser.add_argument("--concept", default="少年觉醒，闯荡修真世界")
    parser.add_argument("--chapters", type=int, default=50, help="本次跑多少章")
    parser.add_argument("--audit-mode", default="lite",
                        choices=["full", "lite", "bootstrap", "draft"],
                        help="audit_mode（默认 lite，单模型评，省 ~70% LLM 成本）")
    parser.add_argument("--budget", type=float, default=500.0, help="USD 预算")
    parser.add_argument("--checkpoint-interval", type=int, default=10,
                        help="每 N 章打印一次中间状态（默认 10）")
    args = parser.parse_args()

    # 强制 audit_mode（按 P3 验证逻辑）
    os.environ["NOVEL_AUDIT_MODE"] = args.audit_mode

    print("=" * 60)
    print(f"📖 批量生成：{args.title}")
    print(f"   novel_id = {args.novel_id}")
    print(f"   目标章数 = {args.chapters}")
    print(f"   audit_mode = {args.audit_mode}")
    print(f"   预算 = ${args.budget:.2f}")
    print(f"   ANTHROPIC_BASE_URL = {os.environ.get('ANTHROPIC_BASE_URL','<unset>')}")
    print("=" * 60)

    # 强制 router 用 MiniMax 模型（替换默认的 claude-sonnet-4-5）
    # 通过 monkey-patching llm.router 的 MODEL_ROUTES_DEFAULT
    from engine import llm as llm_pkg
    from engine.llm.router import MODEL_ROUTES_DEFAULT

    # 把所有 anthropic 改成 MiniMax（API key 也用 ANTHROPIC_AUTH_TOKEN）
    print("\n🔧 配置 MiniMax 路由...")
    for agent_name in MODEL_ROUTES_DEFAULT:
        provider, _ = MODEL_ROUTES_DEFAULT[agent_name]
        if provider == "anthropic":
            MODEL_ROUTES_DEFAULT[agent_name] = ("anthropic", "MiniMax-M3")
    print(f"   路由表已全部切到 MiniMax-M3: {len(MODEL_ROUTES_DEFAULT)} 个 agent")

    # 构造初始 state
    from engine.state import create_initial_state, load_state, save_state
    from engine.orchestrator import run_orchestrator

    state = create_initial_state(
        novel_id=args.novel_id,
        title=args.title,
        platform=args.platform,
        genre=args.genre,
        setting_concept=args.concept,
        budget_limit_usd=args.budget,
    )

    # 如果有保存的 state 则恢复（便于断点续跑）
    state_path = BACKEND / "data" / f"{args.novel_id}_state.json"
    if state_path.exists():
        try:
            saved = load_state(str(state_path))
            if saved.get("novel_id") == args.novel_id:
                # 恢复已有进度（保留 budget_used / quality_history / chapter_task_queue）
                for k, v in saved.items():
                    state[k] = v
                done = state.get("current_chapter", 0)
                print(f"♻️  恢复 state：已完成 {done} 章（预算已用 ${state.get('budget_used_usd', 0):.4f}）")
        except Exception as e:
            print(f"⚠️  恢复 state 失败：{e}，用全新 state")

    started = time.time()
    try:
        final = run_orchestrator(state, max_chapters=args.chapters)
    except KeyboardInterrupt:
        print("\n⏸  用户中断，保存当前 state...")
        final = state
    elapsed = time.time() - started

    save_state(final, str(state_path))

    # 摘要
    print("\n" + "=" * 60)
    print(f"📊 生成摘要")
    print(f"   完成章数: {final.get('current_chapter', 0)}")
    print(f"   预算消耗: ${final.get('budget_used_usd', 0):.4f} / ${args.budget:.2f}")
    print(f"   总耗时: {elapsed:.1f}s（{elapsed / max(1, final.get('current_chapter', 1)):.1f}s / 章）")
    print(f"   错误数: {len(final.get('error_log', []))}")
    print(f"   人工待处理: {len(final.get('human_pending', []))}")
    print(f"   记忆缺口: {len(final.get('memory_gaps', []))}")
    qh = final.get("quality_history", [])
    if qh:
        print(f"   评分区间: min={min(qh):.1f} max={max(qh):.1f} avg={sum(qh)/len(qh):.2f}")
        last10 = qh[-10:]
        print(f"   最近 10 章分数: {[f'{s:.1f}' for s in last10]}")
    print(f"   State 存档: {state_path}")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())