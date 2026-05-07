"""
tools/exporter.py — 章节汇编与导出
功能：
  - 将所有章节合并为单一TXT（番茄投稿格式）
  - 生成带章节标题的完整稿件
  - 支持按弧导出部分章节
  - 统计字数、预估完成度

运行：
  python tools/exporter.py full          # 导出全部已生成章节
  python tools/exporter.py arc 1         # 仅导出第1弧
  python tools/exporter.py range 1 30    # 导出第1-30章
  python tools/exporter.py stats         # 仅统计字数，不导出
"""
import os, sys, json, re, time
from datetime import datetime

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")
EXPORTS_DIR  = os.path.join(BASE_DIR, "output", "exports")
SETTING_PATH = os.path.join(BASE_DIR, "output", "setting_package.json")
STATE_PATH   = os.path.join(BASE_DIR, "output", "orchestrator_state.json")

os.makedirs(EXPORTS_DIR, exist_ok=True)


def get_chapter_list(start: int = 1, end: int = 9999) -> list[tuple[int, str]]:
    """返回已存在的章节文件列表 [(chapter_num, filepath)]"""
    result = []
    for fname in sorted(os.listdir(CHAPTERS_DIR)):
        m = re.match(r'^ch_(\d{4})\.txt$', fname)
        if not m:
            continue
        ch = int(m.group(1))
        if start <= ch <= end:
            path = os.path.join(CHAPTERS_DIR, fname)
            # 跳过「待修订」章节
            with open(path, encoding="utf-8") as f:
                first_line = f.readline().strip()
            if first_line == "[待修订]":
                continue
            result.append((ch, path))
    return result


def load_meta(chapter_num: int) -> dict:
    meta_path = os.path.join(CHAPTERS_DIR, f"ch_{chapter_num:04d}_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def generate_chapter_title(chapter_num: int, meta: dict, setting: dict) -> str:
    """生成章节标题（番茄格式）"""
    role = meta.get("chapter_role", "")
    # 番茄格式：第X章 标题
    # 如果meta里有goal，用前10字作为标题
    goal = meta.get("chapter_goal", "")
    if goal and len(goal) > 2:
        title_text = goal[:12].rstrip("，。！？、")
    else:
        title_text = f"第{chapter_num}章"
    return f"第{chapter_num}章 {title_text}"


def export_chapters(
    start: int = 1,
    end: int = 9999,
    output_filename: str = None,
    include_titles: bool = True,
) -> dict:
    chapters = get_chapter_list(start, end)
    if not chapters:
        print(f"❌ 未找到第{start}-{end}章的章节文件")
        return {}

    setting = {}
    if os.path.exists(SETTING_PATH):
        with open(SETTING_PATH, encoding="utf-8") as f:
            setting = json.load(f)

    novel_title = setting.get("title_candidates", ["债线纵横"])[0]

    lines = []
    total_words = 0
    chapter_stats = []

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

    # 文件名
    if not output_filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M")
        ch_range = f"ch{chapters[0][0]}-{chapters[-1][0]}"
        output_filename = f"{novel_title}_{ch_range}_{ts}.txt"

    out_path = os.path.join(EXPORTS_DIR, output_filename)
    with open(out_path, "w", encoding="utf-8") as f:
        # 写入文件头
        f.write(f"《{novel_title}》\n")
        f.write(f"平台：番茄小说\n")
        f.write(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"章节范围：第{chapters[0][0]}-{chapters[-1][0]}章\n")
        f.write(f"总字数：{total_words:,}字\n")
        f.write("=" * 40 + "\n\n")
        f.write(full_text)

    # 统计
    avg_score = sum(c["score"] for c in chapter_stats if c["score"]) / max(
        sum(1 for c in chapter_stats if c["score"]), 1
    )
    low_score_chapters = [c for c in chapter_stats if 0 < c["score"] < 6.5]

    result = {
        "output_path": out_path,
        "chapters_exported": len(chapters),
        "total_words": total_words,
        "avg_words_per_chapter": total_words // max(len(chapters), 1),
        "avg_quality_score": round(avg_score, 2),
        "low_score_chapters": [(c["chapter"], c["score"]) for c in low_score_chapters],
        "chapter_range": f"{chapters[0][0]}-{chapters[-1][0]}",
    }

    print(f"\n✅ 导出完成：{out_path}")
    print(f"   章节数：{len(chapters)}  |  总字数：{total_words:,}字")
    print(f"   均章字数：{result['avg_words_per_chapter']:,}字  |  平均质量：{avg_score:.2f}")
    if low_score_chapters:
        print(f"   ⚠️  低分章节（<6.5）：{[(c[0], c[1]) for c in result['low_score_chapters'][:5]]}")

    return result


def print_stats():
    """仅打印统计，不导出"""
    chapters = get_chapter_list()
    if not chapters:
        print("❌ 无已生成章节")
        return

    total_words = 0
    scores = []
    roles = {}

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
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)

    total_planned = state.get("total_chapters_planned", 157)
    completion_pct = len(chapters) / total_planned * 100

    print(f"\n{'─'*50}")
    print(f"📖 写作进度统计")
    print(f"{'─'*50}")
    print(f"  已完成章节：{len(chapters)} / {total_planned}（{completion_pct:.1f}%）")
    print(f"  累计字数：{total_words:,}字（目标：3,000,000字）")
    print(f"  进度：{total_words/3_000_000*100:.1f}%")
    print(f"  均章字数：{total_words//max(len(chapters),1):,}字")
    if scores:
        print(f"  平均质量：{sum(scores)/len(scores):.2f}（n={len(scores)}）")
        print(f"  优秀章节（≥7.5）：{sum(1 for s in scores if s>=7.5)}章")
        print(f"  待改进（<6.5）：{sum(1 for s in scores if s<6.5)}章")
    print(f"  章节定位分布：{dict(sorted(roles.items(), key=lambda x:-x[1]))}")
    print(f"{'─'*50}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "stats":
        print_stats()
    elif args[0] == "full":
        export_chapters()
    elif args[0] == "arc" and len(args) > 1:
        # 简单按估算范围导出（每弧约35章）
        arc_num = int(args[1])
        arc_start = (arc_num - 1) * 35 + 1
        arc_end = arc_num * 35
        export_chapters(arc_start, arc_end, f"arc_{arc_num}_export.txt")
    elif args[0] == "range" and len(args) > 2:
        export_chapters(int(args[1]), int(args[2]))
    else:
        print(__doc__)
