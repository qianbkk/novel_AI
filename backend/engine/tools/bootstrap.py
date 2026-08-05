"""tools/bootstrap.py — 黄金三章 Bootstrap 模式

Migrated from novel_AI/tools/bootstrap.py. Uses backend.engine agents
(writer/normalizer/compliance/checker/tracker) via the active router.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

from ..config.paths import (
    CHAPTERS_DIR_STR, STYLE_SAMPLES_DIR_STR, OUTPUT_DIR_STR, SETTING_PATH_STR,
    STATE_PATH_STR,
)
from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..memory.manager import empty_l2
from ..utils import atomic_write_json


CHAPTERS_DIR = CHAPTERS_DIR_STR
STYLE_DIR    = STYLE_SAMPLES_DIR_STR
OUTPUT_DIR   = OUTPUT_DIR_STR
SETTING_PATH = SETTING_PATH_STR
STATE_PATH   = STATE_PATH_STR
BOOTSTRAP_OUT = os.path.join(OUTPUT_DIR, "bootstrap_candidates.json")

os.makedirs(CHAPTERS_DIR, exist_ok=True)
os.makedirs(STYLE_DIR, exist_ok=True)


def _setting_title(setting: dict) -> str:
    titles = setting.get("title_candidates") or []
    return str(titles[0]).strip() if titles else "未命名小说"


def _supporting_character(setting: dict) -> str | None:
    protagonist_name = str((setting.get("protagonist") or {}).get("name") or "").strip()
    for item in setting.get("key_characters") or setting.get("characters") or []:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        if name and name != protagonist_name:
            return name
    return None


def build_golden_tasks(setting: dict) -> list:
    """Build the opening three chapters entirely from the active setting package."""
    mc = setting.get("protagonist") or {}
    hooks = setting.get("golden_chapter_hooks") or {}
    power = setting.get("power_system") or {}
    world = setting.get("world_setting") or {}
    mc_name = str(mc.get("name") or "主角")
    ability_name = str(power.get("name") or power.get("ability_name") or "核心能力")
    support_name = _supporting_character(setting)
    world_name = str(
        world.get("surface_world_name") or world.get("hidden_world_name") or "故事发生地"
    )
    chapter3_characters = [mc_name] + ([support_name] if support_name else [])
    return [
        {
            "chapter_number": 1, "chapter_role": "开局",
            "chapter_goal": f"在{world_name}展示{mc_name}的日常困境，并触发{ability_name}的首次觉醒",
            "main_characters": [mc_name],
            "shuang_type": "揭秘",
            "shuang_description": hooks.get("chapter_1_shuang_point") or f"{mc_name}首次发现{ability_name}并解决眼前危机",
            "ending_hook_type": "信息钩",
            "ending_hook_description": hooks.get("chapter_1_cliffhanger") or "觉醒异象引来未知关注者",
            "setting_constraints": [f"地点遵循设定中的{world_name}", f"{mc_name}刚接触{ability_name}，认知有限"],
            "forbidden_actions": ["主角立即完全掌握核心能力", "提前揭露全书终极秘密"],
            "target_length": "2200-2500", "audit_mode": "bootstrap",
            "is_arc_climax": False, "_opening_direction": hooks.get("chapter_1_opening", ""),
        },
        {
            "chapter_number": 2, "chapter_role": "发展",
            "chapter_goal": f"{mc_name}谨慎验证{ability_name}的边界，为一次现实冲突付出代价",
            "main_characters": [mc_name],
            "shuang_type": "逆袭",
            "shuang_description": hooks.get("chapter_2_shuang_point") or f"{mc_name}利用有限信息完成第一次有效反制",
            "ending_hook_type": "危机钩",
            "ending_hook_description": hooks.get("chapter_2_cliffhanger") or "第一次行动暴露了更大的威胁",
            "setting_constraints": ["紧接第1章", "核心能力仍存在明确限制"],
            "forbidden_actions": ["能力无代价且完全稳定", "一次性解决主线冲突"],
            "target_length": "2000-2200", "audit_mode": "bootstrap",
            "is_arc_climax": False,
        },
        {
            "chapter_number": 3, "chapter_role": "爽点",
            "chapter_goal": (
                f"{support_name}正式介入并推动第一次高强度冲突，{mc_name}完成阶段性反制"
                if support_name else f"关键人物介入并推动第一次高强度冲突，{mc_name}完成阶段性反制"
            ),
            "main_characters": chapter3_characters,
            "shuang_type": "打脸",
            "shuang_description": hooks.get("chapter_3_shuang_point") or f"{mc_name}利用{ability_name}看穿对手筹码并完成反制",
            "ending_hook_type": "信息钩",
            "ending_hook_description": hooks.get("chapter_3_cliffhanger") or "关键人物揭开更大世界的一角",
            "setting_constraints": ["承接前两章的能力边界", "只揭示首个剧情弧所需信息"],
            "forbidden_actions": ["主角完全掌握核心能力", "提前解决全书终极冲突"],
            "target_length": "2200-2500", "audit_mode": "bootstrap",
            "is_arc_climax": False,
        },
    ]


def build_bootstrap_system(setting: dict) -> str:
    mc = setting.get("protagonist") or {}
    power = setting.get("power_system") or {}
    title = _setting_title(setting)
    protagonist = str(mc.get("name") or "主角")
    profile = "，".join(
        str(value).strip() for value in (mc.get("age"), mc.get("background"), mc.get("personality"))
        if value not in (None, "")
    )
    ability = str(power.get("name") or power.get("ability_name") or "核心能力")
    return f"""你是一位顶级网文作者，正在创作《{title}》的黄金三章。

