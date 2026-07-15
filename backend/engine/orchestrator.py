"""Orchestrator — LangGraph 7-node state machine.

Migrated from novel_AI/orchestrator.py. P2 scope:
  - 7 nodes: load_arc_tasks, get_next_task, write_pipeline, rewrite,
             save_and_track, human_escalation, (budget_stop routing)
  - All 8 agents have real implementations (writer + normalizer +
    compliance + checker + rewriter + tracker + summarizer + outline).
  - Real L2/L5 memory manager (hot/cold分层 + L5 arc summaries + style samples).

Import graph (all relative; NO sys.path injection):
  from .state import OrchestratorState, save_state, load_state
  from .agents.writer import run_writer
  from .agents.normalizer / compliance / checker / rewriter / tracker /
                              summarizer / outline
  from .memory.manager import get_l2
"""
from __future__ import annotations
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Literal

from langgraph.graph import StateGraph, END

from .state import OrchestratorState, save_state, load_state, create_initial_state
from .agents.writer import run_writer
from .agents.normalizer import run_normalizer
from .agents.compliance import run_compliance
from .agents.checker    import run_checker
from .agents.rewriter   import run_rewriter
from .agents.tracker    import run_tracker
from .agents.summarizer import run_summarizer
from .agents.outline    import (
    run_outline, run_outline_card, run_outline_talk,
)
from .memory.manager import get_l2
from .utils import atomic_write_json

# ── Paths (relative to backend/) ──
BACKEND_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR  = Path(__file__).resolve().parent

# 优先用 NOVEL_AI_DIR 环境变量（与 binding.novel_ai_dir 一致），
# 否则 fallback 到 backend/data/engine/output（默认位置）。
# 历史包袱：state 路径之前在 novel_AI/output/，chapters 在 backend；
# 统一通过 env 解决（不强行迁移现有数据，避免破坏 in-flight 任务）。
import os as _os
_NOVEL_AI_DIR_OVERRIDE = _os.environ.get("NOVEL_AI_DIR")
if _NOVEL_AI_DIR_OVERRIDE:
    OUTPUT_DIR   = Path(_NOVEL_AI_DIR_OVERRIDE) / "output"
else:
    OUTPUT_DIR   = BACKEND_DIR / "data" / "engine" / "output"
CHAPTERS_DIR = OUTPUT_DIR / "chapters"
STATE_PATH   = OUTPUT_DIR / "orchestrator_state.json"
SETTING_PATH = OUTPUT_DIR / "setting_package.json"
CONFIG_PATH  = BACKEND_DIR / "data" / "engine" / "config" / "novel_config.json"

# Ensure dirs exist
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

MAX_REWRITE  = 3
PASS_SCORE   = 6.5
# 迭代 #201: 文档化预算硬停阈值的 MVP 放宽
# 用户审计报告 (2026-07-05) 标记"界面预算上限 vs 实际硬停阈值不一致"——
# 期望填 $500 → 实际 $750 才硬停。这里给后来者留个明确说明。
#
# 设计决策：MVP 阶段把 BUDGET_HARD 设为 1.50（150%）而不是 1.00（100%），
# 原因是跑网文生成时 LLM 偶尔会超调用预算（如 ruby LLM cache miss / 续写
# 拉长），过早硬停会打断用户正常 50 章流程。100%-150% 区间只会
# 标 budget_paused + 弹 human_pending，不强制 abort。
#
# 想要严格 100% 停 → 改 BUDGET_HARD = 1.00 即可，但建议先跑完一轮
# 50 章看实际预算消耗分布，再调严。生产部署若已明确成本上限，
# 在 .env 设 NOVEL_BUDGET_HARD_OVERRIDE=1.0 覆盖（patch #201 TODO）。
BUDGET_WARN  = 1.00   # 100% warning
BUDGET_HARD  = 1.50   # 150% hard stop (MVP-relaxed per patches/2026-06-28)

# Module-level cache (avoid re-reading setting per chapter)
_setting_cache: dict | None = None
_setting_mtime: float | None = None  # 迭代 #65: 用 mtime 检测文件变化自动 invalidate
_log = logging.getLogger("novel_ai.engine.orchestrator")  # 迭代 #70: stat 失败可观测性


def _setting() -> dict:
    """读 setting_package.json。mtime 变了自动 invalidate cache（同一进程里
    planner 跑完后重新读最新值）。

    迭代 #69: 返回 .copy() 而非内部 cache 引用 — 防止调用方意外修改全局
    缓存（之前 identity 相等测试鼓励了这种行为，违反直觉）。
    """
    global _setting_cache, _setting_mtime
    if not SETTING_PATH.exists():
        # 文件不存在 → cache 也不缓存（下次如果文件被创建能立刻读到）
        _setting_cache = None
        _setting_mtime = None
        return {}
    try:
        mtime = SETTING_PATH.stat().st_mtime
    except OSError as e:
        # 迭代 #70: 之前静默 fallback 到旧 cache —— 在生产环境掩盖真实的
        # 文件系统问题（权限被改 / 文件被删等）。现在 log.warning 让运维知道。
        _log.warning("_setting: stat(%s) failed (%s); falling back to cache", SETTING_PATH, e)
        return dict(_setting_cache) if _setting_cache is not None else {}
    if _setting_cache is None or _setting_mtime != mtime:
        # 文件变了 → 重新 load
        with open(SETTING_PATH, encoding="utf-8") as f:
            _setting_cache = json.load(f)
        _setting_mtime = mtime
    # 迭代 #69: 返回 copy 防止外部修改污染 cache
    return dict(_setting_cache)


