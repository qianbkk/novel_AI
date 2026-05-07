"""
tools/style_manager.py — 风格样本管理
功能：
  - 添加外部参考章节（从番茄爆款文复制的段落）
  - 从已生成高分章节自动提取风格样本
  - 管理风格样本库（增删查）
  - 生成Writer可用的风格提示词前缀

运行：
  python tools/style_manager.py list          # 列出当前样本
  python tools/style_manager.py add <file>    # 添加外部样本文件
  python tools/style_manager.py extract       # 从高分章节自动提取
  python tools/style_manager.py preview       # 预览当前风格提示词
"""
import os, sys, json, glob, re

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STYLE_DIR  = os.path.join(BASE_DIR, "style_samples")
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")

os.makedirs(STYLE_DIR, exist_ok=True)

STYLE_GUIDE = """
【风格要求说明】
style_samples目录用于存放写作风格参考文本。Writer Agent会在生成每章前读取这些样本，
模仿其语感和节奏（但不抄情节）。

【如何添加外部样本】
1. 找一本同类型的高质量番茄爆款文（都市系统流）
2. 复制1-2章的内容（约2000-3000字），确保版权合规（仅用于私人AI训练参考）
3. 保存为 style_samples/ext_sample_01.txt（命名规则：ext_开头）
4. 运行 python tools/style_manager.py list 确认已加载

【内部样本】
系统会自动从得分≥7.5的章节中提取前500字作为内部样本（int_开头）。
通常在生成20章以上后，内部样本质量会超过外部样本，届时可以删除外部样本。
"""


def list_samples() -> list[dict]:
    samples = []
    for fpath in sorted(glob.glob(os.path.join(STYLE_DIR, "*.txt"))):
        fname = os.path.basename(fpath)
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        lines = [l for l in content.split("\n") if not l.startswith("#")]
        text = "\n".join(lines).strip()
        source = "外部" if fname.startswith("ext_") else \
                 "内部高分" if fname.startswith("int_") else \
                 "锚点" if fname.startswith("anchor_") else "其他"
        samples.append({
            "file": fname,
            "source": source,
            "chars": len(text),
            "preview": text[:80].replace("\n", " "),
        })
    return samples


def extract_internal_samples(min_score: float = 7.5, max_samples: int = 5):
    """从高分章节提取内部样本"""
    meta_files = sorted(glob.glob(os.path.join(CHAPTERS_DIR, "ch_*_meta.json")))
    high_score = []

    for mf in meta_files:
        with open(mf, encoding="utf-8") as f:
            meta = json.load(f)
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
        with open(ch_path, encoding="utf-8") as f:
            text = f.read()

        # 跳过待修订章节
        if text.startswith("[待修订]"):
            continue

        # 取前1500字作为样本
        sample_text = text[:1500]
        out_path = os.path.join(STYLE_DIR, f"int_ch{ch:04d}_score{score:.1f}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(f"# 内部样本 | 第{ch}章 | 得分{score:.1f}\n\n{sample_text}")
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

    # 优先锚点，其次内部，最后外部
    priority = {"锚点": 0, "内部高分": 1, "外部": 2, "其他": 3}
    samples.sort(key=lambda x: priority.get(x["source"], 3))

    for s in samples[:3]:
        fpath = os.path.join(STYLE_DIR, s["file"])
        with open(fpath, encoding="utf-8") as f:
            content = f.read()
        lines = [l for l in content.split("\n") if not l.startswith("#")]
        text = "\n".join(lines).strip()

        take = min(len(text), max_chars - total - 50)
        if take <= 0:
            break
        prefix_lines.append(f"\n---（来源：{s['source']}）---\n{text[:take]}")
        total += take

    return "\n".join(prefix_lines)


def cmd_list():
    samples = list_samples()
    if not samples:
        print("  当前无风格样本")
        print(STYLE_GUIDE)
        return
    print(f"\n  当前风格样本（{len(samples)}个）：")
    for s in samples:
        print(f"  [{s['source']:6s}] {s['file']:40s} {s['chars']:5d}字  「{s['preview'][:40]}」")


def cmd_add(filepath: str):
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


def cmd_preview():
    prefix = generate_style_prefix()
    if prefix:
        print(prefix[:1000])
    else:
        print("  暂无风格样本，请先添加或生成内部样本")


if __name__ == "__main__":
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
