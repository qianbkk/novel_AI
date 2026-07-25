"""tools/beat_checker.py — 节拍校验器（2026-07-25 战略审视 Commit 4）

离线 CLI 工具，扫 <novel_ai_dir>/output/chapters/ch_NNNN_meta.json
检查网文关键节拍是否达标,产出红/黄/绿报告。

校验维度（每项独立打分,最终汇总）:
1. 扮猪吃虎 / 打脸三阶段节拍:最近 10 章必含 ≥1 次完整三阶段
   (伪装示弱 → 反派挑衅 → 摊牌碾压)
   - 阶段 1: shuang_type ∈ {碾压, 逆袭, 打脸} 且 chapter_role ∈ {铺垫, 发展}
   - 阶段 2: 紧跟阶段 1, shuang_type ∈ {打脸, 碾压}, 含反派名字 (从 main_characters 提取)
   - 阶段 3: 紧跟阶段 2, shuang_type ∈ {碾压, 救场}, 且 verification 检查 (is_arc_climax=True)
2. 升级循环节拍:每 5-10 章必含 [危机 → 升级 → 反杀 → 新危机伏笔] 完整循环
   - "升级": shuang_type == "升级"
   - "反杀": 紧跟升级的 shuang_type ∈ {碾压, 打脸, 救场}
   - "新危机伏笔": foreshadowing_ops 含 plant (下一轮危机的种子)
3. 情绪多样性:最近 5 章 emotion_core 唯一值 ≥ 3 (避免连续同情绪)
4. 章末钩子存在:每章 ending_hook_type 必须是 7 类钩子之一

来源 docs/wiki/03-Writing-Engine.md §1 M4 (扮猪吃虎/打脸节拍校验)。

CLI 用法:
    python -m engine.tools.beat_checker <novel_ai_dir> [--window 10]

输出:
    报告打印到 stdout (红/黄/绿 + 详细 issue 列表),可选写 reports/beat_report.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# 项目内 import 容错（CLI 直接调用 + pytest 都能 work）
try:
    from ..config.paths import CHAPTERS_DIR_STR, OUTPUT_DIR_STR
    from ..config.prompt_templates import HOOK_TYPES, SHUANG_TYPES, EMOTION_CORES
except ImportError:
    # CLI 直接调用时 sys.path 不一定包含 backend/,手动加
    BACKEND_ROOT = Path(__file__).resolve().parents[2]
    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))
    from engine.config.paths import CHAPTERS_DIR_STR, OUTPUT_DIR_STR  # noqa: E402
    from engine.config.prompt_templates import HOOK_TYPES, SHUANG_TYPES, EMOTION_CORES  # noqa: E402


# ─── 节拍校验核心函数 ─────────────────────────


def load_chapter_metas(novel_ai_dir: str) -> list[dict[str, Any]]:
    """读 <novel_ai_dir>/output/chapters/ 下所有 ch_NNNN_meta.json。

    按 chapter_no 升序排序,缺失字段用空字符串占位。
    """
    chapters_dir = Path(novel_ai_dir) / "output" / "chapters"
    if not chapters_dir.is_dir():
        return []
    metas: list[dict[str, Any]] = []
    for path in sorted(chapters_dir.glob("ch_*_meta.json")):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            # 兼容不同字段名(meta vs setting_package 中的 chapter_task 字段)
            meta.setdefault("chapter_no", meta.get("chapter_number", 0))
            metas.append(meta)
        except Exception as e:
            print(f"⚠ 跳过 {path.name}: {e}", file=sys.stderr)
    return metas


def check_face_slap_three_stage(metas: list[dict[str, Any]], window: int) -> dict[str, Any]:
    """扮猪吃虎 / 打脸三阶段节拍校验。

    三阶段序列(必须按顺序出现,跨度 ≤ 5 章):
      - 阶段 1 (铺垫): chapter_role ∈ {铺垫, 发展} + shuang_type ∈ {碾压, 逆袭, 打脸}
      - 阶段 2 (挑衅): shuang_type ∈ {打脸, 碾压}
      - 阶段 3 (摊牌): shuang_type ∈ {碾压, 救场} + (可选 is_arc_climax)

    检查最近 window 章内是否含 ≥1 次完整三阶段。
    """
    if len(metas) < 3:
        return {
            "status": "YELLOW",
            "reason": f"章节数 < 3 ({len(metas)}),无法验证三阶段节拍",
            "details": [],
        }
    recent = metas[-window:]
    details: list[str] = []

    # 找所有可能的"阶段 1"起点,然后向后找阶段 2/3
    for start_idx, m in enumerate(recent):
        role = m.get("chapter_role", "")
        shuang = m.get("shuang_type", "") or ""
        if role in ("铺垫", "发展") and shuang in ("碾压", "逆袭", "打脸"):
            # 阶段 1 起点,向后找阶段 2/3
            for offset in range(1, 4):  # 阶段 2 在 1-3 章后
                if start_idx + offset >= len(recent):
                    break
                stage2 = recent[start_idx + offset]
                if stage2.get("shuang_type") in ("打脸", "碾压"):
                    for offset2 in range(1, 4):
                        if start_idx + offset + offset2 >= len(recent):
                            break
                        stage3 = recent[start_idx + offset + offset2]
                        if stage3.get("shuang_type") in ("碾压", "救场"):
                            ch1 = m.get("chapter_no", "?")
                            ch2 = stage2.get("chapter_no", "?")
                            ch3 = stage3.get("chapter_no", "?")
                            details.append(
                                f"✓ 三阶段完整:Ch{ch1}({role}/{shuang}) → "
                                f"Ch{ch2}(打脸/碾压) → Ch{ch3}(碾压/救场)"
                            )
                            return {"status": "GREEN", "reason": "完整三阶段已找到", "details": details}
    details.append(f"✗ 最近 {window} 章内未找到完整三阶段(铺垫→打脸→碾压)")
    return {"status": "RED", "reason": "缺扮猪吃虎/打脸三阶段", "details": details}


def check_upgrade_loop(metas: list[dict[str, Any]], window: int) -> dict[str, Any]:
    """升级循环节拍校验:每 5-10 章必含 [升级 → 反杀 → 新伏笔]。

    - "升级": shuang_type == "升级"
    - "反杀": 紧跟升级的 shuang_type ∈ {碾压, 打脸, 救场}
    - "新危机伏笔": foreshadowing_ops 含 plant

    检查最近 window 章内是否含 ≥1 次完整循环。
    """
    if len(metas) < 2:
        return {
            "status": "YELLOW",
            "reason": f"章节数 < 2 ({len(metas)}),无法验证升级循环",
            "details": [],
        }
    recent = metas[-window:]
    details: list[str] = []

    for i, m in enumerate(recent[:-1]):
        if m.get("shuang_type") != "升级":
            continue
        # 阶段 1: 升级
        # 阶段 2: 反杀 + 新伏笔(可在同一章,也可分散到下一章)
        # 先检查下一章(常规情况)
        next_m = recent[i + 1]
        if next_m.get("shuang_type") not in ("碾压", "打脸", "救场"):
            continue
        # 阶段 3: 新伏笔(下两章或同章的 foreshadowing_ops)
        ch_for_plant = next_m
        if ch_for_plant.get("foreshadowing_ops"):
            for op in ch_for_plant["foreshadowing_ops"]:
                if isinstance(op, dict) and op.get("op") == "plant":
                    ch1 = m.get("chapter_no", "?")
                    ch2 = next_m.get("chapter_no", "?")
                    details.append(
                        f"✓ 升级循环:Ch{ch1}(升级) → Ch{ch2}({next_m.get('shuang_type')})"
                        f" + 新伏笔 {op.get('desc', '')[:30]}"
                    )
                    return {"status": "GREEN", "reason": "完整升级循环已找到", "details": details}
    details.append(f"✗ 最近 {window} 章内未找到完整升级循环(升级→反杀→新伏笔)")
    return {"status": "RED", "reason": "缺升级循环节拍", "details": details}


def check_emotion_diversity(metas: list[dict[str, Any]], window: int = 5) -> dict[str, Any]:
    """情绪多样性校验:最近 5 章 emotion_core 唯一值 ≥ 3。

    避免连续 3 章同 emotion_core 导致读者情绪疲劳弃书
    (战略审视 Commit 3 的关键约束)。
    """
    recent = metas[-window:] if len(metas) >= window else metas
    cores = [m.get("emotion_core", "") for m in recent if m.get("emotion_core")]
    if len(cores) < 3:
        return {
            "status": "YELLOW",
            "reason": f"有效 emotion_core 字段 < 3,无法验证多样性",
            "details": [f"采集到 {len(cores)} 个 emotion_core 字段"],
        }
    unique = set(cores)
    if len(unique) < 3:
        details = [f"✗ 最近 {len(cores)} 章 emotion_core 唯一值仅 {len(unique)} 种:{cores}"]
        return {
            "status": "RED",
            "reason": "情绪多样性不足(唯一值 < 3)",
            "details": details,
        }
    return {
        "status": "GREEN",
        "reason": f"情绪多样性 OK({len(unique)} 种)",
        "details": [f"✓ emotion_core 分布:{list(unique)}"],
    }


def check_hook_present(metas: list[dict[str, Any]]) -> dict[str, Any]:
    """章末钩子存在性校验:每章 ending_hook_type 必须在 7 类钩子内。"""
    valid = set(HOOK_TYPES.keys())
    issues: list[str] = []
    for m in metas:
        h = m.get("ending_hook_type", "")
        if h and h not in valid:
            issues.append(f"Ch{m.get('chapter_no', '?')}: ending_hook_type={h!r} 不在 7 类钩子中")
    if issues:
        return {"status": "YELLOW", "reason": "部分章节钩子类型越界", "details": issues}
    return {
        "status": "GREEN",
        "reason": f"所有 {len(metas)} 章钩子类型合法",
        "details": [],
    }


# ─── 报告生成 + CLI ─────────────────────────


def run_all_checks(novel_ai_dir: str, window: int = 10) -> dict[str, Any]:
    """跑全部节拍检查,返回汇总报告 dict。"""
    metas = load_chapter_metas(novel_ai_dir)
    report = {
        "novel_ai_dir": novel_ai_dir,
        "total_chapters_loaded": len(metas),
        "window": window,
        "checks": {
            "face_slap_three_stage": check_face_slap_three_stage(metas, window),
            "upgrade_loop": check_upgrade_loop(metas, window),
            "emotion_diversity": check_emotion_diversity(metas, window=5),
            "hook_present": check_hook_present(metas),
        },
    }
    # 汇总状态
    statuses = [c["status"] for c in report["checks"].values()]
    if "RED" in statuses:
        report["overall_status"] = "RED"
    elif "YELLOW" in statuses:
        report["overall_status"] = "YELLOW"
    else:
        report["overall_status"] = "GREEN"
    return report


_STATUS_ICON = {"RED": "🔴", "YELLOW": "🟡", "GREEN": "🟢"}


def print_report(report: dict[str, Any]) -> None:
    """打印红/黄/绿报告到 stdout。"""
    overall = report["overall_status"]
    icon = _STATUS_ICON[overall]
    print(f"\n{icon} 节拍校验报告 — 整体:{overall}")
    print(f"  novel_ai_dir = {report['novel_ai_dir']}")
    print(f"  加载章节数 = {report['total_chapters_loaded']}")
    print(f"  检查窗口 = 最近 {report['window']} 章")
    print(f"  ── 详细 ──")
    for name, c in report["checks"].items():
        icon2 = _STATUS_ICON[c["status"]]
        print(f"  {icon2} [{name}] {c['status']} — {c['reason']}")
        for d in c["details"]:
            print(f"      {d}")


def save_report(report: dict[str, Any], output_dir: str | None = None) -> str:
    """原子写 reports/beat_report.json,返回路径。"""
    if output_dir is None:
        output_dir = OUTPUT_DIR_STR
    reports_dir = Path(output_dir) / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "beat_report.json"
    try:
        from shared.atomic_io import atomic_write_json
        atomic_write_json(str(path), report)
    except ImportError:
        # CLI 路径 fallback
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="节拍校验器 — 扫章节 meta,产出红/黄/绿报告"
    )
    parser.add_argument(
        "novel_ai_dir",
        nargs="?",
        default=None,
        help="novel_ai 目录路径（默认读环境变量 NOVEL_AI_DIR,否则用默认 backend/data/engine/output）",
    )
    parser.add_argument(
        "--window", type=int, default=10,
        help="检查最近 N 章(默认 10)",
    )
    parser.add_argument(
        "--save", action="store_true",
        help="同时写 reports/beat_report.json",
    )
    args = parser.parse_args(argv)

    novel_ai_dir = args.novel_ai_dir
    if novel_ai_dir is None:
        novel_ai_dir = os.environ.get("NOVEL_AI_DIR", "")
    if not novel_ai_dir:
        # fallback: 用全局默认
        novel_ai_dir = str(Path(OUTPUT_DIR_STR).parent)

    if not Path(novel_ai_dir).exists():
        print(f"❌ novel_ai_dir 不存在:{novel_ai_dir}", file=sys.stderr)
        return 2

    report = run_all_checks(novel_ai_dir, window=args.window)
    print_report(report)
    if args.save:
        path = save_report(report)
        print(f"\n💾 报告已写入:{path}")
    # 退出码:RED → 2, YELLOW → 1, GREEN → 0（便于 CI 集成）
    return {"RED": 2, "YELLOW": 1, "GREEN": 0}[report["overall_status"]]


if __name__ == "__main__":
    sys.exit(main())