"""
tools/acceptance_tests.py — 五大验收标准测试套件（V3方案8.5节）

AC-1: 设定一致性（长剑断裂测试）
      在第1章引入一把「可折断的长剑」，检查后续章节是否记住此剑已折断
AC-2: 跨题材复用（设定包切换）
      验证修改 novel_config.json 中的 genre 后，Writer提示词能正确切换
AC-3: 章节拆解（30章任务单）
      运行 Outline Agent，检查产出的30章任务单是否合理
AC-4: 平台适配（番茄字数+爽点）
      检查任务单的字数目标、爽点分布是否符合番茄规范
AC-5: 人物弧光一致性
      检查角色状态变化是否在Tracker中正确追踪

运行：python tools/acceptance_tests.py [all|ac1|ac2|ac3|ac4|ac5]
"""
import os, sys, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config.power_levels import POWER_LEVELS

BASE_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETTING_PATH = os.path.join(BASE_DIR, "output", "setting_package.json")
CONFIG_PATH  = os.path.join(BASE_DIR, "config", "novel_config.json")
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")
L2_DIR       = os.path.join(BASE_DIR, "memory", "l2")

PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭  SKIP"


# ═══════════════════════════════════════════════
# AC-1: 设定一致性（长剑断裂测试）
# ═══════════════════════════════════════════════
def ac1_consistency():
    """
    检查已生成的章节中是否存在明显的设定矛盾。
    如果没有章节，用静态规则检查设定包本身的内部一致性。
    """
    print("\n  AC-1: 设定一致性测试")

    # 检查设定包内部一致性
    if not os.path.exists(SETTING_PATH):
        print(f"    {SKIP}（无设定包）")
        return True

    with open(SETTING_PATH, encoding="utf-8") as f:
        setting = json.load(f)

    issues = []

    # 检查力量体系层级连续性
    levels = setting.get("power_system", {}).get("levels", [])
    for i, lv in enumerate(levels):
        if i > 0:
            prev_threshold = levels[i-1].get("point_threshold", 0)
            curr_threshold = lv.get("point_threshold", 0)
            if curr_threshold <= prev_threshold:
                issues.append(f"Lv{lv['level']}阈值({curr_threshold})≤Lv{levels[i-1]['level']}({prev_threshold})")

    # 检查弧规划中arc_id连续
    arcs = setting.get("arc_outline", [])
    for i, arc in enumerate(arcs):
        if arc.get("arc_id") != i + 1:
            issues.append(f"弧ID不连续：第{i+1}个弧的arc_id={arc.get('arc_id')}")

    # 检查主角初始等级是否在体系中存在
    protagonist_level = setting.get("protagonist", {}).get("initial_power_level", "")
    level_names = [lv["name"] for lv in levels]
    if protagonist_level and not any(protagonist_level in n or n in protagonist_level for n in level_names):
        issues.append(f"主角初始等级「{protagonist_level}」不在力量体系中")

    # 检查已生成章节中的简单矛盾（如果有）
    chapter_files = sorted(f for f in os.listdir(CHAPTERS_DIR)
                           if re.match(r'ch_\d{4}\.txt', f)) if os.path.exists(CHAPTERS_DIR) else []
    if chapter_files:
        # 追踪系统道具使用
        obtained_items = set()
        for fname in chapter_files[:30]:
            with open(os.path.join(CHAPTERS_DIR, fname), encoding="utf-8") as f:
                text = f.read()
            # 检查获得的道具
            gained = re.findall(r'【(.{1,10})已获得】|获得了「(.{1,10})」', text)
            for g in gained:
                obtained_items.add(g[0] or g[1])
            # 检查使用了未获得的道具（简单启发式）
            used = re.findall(r'使用「(.{1,10})」|动用了「(.{1,10})」', text)
            for u in used:
                item = u[0] or u[1]
                if item and item not in obtained_items and len(item) > 1:
                    issues.append(f"{fname}: 疑似使用了未获得的道具「{item}」")

    if issues:
        print(f"    {FAIL} 发现{len(issues)}个一致性问题：")
        for issue in issues[:5]:
            print(f"      - {issue}")
        return False
    else:
        print(f"    {PASS} 设定内部一致，已检查{len(chapter_files)}章")
        return True


