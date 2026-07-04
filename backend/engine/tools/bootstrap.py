"""tools/bootstrap.py — 黄金三章 Bootstrap 模式

Migrated from novel_AI/tools/bootstrap.py. Uses backend.engine agents
(writer/normalizer/compliance/checker/tracker) via the active router.
"""
from __future__ import annotations
import json
import os
import sys

from ..config.paths import (
    CHAPTERS_DIR_STR, STYLE_SAMPLES_DIR_STR, OUTPUT_DIR_STR, SETTING_PATH_STR,
    STATE_PATH_STR,
)
from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..memory.manager import empty_l2, save_l2
from ..utils import atomic_write_json


CHAPTERS_DIR = CHAPTERS_DIR_STR
STYLE_DIR    = STYLE_SAMPLES_DIR_STR
OUTPUT_DIR   = OUTPUT_DIR_STR
SETTING_PATH = SETTING_PATH_STR
STATE_PATH   = STATE_PATH_STR
BOOTSTRAP_OUT = os.path.join(OUTPUT_DIR, "bootstrap_candidates.json")

os.makedirs(CHAPTERS_DIR, exist_ok=True)
os.makedirs(STYLE_DIR, exist_ok=True)


def build_golden_tasks(setting: dict) -> list:
    mc = setting["protagonist"]
    hooks = setting.get("golden_chapter_hooks", {})
    mc_name = mc["name"]
    return [
        {
            "chapter_number": 1, "chapter_role": "开局",
            "chapter_goal": f"展示{mc_name}的日常与能力，触发人情债系统觉醒，制造第一个爽点",
            "main_characters": [mc_name],
            "shuang_type": "揭秘",
            "shuang_description": hooks.get("chapter_1_shuang_point",
                f"谈判桌上{mc_name}第一次清晰看到人情线，随即目睹债崩，手机出现系统提示"),
            "ending_hook_type": "信息钩",
            "ending_hook_description": hooks.get("chapter_3_cliffhanger",
                "系统提示消失后，有人找上门，显然知道他刚觉醒"),
            "setting_constraints": ["地点：临江市某高端写字楼谈判室", "主角尚未接受自己是债主"],
            "forbidden_actions": ["主角立即接受设定并开始使用系统", "出现其他债主角色"],
            "target_length": "2200-2500", "audit_mode": "bootstrap",
            "is_arc_climax": False, "_opening_direction": hooks.get("chapter_1_opening", ""),
        },
        {
            "chapter_number": 2, "chapter_role": "发展",
            "chapter_goal": f"{mc_name}初步探索系统能力，接受第一个委托，遭遇第一个困难",
            "main_characters": [mc_name, "（委托人，路人级）"],
            "shuang_type": "逆袭",
            "shuang_description": "主角用新获得的人情线感知能力，在一个普通的职场纠纷中精准破局，展示能力的实用性",
            "ending_hook_type": "危机钩",
            "ending_hook_description": "完成委托后发现背后有更复杂的债务网络，且有人在暗中监视他",
            "setting_constraints": ["第1章觉醒后24小时内", "系统功能仍不稳定"],
            "forbidden_actions": ["能力突然变得完全稳定", "引入主要配角"],
            "target_length": "2000-2200", "audit_mode": "bootstrap",
            "is_arc_climax": False,
        },
        {
            "chapter_number": 3, "chapter_role": "爽点",
            "chapter_goal": "贺苗出场，揭示人情局的存在，第一个真正的爽感—主角用人情感知碾压对手",
            "main_characters": [mc_name, "贺苗"],
            "shuang_type": "打脸",
            "shuang_description": "有人试图用普通手段威胁或利用陆承，陆承用人情线分析直接看穿对方所有筹码，不动声色地完成反制",
            "ending_hook_type": "信息钩",
            "ending_hook_description": hooks.get("chapter_3_cliffhanger",
                "贺苗告诉陆承：你祖父欠了我一个答案，现在这笔债算你的了"),
            "setting_constraints": ["贺苗主动找上陆承", "不能提前揭露太多人情局秘密"],
            "forbidden_actions": ["陆承已经完全掌握系统", "出现章廷"],
            "target_length": "2200-2500", "audit_mode": "bootstrap",
            "is_arc_climax": False,
        },
    ]


