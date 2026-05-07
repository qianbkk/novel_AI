"""
tools/human_review.py — 人工审核交互界面
用于处理所有需要人工干预的任务：
  - 设定包确认（节点①）
  - 弧任务单确认（节点②）
  - 问题章节处理（节点③）

运行：python tools/human_review.py
"""
import os, sys, json, time

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")
STATE_PATH   = os.path.join(BASE_DIR, "output", "orchestrator_state.json")
SETTING_PATH = os.path.join(BASE_DIR, "output", "setting_package.json")

sys.path.insert(0, BASE_DIR)


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def pause(prompt: str = "\n按 Enter 继续..."):
    input(prompt)


# ─────────────────────────────────────────────
# 任务处理器
# ─────────────────────────────────────────────
def handle_confirm_setting(task: dict) -> bool:
    """节点①：设定包审核"""
    clear_screen()
    print("═" * 60)
    print("  📋 节点① — 设定包确认")
    print("═" * 60)

    if not os.path.exists(SETTING_PATH):
        print("  ❌ 设定包文件不存在")
        return False

    with open(SETTING_PATH, encoding="utf-8") as f:
        setting = json.load(f)

    mc = setting.get("protagonist", {})
    world = setting.get("world_setting", {})
    arcs = setting.get("arc_outline", [])

    print(f"\n  候选书名：")
    for i, t in enumerate(setting.get("title_candidates", []), 1):
        print(f"    {i}. {t}")

    print(f"\n  简介：{setting.get('tagline', '')}")
    print(f"\n  主角：{mc.get('name','')}，{mc.get('age','')}岁")
    print(f"  性格：{mc.get('personality','')[:80]}")
    print(f"  觉醒：{mc.get('awakening_trigger','')[:100]}")

    print(f"\n  力量体系：{setting.get('power_system',{}).get('name','')}")
    for lv in setting.get("power_system", {}).get("levels", [])[:4]:
        print(f"    Lv{lv['level']} {lv['name']}：{lv['ability'][:50]}")

    print(f"\n  弧规划（共{len(arcs)}弧）：")
    for arc in arcs:
        print(f"    弧{arc['arc_id']} 「{arc['arc_name']}」 ~{arc['estimated_chapters']}章")
        print(f"         目标：{arc['arc_goal'][:60]}")

    print(f"\n  独特元素：")
    for elem in world.get("unique_elements", []):
        print(f"    · {elem}")

    print("\n" + "─" * 60)
    print("  操作选项：")
    print("  [y] 确认通过，开始写作")
    print("  [n] 不通过，需要重新生成（运行 python run.py planner）")
    print("  [v] 查看完整设定包JSON")
    print("  [e] 手动编辑后继续（在编辑器中修改后回来按y）")

    while True:
        choice = input("\n  请选择：").strip().lower()
        if choice == "y":
            return True
        elif choice == "n":
            print("  ⚠️  请运行：python run.py planner 重新生成设定包")
            return False
        elif choice == "v":
            print(json.dumps(setting, ensure_ascii=False, indent=2)[:3000])
            pause()
        elif choice == "e":
            print(f"  设定包路径：{SETTING_PATH}")
            print("  请在编辑器中修改后，回到这里按y确认")
            pause("  修改完成后按 Enter 继续...")


def handle_confirm_arc(task: dict) -> bool:
    """节点②：弧任务单确认"""
    clear_screen()
    payload = task.get("payload", {})
    arc = payload.get("arc", {})
    task_count = payload.get("task_count", 0)

    print("═" * 60)
    print(f"  📋 节点② — 第{arc.get('arc_id','')}弧任务单确认")
    print("═" * 60)
    print(f"\n  弧名：「{arc.get('arc_name','')}」")
    print(f"  目标：{arc.get('arc_goal','')}")
    print(f"  章节数：{task_count}章")
    print(f"  高潮：{arc.get('arc_climax_description','')[:120]}")
    print(f"  情绪曲线：{arc.get('emotion_curve','')}")

    # 显示任务单预览
    arc_id = arc.get("arc_id", 1)
    task_file = os.path.join(BASE_DIR, "output", f"arc_{arc_id}_tasks.json")
    if os.path.exists(task_file):
        with open(task_file, encoding="utf-8") as f:
            tasks = json.load(f)
        print(f"\n  任务单预览（前5章 / 共{len(tasks)}章）：")
        for t in tasks[:5]:
            role_map = {"铺垫": "━", "发展": "→", "爽点": "★", "弧高潮": "🏆", "过渡": "…"}
            icon = role_map.get(t.get("chapter_role", ""), "·")
            print(f"    {icon} Ch{t['chapter_number']:3d} [{t['chapter_role']:3s}] {t['chapter_goal'][:45]}")

        # 爽点分布
        shuang_chapters = [t for t in tasks if t.get("chapter_role") in ("爽点", "弧高潮")]
        if shuang_chapters:
            print(f"\n  爽点分布（{len(shuang_chapters)}个）：")
            for t in shuang_chapters:
                print(f"    Ch{t['chapter_number']:3d}: {t.get('shuang_description','')[:50]}")

    print("\n" + "─" * 60)
    print("  [y] 确认，开始写本弧")
    print("  [s] 跳过本弧（将弧标记为完成，不生成章节）")
    print("  [v] 查看完整任务单JSON")

    while True:
        choice = input("\n  请选择：").strip().lower()
        if choice == "y":
            return True
        elif choice == "s":
            print("  已跳过本弧")
            return False
        elif choice == "v":
            if os.path.exists(task_file):
                with open(task_file, encoding="utf-8") as f:
                    tasks = json.load(f)
                for t in tasks:
                    print(f"Ch{t['chapter_number']}: {t['chapter_goal']}")
            pause()


