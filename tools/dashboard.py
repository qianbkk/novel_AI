"""
tools/dashboard.py — 质量看板（文字版）
打印当前所有章节的质量概览、趋势、弱点分析

运行：python tools/dashboard.py
"""
import os, sys, json, re, math

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")
STATE_PATH   = os.path.join(BASE_DIR, "output", "orchestrator_state.json")
L2_DIR       = os.path.join(BASE_DIR, "memory", "l2")


def load_all_meta() -> list[dict]:
    metas = []
    for fname in sorted(os.listdir(CHAPTERS_DIR)):
        m = re.match(r'^ch_(\d{4})_meta\.json$', fname)
        if not m:
            continue
        with open(os.path.join(CHAPTERS_DIR, fname), encoding="utf-8") as f:
            meta = json.load(f)
        metas.append(meta)
    return metas


def score_bar(score: float, width: int = 20) -> str:
    filled = int(score / 10 * width)
    color_char = "█" if score >= 7.5 else ("▓" if score >= 6.5 else "░")
    return color_char * filled + "·" * (width - filled)


def print_dashboard():
    metas = load_all_meta()
    if not metas:
        print("❌ 暂无章节数据，请先运行写作流程")
        return

    scores     = [m.get("score", 0) for m in metas if m.get("score", 0) > 0]
    rewrites   = [m.get("rewrite_count", 0) for m in metas]
    word_counts = []
    for m in metas:
        ch_path = os.path.join(CHAPTERS_DIR, f"ch_{m['chapter_number']:04d}.txt")
        if os.path.exists(ch_path):
            with open(ch_path, encoding="utf-8") as f:
                word_counts.append(len(f.read()))

    avg_score  = sum(scores) / len(scores) if scores else 0
    avg_words  = sum(word_counts) // max(len(word_counts), 1)
    pass_rate  = sum(1 for s in scores if s >= 7.0) / max(len(scores), 1) * 100

    state = {}
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            state = json.load(f)

    # ─── 标题栏 ───
    print(f"\n{'═'*65}")
    print(f"  📊 AI网文创作系统 · 质量看板  [{len(metas)}章]")
    print(f"{'═'*65}")

    # ─── 总览 ───
    print(f"\n  总览")
    print(f"  {'─'*30}")
    print(f"  已生成章节   : {len(metas)}")
    print(f"  平均质量分   : {avg_score:.2f} / 10.00")
    print(f"  通过率(≥7.0) : {pass_rate:.1f}%")
    print(f"  均章字数     : {avg_words:,} 字")
    print(f"  累计字数     : {sum(word_counts):,} 字")
    print(f"  累计成本     : ${state.get('budget_used_usd', 0):.4f}")
    avg_rewrites = sum(rewrites) / max(len(rewrites), 1)
    print(f"  平均重写次数 : {avg_rewrites:.2f}")

    # ─── 分值热力图 ───
    print(f"\n  章节质量热力图（每格=1章）")
    print(f"  ─────────────────────────────────────")
    print(f"  ≥8.0=█  7-8=▓  6.5-7=▒  <6.5=░  待修=?")
    print(f"  ", end="")
    for i, meta in enumerate(metas):
        if i > 0 and i % 20 == 0:
            print(f"\n  ", end="")
        s = meta.get("score", 0)
        if meta.get("status") == "human_required":
            print("?", end="")
        elif s >= 8.0:
            print("█", end="")
        elif s >= 7.0:
            print("▓", end="")
        elif s >= 6.5:
            print("▒", end="")
        else:
            print("░", end="")
    print()

    # ─── 趋势（最近20章） ───
    recent = metas[-20:]
    if len(recent) > 3:
        print(f"\n  近{len(recent)}章趋势")
        print(f"  ─────────────────────────────────────")
        for meta in recent[-10:]:
            s = meta.get("score", 0)
            bar = score_bar(s, 15)
            role = meta.get("chapter_role", "")[:4]
            rw = meta.get("rewrite_count", 0)
            rw_str = f"(改{rw})" if rw else "     "
            print(f"  Ch{meta['chapter_number']:4d} [{role}] {bar} {s:.1f} {rw_str}")

    # ─── 低分章节 ───
    low_score = [(m["chapter_number"], m.get("score", 0)) for m in metas if 0 < m.get("score", 0) < 6.5]
    if low_score:
        print(f"\n  ⚠️  低分章节（<6.5）：{len(low_score)}章")
        for ch, s in sorted(low_score, key=lambda x: x[1])[:5]:
            print(f"     Ch{ch:4d}: {s:.1f}分")

    # ─── 维度分析 ───
    dim_keys = ["hook_power", "shuang_density", "character_voice", "plot_logic", "writing_naturalness"]
    dim_names = {"hook_power":"钩子力度","shuang_density":"爽感密度",
                 "character_voice":"人物声音","plot_logic":"情节逻辑","writing_naturalness":"文笔自然"}
    all_dims = [m.get("dimensions", {}) for m in metas if m.get("dimensions")]
    if all_dims:
        print(f"\n  维度平均分")
        print(f"  ─────────────────────────────────────")
        for key in dim_keys:
            vals = [d[key] for d in all_dims if key in d]
            if vals:
                avg = sum(vals) / len(vals)
                bar = score_bar(avg, 15)
                print(f"  {dim_names[key]:8s} {bar} {avg:.2f}")

    # ─── 记忆状态 ───
    novel_id = state.get("novel_id", "renqingzhai_v1")
    l2_path = os.path.join(L2_DIR, f"{novel_id}_memory.json")
    if os.path.exists(l2_path):
        with open(l2_path, encoding="utf-8") as f:
            l2 = json.load(f)
        print(f"\n  角色状态")
        print(f"  ─────────────────────────────────────")
        print(f"  主角等级  : {l2.get('protagonist_level','未知')}")
        print(f"  人情点    : {l2.get('protagonist_points',0):,}")
        print(f"  活跃剧情线: {len(l2.get('active_threads',[]))}条")
        print(f"  未解伏笔  : {len(l2.get('foreshadowing_planted',[]))}个")
        print(f"  已揭伏笔  : {len(l2.get('foreshadowing_resolved',[]))}个")

    print(f"\n{'═'*65}\n")


if __name__ == "__main__":
    print_dashboard()
