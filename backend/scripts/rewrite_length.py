"""
把 50 章字数归一到 1800-2700 区间（用户要求「接近 2000-2500」）。

策略：单轮 LLM 改写。给 LLM 原文 + 目标字数 + 改写要求：
  - < 1800 字 → 扩写（加细节 / 对话 / 场景描写，不加新角色名以免破坏 RAG 边）
  - > 2700 字 → 精简（删冗余描写 / 重复对白 / 过度铺陈的旁白）
  - 1800-2700 → 跳过
目标命中点：2200 字（中位数）。

约束：
  - 改写后【首行不能是占位/标题】—— 跟 _derive_title 兼容
  - 不要新增人物名（已有的 5 个角色可以保留）
  - 保持章节核心情节不变（结尾接下章的「场景/人物/物品/悬念」锁不变）
  - 不要写「第N章 标题」开头
  - 不要写 markdown 标题

每章一个 LLM 调用。改写后写回 engine + novel_AI 两个目录 + 更新 meta.json.word_count +
更新 DB 的 content/summary/word_count(用 len(content) 算)。

并发：默认 4 并发。失败重试 1 次。

使用：python -m scripts.rewrite_length [--pid XXX] [--target 2200] [--workers 4] [--only-ids 5,9,27]
"""
from __future__ import annotations
import argparse
import asyncio
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# 把 backend 加进 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.models import Chapter, NovelAIBinding
from engine.llm_router import LLMRouter, get_active_router
from app.logging_setup import get_logger

log = get_logger("novel_ai.rewrite_length")

ENGINE_CH_DIR = Path("data/engine/output/chapters")
NOVELAI_CH_DIR = Path("../novel_AI/output/chapters")

MIN_TARGET = 1800
MAX_TARGET = 2700
DEFAULT_TARGET = 2200
TOLERANCE = 200  # 2000-2400 都算"接近 2000-2500"


# ─────────────────────────────────────────────
# LLM 改写 prompt
# ─────────────────────────────────────────────
def build_prompt(content: str, current: int, target: int) -> tuple[str, str]:
    if current < target:
        action = "扩写"
        rules = """要求：
  1. 在原文骨架上**只加**内容：场景细节、人物动作/表情/心理、对话、感官描写、节奏铺陈
  2. **不要**加新人物、新地点、新主线情节
  3. 保持人物语言风格、力量体系、伏笔不变
  4. 结尾处的内容必须能接上下一章（不要硬断）"""
    else:
        action = "精简"
        # 计算要删多少字
        ratio = target / current
        if ratio < 0.5:
            rules = f"""要求（**严格精简**，必须砍掉至少 {int((1-ratio)*100)}% 文字）：
  1. 大刀阔斧删：每个场景只保留核心动作 + 1 句对话 + 1 句心理
  2. 合并重复描述：相同意象/动作在多次出现时只留第一次的完整版
  3. 删所有修饰性形容词/成语堆砌，保留动词和名词
  4. 删所有「环境+氛围」渲染句，除非对剧情推进必需
  5. 把"想、看、听、感觉"等弱动作改成"做、说、抓、握"等强动作
  6. **不要**删关键情节转折、人物首次出场、关键对话、力量体系要点
  7. 结尾处的内容必须能接上下一章（不要硬断）"""
        elif ratio < 0.7:
            rules = """要求（中等精简）：
  1. 删冗余的旁白、过度铺陈的环境描写、重复对白、修饰性形容词
  2. 合并相邻的同类描写
  3. **不要**删关键情节、人物动作、核心对话、关键信息
  4. 结尾处的内容必须能接上下一章"""
        else:
            rules = """要求（小幅精简）：
  1. 删冗余的旁白、过度铺陈的环境描写、重复对白
  2. **不要**删关键情节、人物动作、核心对话
  3. 结尾处的内容必须能接上下一章"""

    system = f"""你是中国网络小说编辑。任务：把一章玄幻小说正文从 {current} 字{action}到约 {target} 字（允许 1900-2400）。

{rules}

**严格输出要求**：
- 直接输出改写后的正文，不要任何解释/标题/前言
- 首行必须是真正的小说正文，不能是「第N章 标题」/「【卷名】」/'# 标题'/「---」分隔线
- 保持原文段落分隔（用空行分自然段）"""

    user = f"【原文（{current} 字）】\n{content}\n\n【改写后正文】"
    return system, user


# ─────────────────────────────────────────────
# 单章改写 + 自检
# ─────────────────────────────────────────────
def rewrite_chapter(router, n: int, content: str, target: int) -> tuple[str, int]:
    """调 LLM 改写。返回 (new_content, new_word_count)。

    Strategy:
      - 旧：用单次 call()，LLM 写到哪算哪（事后校验）
      - 新：用 call_with_length_budget()，写入路径 truncate+续写
        - > 3500 字：先抽骨架再重生（2-step），用 call() 单次
        - 1800-2700：不需要改
        - < 1800 / > 2700：用 length-budget call，确保 LLM 写到 target 附近
    """
    cur = len(content)

    if cur > 3500:
        # 2-step: 先抽骨架 (500-800 字)，再基于骨架重生
        return rewrite_2step(router, content, target)

    # 单步 length-budget call（写入路径长度控制）
    system, user = build_prompt(content, cur, target)
    text, cost = router.call_with_length_budget(
        agent_name="writer",
        system_prompt=system,
        user_prompt=user,
        target_chars=target,
        tolerance=200,
        temperature=0.4,
    )
    text = text.strip()
    text = re.sub(r"^(改写后[：:].*?\n+|以下是.*?正文[：:].*?\n+|正文[：:]\s*\n+)", "", text)
    return text, len(text)