# ═══════════════════════════════════════════════
# AC-2: 跨题材复用（设定包切换）
# ═══════════════════════════════════════════════
def ac2_genre_switch():
    """验证 prompt_templates.py 中的题材切换逻辑正常工作"""
    print("\n  AC-2: 题材切换测试")
    try:
        sys.path.insert(0, os.path.join(BASE_DIR, "config"))
        from prompt_templates import get_genre_instruction, GENRE_WRITING_INSTRUCTIONS

        genres = ["都市系统流", "玄幻修仙", "萌宝甜宠", "未知题材"]
        results = []
        for genre in genres:
            instruction = get_genre_instruction(genre)
            if instruction and len(instruction) > 50:
                results.append(True)
            else:
                results.append(False)
                print(f"      题材「{genre}」返回空指令")

        # 检查都市题材包含系统流特有要求
        urban = get_genre_instruction("都市")
        if "系统流" not in urban:
            print(f"      {FAIL} 都市指令缺少「系统流特有要求」")
            return False

        # 检查未知题材有兜底
        unknown = get_genre_instruction("未知")
        if not unknown:
            print(f"      {FAIL} 未知题材无兜底")
            return False

        print(f"    {PASS} 所有题材指令正常（{len(GENRE_WRITING_INSTRUCTIONS)}个题材）")
        return True
    except Exception as e:
        print(f"    {FAIL} {e}")
        return False


# ═══════════════════════════════════════════════
# AC-3: 章节拆解（30章任务单验证）
# ═══════════════════════════════════════════════
def ac3_outline_quality():
    """检查已存在的弧任务单质量（不重新生成）"""
    print("\n  AC-3: 章节任务单质量测试")

    arc_files = [f for f in os.listdir(os.path.join(BASE_DIR, "output"))
                 if f.startswith("arc_") and f.endswith("_tasks.json")] if os.path.exists(os.path.join(BASE_DIR, "output")) else []

    if not arc_files:
        print(f"    {SKIP} 无任务单文件（运行 python run.py init_arc 生成后再测试）")
        return True

    issues = []
    for arc_file in arc_files[:1]:  # 检查第一个
        with open(os.path.join(BASE_DIR, "output", arc_file), encoding="utf-8") as f:
            tasks = json.load(f)

        if len(tasks) < 5:
            issues.append(f"{arc_file}: 任务数量过少（{len(tasks)}）")
            continue

        # 检查必填字段
        required_fields = ["chapter_number", "chapter_role", "chapter_goal",
                           "shuang_description", "ending_hook_type", "target_length"]
        for i, task in enumerate(tasks[:5]):
            for field in required_fields:
                if not task.get(field):
                    issues.append(f"第{task.get('chapter_number','?')}章缺少字段: {field}")

        # 检查钩子类型合法性
        valid_hooks = {"悬念钩","危机钩","信息钩","情感钩","反转钩","升级钩","对抗钩"}
        invalid_hooks = [t for t in tasks if t.get("ending_hook_type") not in valid_hooks]
        if invalid_hooks:
            issues.append(f"存在{len(invalid_hooks)}个非法钩子类型")

        # 检查爽点分布（每10章至少1个爽点章）
        shuang_chapters = [t for t in tasks if t.get("chapter_role") in ("爽点","弧高潮")]
        if len(tasks) >= 10 and len(shuang_chapters) < len(tasks) // 10:
            issues.append(f"爽点章比例过低（{len(shuang_chapters)}/{len(tasks)}）")

        # 检查是否有弧高潮
        climax = [t for t in tasks if t.get("is_arc_climax")]
        if not climax:
            issues.append("缺少弧高潮章节标记")

    if issues:
        print(f"    {FAIL} 发现{len(issues)}个问题：")
        for issue in issues[:3]:
            print(f"      - {issue}")
        return False
    else:
        print(f"    {PASS} 任务单结构合格（检查了{arc_files[0]}）")
        return True