【写作要求】
1. 第一段必须在3句话内抓住读者，要有画面、张力和悬念
2. 主角是{protagonist}{('：' + profile) if profile else ''}
3. 核心能力“{ability}”的表现必须遵守设定包，不得自行替换成其他系统
4. 第一章建立欲望与危机，第二章验证能力边界，第三章交付首个强爽点并留下主线钩子
5. 禁用词：此刻、蓦然、不禁、心中一动、深吸一口气、眸子、眼眸
6. 章节结尾最后50字必须是强钩子，让读者立刻想点下一章

直接输出正文，不要标题，不要任何说明。"""


def generate_candidate(task: dict, setting: dict, version: str,
                       temperature: float) -> tuple[str, float]:
    mc = setting["protagonist"]
    world = setting["world_setting"]
    power = setting["power_system"]
    opening_hint = task.get("_opening_direction", "")
    opening_section = f"\n【第一段方向参考】{opening_hint}" if opening_hint else ""
    title = _setting_title(setting)
    speech_quirks = mc.get("speech_quirks") or []
    if isinstance(speech_quirks, str):
        speech_quirks = [speech_quirks]
    world_name = world.get("surface_world_name") or world.get("hidden_world_name") or "未命名世界"
    world_history = str(world.get("hidden_world_history") or world.get("description") or "")
    ability_name = power.get("name") or power.get("ability_name") or "核心能力"
    prompt = f"""【小说基本信息】
书名：{title}
主角：{mc.get('name', '主角')}，{mc.get('age', '年龄未设定')}，{str(mc.get('background') or '')[:100]}
性格：{mc.get('personality', '以设定包为准')}
口癖：{'、'.join(str(item) for item in speech_quirks) or '以设定包为准'}
世界：{world_name}——{world_history[:80]}
核心能力：{ability_name}
{opening_section}

【第{task['chapter_number']}章任务（版本{version}）】
章节目标：{task['chapter_goal']}
必须出现的爽点：{task['shuang_description']}
结尾钩子方向：{task['ending_hook_description']}
出场人物：{', '.join(task['main_characters'])}
字数目标：{task['target_length']}字
限制：{'; '.join(task.get('forbidden_actions', []))}

