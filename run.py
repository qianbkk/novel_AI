#!/usr/bin/env python3
"""
AI网文创作系统 V3 — 统一主入口
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

【写作流程】
  python run.py planner          生成设定包
  python run.py bootstrap        黄金三章多版本生成
  python run.py run [N]          写N章（默认10章）
  python run.py resume           从中断点继续

【查看与监控】
  python run.py status           进度概览
  python run.py dashboard        质量看板
  python run.py show [N]         查看第N章
  python run.py pending          待人工任务

【人工审核】
  python run.py review           交互式审核界面

【导出】
  python run.py export           导出全部章节TXT
  python run.py stats            字数统计

【维护】
  python run.py budget           预算报告
  python run.py scan             一致性扫描
  python run.py style list       风格样本管理
  python run.py init_arc         生成弧任务单

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys, os, json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

env_file = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ[k.strip()] = v.strip()

from orchestrator_state import create_initial_state, save_state, load_state

STATE_PATH   = os.path.join(BASE_DIR, "output", "orchestrator_state.json")
SETTING_PATH = os.path.join(BASE_DIR, "output", "setting_package.json")
CONFIG_PATH  = os.path.join(BASE_DIR, "config", "novel_config.json")
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")


def _check_api():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("❌ 未配置 ANTHROPIC_API_KEY，请编辑 .env 文件")
        sys.exit(1)

def _check_setting():
    if not os.path.exists(SETTING_PATH):
        print("❌ 未找到设定包，请先运行：python run.py planner")
        sys.exit(1)

def _load_or_init_state(quiet=False):
    if os.path.exists(STATE_PATH):
        state = load_state(STATE_PATH)
        if not quiet:
            print(f"📂 已加载状态：第{state.get('current_chapter',0)}章")
        return state
    _check_setting()
    with open(SETTING_PATH, encoding="utf-8") as f:
        setting = json.load(f)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    arc_plans = setting.get("arc_outline", [])
    for arc in arc_plans:
        if "arc_climax_chapter_offset" not in arc:
            arc["arc_climax_chapter_offset"] = arc.get("estimated_chapters", 30) - 3
    state = create_initial_state(
        novel_id=config.get("novel_id","renqingzhai_v1"),
        title=setting.get("title_candidates",["未命名"])[0],
        platform=config.get("platform","fanqie"),
        genre=config.get("genre","都市系统流"),
        setting_concept=config.get("setting_concept",""),
        budget_limit_usd=config.get("budget_limit_usd",500.0),
    )
    state["arc_plans"] = arc_plans
    state["total_arcs_planned"] = len(arc_plans)
    state["total_chapters_planned"] = sum(a.get("estimated_chapters",30) for a in arc_plans)
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    save_state(state, STATE_PATH)
    if not quiet:
        print(f"✅ 初始化：{len(arc_plans)}弧 {state['total_chapters_planned']}章")
    return state

def _print_status(state):
    arc_plans = state.get("arc_plans", [])
    cur = state.get("current_arc", 0)
    arc_name = arc_plans[cur]["arc_name"] if cur < len(arc_plans) else "完结"
    q = state.get("quality_history", [])
    avg = sum(q[-20:])/len(q[-20:]) if q else 0
    print(f"\n{'─'*50}")
    print(f"  进度：Ch{state.get('current_chapter',0)} / {state.get('total_chapters_planned',0)}")
    print(f"  弧{cur+1}「{arc_name}」| 近期均质 {avg:.1f} | ${state.get('budget_used_usd',0):.3f}")
    if state.get("human_pending"):
        print(f"  ⚠️  {len(state['human_pending'])}个待处理 → python run.py review")
    print(f"{'─'*50}\n")

# ── 命令实现 ──

def cmd_planner():
    _check_api()
    from agents.planner_agent import run_planner
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    result = run_planner(config, os.path.join(BASE_DIR, "output"))
    print(f"\n✅ 设定包已生成 | 书名：{result.get('title_candidates',['?'])[0]}")

def cmd_bootstrap():
    _check_api(); _check_setting()
    from tools.bootstrap import run_bootstrap
    run_bootstrap()

def cmd_run(n=10):
    _check_api(); _check_setting()
    from orchestrator import run_orchestrator
    state = _load_or_init_state()
    if state.get("current_phase") == "done":
        print("✅ 全书已完成！运行 python run.py export")
        return
    state = run_orchestrator(state, max_chapters=n)
    _print_status(state)

def cmd_status():
    _print_status(_load_or_init_state(quiet=True))

def cmd_dashboard():
    from tools.dashboard import print_dashboard
    print_dashboard()

def cmd_show(n):
    import os
    path = os.path.join(CHAPTERS_DIR, f"ch_{n:04d}.txt")
    if not os.path.exists(path):
        print(f"❌ 第{n}章不存在")
        return
    with open(path, encoding="utf-8") as f:
        print(f.read())
    mp = path.replace(".txt","_meta.json")
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            m = json.load(f)
        print(f"\n📊 {m.get('score',0):.1f}分 | 重写{m.get('rewrite_count',0)}次")

def cmd_pending():
    state = _load_or_init_state(quiet=True)
    p = state.get("human_pending", [])
    if not p:
        print("✅ 无待处理任务")
        return
    print(f"\n🚨 {len(p)}个待处理任务：")
    for t in p:
        print(f"  {'🔴' if t.get('priority')=='must' else '🟡'} [{t['task_type']}] {t['description']}")
    print(f"\n  → python run.py review")

def cmd_review():
    from tools.human_review import run_review
    run_review()

def cmd_export(rest):
    from tools.exporter import export_chapters
    if not rest or rest[0]=="full":
        export_chapters()
    elif rest[0]=="arc" and len(rest)>1:
        n=int(rest[1]); export_chapters((n-1)*35+1, n*35, f"arc_{n}_export.txt")
    elif rest[0]=="range" and len(rest)>2:
        export_chapters(int(rest[1]), int(rest[2]))
    else:
        export_chapters()

def cmd_stats():
    from tools.exporter import print_stats
    print_stats()

def cmd_budget():
    from tools.budget_manager import print_report
    print_report()

def cmd_scan():
    _check_api()
    from tools.chapter_checker import scan_all_chapters
    state = _load_or_init_state(quiet=True)
    scan_all_chapters(state.get("novel_id","renqingzhai_v1"))

def cmd_style(rest):
    from tools.style_manager import cmd_list, cmd_add, cmd_preview, extract_internal_samples
    sub = rest[0] if rest else "list"
    if sub=="list": cmd_list()
    elif sub=="extract": extract_internal_samples()
    elif sub=="add" and len(rest)>1: cmd_add(rest[1])
    elif sub=="preview": cmd_preview()
    else: cmd_list()

def cmd_init_arc():
    _check_setting()
    from agents.outline_agent import run_outline
    from agents.tracker_agent import load_memory
    with open(SETTING_PATH, encoding="utf-8") as f:
        setting = json.load(f)
    state = _load_or_init_state(quiet=True)
    arcs = state.get("arc_plans", [])
    idx  = state.get("current_arc", 0)
    if idx >= len(arcs):
        print("❌ 所有弧已完成"); return
    arc = arcs[idx]
    mem = load_memory(state["novel_id"])
    tasks, cost = run_outline(arc, state.get("current_chapter",0)+1, setting, mem)
    out = os.path.join(BASE_DIR, "output", f"arc_{arc['arc_id']}_tasks.json")
    with open(out,"w",encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    print(f"✅ 弧{arc['arc_id']}「{arc['arc_name']}」{len(tasks)}章 → {out}")
    for t in tasks[:3]:
        print(f"  Ch{t['chapter_number']:4d} [{t['chapter_role']}] {t['chapter_goal'][:50]}")

# ── 路由 ──
if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(0)
    cmd = args[0].lower()
    rest = args[1:]

    dispatch = {
        "planner":   lambda: cmd_planner(),
        "bootstrap": lambda: cmd_bootstrap(),
        "run":       lambda: cmd_run(int(rest[0]) if rest else 10),
        "resume":    lambda: cmd_run(10),
        "init_arc":  lambda: cmd_init_arc(),
        "status":    lambda: cmd_status(),
        "dashboard": lambda: cmd_dashboard(),
        "show":      lambda: cmd_show(int(rest[0]) if rest else 1),
        "pending":   lambda: cmd_pending(),
        "review":    lambda: cmd_review(),
        "export":    lambda: cmd_export(rest),
        "stats":     lambda: cmd_stats(),
        "budget":    lambda: cmd_budget(),
        "scan":      lambda: cmd_scan(),
        "style":     lambda: cmd_style(rest),
    "calibrate": lambda: cmd_calibrate(),
    "fingerprint": lambda: cmd_fingerprint(rest),
    "ac":        lambda: cmd_acceptance(rest),
    "test":      lambda: cmd_test(),
    "memory":    lambda: cmd_memory(),
    }
    fn = dispatch.get(cmd)
    if fn:
        fn()
    else:
        print(f"未知命令：{cmd}\n{__doc__}")

# ── 补充命令 ──
def cmd_calibrate():
    _check_api()
    from tools.calibrate_checker import run_calibration
    run_calibration()

def cmd_fingerprint(rest):
    from tools.fingerprint_checker import cmd_check, cmd_scan
    if rest: cmd_check(rest[0])
    else: cmd_scan()

def cmd_acceptance(rest):
    from tools.acceptance_tests import run_all, ac1_consistency, ac2_genre_switch, ac3_outline_quality, ac4_platform_compliance, ac5_character_arcs
    sub = rest[0] if rest else "all"
    {"all":run_all,"ac1":ac1_consistency,"ac2":ac2_genre_switch,
     "ac3":ac3_outline_quality,"ac4":ac4_platform_compliance,"ac5":ac5_character_arcs}.get(sub,run_all)()

def cmd_test():
    import subprocess
    subprocess.run([sys.executable, os.path.join(BASE_DIR,"tools","system_test.py")])

def cmd_memory():
    from memory.memory_manager import check_memory_health
    state = _load_or_init_state(quiet=True)
    health = check_memory_health(state.get("novel_id","renqingzhai_v1"))
    print(f"\n  记忆健康：{'✅ 正常' if health['ok'] else '⚠️  有问题'}")
    for issue in health.get("issues",[]): print(f"    - {issue}")
    for k,v in health.get("stats",{}).items(): print(f"  {k}: {v}")