def invalidate_setting_cache() -> None:
    """强制 invalidate _setting_cache（plan 阶段手动调用，或测试用）。"""
    global _setting_cache, _setting_mtime
    _setting_cache = None
    _setting_mtime = None


def _config() -> dict:
    if not CONFIG_PATH.exists():
        return {"novel_id": "default", "platform": "fanqie", "genre": "都市",
                "setting_concept": "", "budget_limit_usd": 500.0}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_chapter(novel_id: str, ch_num: int, text: str, meta: dict) -> None:
    CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHAPTERS_DIR / f"ch_{ch_num:04d}.txt", "w", encoding="utf-8") as f:
        f.write(text)
    # 迭代 #43: ch_NNNN_meta.json 之前直接 open(w) + json.dump，半写损坏后
    # 该章 meta 全丢（score / word_count / selected_version 等），下次 save
    # 覆盖空数据。改用 atomic_write_json 复用 utils 公共工具。
    atomic_write_json(
        str(CHAPTERS_DIR / f"ch_{ch_num:04d}_meta.json"), meta,
    )


def log(msg: str, state: OrchestratorState) -> None:
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] Ch{state.get('current_chapter',0):04d} | {msg}"
    print(line)
    if "ERR" in msg or "FAIL" in msg:
        el = state.get("error_log", [])
        el.append(line)
        state["error_log"] = el[-100:]


def _add_cost(state: OrchestratorState, cost: float) -> None:
    state["budget_used_usd"] = state.get("budget_used_usd", 0.0) + cost


class WriterFailedError(Exception):
    """Writer agent 完全失败的 sentinel 异常。

    之前（你独立验证的 bug）：
      writer 抛 Connection error / SSL 错误时，orchestrator 写一个
      `[writer-stub] {goal}` 占位文本（47 字）并继续 pipeline，checker
      给这个假文本打 7.0 分 PASS，save_and_track 落盘 ch_0064.txt —
      用户视角"7.0 分 PASS"，实际是 47 字占位。
    修复（Commit N）：
      writer 失败时抛 WriterFailedError（不降级到占位），让
      node_write_pipeline 把 task 标为 _writer_failed=True，
      route_after_pipeline 路由到 escalate 而不是 save，
      避免污染下游。
    """


def _budget_ok(state: OrchestratorState) -> bool:
    used  = state.get("budget_used_usd", 0.0)
    limit = state.get("budget_limit_usd", 500.0)
    return used < limit * BUDGET_HARD


