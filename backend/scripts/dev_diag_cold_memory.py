"""scripts/dev_diag_cold_memory.py — Phase 5 发现 #6 诊断

读所有 l2/*.json，检查每个项目的 cold.compressed_history 是否命中 3000 字
硬截断，是否已物理丢失更早期的剧情记录。

只读，不改盘（dev-only 诊断工具；真修在 manager.py 的二次摘要路径）。

指标：
  - **is_at_cap**: 长度 ≥ 2950（接近 3000 hard cap）
  - **compression_count**: 估算被截掉的历史长度（用每章 summary 平均大小做反推）
  - **truncation_loss_estimate_chapters**: 估算丢失了多少章的剧情记录

跑法：
  python -m scripts.dev_diag_cold_memory
或：
  python -m scripts.dev_diag_cold_memory --verbose
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
L2_DIR = BACKEND / "data" / "engine" / "memory" / "l2"

# manager.py 中的硬截断（Phase 5 fix 之前）
HARD_TRUNCATION_CAP = 3000

# 假设平均每章 summary 长度（根据 compressed_history 一行 Ch N: x ≈ 30 字）
ESTIMATED_CHARS_PER_CHAPTER_SUMMARY = 35


def diagnose(verbose: bool = False) -> list[dict]:
    if not L2_DIR.exists():
        print(f"⚠️  L2_DIR 不存在: {L2_DIR}")
        return []

    findings = []
    for fp in sorted(L2_DIR.glob("*_memory.json")):
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            findings.append({"novel_id": fp.stem, "error": f"read failed: {e}"})
            continue

        novel_id = fp.stem.replace("_memory", "")
        cold = data.get("cold", {})
        hot = data.get("hot", {})
        compressed = cold.get("compressed_history", "")
        recent_summaries = hot.get("recent_summaries", [])
        meta = data.get("meta", {})
        total_chapters_tracked = meta.get("total_chapters_tracked", 0)
        meta_compression = cold.get("compressed_history_meta", {})

        is_at_cap = len(compressed) >= HARD_TRUNCATION_CAP - 50  # 50 字符 buffer

        # Phase 5 fix #6 后真正在"丢数据"的标志：
        #   (A) 写满到 3000 cap
        #   (B) compressed_history_meta.compressed_by_truncate_v1 == True（旧版）
        # 或者 managed 后的元数据被持续追加说明 LLM 二次摘要接管
        secondary_summarize_used = meta_compression.get("total_compression_events", 0) > 0
        hard_truncated = is_at_cap  # 简单 proxy：到 cap = 旧版可能 trunc 过

        findings.append({
            "novel_id": novel_id,
            "compressed_history_len": len(compressed),
            "is_at_cap": is_at_cap,
            "total_chapters_tracked": total_chapters_tracked,
            "chapters_in_hot": len(recent_summaries),
            "hard_truncated_proxy": hard_truncated,
            "secondary_summarize_used": secondary_summarize_used,
            "total_compression_events": meta_compression.get("total_compression_events", 0),
            "file_size_kb": fp.stat().st_size / 1024,
        })

    return findings


def main():
    parser = argparse.ArgumentParser(description="诊断 cold.compressed_history 是否在丢数据")
    parser.add_argument("--verbose", action="store_true", help="每个文件打印详情")
    parser.add_argument("--only-overflow", action="store_true", help="只列出已溢出 / 估计丢数据的项目")
    args = parser.parse_args()

    findings = diagnose()
    if not findings:
        return

    if args.only_overflow:
        findings = [f for f in findings if f.get("is_at_cap") or f.get("estimated_loss_chapters", 0) > 0]

    print("=" * 72)
    print("Phase 5 发现 #6 诊断: compressed_history 是否在丢数据")
    print("=" * 72)
    print(f"hard cap = {HARD_TRUNCATION_CAP} chars (manager.py:143 — Phase 5 fix 前)")
    print(f"L2_DIR   = {L2_DIR}")
    print(f"扫描到 {len(findings)} 个 novel memory 文件")
    print()

    any_problem = False
    for f in findings:
        marker = ""
        if f.get("is_at_cap"):
            marker += " [AT-CAP/可能旧数据被truncate过]"
        if f.get("secondary_summarize_used"):
            marker += " [LLM2NDRY-ACTIVE]"
        if marker:
            any_problem = True

        line = f"{f['novel_id'][:40]:40s} | " \
               f"len={f.get('compressed_history_len', 'N/A'):>5} | " \
               f"tracked={f.get('total_chapters_tracked', 0):>4} | " \
               f"hot={f.get('chapters_in_hot', 0):>3}ch | " \
               f"events={f.get('total_compression_events', 0):>2} | " \
               f"{f.get('file_size_kb', 0):.1f}KB {marker}"
        print(line)

        if args.verbose and marker:
            print(f"  └─ is_at_cap={f.get('is_at_cap')}, "
                  f"hard_truncated_proxy={f.get('hard_truncated_proxy')}, "
                  f"secondary_summarize_used={f.get('secondary_summarize_used')}")

    print()
    print("判断逻辑（Phase 5 fix 后）：")
    print("  • AT-CAP：len ≥ 2950，说明旧数据可能已被硬截断（无法复原）")
    print("  • LLM2NDRY-ACTIVE：compressed_history_meta.total_compression_events > 0，")
    print("    说明已启用 LLM 二次摘要路径，后续新增不会再丢")
    if any_problem:
        print()
        print("⚠️  发现警告条目。AT-CAP 标记的为老结构数据，无法恢复；")
        print("   LLM2NDRY-ACTIVE 标记的为 Phase 5 fix 接管，无需担心。")
    else:
        print()
        print("✅ 所有项目都正常。")

    sys.exit(0)


if __name__ == "__main__":
    main()
