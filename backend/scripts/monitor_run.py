"""
monitor_run.py — 端到端测试的**真实时**后台监控脚本（不是 agent）

设计目标：
  - 不依赖前端、不依赖 agent、不依赖 SSE 流
  - file system watching + state 轮询 + 实时 event 分类
  - 任何异常立刻落盘到 test_output/monitor_<run_id>.jsonl
  - 运行结束后输出结构化报告

监控维度：
  1. chapters/ 目录变化（每章落盘即记录）
  2. orchestrator_state.json 变化（每 save_state 即记录）
  3. error_log 增量（每条新 ERR 即记录）
  4. budget 增量（每 USD 跳变即记录）
  5. 字数异常（< 1500 或 > 2700）
  6. score 异常（< 6.0）
  7. human_pending 增量（must 优先关注）

使用：
  python -m scripts.monitor_run --pid <project_id> [--output test_output/monitor.jsonl]
  # 跑完 Ctrl+C 终止，输出结构化报告

历史动机：
  之前的"监控 agent"只是 30 秒轮询 + 离线分析，跑 50 章时大量异常被吞掉。
  本脚本用 file mtime 轮询（state.json + chapters/*.json） + 事件触发后立即
  记录（不依赖 watchdog，避免增加第三方依赖），事件触发后立刻落盘。
  真正实时。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.database import SessionLocal
from app.models import Chapter, Project

OUTPUT_DIR = BACKEND / "test_output"


# ─────────────────────────────────────────
# 事件分类
# ─────────────────────────────────────────
class EventCollector:
    """记录所有事件 + 分类 + 终态时输出报告。"""

    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.events: list[dict] = []
        self.chapter_snapshots: dict[int, dict] = {}  # ch_no -> 最新状态
        self.error_log_seen: set[str] = set()  # 去重 error_log
        self.initial_error_log_len: int = 0
        self.initial_budget_usd: float = 0.0
        self.initial_chapter_count: int = 0

    def record(self, kind: str, **payload):
        ev = {"ts": datetime.now().isoformat(), "kind": kind, **payload}
        self.events.append(ev)
        with self.output_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        # 实时打印
        ts_short = ev["ts"][11:19]
        print(f"[{ts_short}] {kind}: {json.dumps(payload, ensure_ascii=False)[:200]}")

    # ─── state 维度的 helper ───
    def snapshot_chapter(self, ch_no: int, meta: dict):
        """记录一章的 meta snapshot（detect 后续变化）"""
        old = self.chapter_snapshots.get(ch_no)
        self.chapter_snapshots[ch_no] = meta
        if old is None:
            self.record("chapter.new", chapter_no=ch_no, **meta)
        else:
            # 找变化
            changed_keys = {k: (old.get(k), meta.get(k)) for k in meta
                           if old.get(k) != meta.get(k)}
            if changed_keys:
                self.record("chapter.changed", chapter_no=ch_no, changed=changed_keys)

    def check_error_log_increment(self, error_log: list[str]):
        for err in error_log:
            if err not in self.error_log_seen:
                self.error_log_seen.add(err)
                # 分类
                if "tracker failed" in err:
                    self.record("error.tracker", raw=err)
                elif "outline failed" in err:
                    self.record("error.outline", raw=err)
                elif "rewriter failed" in err:
                    self.record("error.rewriter", raw=err)
                elif "SSL" in err or "CERTIFICATE" in err:
                    self.record("error.network", raw=err)
                else:
                    self.record("error.unknown", raw=err)

    def check_chapter_anomalies(self):
        """每章 meta 检查异常：字数 / score / rewrite 次数"""
        for ch_no, meta in self.chapter_snapshots.items():
            wc = meta.get("word_count", 0)
            if wc and wc < 1500:
                self.record("anomaly.short", chapter_no=ch_no, word_count=wc)
            if wc and wc > 2700:
                self.record("anomaly.long", chapter_no=ch_no, word_count=wc)
            score = meta.get("score", 0)
            if score and score < 6.0:
                self.record("anomaly.low_score", chapter_no=ch_no, score=score)
            rc = meta.get("rewrite_count", 0)
            if rc and rc >= 3:
                self.record("anomaly.heavy_rewrite", chapter_no=ch_no, rewrite_count=rc)

    # ─── 终态报告 ───
    def report(self) -> dict:
        # 错误分类
        err_kinds: dict[str, int] = {}
        for ev in self.events:
            k = ev["kind"]
            err_kinds[k] = err_kinds.get(k, 0) + 1
        # 异常统计
        anomalies = [ev for ev in self.events if ev["kind"].startswith("anomaly")]
        new_errors = [ev for ev in self.events if ev["kind"].startswith("error.")]
        return {
            "summary": {
                "total_events": len(self.events),
                "event_kinds": err_kinds,
                "new_errors": len(new_errors),
                "anomalies": len(anomalies),
                "chapters_snapshotted": len(self.chapter_snapshots),
            },
            "new_errors_sample": new_errors[:5],
            "anomalies_sample": anomalies[:5],
        }


# ─────────────────────────────────────────
# state 轮询主循环
# ─────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="端到端测试后台监控脚本")
    parser.add_argument("--pid", required=True, help="project_id")
    parser.add_argument("--output", default=None,
                        help=f"event log 路径（默认 {OUTPUT_DIR}/monitor_<时间戳>.jsonl）")
    parser.add_argument("--interval", type=float, default=2.0,
                        help="state 轮询间隔（秒，默认 2）")
    parser.add_argument("--once", action="store_true",
                        help="只跑一次就退出（用于测试）")
    args = parser.parse_args()

    # 找 state 文件 + chapters 目录
    db = SessionLocal()
    try:
        binding = db.query(Project).filter_by(id=args.pid).first()
        if not binding:
            print(f"project {args.pid} 不存在")
            return 1
        from app.models import NovelAIBinding
        nb = db.query(NovelAIBinding).filter_by(project_id=args.pid).first()
        if not nb:
            print(f"project {args.pid} 没 NovelAIBinding")
            return 1
        state_path = Path(nb.novel_ai_dir) / "output" / "orchestrator_state.json"
        # 真实章节落盘位置可能是 backend 的 data/engine/output/chapters
        # （架构历史包袱：state 在 novel_AI 路径，chapters 在 backend 路径）
        chapters_dirs = [
            Path(nb.novel_ai_dir) / "output" / "chapters",
            BACKEND / "data" / "engine" / "output" / "chapters",
        ]
        chapters_dirs = [d for d in chapters_dirs if d.exists()]
    finally:
        db.close()

    if not state_path.exists():
        print(f"state file 不存在: {state_path}（engine 还没跑过）")
        return 1

    output_path = Path(args.output) if args.output else \
                  OUTPUT_DIR / f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
    collector = EventCollector(output_path)
    print(f"=== monitor_run 启动 ===")
    print(f"  project_id: {args.pid}")
    print(f"  state: {state_path}")
    print(f"  chapters: {chapters_dirs}")
    print(f"  output: {output_path}")
    print(f"  interval: {args.interval}s")
    print(f"=== Ctrl+C 终止并输出报告 ===\n")

    # 初始 state
    initial_state = json.loads(state_path.read_text(encoding="utf-8"))
    collector.initial_error_log_len = len(initial_state.get("error_log", []))
    collector.initial_budget_usd = float(initial_state.get("budget_used_usd", 0))
    collector.initial_chapter_count = db.query(Chapter).filter_by(
        project_id=args.pid).count() if False else 0  # db 已关
    collector.error_log_seen = set(initial_state.get("error_log", []))
    collector.record("start",
                     error_log_len=collector.initial_error_log_len,
                     budget_usd=collector.initial_budget_usd,
                     current_chapter=initial_state.get("current_chapter", 0))

    # 初始 chapters 扫描（多个目录合并去重）
    for chapters_dir in chapters_dirs:
        for meta_path in sorted(chapters_dir.glob("ch_*_meta.json")):
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                ch_no = meta.get("chapter_number", 0)
                if ch_no:
                    collector.chapter_snapshots[ch_no] = meta
            except Exception:
                pass

    last_state_mtime = state_path.stat().st_mtime
    last_chapter_files: set[tuple[str, str]] = set()  # (dir, filename)
    for chapters_dir in chapters_dirs:
        for p in chapters_dir.glob("ch_*_meta.json"):
            last_chapter_files.add((str(chapters_dir), p.name))

    try:
        while True:
            # 1. 扫 chapters/ 看新文件（多个目录）
            current_chapter_files: set[tuple[str, str]] = set()
            for chapters_dir in chapters_dirs:
                for p in chapters_dir.glob("ch_*_meta.json"):
                    current_chapter_files.add((str(chapters_dir), p.name))
            new_files = current_chapter_files - last_chapter_files
            for dir_str, fname in new_files:
                meta_path = Path(dir_str) / fname
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    ch_no = meta.get("chapter_number", 0)
                    if ch_no:
                        collector.snapshot_chapter(ch_no, meta)
                except Exception as e:
                    collector.record("chapter.parse_failed", file=fname, error=str(e))
            last_chapter_files = current_chapter_files

            # 2. 扫 state 看变化
            if state_path.exists():
                mtime = state_path.stat().st_mtime
                if mtime != last_state_mtime:
                    state = json.loads(state_path.read_text(encoding="utf-8"))
                    # error_log 增量
                    collector.check_error_log_increment(state.get("error_log", []))
                    # budget 增量
                    budget_now = float(state.get("budget_used_usd", 0))
                    if budget_now > collector.initial_budget_usd + 0.01:
                        collector.record("budget.tick",
                                         current_usd=budget_now,
                                         delta_usd=budget_now - collector.initial_budget_usd)
                    # current_chapter 推进
                    cc = state.get("current_chapter", 0)
                    if cc and cc > (initial_state.get("current_chapter", 0)):
                        collector.record("state.chapter_advanced",
                                         current_chapter=cc)
                    # human_pending 增量
                    hp = state.get("human_pending", [])
                    if hp and len(hp) > 0:
                        # 记 must 的（重要）
                        for t in hp:
                            if t.get("priority") == "must":
                                collector.record("state.human_pending_must",
                                                 task_id=t.get("task_id"),
                                                 description=t.get("description"))
                    last_state_mtime = mtime
                    initial_state = state  # 推进

            # 3. 检查异常
            collector.check_chapter_anomalies()

            if args.once:
                break
            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n=== Ctrl+C 收到，输出报告 ===\n")
    finally:
        report = collector.report()
        report_path = output_path.with_suffix(".report.json")
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                encoding="utf-8")
        print(f"\n{'='*60}")
        print(f"事件汇总: {json.dumps(report['summary'], ensure_ascii=False, indent=2)}")
        print(f"new_errors 样本: {json.dumps(report['new_errors_sample'], ensure_ascii=False, indent=2)}")
        print(f"anomalies 样本: {json.dumps(report['anomalies_sample'], ensure_ascii=False, indent=2)}")
        print(f"完整报告: {report_path}")
        print(f"事件流: {output_path}")
        print(f"{'='*60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())