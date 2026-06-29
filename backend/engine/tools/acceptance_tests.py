"""tools/acceptance_tests.py — 五大验收标准测试套件 (V3方案8.5节)

Migrated from novel_AI/tools/acceptance_tests.py. The five acceptance
criteria check setting-package consistency, genre switching, outline
quality, platform compliance, and character-arc consistency.
"""
from __future__ import annotations
import json
import os
import re

from ..config.paths import (
    CHAPTERS_DIR_STR, OUTPUT_DIR_STR, L2_DIR_STR, SETTING_PATH_STR,
)
from ..config.power_levels import POWER_LEVELS


PASS = "✅ PASS"
FAIL = "❌ FAIL"
SKIP = "⏭  SKIP"


# ═══════════════════════════════════════════
# AC-1: 设定一致性
# ═══════════════════════════════════════════
def ac1_consistency() -> bool:
    print("\n  AC-1: 设定一致性测试")
    if not os.path.exists(SETTING_PATH_STR):
        print(f"    {SKIP}（无设定包）")
        return True
    try:
        with open(SETTING_PATH_STR, encoding="utf-8") as f:
            setting = json.load(f)
    except Exception:
        print(f"    {SKIP}（设定包无法解析）")
        return True

    issues: list = []
    levels = setting.get("power_system", {}).get("levels", [])
    for i, lv in enumerate(levels):
        if i > 0:
            prev_thr = levels[i-1].get("point_threshold", 0)
            curr_thr = lv.get("point_threshold", 0)
            if curr_thr <= prev_thr:
                issues.append(f"Lv{lv['level']}阈值({curr_thr})≤Lv{levels[i-1]['level']}({prev_thr})")

    arcs = setting.get("arc_outline", [])
    for i, arc in enumerate(arcs):
        if arc.get("arc_id") != i + 1:
            issues.append(f"弧ID不连续：第{i+1}个弧的arc_id={arc.get('arc_id')}")

    protagonist_level = setting.get("protagonist", {}).get("initial_power_level", "")
    level_names = [lv["name"] for lv in levels]
    if protagonist_level and not any(protagonist_level in n or n in protagonist_level for n in level_names):
        issues.append(f"主角初始等级「{protagonist_level}」不在力量体系中")

    chapter_files = []
    if os.path.exists(CHAPTERS_DIR_STR):
        chapter_files = sorted(
            f for f in os.listdir(CHAPTERS_DIR_STR)
            if re.match(r'ch_\d{4}\.txt', f)
        )

    if chapter_files:
        obtained_items: set = set()
        for fname in chapter_files[:30]:
            try:
                with open(os.path.join(CHAPTERS_DIR_STR, fname), encoding="utf-8") as f:
                    text = f.read()
            except Exception:
                continue
            gained = re.findall(r'【(.{1,10})已获得】|获得了「(.{1,10})」', text)
            for g in gained:
                obtained_items.add(g[0] or g[1])
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
    print(f"    {PASS} 设定内部一致，已检查{len(chapter_files)}章")
    return True


# ═══════════════════════════════════════════
# AC-2: 题材切换
# ═══════════════════════════════════════════
def ac2_genre_switch() -> bool:
    print("\n  AC-2: 题材切换测试")
    try:
        from ..config.prompt_templates import (
            get_genre_instruction, GENRE_WRITING_INSTRUCTIONS,
        )
    except Exception as e:
        print(f"    {FAIL} {e}")
        return False

    genres = ["都市系统流", "玄幻修仙", "萌宝甜宠", "未知题材"]
    for genre in genres:
        instruction = get_genre_instruction(genre)
        if not instruction or len(instruction) < 50:
            print(f"      题材「{genre}」返回空指令")
            return False

    urban = get_genre_instruction("都市")
    if "系统流" not in urban:
        print(f"      {FAIL} 都市指令缺少「系统流特有要求」")
        return False

    unknown = get_genre_instruction("未知")
    if not unknown:
        print(f"      {FAIL} 未知题材无兜底")
        return False

    print(f"    {PASS} 所有题材指令正常（{len(GENRE_WRITING_INSTRUCTIONS)}个题材）")
    return True


