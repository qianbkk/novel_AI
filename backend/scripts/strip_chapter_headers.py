"""一次性清理 3 个章节的"假标题"残留头：

- ch1: "【修改后正文】" —— 冒烟测试时留的占位
- ch42: "【玄幻·人族秘史卷】第42章 父债子偿" —— 卷 + 章 标题行
- ch50: "第50章 万族共主" —— 重复标题

策略：
  1. 改 txt 文件（去掉第一行）
  2. 同步到 novel_AI/output/chapters/（这是 import 真正读的地方）
  3. 调 _force_reimport 把 DB 同步过来

使用：python -m scripts.strip_chapter_headers
"""
import sys
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ENGINE_CH_DIR = Path("data/engine/output/chapters")
NOVELAI_CH_DIR = Path("../novel_AI/output/chapters")

# 哪些 chapter_no 需要清理第 1 行（多行也行，strip 掉所有看起来像标题/占位的开头行）
TARGETS = {1, 42, 50}


def looks_like_junk_header(line: str) -> bool:
    """判断一行是不是「冒烟测试占位 / 重复标题 / 章节大标题」之一。"""
    s = line.strip()
    if not s:
        return False
    # 「修改后正文」/「测试」/「TODO」等占位
    bad_words = ("修改后正文", "smoke", "TODO", "FIXME", "测试稿", "scaffold")
    for w in bad_words:
        if w in s:
            return True
    # 「【卷名】第N章 标题」格式
    import re
    if re.match(r"^【[^】]+】第\d+章", s):
        return True
    # 纯「第N章 标题」且短（≤ 30 字）—— 重复标题
    if re.match(r"^第\d+章\s*\S+", s) and len(s) <= 30:
        return True
    return False


def clean_file(path: Path) -> tuple[int, list[str]]:
    """去掉文件开头所有「假标题」行。返回 (stripped_count, removed_lines)。"""
    if not path.exists():
        return 0, []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    removed = []
    i = 0
    while i < len(lines) and looks_like_junk_header(lines[i]):
        removed.append(lines[i].rstrip("\n"))
        i += 1
    if i == 0:
        return 0, []
    new_text = "".join(lines[i:])
    path.write_text(new_text, encoding="utf-8")
    return len(removed), removed


def main():
    fixed_engine = 0
    fixed_novelai = 0
    print("=" * 60)
    print("Stripping junk headers from chapter txt files")
    print("=" * 60)
    for n in TARGETS:
        for label, d in [("engine", ENGINE_CH_DIR), ("novel_AI", NOVELAI_CH_DIR)]:
            f = d / f"ch_{n:04d}.txt"
            if not f.exists():
                print(f"  [{label}] {f.name}  MISSING")
                continue
            count, removed = clean_file(f)
            if count:
                print(f"  [{label}] {f.name}  stripped {count} line(s):")
                for r in removed:
                    print(f"      {r!r}")
                if label == "engine":
                    fixed_engine += count
                else:
                    fixed_novelai += count
    print()
    print("=" * 60)
    print("Reimport chapters into DB")
    print("=" * 60)
    import asyncio
    from app.database import SessionLocal
    from app.models import NovelAIBinding
    from app.bridge.chapter_import import _force_reimport

    db = SessionLocal()
    try:
        pid = "c12345678901234567890123456789012"
        binding = db.query(NovelAIBinding).filter_by(project_id=pid).first()
        if binding:
            result = asyncio.run(_force_reimport(pid, binding.novel_ai_dir, db))
            print(f"  reimported {len(result)} chapters")
            # show the 3 affected ones
            for r in result:
                if r["chapter_no"] in TARGETS:
                    print(f"  ch{r['chapter_no']:>2} [{r['mode']}]: {r['title'][:60]}")
    finally:
        db.close()

    print()
    print(f"DONE: engine={fixed_engine} stripped, novel_AI={fixed_novelai} stripped")


if __name__ == "__main__":
    main()