# ══════════════════════════════════════════
# 节点 — all 7 implemented
# ══════════════════════════════════════════
def node_load_arc_tasks(state: OrchestratorState) -> OrchestratorState:
    if state.get("chapter_task_queue"):
        return state
    if not _budget_ok(state):
        log("🚨 预算已达硬停上限，系统暂停", state)
        state["current_phase"] = "budget_paused"
        state["human_pending"] = state.get("human_pending", []) + [{
            "task_id": "budget_exceeded",
            "task_type": "fix_chapter",
            "description": f"预算已用{state.get('budget_used_usd',0):.2f}/{state.get('budget_limit_usd',500):.0f}USD，请确认是否继续",
            "payload": {},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "priority": "must",
        }]
        save_state(state, str(STATE_PATH))
        return state

    setting   = _setting()
    arc_plans = state.get("arc_plans", [])
    arc_idx   = state.get("current_arc", 0)

    if arc_idx >= len(arc_plans):
        state["current_phase"] = "done"
        return state

    arc    = arc_plans[arc_idx]
    memory = get_l2(state.get("novel_id", "default"))
    start  = state.get("current_chapter", 0) + 1

    # P3 大纲模式路由：batch/card/talk 走不同 outline 变体
    # env NOVEL_OUTLINE_MODE 由 backend/app/api/bridge.py 注入
    outline_mode = os.environ.get("NOVEL_OUTLINE_MODE", "batch").lower()
    log(f"📋 拆解弧{arc.get('arc_id', arc_idx+1)}「{arc.get('arc_name','')}」[mode={outline_mode}]", state)

    tasks: list = []
    try:
        if outline_mode == "card":
            # card 模式：抽卡探索 — 生成 3 个候选分支让作者挑
            candidates, cost = run_outline_card(arc, start, setting, memory)
            _add_cost(state, cost)
            # 把所有候选展开成 chapter_task_queue，第一个候选被默认采纳；
            # 其余两个作为 human_pending 推给前端做"三选一"
            tasks = candidates[0]["tasks"] if candidates else \
                    [_placeholder_task(arc_idx, i, arc) for i in range(10)]
            state.setdefault("outline_candidates", []).append({
                "arc_id": arc.get("arc_id", arc_idx+1),
                "arc_name": arc.get("arc_name", ""),
                "candidates": candidates,
            })
            log(f"  🎴 生成 {len(candidates)} 个候选分支（card 模式）", state)
        elif outline_mode == "talk":
            # talk 模式：交互头脑风暴 — 先输出 1 个大纲 + 一些"分歧点"等作者回应
            result, cost = run_outline_talk(arc, start, setting, memory)
            _add_cost(state, cost)
            tasks = result.get("tasks", [_placeholder_task(arc_idx, i, arc) for i in range(10)])
            state.setdefault("talk_questions", []).extend(result.get("questions", []))
            log(f"  💬 生成大纲 + {len(result.get('questions', []))} 个待讨论点（talk 模式）", state)
        else:
            # batch 默认：传统批量
            tasks, cost = run_outline(arc, start, setting, memory)
            _add_cost(state, cost)
    except Exception as e:
        # 之前：兜底 10 个 placeholder task，engine 继续跑但全是占位——
        # 比"fake PASS 章节"更隐蔽：用户看到的 chapter 数字在动，
        # 但所有内容都是 placeholder 模板。
        # 改为：标记 _outline_failed=True，run_orchestrator 检测后停。
        log(f"ERR outline failed: {e}", state)
        state["error_log"] = (state.get("error_log", []) +
                              [f"outline failed arc{arc_idx}: {e}"])
        state["_outline_failed"] = True
        return state
    # 注意：每个分支内部已调 _add_cost(state, cost)，
    # 之前这里多调一次导致 outline 费用被计 2 倍（多弧叠加后 budget_used 虚高）。
    # P5 fix：删掉这行重复。

    state["chapter_task_queue"]      = tasks
    state["total_chapters_planned"]  = state.get("total_chapters_planned", 0) + len(tasks)

    # 草稿模式 override：NOVEL_AUDIT_MODE=draft 时把所有任务的 audit_mode 改成 "draft"。
    # 个人试错用：跳过 compliance + checker，每章只跑 writer → normalizer → tracker，
    # 节省 ~70% LLM 成本。试完方向后设回 NOVEL_AUDIT_MODE=full 走完整 run 定稿。
    if os.environ.get("NOVEL_AUDIT_MODE", "").lower() == "draft":
        for t in tasks:
            t["audit_mode"] = "draft"
        log(f"  📝 草稿模式覆盖：{len(tasks)} 个任务全部 audit_mode=draft", state)
    else:
        # 审计 P3：基于已收集的 quality_history / consecutive_low_score
        # 自适应决定本 arc 的 audit_mode：
        #   - 连续 LOW_GOOD_RUN 章 ≥ GOOD_SCORE → 该 arc 后续自动降级 lite
        #     （节省每章 2 个交叉评 LLM 调用）
        #   - 连续 LOW_BAD_RUN 章 < BAD_SCORE → 强制 full（甚至不允许降级）
        #   - 中间档 → 保持 task 原始 audit_mode
        # 不写到 env（避免污染测试），只覆盖 task 字段。
        adaptive_mode = _decide_adaptive_audit_mode(
            state.get("quality_history", []),
            state.get("consecutive_low_score", 0),
        )
        if adaptive_mode:
            for t in tasks:
                t["audit_mode"] = adaptive_mode
            log(f"  🎯 自适应审核覆盖（{adaptive_mode}）："
                f"history_tail={state.get('quality_history', [])[-10:]} "
                f"consecutive_low={state.get('consecutive_low_score', 0)}",
                state)

    # Save task sheet
    out_path = OUTPUT_DIR / f"arc_{arc.get('arc_id', arc_idx+1)}_tasks.json"
    # 迭代 #43: arc_N_tasks.json 是 chapter_task_queue 的磁盘镜像，
    # 半写损坏 → 下次 run 拿到 corrupted JSON → 整次 run 启动失败。
    # 改用 atomic_write_json（跟 save_state 同模式）。
    atomic_write_json(str(out_path), tasks)

    if arc_idx > 0:
        state["human_pending"] = state.get("human_pending", []) + [{
            "task_id": f"arc_{arc.get('arc_id', arc_idx+1)}_confirm",
            "task_type": "confirm_arc",
            "description": f"弧{arc.get('arc_id', arc_idx+1)}「{arc.get('arc_name','')}」{len(tasks)}章任务单已生成，建议审阅",
            "payload": {"arc": arc, "task_count": len(tasks)},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "priority": "recommended",
        }]
    save_state(state, str(STATE_PATH))
    return state


# 审计 P3：自适应审核阈值。连续 LOW_GOOD_RUN 章分数 ≥ GOOD_SCORE →
# 后续章节自动降级 lite（每章省 2 个交叉评 LLM 调用）；连续 LOW_BAD_RUN
# 章 < BAD_SCORE → 强制 full（不允许降级）。中间档保持原 audit_mode。
LOW_GOOD_RUN = 5      # 连续 5 章高分才能触发降级（避免早期数据不稳误判）
LOW_BAD_RUN  = 2      # 连续 2 章低分立刻触发 full
GOOD_SCORE   = 7.5    # 番茄网文口径"中上"门槛
BAD_SCORE    = 5.5    # 低于此为质量压力信号