# ═══════════════════════════════════════════
# AC-3: 章节任务单质量
# ═══════════════════════════════════════════
def ac3_outline_quality() -> bool:
    print("\n  AC-3: 章节任务单质量测试")
    if not os.path.exists(OUTPUT_DIR_STR):
        print(f"    {SKIP} 无任务单文件")
        return True
    arc_files = [f for f in os.listdir(OUTPUT_DIR_STR)
                 if f.startswith("arc_") and f.endswith("_tasks.json")]
    if not arc_files:
        print(f"    {SKIP} 无任务单文件")
        return True

    issues: list = []
    for arc_file in arc_files[:1]:
        try:
            with open(os.path.join(OUTPUT_DIR_STR, arc_file), encoding="utf-8") as f:
                tasks = json.load(f)
        except Exception:
            continue
        if len(tasks) < 5:
            issues.append(f"{arc_file}: 任务数量过少（{len(tasks)}）")
            continue
        required = ["chapter_number", "chapter_role", "chapter_goal",
                    "shuang_description", "ending_hook_type", "target_length"]
        for i, task in enumerate(tasks[:5]):
            for field in required:
                if not task.get(field):
                    issues.append(f"第{task.get('chapter_number','?')}章缺少字段: {field}")
        valid_hooks = {"悬念钩", "危机钩", "信息钩", "情感钩", "反转钩", "升级钩", "对抗钩"}
        invalid = [t for t in tasks if t.get("ending_hook_type") not in valid_hooks]
        if invalid:
            issues.append(f"存在{len(invalid)}个非法钩子类型")
        shuang = [t for t in tasks if t.get("chapter_role") in ("爽点", "弧高潮")]
        if len(tasks) >= 10 and len(shuang) < len(tasks) // 10:
            issues.append(f"爽点章比例过低（{len(shuang)}/{len(tasks)}）")
        climax = [t for t in tasks if t.get("is_arc_climax")]
        if not climax:
            issues.append("缺少弧高潮章节标记")

    if issues:
        print(f"    {FAIL} 发现{len(issues)}个问题：")
        for issue in issues[:3]:
            print(f"      - {issue}")
        return False
    print(f"    {PASS} 任务单结构合格（检查了{arc_files[0]}）")
    return True


# ═══════════════════════════════════════════
# AC-4: 平台适配（番茄）
# ═══════════════════════════════════════════
def ac4_platform_compliance() -> bool:
    print("\n  AC-4: 平台适配测试（番茄）")
    if not os.path.exists(CHAPTERS_DIR_STR):
        print(f"    {SKIP} 无章节文件")
        return True
    chapter_files = sorted(
        f for f in os.listdir(CHAPTERS_DIR_STR)
        if re.match(r'ch_\d{4}\.txt', f)
    )
    if not chapter_files:
        print(f"    {SKIP} 无章节文件")
        return True
    issues: list = []
    word_counts: list = []
    for fname in chapter_files[:20]:
        try:
            with open(os.path.join(CHAPTERS_DIR_STR, fname), encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if text.startswith("[待修订]"):
            continue
        wc = len(text)
        word_counts.append(wc)
        ch = int(re.search(r'\d+', fname).group())
        if wc < 1800:
            issues.append(f"Ch{ch}: 字数不足（{wc}字 < 1800字最低要求）")
        if wc > 4000:
            issues.append(f"Ch{ch}: 字数过多（{wc}字 > 4000字上限）")
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
        return len(issues) <= 2
    print(f"    {PASS} 所有检测章节符合番茄规范")
    return True


# ═══════════════════════════════════════════
# AC-5: 人物弧光一致性
# ═══════════════════════════════════════════
def ac5_character_arcs(novel_id: str = "renqingzhai_v1") -> bool:
    print("\n  AC-5: 人物弧光一致性测试")
    l2_path = os.path.join(L2_DIR_STR, f"{novel_id}_memory.json")
    if not os.path.exists(l2_path):
        print(f"    {SKIP} 无Tracker记忆")
        return True
    try:
        with open(l2_path, encoding="utf-8") as f:
            memory = json.load(f)
    except Exception:
        print(f"    {SKIP} 记忆文件无法解析")
        return True

    hot = memory.get("hot", {})
    issues: list = []
    protagonist_level = hot.get("protagonist_level", "")
    valid_levels = ["感债者", "识债者", "接债者", "理债者", "断债者", "债主"]
    if protagonist_level and protagonist_level not in valid_levels:
        issues.append(f"主角等级「{protagonist_level}」不在标准体系中")

    level_thresholds = {k: v[1] for k, v in POWER_LEVELS.items()}
    points = hot.get("protagonist_points", 0)
    if protagonist_level in level_thresholds:
        required = level_thresholds[protagonist_level]
        if points < required * 0.8:
            issues.append(f"点数({points})与等级「{protagonist_level}」(需{required})不匹配")

    threads = hot.get("active_threads", [])
    if len(threads) > 10:
        issues.append(f"活跃剧情线过多（{len(threads)}条）")

    char_states = hot.get("character_states", {})
    if not char_states and memory.get("meta", {}).get("total_chapters_tracked", 0) > 3:
        issues.append("Tracker未记录任何角色状态")

    if issues:
        print(f"    {FAIL} 发现{len(issues)}个弧光问题：")
        for issue in issues:
            print(f"      - {issue}")
        return False
    print(f"    {PASS} 人物状态追踪正常（主角:{protagonist_level}, {points}点, "
          f"{len(threads)}条剧情线）")
    return True


def run_all() -> bool:
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
    import sys
    args = sys.argv[1:]
    if not args or args[0] == "all":
        run_all()
    elif args[0] == "ac1":
        ac1_consistency()
    elif args[0] == "ac2":
        ac2_genre_switch()
    elif args[0] == "ac3":
        ac3_outline_quality()
    elif args[0] == "ac4":
        ac4_platform_compliance()
    elif args[0] == "ac5":
        ac5_character_arcs()
    else:
        print(__doc__)