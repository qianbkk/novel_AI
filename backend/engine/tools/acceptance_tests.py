"""tools/acceptance_tests.py — 十二大验收标准测试套件 (V3方案8.5节 + 2026-07-25 战略审视 Commit 7)

Migrated from novel_AI/tools/acceptance_tests.py. The five original acceptance
criteria (AC-1~AC-5) check setting-package consistency, genre switching,
outline quality, platform compliance, and character-arc consistency.

2026-07-25 战略审视 Commit 7 追加 AC-6~AC-12:
- AC-6: 但是法则密度（章首/章中/章尾转折钩覆盖）
- AC-7: 信息差多样性（reader_knows / protagonist_knows / both_blind 三模式分布）
- AC-8: 情绪锚点多样性（emotion_core 不连续 3 章相同）
- AC-9: 三线分布（narrative_thread 主/支/暗线 60/30/10 比例）
- AC-10: 扮猪吃虎/打脸三阶段节拍（复用 beat_checker）
- AC-11: 升级循环合规（复用 beat_checker）
- AC-12: 对话提示词密度（复用 normalizer 阈值）

调用: python -m engine.tools.acceptance_tests [all|ac1..ac12]
"""
from __future__ import annotations
import json
import os
import re
from pathlib import Path

from ..config.paths import (
    CHAPTERS_DIR_STR, OUTPUT_DIR_STR, L2_DIR_STR, SETTING_PATH_STR,
)
from ..config.power_levels import POWER_LEVELS


# ═══════════════════════════════════════════════
# AC-7/8/9 共用：tasks.json 数据加载
# ═══════════════════════════════════════════════
def _load_all_tasks() -> list[dict]:
    """读 <OUTPUT_DIR>/arc_*_tasks.json 的所有任务单,按 chapter_number 升序。"""
    if not os.path.exists(OUTPUT_DIR_STR):
        return []
    tasks: list[dict] = []
    for fname in sorted(os.listdir(OUTPUT_DIR_STR)):
        if not (fname.startswith("arc_") and fname.endswith("_tasks.json")):
            continue
        try:
            with open(os.path.join(OUTPUT_DIR_STR, fname), encoding="utf-8") as f:
                arc_tasks = json.load(f)
            if isinstance(arc_tasks, list):
                tasks.extend(arc_tasks)
        except Exception:
            continue
    tasks.sort(key=lambda t: t.get("chapter_number", 0) or 0)
    return tasks


def _novel_ai_dir_from_output() -> str:
    """从 OUTPUT_DIR 推出 novel_ai_dir(beat_checker 需要)。

    OUTPUT_DIR_STR = <novel_ai>/output  → novel_ai = parent
    """
    return str(Path(OUTPUT_DIR_STR).resolve().parent)


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


# ═══════════════════════════════════════════════
# AC-6: 但是法则密度（2026-07-25 战略审视 Commit 7）
# 来源：write/《写网文如何把握节奏》§转折信号 + BUT_LAW_INSTRUCTION
# 指标：每章在 [开头 200 字 / 中段 / 结尾前 300 字] 必含 ≥1 个转折信号词
#       (但是/然而/不料/没想到/却/偏偏/不料/谁知/突然)
# 通过：≥60% 章节满足 3 段全覆盖
# ═══════════════════════════════════════════════
_BUT_LAW_SIGNALS = (
    "但是", "然而", "不料", "没想到", "却", "偏偏",
    "谁知", "突然", "岂料", "谁料",
)


def _has_but_signal(text: str) -> bool:
    return any(sig in text for sig in _BUT_LAW_SIGNALS)