def _decide_adaptive_audit_mode(
    quality_history: list[float],
    consecutive_low_score: int,
) -> str | None:
    """审计 P3：基于 quality_history / consecutive_low_score 自适应选 audit_mode。

    返回值：
      - "lite"  → 该 arc 所有任务用 lite（单模型评，省 2 个交叉评）
      - "full"  → 强制 full（不允许降级）
      - None    → 保持 task 原始 audit_mode

    规则（按优先级）：
      1. 连续 LOW_BAD_RUN 章 < BAD_SCORE → "full"（质量压力，加严）
      2. 最近 LOW_GOOD_RUN 章分数全部 ≥ GOOD_SCORE → "lite"（稳定高分，省钱）
      3. 否则 → None（保持原始 audit_mode）
    """
    if consecutive_low_score >= LOW_BAD_RUN:
        return "full"

    if len(quality_history) >= LOW_GOOD_RUN:
        tail = quality_history[-LOW_GOOD_RUN:]
        if all(s >= GOOD_SCORE for s in tail):
            return "lite"
    return None


def _placeholder_task(arc_idx: int, i: int, arc: dict) -> dict:
    """Minimal ChapterTask used when outline agent is a stub."""
    return {
        "chapter_number": arc_idx * 30 + i + 1,
        "chapter_role":   "发展",
        "chapter_goal":   f"第{i+1}章：推进剧情",
        "main_characters": ["主角"],
        "shuang_type":    None,
        "shuang_description": "",
        "ending_hook_type":       "信息钩",
        "ending_hook_description": "下一章揭示",
        "setting_constraints": [],
        "forbidden_actions": [],
        "target_length": "2000-2200",
        "audit_mode":    "full",
        "is_arc_climax": False,
    }


def node_get_next_task(state: OrchestratorState) -> OrchestratorState:
    queue = state.get("chapter_task_queue", [])
    if not queue:
        return state
    task = queue.pop(0)
    state["chapter_task_queue"]    = queue
    state["current_task"]          = task
    state["current_chapter"]       = task["chapter_number"]
    state["rewrite_count_current"] = 0
    state["current_phase"]         = "writing"
    log(f"▶  [{task.get('chapter_role','')}] {task.get('chapter_goal','')[:50]}", state)
    return state


def node_write_pipeline(state: OrchestratorState) -> OrchestratorState:
    task    = state["current_task"]
    setting = _setting()
    setting = {**setting, "novel_id": state.get("novel_id", "default")}

    log("  ✍️  Writer生成中...", state)
    try:
        raw_text, cost = run_writer(task, {}, setting)
        _add_cost(state, cost)
    except Exception as e:
        # 之前：写 "[writer-stub] {goal}" 占位并继续 pipeline → checker 给
        # 占位文本打 7.0 分 PASS，save_and_track 落盘假章节（ch_0064 bug）。
        # 现在：raw_text 留空、task._writer_failed=True，route_after_pipeline
        # 会路由到 escalate 而不是 save，下游不会再处理。
        log(f"ERR writer failed: {e}", state)
        state["error_log"] = (state.get("error_log", []) +
                              [f"writer failed ch{task['chapter_number']}: {e}"])
        task["_writer_failed"] = True
        task["_draft_text"]    = ""
        state["current_task"]  = task
        return state

    log("  🔧 Normalizer处理...", state)
    try:
        clean_text, fmt_issues, cost = run_normalizer(raw_text, task)
    except Exception as e:
        log(f"ERR normalizer failed: {e}", state)
        clean_text, fmt_issues, cost = raw_text, [], 0.0
    _add_cost(state, cost)

    # 草稿模式（个人试错）：跳过 compliance + checker，只保留 writer+normalizer+tracker。
    # 设计动机：用户在试不同开篇 / 调试 prompt 时，不想为每章花 6-9 次 LLM 调用的全
    # 套质检成本；选定方向后再切回 full 模式走完整流程定稿。
    # 详见 audit_mode='draft' 文档注释。
    audit_mode = task.get("audit_mode", "full")
    # personal 平台：跳过平台合规（番茄规则这类硬约束）；checker 仍跑
    # （结构一致性还是要的）。区别于 draft（草稿全跳）。
    current_platform = state.get("platform", "fanqie")
    personal_platform = current_platform in ("personal", "none", "internal")
    if personal_platform and audit_mode != "draft":
        log(f"  🌱 personal 平台：跳过平台合规（checker 仍跑）[platform={current_platform}]", state)

    if audit_mode == "draft":
        log("  📝 草稿模式：跳过合规 + 质检（writer+normalizer+tracker 链路）", state)
        task["_draft_text"] = clean_text
        # 合成一个 PASS 占位 _checker_result（save_and_track 不需要真评）
        task["_checker_result"] = {
            "score": 6.5,
            "verdict": "DRAFT",
            "dimensions": {},
            "rewrite_level": "none",
            "feedback": "draft mode 跳过质检",
            "strongest_point": "",
            "weakest_point": "",
        }
        task["_compliance_failed"] = False
        state["current_task"] = task
        qh = state.get("quality_history", [])
        qh.append(6.5)
        state["quality_history"] = qh[-100:]
        return state

    # personal 平台：跳 compliance，但走 checker。
    log("  🛡️  合规检查...", state)
    if not personal_platform:
        try:
            comp_result, cost = run_compliance(clean_text, state.get("platform", "fanqie"))
            _add_cost(state, cost)
        except Exception as e:
            log(f"ERR compliance failed: {e}", state)
            state["error_log"] = (state.get("error_log", []) +
                                  [f"compliance failed ch{task['chapter_number']}: {e}"])
            task["_compliance_check_failed"] = True
            task["_draft_text"]              = clean_text
            state["current_task"]            = task
            return state

        if not comp_result.get("passed", True):
            log(f"  ❌ 合规失败", state)
            task["_compliance_failed"]   = True
            task["_compliance_feedback"] = comp_result.get("suggestion", "")
            task["_draft_text"]          = clean_text
            state["current_task"]        = task
            return state
    else:
        log("  ⏭  跳过合规检查（personal 平台）", state)

    log(f"  🔍 质检（{audit_mode}）...", state)
    try:
        checker_result, cost = run_checker(clean_text, task, audit_mode)
        _add_cost(state, cost)
    except Exception as e:
        # 之前：兜底 score=7.0 / verdict=PASS——任何 checker 失败都假 PASS。
        # 改为：标记 _checker_failed=True，route_after_pipeline 路由到 escalate。
        log(f"ERR checker failed: {e}", state)
        state["error_log"] = (state.get("error_log", []) +
                              [f"checker failed ch{task['chapter_number']}: {e}"])
        task["_checker_failed"] = True
        task["_draft_text"]     = clean_text
        state["current_task"]   = task
        return state

    score = checker_result.get("score", 0)
    log(f"  📊 {score:.1f}分 | {checker_result.get('verdict','')}", state)

    task["_draft_text"]        = clean_text
    task["_checker_result"]    = checker_result
    task["_compliance_failed"] = False
    state["current_task"]      = task

    qh = state.get("quality_history", [])
    qh.append(score)
    state["quality_history"] = qh[-100:]
    state["consecutive_low_score"] = (state.get("consecutive_low_score", 0) + 1 if score < PASS_SCORE else 0)
    return state