请写第{task['chapter_number']}章版本{version}的完整正文："""
    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    text, cost = router.call(
        agent_name="writer",
        system_prompt=build_bootstrap_system(setting),
        user_prompt=prompt,
        max_tokens=4000,
        temperature=temperature,
    )
    return text, cost


def score_candidate(text: str, task: dict, version: str,
                    checker_callable) -> tuple[dict, float]:
    from ..agents.checker import run_checker
    if checker_callable is None:
        checker_callable = run_checker
    check_result, cost = checker_callable(text, task, audit_mode="bootstrap")
    check_result["version"] = version
    check_result["word_count"] = len(text)
    return check_result, cost


def run_bootstrap(novel_id: str = "renqingzhai_v1", num_candidates: int = 3) -> dict:
    if not os.path.exists(SETTING_PATH):
        raise FileNotFoundError(f"设定包不存在：{SETTING_PATH}。请先运行 planner agent。")
    with open(SETTING_PATH, encoding="utf-8") as f:
        setting = json.load(f)

    tasks = build_golden_tasks(setting)
    all_candidates: dict = {}
    total_cost = 0.0

    from ..agents.normalizer import run_normalizer
    from ..agents.compliance import run_compliance
    from ..agents.checker import run_checker

    print(f"\n{'='*60}")
    print(f"🥇 黄金三章 Bootstrap 模式启动")
    print(f"   每章生成{num_candidates}个候选版本，供人工选择")
    print(f"{'='*60}\n")

    for task in tasks:
        ch = task["chapter_number"]
        print(f"\n── 第{ch}章 [{task['chapter_role']}] ──")
        candidates: list = []
        temperatures = [0.75, 0.85, 0.92][:num_candidates]
        versions = ["A", "B", "C"][:num_candidates]

        for ver, temp in zip(versions, temperatures):
            print(f"  生成版本{ver}（temperature={temp}）...", end="", flush=True)
            text, c1 = generate_candidate(task, setting, ver, temp)
            total_cost += c1
            clean_text, _, c2 = run_normalizer(text, task)
            total_cost += c2
            comp_result, c3 = run_compliance(clean_text)
            total_cost += c3
            score_result, c4 = score_candidate(clean_text, task, ver, run_checker)
            total_cost += c4
            candidates.append({
                "version": ver,
                "text": clean_text,
                "score": score_result["score"],
                "dimensions": score_result["dimensions"],
                "strongest_point": score_result.get("strongest_point", ""),
                "weakest_point": score_result.get("weakest_point", ""),
                "word_count": len(clean_text),
                "compliance_passed": comp_result["passed"],
                "compliance_warnings": comp_result.get("warnings", []),
            })
            print(f" 得分：{score_result['score']:.1f} | {len(clean_text)}字")

        candidates.sort(key=lambda x: x["score"], reverse=True)
        all_candidates[f"chapter_{ch}"] = candidates

        print(f"\n  📊 第{ch}章候选对比：")
        for c in candidates:
            flag = "🏆" if c == candidates[0] else "  "
            comp_flag = "✅" if c["compliance_passed"] else "❌合规"
            print(f"  {flag} 版本{c['version']}: {c['score']:.1f}分 | "
                  f"{c['word_count']}字 | {comp_flag}")
            print(f"      优：{c['strongest_point']}")
            print(f"      弱：{c['weakest_point']}")

    # Save summary (without full text, to keep file small)
    summary = {}
    for key, cands in all_candidates.items():
        summary[key] = [{k: v for k, v in c.items() if k != "text"} for c in cands]
    # 迭代 #49: 改用 atomic_write_json（避免 bootstrap_candidates.json 半写损坏）
    atomic_write_json(BOOTSTRAP_OUT, summary)

    # Save each version + the highest-scored default
    for task in tasks:
        ch = task["chapter_number"]
        cands = all_candidates[f"chapter_{ch}"]
        best = cands[0]
        for c in cands:
            ver_path = os.path.join(CHAPTERS_DIR, f"ch_{ch:04d}_v{c['version']}.txt")
            with open(ver_path, "w", encoding="utf-8") as f:
                f.write(c["text"])
        best_path = os.path.join(CHAPTERS_DIR, f"ch_{ch:04d}.txt")
        with open(best_path, "w", encoding="utf-8") as f:
            f.write(best["text"])
        meta = {
            "chapter_number": ch,
            "chapter_role": task["chapter_role"],
            "selected_version": best["version"],
            "score": best["score"],
            # 2026-07-26 审计修 Critical#1: bootstrap 路径也补方法论字段,
            # 否则 bootstrap 候选 + 选定版本的 meta.json 同样缺 beat_checker 依赖字段。
            # bootstrap 是人工选版流程,任务单上下文仍在,直接透传 task 字段。
            "shuang_type":        task.get("shuang_type", "") or "",
            "ending_hook_type":   task.get("ending_hook_type", "") or "",
            "is_arc_climax":      bool(task.get("is_arc_climax", False)),
            "narrative_thread":   task.get("narrative_thread", "") or "",
            "emotion_core":       task.get("emotion_core", "") or "",
            "emotion_intensity":  task.get("emotion_intensity", 0) or 0,
            "foreshadowing_ops":  task.get("foreshadowing_ops", []) or [],
            "word_count": best["word_count"],
            "bootstrap": True,
            "all_scores": {c["version"]: c["score"] for c in cands},
        }
        atomic_write_json(
            os.path.join(CHAPTERS_DIR, f"ch_{ch:04d}_meta.json"), meta,
        )
        # 默认采用最高分版本，但必须走与人工选版相同的风格、记忆和状态收尾。
        _finalize_selection(
            ch,
            best["version"],
            novel_id,
            manually_selected=False,
        )

    print(f"\n{'='*60}")
    print(f"✅ Bootstrap完成！总成本：${total_cost:.4f}")
    print(f"\n候选文件已保存：")
    for ch in range(1, len(tasks) + 1):
        for ver in versions:
            print(f"  output/chapters/ch_{ch:04d}_v{ver}.txt")
    print(f"\n📋 得分摘要：output/bootstrap_candidates.json")
    return all_candidates


def _finalize_selection(
    chapter_num: int,
    version: str,
    novel_id: str,
    *,
    manually_selected: bool,
    novel_ai_dir: str | None = None,
) -> bool:
    """Apply one bootstrap selection and all required state side effects."""
    engine_root = os.path.abspath(novel_ai_dir) if novel_ai_dir else os.path.dirname(OUTPUT_DIR)
    output_dir = os.path.join(engine_root, "output")
    chapters_dir = os.path.join(output_dir, "chapters")
    style_dir = os.path.join(output_dir, "style_samples")
    setting_path = os.path.join(output_dir, "setting_package.json")
    state_path = os.path.join(output_dir, "orchestrator_state.json")
    os.makedirs(chapters_dir, exist_ok=True)
    os.makedirs(style_dir, exist_ok=True)
    ver_path = os.path.join(chapters_dir, f"ch_{chapter_num:04d}_v{version}.txt")
    if not os.path.exists(ver_path):
        print(f"❌ 版本文件不存在：{ver_path}")
        return False
    with open(ver_path, encoding="utf-8") as f:
        text = f.read()
    dest = os.path.join(chapters_dir, f"ch_{chapter_num:04d}.txt")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)

    meta_path = os.path.join(chapters_dir, f"ch_{chapter_num:04d}_meta.json")
    meta = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    meta["selected_version"] = version
    meta["manually_selected"] = manually_selected
    meta["automatically_selected"] = not manually_selected
    atomic_write_json(meta_path, meta)

    if chapter_num == 1:
        style_path = os.path.join(style_dir, "anchor_ch01.txt")
        with open(style_path, "w", encoding="utf-8") as f:
            f.write(f"# 风格锚点：第1章选定版本{version}\n# 全书风格基准\n\n{text}")
        print(f"✅ 第1章版本{version}已设为全书风格锚点 → style_samples/anchor_ch01.txt")

    if chapter_num == 3:
        setting = {}
        if os.path.exists(setting_path):
            with open(setting_path, encoding="utf-8") as f:
                setting = json.load(f)
        protagonist = str((setting.get("protagonist") or {}).get("name") or "主角")
        ability = str((setting.get("power_system") or {}).get("name") or "核心能力")
        memory = empty_l2()
        memory["hot"]["last_chapter_ending"] = text[-200:]
        memory["hot"]["recent_summaries"] = [
            {"chapter": i, "summary": f"黄金第{i}章（bootstrap）"} for i in range(1, 4)
        ]
        memory["hot"]["recent_events"] = f"黄金三章完成：{protagonist}觉醒并初步验证{ability}"
        memory_path = os.path.join(engine_root, "memory", "l2", f"{novel_id}_memory.json")
        atomic_write_json(memory_path, memory)
        print("✅ Tracker记忆已初始化（基于黄金三章）")

        from ..state import load_state, save_state
        if os.path.exists(state_path):
            state = load_state(state_path)
        else:
            from ..agents.init_arc import build_state_from_paths
            state = build_state_from_paths(
                novel_id,
                setting_path=Path(setting_path),
                state_path=Path(state_path),
                chapters_dir=Path(chapters_dir),
            )
        state["current_chapter"] = max(3, int(state.get("current_chapter", 0) or 0))
        state["current_phase"] = "writing"
        save_state(state, state_path)
        print("✅ Orchestrator状态已更新：当前至少第3章")

    print(f"✅ 第{chapter_num}章已选定版本{version}")
    all_selected = all(
        os.path.exists(os.path.join(chapters_dir, f"ch_{i:04d}.txt"))
        for i in range(1, 4)
    )
    if all_selected and chapter_num == 3:
        print("\n🚀 黄金三章全部选定！可以开始正式生产。")
    return True


def select_version(chapter_num: int, version: str,
                   novel_id: str = "renqingzhai_v1") -> None:
    """人工确认版本选择，并执行与自动默认选择一致的收尾。"""
    _finalize_selection(chapter_num, version, novel_id, manually_selected=True)


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "run":
        run_bootstrap()
    elif args[0] == "select" and len(args) >= 3:
        select_version(int(args[1]), args[2].upper())
    else:
        print("用法：python bootstrap.py [run|select <ch> <ver>]")