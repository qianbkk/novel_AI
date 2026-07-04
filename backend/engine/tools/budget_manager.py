"""tools/budget_manager.py — 预算管理与成本追踪

Migrated from novel_AI/tools/budget_manager.py. Reads/writes
backend/data/engine/output/budget_log.jsonl and
backend/data/engine/output/orchestrator_state.json (via config.paths).
"""
from __future__ import annotations
import json
import os
import time
from datetime import datetime
from typing import Optional

from ..config.paths import (
    OUTPUT_DIR_STR, STATE_PATH_STR,
)
from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..utils import atomic_write_json


# Budget log lives alongside the orchestrator state
BUDGET_LOG  = os.path.join(OUTPUT_DIR_STR, "logs", "budget_log.jsonl")
REPORT_DIR  = os.path.join(OUTPUT_DIR_STR, "reports")

os.makedirs(os.path.dirname(BUDGET_LOG), exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


def log_cost(chapter: int, agent: str, model: str,
             input_tokens: int, output_tokens: int, cost_usd: float,
             arc: int = 0) -> None:
    """Append a cost record to the JSONL log. Called by router after each LLM call."""
    record = {
        "ts": datetime.now().isoformat(),
        "chapter": chapter,
        "arc": arc,
        "agent": agent,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": round(cost_usd, 6),
    }
    with open(BUDGET_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_all_records() -> list:
    if not os.path.exists(BUDGET_LOG):
        return []
    records = []
    with open(BUDGET_LOG, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                pass
    return records


def generate_report(budget_limit: float = 500.0) -> dict:
    records = load_all_records()
    if not records:
        total, chapters_done, budget_limit = 0.0, 0, budget_limit
        # 跟 records 路径一样，从 STATE_PATH 读 planned + budget_limit
        total_planned = 157  # 默认值，跟 records 路径一致
        if os.path.exists(STATE_PATH_STR):
            try:
                with open(STATE_PATH_STR, encoding="utf-8") as f:
                    state = json.load(f)
                total = state.get("budget_used_usd", 0.0)
                budget_limit = state.get("budget_limit_usd", budget_limit)
                chapters_done = state.get("current_chapter", 0)
                # 迭代 #50: 也读 planned，否则 print_report 会 KeyError
                total_planned = state.get("total_chapters_planned", total_planned)
            except Exception:
                pass
        return {
            "total_cost_usd": total,
            "budget_limit_usd": budget_limit,
            "budget_used_pct": total / budget_limit * 100 if budget_limit else 0,
            "chapters_done": chapters_done,
            "cost_per_chapter": total / chapters_done if chapters_done else 0,
            # 迭代 #50: 加上 total_chapters_planned 键，否则 print_report KeyError
            "total_chapters_planned": total_planned,
            "records_available": False,
        }

    total_cost = sum(r["cost_usd"] for r in records)
    chapters_done = len(set(r["chapter"] for r in records if r["chapter"] > 0))

    by_agent: dict = {}
    for r in records:
        a = r["agent"]
        slot = by_agent.setdefault(a, {"calls": 0, "cost": 0.0, "tokens": 0})
        slot["calls"] += 1
        slot["cost"] += r["cost_usd"]
        slot["tokens"] += r.get("input_tokens", 0) + r.get("output_tokens", 0)

    by_arc: dict = {}
    for r in records:
        arc = r.get("arc", 0)
        by_arc[arc] = by_arc.get(arc, 0.0) + r["cost_usd"]

    recent = sorted(set(r["chapter"] for r in records if r["chapter"] > 0))[-20:]
    if recent:
        recent_cost = sum(r["cost_usd"] for r in records if r["chapter"] in recent)
        cost_per_recent = recent_cost / len(recent)
    else:
        cost_per_recent = total_cost / max(chapters_done, 1)

    total_planned = 157
    if os.path.exists(STATE_PATH_STR):
        try:
            with open(STATE_PATH_STR, encoding="utf-8") as f:
                state = json.load(f)
            total_planned = state.get("total_chapters_planned", 157)
            budget_limit = state.get("budget_limit_usd", budget_limit)
        except Exception:
            pass

    remaining = max(0, total_planned - chapters_done)
    projected_total = total_cost + remaining * cost_per_recent

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_cost_usd": round(total_cost, 4),
        "budget_limit_usd": budget_limit,
        "budget_used_pct": round(total_cost / budget_limit * 100, 1),
        "chapters_done": chapters_done,
        "total_chapters_planned": total_planned,
        "cost_per_chapter_avg": round(total_cost / max(chapters_done, 1), 4),
        "cost_per_chapter_recent20": round(cost_per_recent, 4),
        "projected_total_cost": round(projected_total, 2),
        "projected_within_budget": projected_total <= budget_limit,
        "by_agent": {k: {**v, "cost": round(v["cost"], 4)}
                     for k, v in sorted(by_agent.items(), key=lambda x: -x[1]["cost"])},
        "by_arc": {f"arc_{k}": round(v, 4) for k, v in sorted(by_arc.items())},
        "alerts": [],
        "records_available": True,
    }
    pct = report["budget_used_pct"]
    if pct >= 95:
        report["alerts"].append({"level": "CRITICAL", "msg": f"预算已用{pct:.1f}%，立即检查！"})
    elif pct >= 80:
        report["alerts"].append({"level": "WARNING", "msg": f"预算已用{pct:.1f}%，注意控制"})
    if not report["projected_within_budget"]:
        report["alerts"].append({"level": "WARNING",
            "msg": f"按当前速率，预计总成本${projected_total:.0f}，超出预算${budget_limit:.0f}"})
    return report


def print_report() -> None:
    report = generate_report()
    pct = report["budget_used_pct"]
    bar_len = int(pct / 5)
    bar = "█" * bar_len + "░" * (20 - bar_len)
    print(f"\n{'═'*55}")
    print(f"  💰 预算报告")
    print(f"{'═'*55}")
    print(f"  已用：${report['total_cost_usd']:.4f} / ${report['budget_limit_usd']:.0f}")
    print(f"  [{bar}] {pct:.1f}%")
    print(f"  章节：{report['chapters_done']} / {report.get('total_chapters_planned', '?')}")
    # 迭代 #50: 空 records 路径返回 cost_per_chapter 不是 _avg，
    # 且没有 cost_per_chapter_recent20 / projected_total_cost，用 .get()
    # 拿默认值，避免 KeyError。
    print(f"  均价：${report.get('cost_per_chapter_avg', report.get('cost_per_chapter', 0)):.4f}/章"
          f"（近20章：${report.get('cost_per_chapter_recent20', 0):.4f}）")
    print(f"  预计总成本：${report.get('projected_total_cost', 0):.2f} "
          f"({'✅在预算内' if report.get('projected_within_budget') else '⚠️超预算'})")
    if report.get("by_agent"):
        print(f"\n  Agent成本分布：")
        for agent, data in list(report["by_agent"].items())[:5]:
            print(f"    {agent:20s}  ${data['cost']:.4f}  ({data['calls']}次调用)")
    for alert in report.get("alerts", []):
        lvl = "🚨" if alert["level"] == "CRITICAL" else "⚠️"
        print(f"\n  {lvl} {alert['msg']}")
    print(f"{'═'*55}\n")
    report_path = os.path.join(REPORT_DIR, "budget_report.json")
    # 迭代 #49: 改用 atomic_write_json（避免 budget_report.json 半写损坏）
    atomic_write_json(report_path, report)


if __name__ == "__main__":
    print_report()