def node_rewrite(state: OrchestratorState) -> OrchestratorState:
    task         = state["current_task"]
    setting      = {**_setting(), "novel_id": state.get("novel_id", "default")}
    memory       = get_l2(state.get("novel_id", "default"))
    failed_comp  = task.get("_compliance_failed", False)
    cr           = task.get("_checker_result", {})
    draft_text   = task.get("_draft_text", "")
    feedback     = (task.get("_compliance_feedback", "违规内容需重写") if failed_comp
                    else cr.get("feedback", ""))
    rewrite_lvl  = "P1" if failed_comp else cr.get("rewrite_level", "P1")

    state["rewrite_count_current"] = state.get("rewrite_count_current", 0) + 1
    log(f"  ♻️  第{state['rewrite_count_current']}次重写（{rewrite_lvl}）", state)

    try:
        new_text, cost = run_rewriter(draft_text, rewrite_lvl, feedback, task, cr, memory, setting)
        _add_cost(state, cost)
    except Exception as e:
        # 之前：new_text = draft_text（重写失败时用原文本当重写结果——
        # 实际上没重写，但 state 显示重写完成，rewrite_count++ 误导用户）。
        # 改为：标记 _rewriter_failed=True，task 标 _checker_failed 复用同路径 escalate。
        log(f"ERR rewriter failed: {e}", state)
        state["error_log"] = (state.get("error_log", []) +
                              [f"rewriter failed ch{task['chapter_number']}: {e}"])
        task["_rewriter_failed"] = True
        task["_checker_failed"] = True  # 让 route_after_rewrite 走 escalate
        task["_draft_text"]    = draft_text
        state["current_task"]  = task
        return state

    try:
        clean_text, _, cost = run_normalizer(new_text, task)
        _add_cost(state, cost)
    except Exception as e:
        log(f"ERR normalizer (post-rewrite) failed: {e}", state)
        # normalizer 失败但 rewriter 成功 → 退到 raw new_text，不丢重写结果
        clean_text, cost = new_text, 0.0

    # Re-verify compliance — 同 write_pipeline：personal 平台跳过
    rewrite_platform = state.get("platform", "fanqie")
    rewrite_personal = rewrite_platform in ("personal", "none", "internal")
    if not rewrite_personal:
        try:
            comp_result, cost = run_compliance(clean_text, rewrite_platform)
        except Exception as e:
            log(f"ERR compliance (post-rewrite) failed: {e}", state)
            state["error_log"] = (state.get("error_log", []) +
                                  [f"compliance (post-rewrite) failed ch{task['chapter_number']}: {e}"])
            task["_compliance_check_failed"] = True
            task["_draft_text"]              = clean_text
            state["current_task"]            = task
            return state
        _add_cost(state, cost)
        if not comp_result.get("passed", True):
            log(f"  🛡️  重写后仍违规", state)
            task["_draft_text"]          = clean_text
            task["_compliance_failed"]   = True
            task["_compliance_feedback"] = comp_result.get("reason", "违规内容需重写")
            state["current_task"]        = task
            return state
    else:
        log("  ⏭  跳过重写后合规检查（personal 平台）", state)

    try:
        cr2, cost = run_checker(clean_text, task, "lite")
        _add_cost(state, cost)
    except Exception as e:
        # 之前：cr2 = cr（用上次 checker 结果当这次结果——重写后没真的评分，
        # 但显示"重写后分数"，rewrite 循环可能基于错误的分数继续）。
        # 改为：标记 _checker_failed=True，让 route_after_rewrite 走 escalate。
        log(f"ERR checker (post-rewrite) failed: {e}", state)
        state["error_log"] = (state.get("error_log", []) +
                              [f"checker (post-rewrite) failed ch{task['chapter_number']}: {e}"])
        task["_checker_failed"] = True
        task["_draft_text"]     = clean_text
        state["current_task"]   = task
        return state
    log(f"  📊 重写后：{cr2.get('score',0):.1f}分", state)

    task["_draft_text"]        = clean_text
    task["_checker_result"]    = cr2
    task["_compliance_failed"] = False
    state["current_task"]      = task

    qh = state.get("quality_history", [])
    qh.append(cr2.get("score", 0))
    state["quality_history"] = qh[-100:]
    return state


