"""tools/exporter.py — 章节汇编与导出

Migrated from novel_AI/tools/exporter.py. Reads from
backend/data/engine/output/chapters/ and writes to
backend/data/engine/output/exports/.
"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime

from ..config.paths import CHAPTERS_DIR_STR, OUTPUT_DIR_STR, SETTING_PATH_STR, STATE_PATH_STR


EXPORTS_DIR = os.path.join(OUTPUT_DIR_STR, "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)


def get_chapter_list(start: int = 1, end: int = 9999) -> list[tuple[int, str]]:
    """Return list of (chapter_num, filepath) for chapters in [start, end]."""
    result = []
    if not os.path.exists(CHAPTERS_DIR_STR):
        return result
    for fname in sorted(os.listdir(CHAPTERS_DIR_STR)):
        m = re.match(r'^ch_(\d{4})\.txt$', fname)
        if not m:
            continue
        ch = int(m.group(1))
        if not (start <= ch <= end):
            continue
        path = os.path.join(CHAPTERS_DIR_STR, fname)
        try:
            with open(path, encoding="utf-8") as f:
                first_line = f.readline().strip()
        except Exception:
            continue
        if first_line == "[待修订]":
            continue
        result.append((ch, path))
    return result


def load_meta(chapter_num: int) -> dict:
    meta_path = os.path.join(CHAPTERS_DIR_STR, f"ch_{chapter_num:04d}_meta.json")
    if os.path.exists(meta_path):
        try:
            with open(meta_path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def generate_chapter_title(chapter_num: int, meta: dict, setting: dict) -> str:
    role = meta.get("chapter_role", "")
    goal = meta.get("chapter_goal", "")
    if goal and len(goal) > 2:
        title_text = goal[:12].rstrip("，。！？、")
    else:
        title_text = f"第{chapter_num}章"
    return f"第{chapter_num}章 {title_text}"


def export_chapters(start: int = 1, end: int = 9999,
                    output_filename: str = None,
                    include_titles: bool = True) -> dict:
    chapters = get_chapter_list(start, end)
    if not chapters:
        print(f"❌ 未找到第{start}-{end}章的章节文件")
        return {}

    setting = {}
    if os.path.exists(SETTING_PATH_STR):
        try:
            with open(SETTING_PATH_STR, encoding="utf-8") as f:
                setting = json.load(f)
        except Exception:
            pass

    novel_title = setting.get("title_candidates", ["未命名"])[0]

    lines: list[str] = []
    total_words = 0
    chapter_stats: list[dict] = []

    for ch_num, ch_path in chapters:
        with open(ch_path, encoding="utf-8") as f:
            text = f.read().strip()
        meta = load_meta(ch_num)
        word_count = len(text)
        total_words += word_count
        if include_titles:
            title = generate_chapter_title(ch_num, meta, setting)
            lines.append(f"\n\n{title}\n\n")
        lines.append(text)
        chapter_stats.append({
            "chapter": ch_num,
            "words": word_count,
            "score": meta.get("score", 0),
            "role": meta.get("chapter_role", ""),
        })

    full_text = "\n".join(lines).strip()

    if not output_filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        ch_range = f"ch{chapters[0][0]}-{chapters[-1][0]}"
        output_filename = f"{novel_title}_{ch_range}_{ts}.txt"

    out_path = os.path.join(EXPORTS_DIR, output_filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"《{novel_title}》\n")
        f.write("平台：番茄小说\n")
        f.write(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"章节范围：第{chapters[0][0]}-{chapters[-1][0]}章\n")
        f.write(f"总字数：{total_words:,}字\n")
        f.write("=" * 40 + "\n\n")
        f.write(full_text)

    scored = [c["score"] for c in chapter_stats if c["score"]]
    avg_score = sum(scored) / len(scored) if scored else 0
    low_score = [(c["chapter"], c["score"]) for c in chapter_stats if 0 < c["score"] < 6.5]

    result = {
        "output_path": out_path,
        "chapters_exported": len(chapters),
        "total_words": total_words,
        "avg_words_per_chapter": total_words // max(len(chapters), 1),
        "avg_quality_score": round(avg_score, 2),
        "low_score_chapters": low_score,
        "chapter_range": f"{chapters[0][0]}-{chapters[-1][0]}",
    }

    print(f"\n✅ 导出完成：{out_path}")
    print(f"   章节数：{len(chapters)}  |  总字数：{total_words:,}字")
    print(f"   均章字数：{result['avg_words_per_chapter']:,}字  |  平均质量：{avg_score:.2f}")
    if low_score:
        print(f"   ⚠️  低分章节（<6.5）：{low_score[:5]}")
    return result


def print_stats() -> None:
    chapters = get_chapter_list()
    if not chapters:
        print("❌ 无已生成章节")
        return

    total_words = 0
    scores: list[float] = []
    roles: dict = {}

    for ch_num, ch_path in chapters:
        with open(ch_path, encoding="utf-8") as f:
            text = f.read()
        total_words += len(text)
        meta = load_meta(ch_num)
        if meta.get("score"):
            scores.append(meta["score"])
        role = meta.get("chapter_role", "未知")
        roles[role] = roles.get(role, 0) + 1

    state = {}
    if os.path.exists(STATE_PATH_STR):
        try:
            with open(STATE_PATH_STR, encoding="utf-8") as f:
                state = json.load(f)
        except Exception:
            pass

    total_planned = state.get("total_chapters_planned", 157)
    completion_pct = len(chapters) / total_planned * 100 if total_planned else 0

    print(f"\n{'─'*50}")
    print(f"📖 写作进度统计")
    print(f"{'─'*50}")
    print(f"  已完成章节：{len(chapters)} / {total_planned}（{completion_pct:.1f}%）")
    print(f"  累计字数：{total_words:,}字")
    print(f"  均章字数：{total_words//max(len(chapters),1):,}字")
    if scores:
        print(f"  平均质量：{sum(scores)/len(scores):.2f}（n={len(scores)}）")
        print(f"  优秀章节（≥7.5）：{sum(1 for s in scores if s >= 7.5)}章")
        print(f"  待改进（<6.5）：{sum(1 for s in scores if s < 6.5)}章")
    print(f"  章节定位分布：{dict(sorted(roles.items(), key=lambda x: -x[1]))}")
    print(f"{'─'*50}\n")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args or args[0] == "stats":
        print_stats()
    elif args[0] == "full":
        export_chapters()
    elif args[0] == "arc" and len(args) > 1:
        arc_num = int(args[1])
        export_chapters((arc_num - 1) * 35 + 1, arc_num * 35, f"arc_{arc_num}_export.txt")
    elif args[0] == "range" and len(args) > 2:
        export_chapters(int(args[1]), int(args[2]))
    else:
        print(__doc__)