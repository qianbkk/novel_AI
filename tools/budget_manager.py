"""
tools/budget_manager.py — 预算管理与成本追踪
功能：
  - 实时成本记录（每次API调用后写入）
  - 预算预警（80%/95%两档）
  - 每章/每弧/每模型成本分析
  - 投影未来总成本

运行：python tools/budget_manager.py report
"""
import os, sys, json, time
from datetime import datetime

BASE_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUDGET_LOG  = os.path.join(BASE_DIR, "logs", "budget_log.jsonl")
REPORT_DIR  = os.path.join(BASE_DIR, "output", "reports")
STATE_PATH  = os.path.join(BASE_DIR, "output", "orchestrator_state.json")

os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)
os.makedirs(REPORT_DIR, exist_ok=True)


# ─────────────────────────────────────────────
# 写入成本记录
# ─────────────────────────────────────────────
def log_cost(
    chapter: int,
    agent: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    arc: int = 0,
):
    """每次API调用后调用此函数记录成本"""
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
            if line:
                try:
                    records.append(json.loads(line))
                except:
                    pass
    return records


# ─────────────────────────────────────────────
# 生成报告
# ─────────────────────────────────────────────
def generate_report(budget_limit: float = 500.0) -> dict:
    records = load_all_records()

    if not records:
        # 尝试从orchestrator_state读取
        if os.path.exists(STATE_PATH):
            with open(STATE_PATH, encoding="utf-8") as f:
                state = json.load(f)
            total = state.get("budget_used_usd", 0.0)
            budget_limit = state.get("budget_limit_usd", budget_limit)
            chapters_done = state.get("current_chapter", 0)
        else:
            total = 0.0
            chapters_done = 0

        return {
            "total_cost_usd": total,
            "budget_limit_usd": budget_limit,
            "budget_used_pct": total / budget_limit * 100 if budget_limit else 0,
            "chapters_done": chapters_done,
            "cost_per_chapter": total / chapters_done if chapters_done else 0,
            "records_available": False,
        }

    total_cost    = sum(r["cost_usd"] for r in records)
    chapters_done = len(set(r["chapter"] for r in records if r["chapter"] > 0))

    # 按Agent分组
    by_agent = {}
    for r in records:
        a = r["agent"]
        by_agent.setdefault(a, {"calls": 0, "cost": 0.0, "tokens": 0})
        by_agent[a]["calls"] += 1
        by_agent[a]["cost"] += r["cost_usd"]
        by_agent[a]["tokens"] += r.get("input_tokens", 0) + r.get("output_tokens", 0)

    # 按弧分组
    by_arc = {}
    for r in records:
        arc = r.get("arc", 0)
        by_arc.setdefault(arc, 0.0)
        by_arc[arc] += r["cost_usd"]

    # 每章均价（近20章）
    recent_chapters = sorted(set(r["chapter"] for r in records if r["chapter"] > 0))[-20:]
    if recent_chapters:
        recent_cost = sum(r["cost_usd"] for r in records if r["chapter"] in recent_chapters)
        cost_per_chapter_recent = recent_cost / len(recent_chapters)
    else:
        cost_per_chapter_recent = total_cost / max(chapters_done, 1)

    # 投影
    total_chapters_planned = 157
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)
        total_chapters_planned = state.get("total_chapters_planned", 157)
        budget_limit = state.get("budget_limit_usd", budget_limit)

    remaining_chapters = max(0, total_chapters_planned - chapters_done)
    projected_remaining = remaining_chapters * cost_per_chapter_recent
    projected_total = total_cost + projected_remaining

    report = {
        "generated_at": datetime.now().isoformat(),
        "total_cost_usd": round(total_cost, 4),
        "budget_limit_usd": budget_limit,
        "budget_used_pct": round(total_cost / budget_limit * 100, 1),
        "chapters_done": chapters_done,
        "total_chapters_planned": total_chapters_planned,
        "cost_per_chapter_avg": round(total_cost / max(chapters_done, 1), 4),
        "cost_per_chapter_recent20": round(cost_per_chapter_recent, 4),
        "projected_total_cost": round(projected_total, 2),
        "projected_within_budget": projected_total <= budget_limit,
        "by_agent": {k: {**v, "cost": round(v["cost"], 4)} for k, v in sorted(by_agent.items(), key=lambda x: -x[1]["cost"])},
        "by_arc": {f"arc_{k}": round(v, 4) for k, v in sorted(by_arc.items())},
        "alerts": [],
        "records_available": True,
    }

    # 预警
    pct = report["budget_used_pct"]
    if pct >= 95:
        report["alerts"].append({"level": "CRITICAL", "msg": f"预算已用{pct:.1f}%，立即检查！"})
    elif pct >= 80:
        report["alerts"].append({"level": "WARNING", "msg": f"预算已用{pct:.1f}%，注意控制"})
    if not report["projected_within_budget"]:
        report["alerts"].append({
            "level": "WARNING",
            "msg": f"按当前速率，预计总成本${projected_total:.0f}，超出预算${budget_limit:.0f}"
        })

    return report


def print_report():
    report = generate_report()
    pct = report["budget_used_pct"]
    bar_len = int(pct / 5)
    bar = "█" * bar_len + "░" * (20 - bar_len)

    print(f"\n{'═'*55}")
    print(f"  💰 预算报告")
    print(f"{'═'*55}")
    print(f"  已用：${report['total_cost_usd']:.4f} / ${report['budget_limit_usd']:.0f}")
    print(f"  [{bar}] {pct:.1f}%")
    print(f"  章节：{report['chapters_done']} / {report['total_chapters_planned']}")
    print(f"  均价：${report['cost_per_chapter_avg']:.4f}/章（近20章：${report['cost_per_chapter_recent20']:.4f}）")
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

    # 保存报告
    report_path = os.path.join(REPORT_DIR, "budget_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    print_report()