def node_save_and_track(state: OrchestratorState) -> OrchestratorState:
    task   = state["current_task"]
    text   = task.get("_draft_text", "")
    cr     = task.get("_checker_result", {})
    memory = get_l2(state.get("novel_id", "default"))

    meta = {
        "chapter_number": task["chapter_number"],
        "chapter_role":   task.get("chapter_role", ""),
        "chapter_goal":   task.get("chapter_goal", ""),
        "score":          cr.get("score", 0),
        "verdict":        cr.get("verdict", ""),
        "dimensions":     cr.get("dimensions", {}),
        "rewrite_count":  state.get("rewrite_count_current", 0),
        "word_count":     len(text),
    }
    save_chapter(state.get("novel_id", "default"), task["chapter_number"], text, meta)
    log(f"  💾 已保存（{len(text)}字，{cr.get('score',0):.1f}分）", state)

    try:
        updated_mem, cost = run_tracker(text, task, memory, state.get("novel_id", "default"))
    except Exception as e:
        # 迭代 #58: 之前 except Exception 静默兜底 updated_mem=memory, cost=0
        # —— tracker LLM 调用失败时 memory 不更新、cost 不记，
        # 下一章 tracker 又从老 memory 接着跑，但用户看不到任何「tracker
        # 连续失败」的信号（跟 fake-pass 同型风险）。
        # 修法：log error_log 标 _tracker_failed=True + updated_mem
        # 仍用旧 memory（保持连续性）+ 标 task._tracker_failed 让
        # 后续 summarizer / 报告能看到。cost=0.0 保持（LLM 没真调用）。
        log(f"ERR tracker failed: {e}", state)
        state["error_log"] = (state.get("error_log", []) +
                              [f"tracker failed ch{task['chapter_number']}: {e}"])
        task["_tracker_failed"] = True
        updated_mem, cost = memory, 0.0
    _add_cost(state, cost)

    # Arc end check
    if not state.get("chapter_task_queue"):
        arc_plans = state.get("arc_plans", [])
        arc_idx   = state.get("current_arc", 0)
        log(f"🏁 弧{arc_idx+1}完成，触发Summarizer", state)
        if arc_idx < len(arc_plans):
            try:
                _, cost = run_summarizer("arc_end", arc_plans[arc_idx], updated_mem, state.get("novel_id", "default"))
            except Exception as e:
                # 迭代 #60: 跟 #58 同型 — 之前 silent fallback cost=0.0
                # 没有 _summarizer_failed 标记，下一弧还是基于老 L5 接着跑。
                log(f"ERR summarizer failed: {e}", state)
                state["error_log"] = (state.get("error_log", []) +
                                      [f"summarizer failed arc{arc_idx+1}: {e}"])
                arc_plans[arc_idx]["_summarizer_failed"] = True
                cost = 0.0
            _add_cost(state, cost)
        state["current_arc"] = arc_idx + 1

    # Budget warning
    used  = state.get("budget_used_usd", 0.0)
    limit = state.get("budget_limit_usd", 500.0)
    if used >= limit * BUDGET_WARN and int(used / (limit * 0.01)) % 5 == 0:
        log(f"  💰 预算已用{used/limit:.0%}（${used:.2f}/${limit:.0f}）", state)

    save_state(state, str(STATE_PATH))
    return state


