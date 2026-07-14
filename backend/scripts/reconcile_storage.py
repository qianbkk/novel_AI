"""跨存储对账脚本（审计 P2）

四套持久化存储之间没有事务保证，长跑之后容易"账对不上"：
  - novel_assistant.db       — SQLite（Chapter / Project 等 ORM 表）
  - checkpoints.sqlite       — LangGraph state checkpoint
  - memory/*.json            — L2/L5 记忆（分层）
  - 章节 txt + meta.json     — novel_AI 输出目录（磁盘）

现状：`check_memory_health()` 只检查单一 L2 文件内部的数量阈值
（摘要是否过多 / 约束是否过多），**不检查跨存储一致性**。
这类问题通常不会立刻炸，而是积累到几十章之后才在导入/导出时
以很奇怪的方式冒出来。

修法（审计 P2）：加一个轻量级对账脚本，定期跑一遍三方 chapter
计数 / 编号是否一致。退出码 0 = 通过；非 0 = 有差异（CI 友好）。

使用：
  python -m scripts.reconcile_storage             # 对账默认项目
  python -m scripts.reconcile_storage --pid XXX  # 对账指定项目
  python -m scripts.reconcile_storage --strict   # WARN 也算失败
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# 把 backend 加进 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal, engine
from app.models import Chapter, Project


# ─── 报告辅助 ─────────────────────────────────────────
class ReconcileReport:
    def __init__(self, strict: bool = False):
        self.strict = strict
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def err(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note(self, msg: str) -> None:
        self.info.append(msg)

    def print(self) -> None:
        print("=" * 60)
        print("跨存储对账报告")
        print("=" * 60)
        for line in self.info:
            print(f"  ℹ️  {line}")
        for line in self.warnings:
            print(f"  ⚠️  {line}")
        for line in self.errors:
            print(f"  ❌ {line}")
        print()
        print(f"Summary: {len(self.errors)} errors, {len(self.warnings)} warnings, {len(self.info)} info")
        if self.errors:
            print("STATUS: FAIL")
        elif self.warnings and self.strict:
            print("STATUS: FAIL (strict mode)")
        else:
            print("STATUS: OK")


# ─── 数据源 1：SQLite Chapter 表 ──────────────────────
def collect_db_chapters(report: ReconcileReport, project_id: str | None) -> dict[int, str]:
    """返回 {chapter_no: status}。status 来自 DB 的 chapter.status 字段。"""
    db = SessionLocal()
    try:
        q = db.query(Chapter)
        if project_id:
            q = q.filter(Chapter.project_id == project_id)
        rows = q.order_by(Chapter.chapter_no).all()
        result = {}
        for c in rows:
            status = getattr(c, "status", "") or ""
            result[c.chapter_no] = status
        report.note(f"DB: {len(result)} chapters" + (f" (project={project_id})" if project_id else ""))
        return result
    finally:
        db.close()


# ─── 数据源 2：LangGraph checkpoint 文件 ─────────────
def collect_checkpoint_chapters(report: ReconcileReport) -> dict[int, dict]:
    """从 checkpoints.sqlite 提取 LangGraph state 里 chapter_task_queue 的 chapter_no 集合。

    注：checkpoints.sqlite 是 LangGraph 内部状态，schema 不公开。我们
    走「try to extract chapter_no from any column that looks like one」
    的稳妥策略——只读 chapter_no 数字字段。
    """
    backend_dir = Path(__file__).resolve().parents[1]
    ckpt_path = backend_dir / "data" / "checkpoints.sqlite"
    if not ckpt_path.exists():
        report.warn(f"checkpoints.sqlite 不存在 ({ckpt_path})，跳过")
        return {}

    result: dict[int, dict] = {}
    try:
        con = sqlite3.connect(str(ckpt_path))
        try:
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]
            # 尝试常见的 LangGraph 表名
            for tbl in ("checkpoints", "writes", "checkpoint_blobs"):
                if tbl not in tables:
                    continue
                try:
                    cur = con.execute(f"SELECT * FROM {tbl} LIMIT 0")
                    cols = [d[0] for d in cur.description]
                    # 找含 'chapter' 或 'task' 字样的列 → 解析 JSON
                    for col_name in cols:
                        if any(k in col_name.lower() for k in ("blob", "channel", "task", "queue", "data")):
                            try:
                                cur2 = con.execute(f"SELECT {col_name} FROM {tbl} WHERE {col_name} LIKE '%chapter%' LIMIT 50")
                                for row in cur2.fetchall():
                                    blob = row[0]
                                    if isinstance(blob, bytes):
                                        try:
                                            blob = blob.decode("utf-8", errors="ignore")
                                        except Exception:
                                            continue
                                    if isinstance(blob, str) and ("chapter_number" in blob or "chapter_no" in blob):
                                        # 找数字字段
                                        import re
                                        for m in re.finditer(r'"chapter_(?:number|no)"\s*:\s*(\d+)', blob):
                                            try:
                                                n = int(m.group(1))
                                                result.setdefault(n, {"source": f"{tbl}.{col_name}"})
                                            except Exception:
                                                pass
                            except Exception:
                                continue
                except Exception:
                    continue
            report.note(f"checkpoints.sqlite: 提取到 {len(result)} unique chapter_no")
        finally:
            con.close()
    except Exception as e:
        report.warn(f"读 checkpoints.sqlite 失败: {e}")
    return result


# ─── 数据源 3：novel_AI 输出目录（章节 txt） ───────
def collect_disk_chapters(report: ReconcileReport, novel_ai_dir: str | None) -> dict[int, int]:
    """从 novel_AI/output/chapters/ch_NNNN.txt 提取章节号 + 字数。

    返回 {chapter_no: word_count}。
    """
    if not novel_ai_dir:
        report.warn("novel_ai_dir 未提供，跳过磁盘对账")
        return {}

    chapters_dir = Path(novel_ai_dir) / "output" / "chapters"
    if not chapters_dir.exists():
        report.warn(f"输出目录不存在 ({chapters_dir})，跳过")
        return {}

    result: dict[int, int] = {}
    for txt in chapters_dir.glob("ch_*.txt"):
        m = txt.stem.split("_", 1)
        if len(m) != 2:
            continue
        try:
            n = int(m[1])
        except ValueError:
            continue
        try:
            text = txt.read_text(encoding="utf-8")
            # 排除 [待修订] 前缀和空白行后再计字数
            cleaned = text.replace("[待修订]\n", "").strip()
            result[n] = len(cleaned)
        except Exception as e:
            report.warn(f"读 {txt.name} 失败: {e}")
    report.note(f"磁盘: {len(result)} chapter txt files in {chapters_dir}")
    return result


# ─── 数据源 4：L2 记忆中的 chapter 计数 ───────────
def collect_l2_chapter_count(report: ReconcileReport, novel_id: str) -> int | None:
    """读 L2 记忆 JSON 里 last_chapter / total_chapters_tracked 字段。

    L2 schema 实际嵌套在 meta.total_chapters_tracked / meta.last_updated_chapter
    （之前以为在 root 是错的）。同时宽松匹配 schema 演化路径：
      - 新版: meta.total_chapters_tracked
      - 旧版: 直接在根级 total_chapters_tracked / last_chapter
      - 兜底: hot.recent_summaries 列表长度
    """
    from engine.memory.manager import L2_DIR_STR  # 已知 backend/data/engine/memory 路径常量
    p = Path(L2_DIR_STR) / f"{novel_id}_memory.json"
    if not p.exists():
        report.warn(f"L2 记忆文件不存在 ({p})")
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        meta = data.get("meta", {}) if isinstance(data.get("meta"), dict) else {}

        # 按优先级匹配字段
        for key in ("total_chapters_tracked", "last_updated_chapter", "last_chapter", "chapter_count"):
            for src in (meta, data):
                if key in src and isinstance(src[key], int) and src[key] > 0:
                    return src[key]
        # 兜底：hot.recent_summaries 列表长度
        hot = data.get("hot", {}) if isinstance(data.get("hot"), dict) else {}
        summaries = hot.get("recent_summaries", [])
        if isinstance(summaries, list) and summaries:
            return len(summaries)
        report.warn(f"L2 记忆 {p} 没找到 chapter 计数字段（meta keys={list(meta.keys())[:5]}）")
        return None
    except Exception as e:
        report.warn(f"读 L2 记忆失败: {e}")
        return None


# ─── 对账主函数 ─────────────────────────────────────
def reconcile(
    project_id: str | None = None,
    novel_ai_dir: str | None = None,
    novel_id: str = "default",
    strict: bool = False,
) -> int:
    report = ReconcileReport(strict=strict)

    # 取各数据源
    db_chs       = collect_db_chapters(report, project_id)
    disk_chs     = collect_disk_chapters(report, novel_ai_dir)
    ckpt_chs     = collect_checkpoint_chapters(report)
    l2_ch_count  = collect_l2_chapter_count(report, novel_id)

    db_set   = set(db_chs.keys())
    disk_set = set(disk_chs.keys())
    ckpt_set = set(ckpt_chs.keys())

    # ─── 检查 1: DB vs 磁盘 ─────────────────────
    only_in_db    = db_set - disk_set
    only_on_disk  = disk_set - db_set
    if only_in_db:
        report.warn(f"DB 有但磁盘无 {len(only_in_db)} 章: {sorted(only_in_db)[:20]}"
                    f"{'...' if len(only_in_db) > 20 else ''}")
    if only_on_disk:
        report.warn(f"磁盘有但 DB 无 {len(only_on_disk)} 章: {sorted(only_on_disk)[:20]}"
                    f"{'...' if len(only_on_disk) > 20 else ''}")
    if not only_in_db and not only_on_disk and db_set:
        report.note(f"DB ↔ 磁盘 一致（{len(db_set)} 章）")

    # ─── 检查 2: 编号连续性（DB）────────────────
    if db_chs:
        sorted_nums = sorted(db_chs.keys())
        expected = list(range(sorted_nums[0], sorted_nums[-1] + 1))
        missing = set(expected) - db_set
        if missing:
            report.warn(f"DB 章节编号不连续（缺 {len(missing)} 章）: {sorted(missing)[:20]}"
                        f"{'...' if len(missing) > 20 else ''}")

    # ─── 检查 3: L2 vs DB ──────────────────────
    if l2_ch_count is not None and db_chs:
        if l2_ch_count > len(db_chs):
            report.err(f"L2 记忆说追踪了 {l2_ch_count} 章，但 DB 只有 {len(db_chs)} 章"
                       f"（L2 领先 {l2_ch_count - len(db_chs)} 章——可能是 tracker 跑了但"
                       f" chapter 没成功落库，或 export 时漏了）")
        elif l2_ch_count < len(db_chs):
            report.warn(f"L2 记忆说追踪了 {l2_ch_count} 章，DB 有 {len(db_chs)} 章"
                        f"（DB 领先 {len(db_chs) - l2_ch_count} 章——可能是 tracker 漏跑了"
                        f" 某些章节，或 chapter 是 import 进来的没走 orchestrator）")
        else:
            report.note(f"L2 ↔ DB chapter 数一致（{l2_ch_count}）")

    # ─── 检查 4: checkpoint vs DB（参考性）──────
    if ckpt_chs:
        only_in_ckpt = ckpt_set - db_set
        only_in_db_ckpt = db_set - ckpt_set
        if only_in_ckpt:
            report.info(f"checkpoint 提到但 DB 无 {len(only_in_ckpt)} 章"
                        f"（可能任务队列还没跑完）")
        if only_in_db_ckpt:
            report.info(f"DB 有但 checkpoint 不提 {len(only_in_db_ckpt)} 章"
                        f"（已完成章节，task queue 已 pop）")

    # ─── 检查 5: human_required 章节一致性 ───
    hr_in_db   = {n for n, s in db_chs.items() if s == "human_required"}
    hr_on_disk = set()
    if disk_chs:
        chapters_dir = Path(novel_ai_dir) / "output" / "chapters"
        for n in disk_chs:
            txt = chapters_dir / f"ch_{n:04d}.txt"
            if txt.exists() and txt.read_text(encoding="utf-8").startswith("[待修订]"):
                hr_on_disk.add(n)
    if hr_in_db != hr_on_disk:
        report.warn(f"human_required 不一致：DB={len(hr_in_db)} 磁盘={len(hr_on_disk)}"
                    f"（DB-only={sorted(hr_in_db - hr_on_disk)[:5]} "
                    f" 磁盘-only={sorted(hr_on_disk - hr_in_db)[:5]}）")

    report.print()
    if report.errors:
        return 1
    if report.warnings and strict:
        return 2
    return 0


# ─── CLI ───────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="跨存储对账脚本（审计 P2）")
    parser.add_argument("--pid", default=None, help="指定 project_id（默认所有项目）")
    parser.add_argument("--novel-id", default="default", help="novel_id（默认 'default'）")
    parser.add_argument("--novel-ai-dir", default=None, help="novel_AI 输出目录（用于磁盘对账）")
    parser.add_argument("--strict", action="store_true", help="WARN 也算失败")
    args = parser.parse_args()

    return reconcile(
        project_id=args.pid,
        novel_ai_dir=args.novel_ai_dir,
        novel_id=args.novel_id,
        strict=args.strict,
    )


if __name__ == "__main__":
    sys.exit(main())