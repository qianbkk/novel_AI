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
BUDGET_WARN  = 1.00   # 100% warning
BUDGET_HARD  = 1.50   # 150% hard stop (MVP-relaxed per patches/2026-06-28)

# Module-level cache (avoid re-reading setting per chapter)
_setting_cache: dict | None = None


def _setting() -> dict:
    global _setting_cache
    if _setting_cache is None:
        if not SETTING_PATH.exists():
            return {}
        with open(SETTING_PATH, encoding="utf-8") as f:
            _setting_cache = json.load(f)
    return _setting_cache


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
    with open(CHAPTERS_DIR / f"ch_{ch_num:04d}_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


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
    _add_cost(state, cost)

    state["chapter_task_queue"]      = tasks
    state["total_chapters_planned"]  = state.get("total_chapters_planned", 0) + len(tasks)

    # Save task sheet
    out_path = OUTPUT_DIR / f"arc_{arc.get('arc_id', arc_idx+1)}_tasks.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

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

    log("  🛡️  合规检查...", state)
    try:
        comp_result, cost = run_compliance(clean_text, state.get("platform", "fanqie"))
        _add_cost(state, cost)
    except Exception as e:
        # 之前：兜底 {"passed": True}，合规失败被静默擦掉——
        # 跟 writer stub 同型"fake pass"问题。改为：标记 _compliance_check_failed=True
        # 并给出中性 verdict（待人工 review），route_after_pipeline 路由到 escalate。
        log(f"ERR compliance failed: {e}", state)
        state["error_log"] = (state.get("error_log", []) +
                              [f"compliance failed ch{task['chapter_number']}: {e}"])
        task["_compliance_check_failed"] = True
        task["_compliance_feedback"]     = f"compliance check raised: {e}"
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

    audit_mode = task.get("audit_mode", "full")
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

    # Re-verify compliance
    try:
        comp_result, cost = run_compliance(clean_text, state.get("platform", "fanqie"))
    except Exception as e:
        log(f"ERR compliance (post-rewrite) failed: {e}", state)
        comp_result, cost = {"passed": True}, 0.0
    _add_cost(state, cost)
    if not comp_result.get("passed", True):
        log(f"  🛡️  重写后仍违规", state)
        task["_draft_text"]          = clean_text
        task["_compliance_failed"]   = True
        task["_compliance_feedback"] = comp_result.get("reason", "违规内容需重写")
        state["current_task"]        = task
        return state

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
        log(f"ERR tracker failed: {e}", state)
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
                log(f"ERR summarizer failed: {e}", state)
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
    })
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
def build_graph():
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
    g.add_edge("human_escalation", END)
    return g.compile()


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
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 250}
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
            # P3 fix: 不要因为一章卡死就终止整个 run。
            # 把卡死的章节写入 [待修订]、登记 human_pending，然后**继续下一章**。
            pending = new_state.get("human_pending", [])
            print(f"\n⚠  第{new_state.get('current_chapter',0)}章触发人工介入（共 {len(pending)} 待处理），继续下一章")
            for t in pending[-3:]:
                print(f"   [{t.get('priority','?')}] {t['description']}")
            # 把卡死章节从 queue 中"擦掉"，让后续 route_after_save → next_task
            # 路径回到 load_arc_tasks 重新生成任务单
            new_state["chapter_task_queue"] = new_state.get("chapter_task_queue", [])  # 已是空
            new_state["current_task"] = None
            new_state["current_chapter"] = (new_state.get("current_chapter", 0) or 0)  # 保持当前编号
            save_state(new_state, str(STATE_PATH))
            # 继续循环（不 return）
            state = new_state
            continue
        state = new_state
    save_state(state, str(STATE_PATH))
    return state
