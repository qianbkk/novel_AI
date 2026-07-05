"""tools/human_review.py — 人工审核交互界面

Migrated from novel_AI/tools/human_review.py. Three handlers:
  - confirm_setting (节点①)
  - confirm_arc     (节点②)
  - fix_chapter     (节点③)

Reads from backend/data/engine/output/.
"""
from __future__ import annotations
import json
import os

from ..config.paths import OUTPUT_DIR_STR, STATE_PATH_STR, SETTING_PATH_STR
# 迭代 #59: load_state 损坏时 raise（不再 silent fallback 到 {}），
# save_state 改用 atomic_write_json（防止 orchestrator_state.json 半写损坏）。
from ..utils import atomic_write_json


BASE_DIR = OUTPUT_DIR_STR
CHAPTERS_DIR = os.path.join(OUTPUT_DIR_STR, "chapters")
STATE_PATH = STATE_PATH_STR
SETTING_PATH = SETTING_PATH_STR


def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            # 迭代 #59: 之前 silent pass — state 文件损坏时返回 {} →
            # 人工审核看到「空 state」却不知道文件坏了 → 用户继续审核 = 假审核
            # 现在 backup 损坏文件到 .corrupted.{ts}（iter #36/#53 同型）然后 raise
            try:
                import time as _t
                corrupted = STATE_PATH + f".corrupted.{int(_t.time())}"
                os.replace(STATE_PATH, corrupted)
                print(f"⚠️  human_review: state 损坏，已备份 {corrupted}: {e}")
            except Exception:
                pass
            raise
    return {}


def save_state(state: dict) -> None:
    # 迭代 #59: 改用 atomic_write_json
    atomic_write_json(STATE_PATH, state)


def clear_screen() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def pause(prompt: str = "\n按 Enter 继续...") -> None:
    input(prompt)


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
    print(f"\n  主角：{mc.get('name','')}, {mc.get('age','')}岁")
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
    print("  [n] 不通过，需要重新生成")
    print("  [v] 查看完整设定包JSON")
    print("  [e] 手动编辑后继续")
    while True:
        choice = input("\n  请选择：").strip().lower()
        if choice == "y":
            return True
        elif choice == "n":
            return False
        elif choice == "v":
            print(json.dumps(setting, ensure_ascii=False, indent=2)[:3000])
            pause()
        elif choice == "e":
            print(f"  设定包路径：{SETTING_PATH}")
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
    arc_id = arc.get("arc_id", 1)
    task_file = os.path.join(BASE_DIR, f"arc_{arc_id}_tasks.json")
    if os.path.exists(task_file):
        with open(task_file, encoding="utf-8") as f:
            tasks = json.load(f)
        print(f"\n  任务单预览（前5章 / 共{len(tasks)}章）：")
        for t in tasks[:5]:
            role_map = {"铺垫": "━", "发展": "→", "爽点": "★", "弧高潮": "🏆", "过渡": "…"}
            icon = role_map.get(t.get("chapter_role", ""), "·")
            print(f"    {icon} Ch{t['chapter_number']:3d} [{t['chapter_role']:3s}] {t['chapter_goal'][:45]}")
        shuang_chapters = [t for t in tasks if t.get("chapter_role") in ("爽点", "弧高潮")]
        if shuang_chapters:
            print(f"\n  爽点分布（{len(shuang_chapters)}个）：")
            for t in shuang_chapters:
                print(f"    Ch{t['chapter_number']:3d}: {t.get('shuang_description','')[:50]}")
    print("\n" + "─" * 60)
    print("  [y] 确认，开始写本弧")
    print("  [s] 跳过本弧")
    print("  [v] 查看完整任务单JSON")
    while True:
        choice = input("\n  请选择：").strip().lower()
        if choice == "y":
            return True
        elif choice == "s":
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
    print(f"\n  状态：重写3次后质量仍为 {score:.1f}/10")
    print(f"  最弱点：{weak}")
    print(f"  反馈：{fb[:200]}")
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
    print("  [a] 接受当前版本")
    print("  [r] 强制重新生成一次（P0级完整重写）")
    print("  [m] 手动修改")
    print("  [d] 删除本章，从队列移除")
    while True:
        choice = input("\n  请选择：").strip().lower()
        if choice == "a":
            meta_path = os.path.join(CHAPTERS_DIR, f"ch_{ch_num:04d}_meta.json")
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                meta["status"] = "human_accepted"
                meta["human_note"] = "人工确认接受"
                # 迭代 #59: meta.json 也改用 atomic_write_json
                atomic_write_json(meta_path, meta)
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


def run_review() -> None:
    state = load_state()
    pending = state.get("human_pending", [])
    if not pending:
        print("\n✅ 当前无待处理的人工任务")
        return
    print(f"\n🚨 发现 {len(pending)} 个待处理任务\n")
    resolved: list = []
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
    state["human_pending"] = [t for t in pending if t["task_id"] not in resolved]
    save_state(state)
    print(f"✅ 处理完成：{len(resolved)}/{len(pending)} 个任务")
    if state["human_pending"]:
        print(f"  剩余 {len(state['human_pending'])} 个任务待处理")
    else:
        print(f"  所有任务已处理，可以继续运行。")


if __name__ == "__main__":
    run_review()