def rewrite_2step(router, content: str, target: int) -> tuple[str, int]:
    """2-step rewrite: extract skeleton → regenerate at target length.
    适用于 > 3500 字的大章节（单步 prompt 砍不动 LLM 会「保底多写」）。
    """
    # Step 1: extract skeleton
    sys1 = """你是网络小说编辑。从原文中提取本章的"剧情骨架"：
- 主要事件（按顺序，3-7 个）
- 关键人物对话（保留原文原话）
- 关键心理 / 转折 / 悬念点
用列表 + 简洁短句输出，**不要**任何描写、修饰、铺陈。目标：500-800 字。
直接输出骨架，不要前言。"""
    skeleton, _ = router.call("writer", sys1, content, max_tokens=2000, temperature=0.3)
    skeleton = skeleton.strip()

    # Step 2: regenerate from skeleton
    sys2 = f"""你是网络小说作家。基于给定的"剧情骨架"，用紧凑的笔法重新写成完整章节。

要求：
- 目标 {target} 字（允许 ±200）
- 把骨架里的事件串成连贯的场景（场景顺序与骨架一致）
- 关键对话必须保留原文原话（可以加「XX 道」之类的标签）
- 不要新增支线情节 / 新人物
- 删所有渲染性旁白，保留动词和对话
- 首行必须是真正的小说正文，不能是「第N章」/「【卷名】」/'# 标题'/「---」分隔线
- 直接输出改写后正文，不要前言。"""
    text, _ = router.call("writer", sys2, skeleton,
                          max_tokens=int(target * 1.4),
                          temperature=0.5)
    text = text.strip()
    text = re.sub(r"^```\w*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text, len(text)
    # Strip 常见 LLM 残留
    text = text.strip()
    # 去掉开头 "以下是改写后正文：" / "改写后：" 等
    text = re.sub(r"^(改写后[：:].*?\n+|以下是.*?正文[：:].*?\n+|正文[：:]\s*\n+)", "", text)
    # 去掉代码块包裹
    text = re.sub(r"^```\w*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    return text, len(text)


def looks_like_junk(text: str) -> bool:
    """首 200 字内不能出现占位/标题/markdown 标题/「第N章」「---」开头"""
    head = "\n".join(text.splitlines()[:5])
    bad_patterns = [
        r"^第\d+[章卷]\s",          # "第N章 标题"
        r"^#{1,6}\s",                # markdown heading
        r"^---",                     # 分隔线
        r"【修改后正文】",            # 占位
    ]
    for p in bad_patterns:
        if re.search(p, head.strip()):
            return True
    return False


# ─────────────────────────────────────────────
# 持久化
# ─────────────────────────────────────────────
def persist_chapter(n: int, content: str, db):
    """写回 engine + novel_AI 两个目录 + 更新 meta.json.word_count + 更新 DB 行"""
    # 写 engine 目录
    f_engine = ENGINE_CH_DIR / f"ch_{n:04d}.txt"
    f_meta = ENGINE_CH_DIR / f"ch_{n:04d}_meta.json"
    f_engine.write_text(content, encoding="utf-8")
    if f_meta.exists():
        meta = json.loads(f_meta.read_text(encoding="utf-8"))
        meta["word_count"] = len(content)
        f_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写 novel_AI 目录
    f_novelai = NOVELAI_CH_DIR / f"ch_{n:04d}.txt"
    f_novelai.write_text(content, encoding="utf-8")

    # 更新 DB
    ch = db.query(Chapter).filter_by(project_id=PID, chapter_no=n).first()
    if ch:
        ch.content = content
        # 重新生成 summary
        from app.bridge.chapter_import import _build_summary
        if f_meta.exists():
            meta = json.loads(f_meta.read_text(encoding="utf-8"))
            ch.summary = _build_summary(meta, content)
        db.commit()


# ─────────────────────────────────────────────
# 决定哪些章节需要改
# ─────────────────────────────────────────────
def plan_chapters(chs: list, target: int) -> list[tuple[int, str, int, int]]:
    """返回 [(n, content, current, target), ...]"""
    plan = []
    for c in chs:
        cur = len(c.content or "")
        if cur < MIN_TARGET:
            plan.append((c.chapter_no, c.content, cur, target))
        elif cur > MAX_TARGET:
            plan.append((c.chapter_no, c.content, cur, target))
    return plan


# ─────────────────────────────────────────────
# 并发执行
# ─────────────────────────────────────────────
def do_one(router, n, content, target, db_holder):
    """单章改写。优先用 length-budget call（写入路径长度控制）。
    超 3500 字时走 2-step（骨架 + 重生）。
    """
    cur = len(content)
    try:
        if cur > 3500:
            new_text, new_len = rewrite_2step(router, content, target)
        else:
            system, user = build_prompt(content, cur, target)
            new_text, new_len = rewrite_chapter(router, n, content, target)

        if looks_like_junk(new_text):
            return n, "junk", new_len, new_text[:50]

        with db_holder() as db:
            persist_chapter(n, new_text, db)

        if 1800 <= new_len <= 2700:
            return n, "ok", new_len, ""
        elif new_len < 1800:
            return n, "ok_short", new_len, ""
        else:
            return n, "ok_long", new_len, ""
    except Exception as e:
        return n, f"err: {e}", 0, ""


def retry_overflow(router, n: int, db_holder):
    """对单章做 2-step 强制砍到 < 2700。返回 (status, final_len)。"""
    db = db_holder().__enter__()
    try:
        ch = db.query(Chapter).filter_by(project_id=PID, chapter_no=n).first()
        if not ch:
            return "no_chapter", 0
        content = ch.content or ""
        cur = len(content)
        if cur <= 2700:
            return "ok", cur
        # 用更低的 target + 2-step
        target = 2200
        new_text, new_len = rewrite_2step(router, content, target)
        if looks_like_junk(new_text):
            return "junk", new_len
        persist_chapter(n, new_text, db)
        return "ok", new_len
    except Exception as e:
        return f"err: {e}", 0
    finally:
        db_holder().__exit__(None, None, None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pid", default="c12345678901234567890123456789012")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET,
                        help=f"目标字数（默认 {DEFAULT_TARGET}）")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--only-ids", default="",
                        help="只改这些 chapter_no（逗号分隔）")
    args = parser.parse_args()

    global PID
    PID = args.pid

    # 装 router
    r = LLMRouter(project_id=PID)
    r.load_routes()
    router = r.install()
    log.info("rewrite-length start: target=%d, workers=%d, only_ids=%s",
             args.target, args.workers, args.only_ids or "(all out-of-range)")

    # 读所有章节
    db = SessionLocal()
    try:
        chs = db.query(Chapter).filter_by(project_id=PID).order_by(Chapter.chapter_no).all()
    finally:
        db.close()

    # 决定改写清单
    only = set(int(x) for x in args.only_ids.split(",") if x.strip()) if args.only_ids else None
    plan = plan_chapters(chs, args.target)
    if only:
        plan = [(n, c, cur, tgt) for n, c, cur, tgt in plan if n in only]
    log.info("plan: %d chapters to rewrite", len(plan))
    if not plan:
        print("nothing to rewrite (all in [1800, 2700])")
        return 0

    # 显示计划
    print(f"\n将改写 {len(plan)} 章：")
    for n, _, cur, tgt in plan:
        print(f"  ch{n:>2}: {cur}字 -> 目标 {tgt}字")
    print()

    # 估算成本
    est_tokens = sum(int(tgt * 2.5) + 500 for _, _, _, tgt in plan)
    est_cost = est_tokens * 0.000005  # 粗估
    print(f"估算 tokens: {est_tokens}, 估算成本: ${est_cost:.2f}\n")

    # 并发执行
    from contextlib import contextmanager
    @contextmanager
    def db_holder():
        db = SessionLocal()
        try: yield db
        finally: db.close()

    start = time.time()
    results = {"ok": 0, "junk": 0, "err": 0}
    junk_samples = []
    err_samples = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {
            pool.submit(do_one, router, n, c, t, db_holder): n
            for n, c, _, t in plan
        }
        for fut in as_completed(futs):
            n, status, new_len, sample = fut.result()
            results[status.split(":")[0] if ":" in status else status] = results.get(status.split(":")[0] if ":" in status else status, 0) + 1
            if "junk" in status:
                results["junk"] = results.get("junk", 0) + 1
                junk_samples.append((n, sample))
            elif "err" in status:
                results["err"] = results.get("err", 0) + 1
                err_samples.append((n, status))
            else:
                results["ok"] = results.get("ok", 0) + 1
                print(f"  ✓ ch{n:>2}: -> {new_len}字")

    elapsed = time.time() - start
    print()
    print("=" * 60)
    print(f"完成: ok={results.get('ok',0)} junk={results.get('junk',0)} err={results.get('err',0)}  ({elapsed:.0f}s)")
    if junk_samples:
        print("  junk (首行是占位/标题/markdown):")
        for n, s in junk_samples[:5]:
            print(f"    ch{n}: {s!r}")
    if err_samples:
        print("  errors:")
        for n, s in err_samples[:5]:
            print(f"    ch{n}: {s}")
    print()
    print("=" * 60)
    print("后续建议：")
    print("  1. 跑 python -m scripts.audit_project 看新字数分布")
    print("  2. 跑 python -m scripts.audit_project --strict 验证 PASS")
    print("  3. 如果还有少量 out-of-range，重复此脚本（--only-ids 列出那些章）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