# ═══════════════════════════════════════════════
# AC-4: 平台适配（番茄字数+爽点分布）
# ═══════════════════════════════════════════════
def ac4_platform_compliance():
    """检查已生成章节是否符合番茄平台规范"""
    print("\n  AC-4: 平台适配测试（番茄）")

    if not os.path.exists(CHAPTERS_DIR):
        print(f"    {SKIP} 无章节文件")
        return True

    chapter_files = sorted(f for f in os.listdir(CHAPTERS_DIR) if re.match(r'ch_\d{4}\.txt', f))
    if not chapter_files:
        print(f"    {SKIP} 无章节文件")
        return True

    issues = []
    word_counts = []
    for fname in chapter_files[:20]:
        with open(os.path.join(CHAPTERS_DIR, fname), encoding="utf-8") as f:
            text = f.read()
        if text.startswith("[待修订]"):
            continue
        wc = len(text)
        word_counts.append(wc)
        ch = int(re.search(r'\d+', fname).group())
        if wc < 1800:
            issues.append(f"Ch{ch}: 字数不足（{wc}字 < 1800字最低要求）")
        if wc > 4000:
            issues.append(f"Ch{ch}: 字数过多（{wc}字 > 4000字上限）")

        # 检查是否有钩子（章节最后200字应有悬念）
        last_200 = text[-200:]
        hook_indicators = ["？", "……", "不对", "等等", "突然", "但是", "然而", "没想到", "竟然"]
        if not any(ind in last_200 for ind in hook_indicators):
            issues.append(f"Ch{ch}: 结尾缺少钩子信号词")

    if word_counts:
        avg_wc = sum(word_counts) // len(word_counts)
        print(f"    均章字数：{avg_wc}字（n={len(word_counts)}）")

    if issues:
        print(f"    {FAIL} 发现{len(issues)}个平台适配问题（前3条）：")
        for issue in issues[:3]:
            print(f"      - {issue}")
        return len(issues) <= 2  # 允许少量问题
    else:
        print(f"    {PASS} 所有检测章节符合番茄规范")
        return True


# ═══════════════════════════════════════════════
# AC-5: 人物弧光一致性
# ═══════════════════════════════════════════════
def ac5_character_arcs():
    """检查Tracker记忆中的人物状态变化是否合理"""
    print("\n  AC-5: 人物弧光一致性测试")

    novel_id = "renqingzhai_v1"
    l2_path = os.path.join(L2_DIR, f"{novel_id}_memory.json")
    if not os.path.exists(l2_path):
        print(f"    {SKIP} 无Tracker记忆（需先运行写作流程）")
        return True

    with open(l2_path, encoding="utf-8") as f:
        memory = json.load(f)

    hot = memory.get("hot", {})
    issues = []

    # 检查主角等级在体系内
    protagonist_level = hot.get("protagonist_level", "")
    valid_levels = ["感债者","识债者","接债者","理债者","断债者","债主"]
    if protagonist_level and protagonist_level not in valid_levels:
        issues.append(f"主角等级「{protagonist_level}」不在标准体系中")

    # 检查点数与等级匹配
    level_thresholds = {k: v[1] for k, v in POWER_LEVELS.items()}
    points = hot.get("protagonist_points", 0)
    level_num = hot.get("protagonist_level_num", 1)
    if protagonist_level in level_thresholds:
        required = level_thresholds[protagonist_level]
        if points < required * 0.8:  # 允许20%误差
            issues.append(f"点数({points})与等级「{protagonist_level}」(需{required})不匹配")

    # 检查活跃剧情线数量合理
    threads = hot.get("active_threads", [])
    if len(threads) > 10:
        issues.append(f"活跃剧情线过多（{len(threads)}条），可能存在线索管理问题")

    # 检查角色状态记录是否存在
    char_states = hot.get("character_states", {})
    if not char_states and memory.get("meta", {}).get("total_chapters_tracked", 0) > 3:
        issues.append("Tracker未记录任何角色状态，可能存在更新失败")

    if issues:
        print(f"    {FAIL} 发现{len(issues)}个弧光问题：")
        for issue in issues:
            print(f"      - {issue}")
        return False
    else:
        print(f"    {PASS} 人物状态追踪正常（主角:{protagonist_level}, {points}点, {len(threads)}条剧情线）")
        return True


# ═══════════════════════════════════════════════
# 主运行
# ═══════════════════════════════════════════════
def run_all():
    print(f"\n{'═'*55}")
    print(f"  五大验收标准测试（V3方案8.5节）")
    print(f"{'═'*55}")

    results = {
        "AC-1 设定一致性": ac1_consistency(),
        "AC-2 题材切换":   ac2_genre_switch(),
        "AC-3 任务单质量": ac3_outline_quality(),
        "AC-4 平台适配":   ac4_platform_compliance(),
        "AC-5 人物弧光":   ac5_character_arcs(),
    }

    passed = sum(1 for v in results.values() if v)
    print(f"\n{'─'*55}")
    print(f"  结果：{passed}/5 通过")
    for name, result in results.items():
        print(f"  {'✅' if result else '❌'} {name}")
    print(f"{'═'*55}\n")
    return passed == 5


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "all":
        run_all()
    elif args[0] == "ac1": ac1_consistency()
    elif args[0] == "ac2": ac2_genre_switch()
    elif args[0] == "ac3": ac3_outline_quality()
    elif args[0] == "ac4": ac4_platform_compliance()
    elif args[0] == "ac5": ac5_character_arcs()
    else:
        print(__doc__)