def node_human_escalation(state: OrchestratorState) -> OrchestratorState:
    """人工介入节点：保存带 [待修订] 前缀的章节 + 更新 L2 记忆（审计 P1）。

    历史问题：escalation 不调 run_tracker → L2 记忆缺失这一章。
    在 100+ 章长篇里越往后漂移越严重——后续章节按"跳过了这一章"写。

    修法（审计 P1，选项 b——保留"全自动"）：用现有 draft_text 给
    run_tracker 喂一份「近似 track」，让 L2 至少反映这一章原本打算
    发生什么。同时打 memory_gap 标记，下游 summarizer / arc_end 报告
    能看到这个缺口，便于人工回头处理时定位。
    """
    task = state["current_task"]
    cr   = task.get("_checker_result", {})
    log(f"  🚨 超过{MAX_REWRITE}次重写，需人工介入", state)
    state["human_pending"] = state.get("human_pending", []) + [{
        "task_id":     f"fix_ch_{task['chapter_number']}",
        "task_type":   "fix_chapter",
        "description": f"第{task['chapter_number']}章重写{MAX_REWRITE}次仍不达标({cr.get('score',0):.1f}分)",
        "payload": {
            "chapter_number": task["chapter_number"],
            "last_score":     cr.get("score", 0),
            "weakest_point":  cr.get("weakest_point", ""),
            "feedback":       cr.get("feedback", ""),
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "priority": "must",
    }]
    text = task.get("_draft_text", "")
    save_chapter(state.get("novel_id", "default"), task["chapter_number"],
                 f"[待修订]\n{text}", {
        "chapter_number": task["chapter_number"],
        "status":         "human_required",
        "score":          cr.get("score", 0),
        "word_count":     len(text),
        "memory_gap":     True,   # 审计 P1：标缺口便于 observability
    })

    # 审计 P1：即使 escalate，也喂一份 draft_text 给 run_tracker
    # 让 L2 记忆反映这一章「原本要发生什么」（人物状态 / 剧情线 / 伏笔）。
    # 这是「保留全自动 + 缩小一致性漂移」的折中——比完全跳过好很多。
    # 用 try/except 跟 node_save_and_track 同样模式：tracker 失败不阻塞
    # escalation（人工介入本身就是兜底，不该再因 tracker 失败阻塞）。
    if text:
        try:
            memory = get_l2(state.get("novel_id", "default"))
            _updated_mem, _cost = run_tracker(
                text, task, memory, state.get("novel_id", "default")
            )
            _add_cost(state, _cost)
            log(f"  📌 escalation 记忆已兜底（用 draft_text 喂 tracker）", state)
        except Exception as e:
            log(f"  ⚠️  escalation tracker 失败（不阻塞 escalate）: {e}", state)
            state["error_log"] = (state.get("error_log", []) +
                                  [f"escalation tracker failed ch{task['chapter_number']}: {e}"])

    # 审计 P3：escalation 也算"连续低分"信号——把这一章标进 consecutive_low_score
    # 让下游自适应审核能感知到「arc 里出现 escalation 是质量压力信号」。
    state["consecutive_low_score"] = (
        state.get("consecutive_low_score", 0) + 1
    )
    # memory_gap 也写到 state 级，便于 arc_end 报告 / 测试断言
    state["memory_gaps"] = (state.get("memory_gaps", []) +
                            [{"chapter": task["chapter_number"],
                              "reason":  "human_escalation",
                              "score":   cr.get("score", 0)}])

    save_state(state, str(STATE_PATH))
    return state


# ══════════════════════════════════════════
# 路由
# ══════════════════════════════════════════
def route_after_pipeline(state) -> Literal["save", "rewrite", "escalate", "budget_stop"]:
    if state.get("current_phase") in ("done", "budget_paused"):
        return "save"
    task = state.get("current_task", {})
    # 任一 pipeline 阶段异常 → 直接 escalate，不进入 save
    # 之前这些异常会被"fake pass 默认值"吞掉（ch_0064 同型问题）。
    if task.get("_writer_failed"):
        return "escalate"
    if task.get("_compliance_check_failed"):
        return "escalate"
    if task.get("_checker_failed"):
        return "escalate"
    score = task.get("_checker_result", {}).get("score", 0) if task.get("_checker_result") else 0
    rw = state.get("rewrite_count_current", 0)
    if task.get("_compliance_failed"):
        return "escalate" if rw >= MAX_REWRITE else "rewrite"
    if score >= PASS_SCORE:
        return "save"
    return "escalate" if rw >= MAX_REWRITE else "rewrite"


def route_after_rewrite(state) -> Literal["save", "rewrite", "escalate"]:
    task  = state.get("current_task", {})
    # 任一阶段异常 → 直接 escalate（不进入 save）
    # 跟 route_after_pipeline 同型防御：compliance check (post-rewrite) 失败时
    # _compliance_check_failed=True，_checker_result 是 pre-rewrite 旧值，保留它
    # 可能误判"重写成功"→ 显式 escalate。
    if task.get("_writer_failed"):
        return "escalate"
    if task.get("_compliance_check_failed"):
        return "escalate"
    if task.get("_checker_failed"):
        return "escalate"
    score = task.get("_checker_result", {}).get("score", 0) if task.get("_checker_result") else 0
    rw    = state.get("rewrite_count_current", 0)
    if score >= PASS_SCORE:
        return "save"
    return "escalate" if rw >= MAX_REWRITE else "rewrite"


def route_after_save(state) -> Literal["next_task", "done"]:
    if state.get("current_phase") in ("done", "budget_paused"):
        return "done"
    if (not state.get("chapter_task_queue")
        and state.get("current_arc", 0) >= len(state.get("arc_plans", []))):
        return "done"
    return "next_task"


# ══════════════════════════════════════════
# 构建图
# ══════════════════════════════════════════
def build_graph(checkpointer=None):
    g = StateGraph(OrchestratorState)  # type: ignore
    g.add_node("load_arc_tasks",   node_load_arc_tasks)
    g.add_node("get_next_task",    node_get_next_task)
    g.add_node("write_pipeline",   node_write_pipeline)
    g.add_node("rewrite",          node_rewrite)
    g.add_node("save_and_track",   node_save_and_track)
    g.add_node("human_escalation", node_human_escalation)
    g.set_entry_point("load_arc_tasks")
    g.add_edge("load_arc_tasks", "get_next_task")
    g.add_edge("get_next_task",  "write_pipeline")
    g.add_conditional_edges("write_pipeline", route_after_pipeline,
        {"save": "save_and_track", "rewrite": "rewrite",
         "escalate": "human_escalation", "budget_stop": END})
    g.add_conditional_edges("rewrite", route_after_rewrite,
        {"save": "save_and_track", "rewrite": "rewrite", "escalate": "human_escalation"})
    g.add_conditional_edges("save_and_track", route_after_save,
        {"next_task": "load_arc_tasks", "done": END})
    # P3 fix: human_escalation 后回到 load_arc_tasks 继续下一章（不再 END）。
    # 与 engine/graph.py 的 build_project_graph 保持一致（之前两边不同步，
    # run/resume 走的是这里的图，导致章节触发人工介入时整次 run 静默终止）。
    g.add_edge("human_escalation", "load_arc_tasks")
    if checkpointer is None:
        return g.compile()
    return g.compile(checkpointer=checkpointer)


# ══════════════════════════════════════════
# 对外接口（与原 novel_AI/orchestrator.py 签名完全一致）
# ══════════════════════════════════════════
def run_orchestrator(state: OrchestratorState, max_chapters: int = 10) -> OrchestratorState:
    app = build_graph()
    chapters_done = 0
    print(f"\n{'='*60}")
    print(f"🚀 Orchestrator | 目标{max_chapters}章 | 起始Ch{state.get('current_chapter',0)+1}")
    print(f"   {state.get('novel_id')} | 预算${state.get('budget_used_usd',0):.2f}/${state.get('budget_limit_usd',500):.0f}")
    print(f"{'='*60}\n")
    # graph 用 checkpointer 编译 → 必须传 config.configurable.thread_id
    # 否则 LangGraph 报 "Checkpointer requires one or more of the following
    # 'configurable' keys: thread_id, ..." → exit_code=1（你独立验证）
    thread_id = state.get("novel_id") or "default"
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 2500}
    for event in app.stream(state, config):
        node_name = list(event.keys())[0]
        new_state = event[node_name]
        # outline 失败时立即停（不跑出 10 个 placeholder 章节）
        if new_state.get("_outline_failed"):
            print("\n❌ outline 失败，run 终止（避免跑出 placeholder 章节）")
            save_state(new_state, str(STATE_PATH))
            return new_state
        if node_name == "save_and_track":
            chapters_done += 1
            print(f"\n✅ [{chapters_done}/{max_chapters}] Ch{new_state.get('current_chapter',0)} "
                  f"完成 | ${new_state.get('budget_used_usd',0):.4f}\n")
            if chapters_done >= max_chapters:
                print(f"⏸  已完成{max_chapters}章，暂停。")
                save_state(new_state, str(STATE_PATH))
                return new_state
        if node_name == "human_escalation":
            # 历史背景（独立 AI 深度审查发现的 bug，commit 待定）：
            #   之前 g.add_edge("human_escalation", END) → 一旦某章触发
            #   人工介入，整次 run 静默提前结束（即便 chapters_done < max_chapters）。
            #   报告：orchestrator.py:573 与 graph.py:290 拓扑不一致 —
            #   graph.py 是 human_escalation → load_arc_tasks（继续下一章），
            #   orchestrator.py 是 human_escalation → END（提前终止）。
            #
            # 修法：把边改成 load_arc_tasks（与 graph.py 一致），
            # 图自己循环，重新生成本章及后续章节。
            pending = new_state.get("human_pending", [])
            print(f"\n⚠  第{new_state.get('current_chapter',0)}章触发人工介入（共 {len(pending)} 待处理），继续下一章")
            for t in pending[-3:]:
                print(f"   [{t.get('priority','?')}] {t['description']}")
            save_state(new_state, str(STATE_PATH))
            state = new_state
            continue
        state = new_state
    save_state(state, str(STATE_PATH))
    return state