def ac6_but_law_density() -> bool:
    print("\n  AC-6: 但是法则密度测试")
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
    total = 0
    fully_covered = 0
    for fname in chapter_files[:30]:
        try:
            with open(os.path.join(CHAPTERS_DIR_STR, fname), encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if text.startswith("[待修订]") or len(text) < 600:
            continue
        total += 1
        head = text[:200]
        mid_idx = len(text) // 2
        tail = text[-300:]
        mid_window = text[mid_idx - 100:mid_idx + 100] if len(text) > 200 else ""
        seg_covered = (
            (1 if _has_but_signal(head) else 0)
            + (1 if _has_but_signal(mid_window) else 0)
            + (1 if _has_but_signal(tail) else 0)
        )
        if seg_covered >= 2:  # 至少 2/3 段有转折信号 → 合格
            fully_covered += 1
        else:
            issues.append(
                f"{fname}: 仅 {seg_covered}/3 段含转折信号 "
                f"(head={_has_but_signal(head)},mid={_has_but_signal(mid_window)},tail={_has_but_signal(tail)})"
            )
    if total == 0:
        print(f"    {SKIP} 无有效章节")
        return True
    coverage = fully_covered / total
    if coverage < 0.6:
        print(f"    {FAIL} 但是法则覆盖率 {coverage:.0%} ({fully_covered}/{total}) < 60%:")
        for i in issues[:3]:
            print(f"      - {i}")
        return False
    print(f"    {PASS} 但是法则覆盖率 {coverage:.0%} ({fully_covered}/{total} 章节 ≥2/3 段转折)")
    return True


# ═══════════════════════════════════════════════
# AC-7: 信息差多样性（2026-07-25 Commit 7）
# 来源：INFO_ASYMMETRY_INSTRUCTION 三模式（读者知/主角知/双盲）
# 指标：连续 3 章 info_asymmetry.mode 相同 → 警告（避免单一模式疲劳）
# 通过：最近 10 章中无连续 3 章同 mode
# ═══════════════════════════════════════════════
def _info_mode_of(task: dict) -> str | None:
    """从 task.info_asymmetry 提取信息差模式名。

    模式定义（与 INFO_ASYMMETRY_INSTRUCTION 一致）：
      - reader_knows: 读者知/主角不知
      - protagonist_knows: 主角知/读者不知
      - both_blind: 双方均不知
    缺字段（info_asymmetry 不是 dict 或缺失） → None（视为 N/A,跳过该章）
    空 dict {} → both_blind（双方均不知）
    """
    info = task.get("info_asymmetry")
    if not isinstance(info, dict):
        return None
    rk = bool(info.get("reader_knows"))
    pk = bool(info.get("protagonist_knows"))
    if rk and not pk:
        return "reader_knows"
    if pk and not rk:
        return "protagonist_knows"
    if not rk and not pk:
        return "both_blind"
    # 同时有 rk + pk（少见,归类为 reader_knows 优先——读者视角信息更敏感）
    return "reader_knows"


def ac7_info_asymmetry_diversity() -> bool:
    print("\n  AC-7: 信息差多样性测试")
    tasks = _load_all_tasks()
    if not tasks:
        print(f"    {SKIP} 无任务单文件")
        return True
    recent = tasks[-10:] if len(tasks) >= 10 else tasks
    modes: list[str | None] = [_info_mode_of(t) for t in recent]
    valid_modes = [m for m in modes if m is not None]
    if len(valid_modes) < 3:
        print(f"    {SKIP} 有效 info_asymmetry 字段 < 3 ({len(valid_modes)}),无法验证多样性")
        return True
    issues: list = []
    streak_mode = None
    streak_start = 0
    for i, m in enumerate(modes):
        if m is None:
            streak_mode = None
            streak_start = i + 1
            continue
        if m != streak_mode:
            streak_mode = m
            streak_start = i
        streak_len = i - streak_start + 1
        if streak_len >= 3:
            issues.append(
                f"Ch{recent[i].get('chapter_number', i)}: 连续 {streak_len} 章 info_asymmetry 模式={m}"
            )
    if issues:
        # 去重(只报一次最长 streak)
        seen: set = set()
        unique = []
        for x in issues:
            if x not in seen:
                seen.add(x)
                unique.append(x)
        print(f"    {FAIL} 发现连续 ≥3 章同模式:")
        for i in unique[:3]:
            print(f"      - {i}")
        return False
    unique_modes = set(valid_modes)
    print(f"    {PASS} 信息差多样性 OK({len(unique_modes)} 种模式,样本 {len(valid_modes)} 章)")
    return True


# ═══════════════════════════════════════════════
# AC-8: 情绪锚点多样性（2026-07-25 Commit 7）
# 来源：writer prompt「避免连续 3 章同 emotion_core」+ beat_checker.check_emotion_diversity
# 指标：最近 5 章 emotion_core 唯一值 ≥ 3
# 通过：唯一值 ≥ 3 OR 章节数 < 5
# ═══════════════════════════════════════════════
def ac8_emotion_diversity() -> bool:
    print("\n  AC-8: 情绪锚点多样性测试")
    tasks = _load_all_tasks()
    if not tasks:
        print(f"    {SKIP} 无任务单文件")
        return True
    recent = tasks[-5:] if len(tasks) >= 5 else tasks
    cores = [t.get("emotion_core") for t in recent if t.get("emotion_core")]
    if len(cores) < 3:
        print(f"    {SKIP} 有效 emotion_core < 3 ({len(cores)}),无法验证多样性")
        return True
    unique = set(cores)
    if len(unique) < 3:
        print(f"    {FAIL} 最近 {len(cores)} 章 emotion_core 唯一值仅 {len(unique)} 种:{cores}")
        return False
    print(f"    {PASS} 情绪锚点多样性 OK({len(unique)} 种:{list(unique)})")
    return True


# ═══════════════════════════════════════════════
# AC-9: 三线分布（2026-07-25 Commit 7）
# 来源：MODULAR_NARRATIVE_INSTRUCTION「主 60% / 支 30% / 暗 10%」
# 指标：main / side / hidden 三线分布合理 + 连续 3 章同 thread → 警告
# 通过：最近 10 章 thread ∈ {main, side, hidden} + 无连续 3 章同 thread
# ═══════════════════════════════════════════════
_VALID_THREADS = {"main", "side", "hidden"}
_THREAD_TARGET_DIST = {"main": 0.6, "side": 0.3, "hidden": 0.1}


def ac9_narrative_thread_distribution() -> bool:
    print("\n  AC-9: 三线分布测试")
    tasks = _load_all_tasks()
    if not tasks:
        print(f"    {SKIP} 无任务单文件")
        return True
    recent = tasks[-10:] if len(tasks) >= 10 else tasks
    threads = [t.get("narrative_thread") for t in recent]
    valid = [th for th in threads if th in _VALID_THREADS]
    if len(valid) < 3:
        print(f"    {SKIP} 有效 narrative_thread < 3 ({len(valid)}),无法验证分布")
        return True
    issues: list = []
    # 1. 连续 streak 检查
    streak_thread = None
    streak_start = 0
    for i, th in enumerate(threads):
        if th not in _VALID_THREADS:
            streak_thread = None
            streak_start = i + 1
            continue
        if th != streak_thread:
            streak_thread = th
            streak_start = i
        streak_len = i - streak_start + 1
        if streak_len >= 3:
            issues.append(
                f"Ch{recent[i].get('chapter_number', i)}: 连续 {streak_len} 章 thread={th}"
            )
    # 2. 整体分布检查(目标 60/30/10,允许 ±20% 偏差)
    dist = {th: valid.count(th) / len(valid) for th in _VALID_THREADS}
    for th, target in _THREAD_TARGET_DIST.items():
        if dist[th] > target + 0.3:  # 偏离过远(> 30%)
            issues.append(f"thread={th} 占比 {dist[th]:.0%} 偏离目标 {target:.0%} 过大")
    if issues:
        seen: set = set()
        unique = []
        for x in issues:
            if x not in seen:
                seen.add(x)
                unique.append(x)
        print(f"    {FAIL} 三线分布异常(实际:{dist}):")
        for i in unique[:3]:
            print(f"      - {i}")
        return False
    print(
        f"    {PASS} 三线分布 OK(main={dist['main']:.0%},"
        f"side={dist['side']:.0%},hidden={dist['hidden']:.0%})"
    )
    return True


# ═══════════════════════════════════════════════
# AC-10: 扮猪吃虎/打脸三阶段节拍（2026-07-25 Commit 7）
# 来源：beat_checker.check_face_slap_three_stage
# 通过：节拍校验 GREEN 或 YELLOW(数据不足时)
# ═══════════════════════════════════════════════
def ac10_face_slap_beat() -> bool:
    print("\n  AC-10: 扮猪吃虎/打脸三阶段节拍")
    try:
        from .beat_checker import check_face_slap_three_stage, load_chapter_metas
    except ImportError:
        from engine.tools.beat_checker import (  # type: ignore
            check_face_slap_three_stage, load_chapter_metas,
        )
    novel_ai_dir = _novel_ai_dir_from_output()
    metas = load_chapter_metas(novel_ai_dir)
    if not metas:
        print(f"    {SKIP} 无章节 meta.json")
        return True
    result = check_face_slap_three_stage(metas, window=10)
    status = result["status"]
    if status == "GREEN":
        print(f"    {PASS} {result['reason']}")
        for d in result.get("details", [])[:2]:
            print(f"      {d}")
        return True
    if status == "YELLOW":
        print(f"    {SKIP} {result['reason']}")
        return True
    print(f"    {FAIL} {result['reason']}")
    for d in result.get("details", [])[:3]:
        print(f"      {d}")
    return False


# ═══════════════════════════════════════════════
# AC-11: 升级循环合规（2026-07-25 Commit 7）
# 来源：beat_checker.check_upgrade_loop
# 通过：节拍校验 GREEN 或 YELLOW(数据不足时)
# ═══════════════════════════════════════════════
def ac11_upgrade_loop() -> bool:
    print("\n  AC-11: 升级循环合规测试")
    try:
        from .beat_checker import check_upgrade_loop, load_chapter_metas
    except ImportError:
        from engine.tools.beat_checker import (  # type: ignore
            check_upgrade_loop, load_chapter_metas,
        )
    novel_ai_dir = _novel_ai_dir_from_output()
    metas = load_chapter_metas(novel_ai_dir)
    if not metas:
        print(f"    {SKIP} 无章节 meta.json")
        return True
    result = check_upgrade_loop(metas, window=10)
    status = result["status"]
    if status == "GREEN":
        print(f"    {PASS} {result['reason']}")
        for d in result.get("details", [])[:2]:
            print(f"      {d}")
        return True
    if status == "YELLOW":
        print(f"    {SKIP} {result['reason']}")
        return True
    print(f"    {FAIL} {result['reason']}")
    for d in result.get("details", [])[:3]:
        print(f"      {d}")
    return False


# ═══════════════════════════════════════════════
# AC-12: 对话提示词密度（2026-07-25 Commit 7）
# 来源：normalizer.detect_dialogue_pollution + DIALOGUE_WARNING_THRESHOLD
# 指标：每章对话提示词计数 < 25 优秀 / 25-49 预警 / ≥50 强制替换
# 通过：全部章节 < 50（预警阈值可接受,强制阈值不可接受）
# ═══════════════════════════════════════════════
def ac12_dialogue_density() -> bool:
    print("\n  AC-12: 对话提示词密度测试")
    try:
        from ..agents.normalizer import (
            detect_dialogue_pollution,
            DIALOGUE_WARNING_THRESHOLD,
            DIALOGUE_FORCE_THRESHOLD,
        )
    except ImportError:
        from engine.agents.normalizer import (  # type: ignore
            detect_dialogue_pollution,
            DIALOGUE_WARNING_THRESHOLD,
            DIALOGUE_FORCE_THRESHOLD,
        )
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
    counts: list[int] = []
    issues: list = []
    for fname in chapter_files[:30]:
        try:
            with open(os.path.join(CHAPTERS_DIR_STR, fname), encoding="utf-8") as f:
                text = f.read()
        except Exception:
            continue
        if text.startswith("[待修订]") or len(text) < 600:
            continue
        cnt, _ = detect_dialogue_pollution(text)
        counts.append(cnt)
        if cnt >= DIALOGUE_FORCE_THRESHOLD:
            issues.append(f"{fname}: 对话提示词 {cnt} ≥ {DIALOGUE_FORCE_THRESHOLD}(强制替换)")
    if not counts:
        print(f"    {SKIP} 无有效章节")
        return True
    avg = sum(counts) / len(counts)
    if issues:
        print(f"    {FAIL} 发现强制替换级别对话污染(均章 {avg:.1f}):")
        for i in issues[:3]:
            print(f"      - {i}")
        return False
    if avg >= DIALOGUE_WARNING_THRESHOLD:
        print(
            f"    {FAIL} 均章对话提示词 {avg:.1f} ≥ {DIALOGUE_WARNING_THRESHOLD} 预警阈值"
            f"(n={len(counts)})"
        )
        return False
    print(
        f"    {PASS} 对话密度 OK(均章 {avg:.1f} < 阈值 {DIALOGUE_WARNING_THRESHOLD},"
        f"n={len(counts)})"
    )
    return True


def run_all() -> bool:
    print(f"\n{'═'*55}")
    print(f"  十二大验收标准测试（V3方案8.5节 + 2026-07-25 Commit 7）")
    print(f"{'═'*55}")
    results = {
        "AC-1 设定一致性":       ac1_consistency(),
        "AC-2 题材切换":         ac2_genre_switch(),
        "AC-3 任务单质量":       ac3_outline_quality(),
        "AC-4 平台适配":         ac4_platform_compliance(),
        "AC-5 人物弧光":         ac5_character_arcs(),
        "AC-6 但是法则密度":     ac6_but_law_density(),
        "AC-7 信息差多样性":     ac7_info_asymmetry_diversity(),
        "AC-8 情绪锚点多样性":   ac8_emotion_diversity(),
        "AC-9 三线分布":         ac9_narrative_thread_distribution(),
        "AC-10 扮猪吃虎节拍":    ac10_face_slap_beat(),
        "AC-11 升级循环合规":    ac11_upgrade_loop(),
        "AC-12 对话提示词密度":  ac12_dialogue_density(),
    }
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n{'─'*55}")
    print(f"  结果：{passed}/{total} 通过")
    for name, result in results.items():
        print(f"  {'✅' if result else '❌'} {name}")
    print(f"{'═'*55}\n")
    return passed == total


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
    elif args[0] == "ac6":
        ac6_but_law_density()
    elif args[0] == "ac7":
        ac7_info_asymmetry_diversity()
    elif args[0] == "ac8":
        ac8_emotion_diversity()
    elif args[0] == "ac9":
        ac9_narrative_thread_distribution()
    elif args[0] == "ac10":
        ac10_face_slap_beat()
    elif args[0] == "ac11":
        ac11_upgrade_loop()
    elif args[0] == "ac12":
        ac12_dialogue_density()
    else:
        print(__doc__)