BOOTSTRAP_SYSTEM = """你是一位顶级网文作者，正在创作一部都市系统流小说《债线纵横》。
这是整部小说最关键的章节——黄金三章。你需要全力以赴。

【写作要求】
1. 第一段必须在3句话内抓住读者——要有画面感、有张力、有悬念
2. 主角陆承：27岁，律所谈判顾问，有阅历感但不老气，说话简洁有力
3. 人情线的描写要有画面感——颜色、粗细、光泽、震动感都可以用来刻画
4. 系统提示用【】标注：如【人情感知已激活】【委托：×××】【人情点+50】
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
    prompt = f"""【小说基本信息】
书名：债线纵横
主角：{mc['name']}，{mc['age']}岁，{mc['background'][:100]}
性格：{mc['personality']}
口癖：{'、'.join(mc['speech_quirks'])}
世界：{world['hidden_world_name']}——{world['hidden_world_history'][:80]}
系统货币：{power['currency']}
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
        system_prompt=BOOTSTRAP_SYSTEM,
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
    with open(BOOTSTRAP_OUT, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

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
            "word_count": best["word_count"],
            "bootstrap": True,
            "all_scores": {c["version"]: c["score"] for c in cands},
        }
        atomic_write_json(
            os.path.join(CHAPTERS_DIR, f"ch_{ch:04d}_meta.json"), meta,
        )

    print(f"\n{'='*60}")
    print(f"✅ Bootstrap完成！总成本：${total_cost:.4f}")
    print(f"\n候选文件已保存：")
    for ch in range(1, len(tasks) + 1):
        for ver in versions:
            print(f"  output/chapters/ch_{ch:04d}_v{ver}.txt")
    print(f"\n📋 得分摘要：output/bootstrap_candidates.json")
    return all_candidates


def select_version(chapter_num: int, version: str,
                   novel_id: str = "renqingzhai_v1") -> None:
    """人工确认版本选择，并将选定章节存入style_samples。"""
    ver_path = os.path.join(CHAPTERS_DIR, f"ch_{chapter_num:04d}_v{version}.txt")
    if not os.path.exists(ver_path):
        print(f"❌ 版本文件不存在：{ver_path}")
        return
    with open(ver_path, encoding="utf-8") as f:
        text = f.read()
    dest = os.path.join(CHAPTERS_DIR, f"ch_{chapter_num:04d}.txt")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(text)
    meta_path = os.path.join(CHAPTERS_DIR, f"ch_{chapter_num:04d}_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
        meta["selected_version"] = version
        meta["manually_selected"] = True
        atomic_write_json(meta_path, meta)
    if chapter_num == 1:
        style_path = os.path.join(STYLE_DIR, "anchor_ch01.txt")
        with open(style_path, "w", encoding="utf-8") as f:
            f.write(f"# 风格锚点：第1章选定版本{version}\n# 全书风格基准\n\n{text}")
        print(f"✅ 第1章版本{version}已设为全书风格锚点 → style_samples/anchor_ch01.txt")

    if chapter_num == 3:
        memory = empty_l2()
        memory["hot"]["last_chapter_ending"] = text[-200:]
        memory["hot"]["recent_summaries"] = [
            {"chapter": i, "summary": f"黄金第{i}章（bootstrap）"} for i in range(1, 4)
        ]
        memory["hot"]["recent_events"] = "黄金三章完成：陆承觉醒、初探系统、贺苗登场"
        save_l2(novel_id, memory)
        print(f"✅ Tracker记忆已初始化（基于黄金三章）")
        if os.path.exists(STATE_PATH):
            from ..state import load_state, save_state
            state = load_state(STATE_PATH)
            state["current_chapter"] = 3
            state["current_phase"] = "writing"
            save_state(state, STATE_PATH)
            print(f"✅ Orchestrator状态已更新：当前第3章")

    print(f"✅ 第{chapter_num}章已选定版本{version}")
    all_selected = all(
        os.path.exists(os.path.join(CHAPTERS_DIR, f"ch_{i:04d}.txt"))
        for i in range(1, 4)
    )
    if all_selected and chapter_num == 3:
        print(f"\n🚀 黄金三章全部选定！可以开始正式生产。")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "run":
        run_bootstrap()
    elif args[0] == "select" and len(args) >= 3:
        select_version(int(args[1]), args[2].upper())
    else:
        print("用法：python bootstrap.py [run|select <ch> <ver>]")