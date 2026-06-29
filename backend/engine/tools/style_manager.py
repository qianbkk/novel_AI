"""tools/style_manager.py — 风格样本管理

Migrated from novel_AI/tools/style_manager.py. Reads/writes
backend/data/engine/output/style_samples/.
"""
from __future__ import annotations
import glob
import json
import os

from ..config.paths import STYLE_SAMPLES_DIR_STR, CHAPTERS_DIR_STR


STYLE_DIR = STYLE_SAMPLES_DIR_STR
CHAPTERS_DIR = CHAPTERS_DIR_STR
os.makedirs(STYLE_DIR, exist_ok=True)


def list_samples() -> list[dict]:
    samples = []
    for fpath in sorted(glob.glob(os.path.join(STYLE_DIR, "*.txt"))):
        fname = os.path.basename(fpath)
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        lines = [l for l in content.split("\n") if not l.startswith("#")]
        text = "\n".join(lines).strip()
        if fname.startswith("ext_"):
            source = "外部"
        elif fname.startswith("int_"):
            source = "内部高分"
        elif fname.startswith("anchor_"):
            source = "锚点"
        else:
            source = "其他"
        samples.append({
            "file": fname,
            "source": source,
            "chars": len(text),
            "preview": text[:80].replace("\n", " "),
        })
    return samples


def extract_internal_samples(min_score: float = 7.5, max_samples: int = 5) -> int:
    """从高分章节提取内部样本。"""
    meta_files = sorted(glob.glob(os.path.join(CHAPTERS_DIR, "ch_*_meta.json")))
    high_score: list[tuple[float, int]] = []
    for mf in meta_files:
        try:
            with open(mf, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        score = meta.get("score", 0)
        ch = meta.get("chapter_number", 0)
        if score >= min_score:
            high_score.append((score, ch))
    high_score.sort(reverse=True)

    extracted = 0
    for score, ch in high_score[:max_samples]:
        ch_path = os.path.join(CHAPTERS_DIR, f"ch_{ch:04d}.txt")
        if not os.path.exists(ch_path):
            continue
        try:
            with open(ch_path, encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if text.startswith("[待修订]"):
            continue
        out_path = os.path.join(STYLE_DIR, f"int_ch{ch:04d}_score{score:.1f}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# 内部样本 | 第{ch}章 | 得分{score:.1f}\n\n{text[:1500]}")
        extracted += 1
        print(f"  ✅ 提取第{ch}章（{score:.1f}分）→ {os.path.basename(out_path)}")

    print(f"\n共提取 {extracted} 个内部样本")
    return extracted


def generate_style_prefix(max_chars: int = 800) -> str:
    """生成供Writer使用的风格参考前缀"""
    samples = list_samples()
    if not samples:
        return ""
    prefix_lines = ["【风格参考（请模仿以下段落的语感和节奏，但不抄内容）】"]
    total = 0
    priority = {"锚点": 0, "内部高分": 1, "外部": 2, "其他": 3}
    samples.sort(key=lambda x: priority.get(x["source"], 3))
    for s in samples[:3]:
        fpath = os.path.join(STYLE_DIR, s["file"])
        try:
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
        except Exception:
            continue
        lines = [l for l in content.split("\n") if not l.startswith("#")]
        text = "\n".join(lines).strip()
        take = min(len(text), max_chars - total - 50)
        if take <= 0:
            break
        prefix_lines.append(f"\n---（来源：{s['source']}）---\n{text[:take]}")
        total += take
    return "\n".join(prefix_lines)


def cmd_list() -> None:
    samples = list_samples()
    if not samples:
        print("  当前无风格样本")
        return
    print(f"\n  当前风格样本（{len(samples)}个）：")
    for s in samples:
        print(f"  [{s['source']:6s}] {s['file']:40s} {s['chars']:5d}字  「{s['preview'][:40]}」")


def cmd_add(filepath: str) -> None:
    if not os.path.exists(filepath):
        print(f"❌ 文件不存在：{filepath}")
        return
    fname = os.path.basename(filepath)
    if not fname.startswith("ext_"):
        fname = "ext_" + fname
    dest = os.path.join(STYLE_DIR, fname)
    with open(filepath, encoding="utf-8") as f:
        content = f.read()
    with open(dest, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"✅ 已添加样本：{fname}（{len(content)}字）")


def cmd_preview() -> None:
    prefix = generate_style_prefix()
    if prefix:
        print(prefix[:1000])
    else:
        print("  暂无风格样本，请先添加或生成内部样本")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if not args or args[0] == "list":
        cmd_list()
    elif args[0] == "extract":
        extract_internal_samples()
    elif args[0] == "add" and len(args) > 1:
        cmd_add(args[1])
    elif args[0] == "preview":
        cmd_preview()
    else:
        print(__doc__)