def handle_fix_chapter(task: dict) -> str:
    """节点③：问题章节处理"""
    clear_screen()
    payload = task.get("payload", {})
    ch_num  = payload.get("chapter_number", 0)
    score   = payload.get("last_score", 0)
    weak    = payload.get("weakest_point", "")
    fb      = payload.get("feedback", "")

    print("═" * 60)
    print(f"  🚨 节点③ — 问题章节处理（第{ch_num}章）")
    print("═" * 60)
    print(f"\n  状态：重写{3}次后质量仍为 {score:.1f}/10")
    print(f"  最弱点：{weak}")
    print(f"  反馈：{fb[:200]}")

    # 显示当前草稿的前500字
    ch_path = os.path.join(CHAPTERS_DIR, f"ch_{ch_num:04d}.txt")
    if os.path.exists(ch_path):
        with open(ch_path, encoding="utf-8") as f:
            text = f.read()
        print(f"\n  当前草稿（前400字）：")
        print(f"  ┌{'─'*50}┐")
        for line in text[:400].split("\n")[:8]:
            print(f"  │ {line[:48]}")
        print(f"  └{'─'*50}┘")

    print("\n" + "─" * 60)
    print("  处理选项：")
    print("  [a] 接受当前版本（跳过质量要求，标记为[人工接受]）")
    print("  [r] 强制重新生成一次（P0级完整重写）")
    print("  [m] 手动修改（编辑对应txt文件后按m确认）")
    print("  [d] 删除本章，从队列移除（本章跳过）")

    while True:
        choice = input("\n  请选择：").strip().lower()
        if choice == "a":
            # 更新meta标记
            meta_path = os.path.join(CHAPTERS_DIR, f"ch_{ch_num:04d}_meta.json")
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                meta["status"] = "human_accepted"
                meta["human_note"] = "人工确认接受"
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            # 移除[待修订]标记
            if os.path.exists(ch_path):
                with open(ch_path, encoding="utf-8") as f:
                    content = f.read()
                if content.startswith("[待修订]\n"):
                    content = content[len("[待修订]\n"):]
                    with open(ch_path, "w", encoding="utf-8") as f:
                        f.write(content)
            print(f"  ✅ 第{ch_num}章已标记为[人工接受]")
            return "accepted"
        elif choice == "r":
            print(f"  将触发P0级重写（第{ch_num}章）...")
            return "rewrite_p0"
        elif choice == "m":
            print(f"  文件路径：{ch_path}")
            pause("  编辑完成后按 Enter...")
            return "manual_edited"
        elif choice == "d":
            print(f"  已跳过第{ch_num}章")
            return "skipped"


# ─────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────
def run_review():
    state = load_state()
    pending = state.get("human_pending", [])

    if not pending:
        print("\n✅ 当前无待处理的人工任务")
        return

    print(f"\n🚨 发现 {len(pending)} 个待处理任务\n")
    resolved = []

    for task in pending:
        print(f"  处理：[{task['priority'].upper()}] {task['task_type']} — {task['description']}")
        task_type = task["task_type"]

        if task_type == "confirm_setting":
            ok = handle_confirm_setting(task)
            if ok:
                resolved.append(task["task_id"])
                print("  ✅ 设定包已确认")

        elif task_type == "confirm_arc":
            ok = handle_confirm_arc(task)
            if ok:
                resolved.append(task["task_id"])
                print("  ✅ 弧任务单已确认")

        elif task_type == "fix_chapter":
            result = handle_fix_chapter(task)
            if result in ("accepted", "manual_edited", "skipped"):
                resolved.append(task["task_id"])

        print()

    # 从pending队列移除已处理任务
    state["human_pending"] = [t for t in pending if t["task_id"] not in resolved]
    save_state(state)

    print(f"✅ 处理完成：{len(resolved)}/{len(pending)} 个任务")
    if state["human_pending"]:
        print(f"  剩余 {len(state['human_pending'])} 个任务待处理")
    else:
        print(f"  所有任务已处理，可以继续运行：python run.py resume")


if __name__ == "__main__":
    # 加载.env
    env_file = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()
    run_review()
