"""
AI网文创作系统 V3 — LangGraph Orchestrator V2
集成：memory_manager / fingerprint_checker / style切换 / 预算硬停
"""
import json, os, sys, time
from typing import Literal
from langgraph.graph import StateGraph, END

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from orchestrator_state import OrchestratorState, save_state, load_state
from agents.writer_agent     import run_writer
from agents.normalizer_agent import run_normalizer
from agents.compliance_agent import run_compliance
from agents.checker_agent    import run_checker
from agents.rewriter_agent   import run_rewriter
from agents.tracker_agent    import run_tracker
from agents.outline_agent    import run_outline
from agents.summarizer_agent import run_summarizer
from memory.memory_manager   import get_l2, get_writer_context, maybe_update_style_samples

BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR   = os.path.join(BASE_DIR, "output")
CHAPTERS_DIR = os.path.join(OUTPUT_DIR, "chapters")
STATE_PATH   = os.path.join(OUTPUT_DIR, "orchestrator_state.json")

MAX_REWRITE   = 3
PASS_SCORE    = 6.5
BUDGET_WARN   = 0.80   # 80%发警告
BUDGET_HARD   = 0.95   # 95%硬停

# 模块级缓存（避免每章重复解析JSON）
_setting_cache = None

def _setting():
    global _setting_cache
    if _setting_cache is None:
        with open(os.path.join(OUTPUT_DIR, "setting_package.json"), encoding="utf-8") as f:
            _setting_cache = json.load(f)
    return _setting_cache

def _config():
    with open(os.path.join(BASE_DIR, "config", "novel_config.json"), encoding="utf-8") as f:
        return json.load(f)

def save_chapter(novel_id, ch_num, text, meta):
    os.makedirs(CHAPTERS_DIR, exist_ok=True)
    with open(os.path.join(CHAPTERS_DIR, f"ch_{ch_num:04d}.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(CHAPTERS_DIR, f"ch_{ch_num:04d}_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

def log(msg, state):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] Ch{state.get('current_chapter',0):04d} | {msg}"
    print(line)
    if "ERR" in msg or "FAIL" in msg:
        el = state.get("error_log", [])
        el.append(line)
        state["error_log"] = el[-100:]

def _add_cost(state, cost):
    state["budget_used_usd"] = state.get("budget_used_usd", 0) + cost

def _budget_ok(state) -> bool:
    used  = state.get("budget_used_usd", 0)
    limit = state.get("budget_limit_usd", 500)
    return used < limit * BUDGET_HARD


# ══════════════════════════════════════════
# 节点
# ══════════════════════════════════════════
def node_load_arc_tasks(state: OrchestratorState) -> OrchestratorState:
    if state["chapter_task_queue"]:
        return state
    if not _budget_ok(state):
        log("🚨 预算已达95%上限，系统暂停", state)
        state["current_phase"] = "budget_paused"
        state["human_pending"] = state.get("human_pending", []) + [{
            "task_id": "budget_exceeded",
            "task_type": "fix_chapter",
            "description": f"预算已用{state.get('budget_used_usd',0):.2f}/{state.get('budget_limit_usd',500):.0f}USD，请确认是否继续",
            "payload": {},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "priority": "must",
        }]
        save_state(state, STATE_PATH)
        return state

    setting    = _setting()
    arc_plans  = state.get("arc_plans", [])
    arc_idx    = state["current_arc"]

    if arc_idx >= len(arc_plans):
        state["current_phase"] = "done"
        return state

    arc    = arc_plans[arc_idx]
    memory = get_l2(state["novel_id"])
    start  = state["current_chapter"] + 1

    log(f"📋 拆解弧{arc['arc_id']}「{arc['arc_name']}」", state)
    tasks, cost = run_outline(arc, start, setting, memory)
    _add_cost(state, cost)

    state["chapter_task_queue"]   = tasks
    state["total_chapters_planned"] = state.get("total_chapters_planned", 0) + len(tasks)

    # 保存任务单文件
    out_path = os.path.join(OUTPUT_DIR, f"arc_{arc['arc_id']}_tasks.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

    # 非第一弧时加入人工确认队列（recommended级）
    if arc_idx > 0:
        state["human_pending"] = state.get("human_pending", []) + [{
            "task_id": f"arc_{arc['arc_id']}_confirm",
            "task_type": "confirm_arc",
            "description": f"弧{arc['arc_id']}「{arc['arc_name']}」{len(tasks)}章任务单已生成，建议审阅",
            "payload": {"arc": arc, "task_count": len(tasks)},
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "priority": "recommended",
        }]
    save_state(state, STATE_PATH)
    return state


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
    log(f"▶  [{task['chapter_role']}] {task['chapter_goal'][:50]}", state)
    return state


def node_write_pipeline(state: OrchestratorState) -> OrchestratorState:
    task    = state["current_task"]
    setting = _setting()
    # 传递setting（writer_agent内部自行调用memory_manager）
    setting["novel_id"] = state.get("novel_id", "renqingzhai_v1")

    log("  ✍️  Writer生成中...", state)
    raw_text, cost = run_writer(task, {}, setting)
    _add_cost(state, cost)

    log("  🔧 Normalizer处理...", state)
    clean_text, fmt_issues, cost = run_normalizer(raw_text, task)
    _add_cost(state, cost)

    # 文风指纹检测（不阻断流程，只记录）
    try:
        from tools.fingerprint_checker import run_fingerprint_check
        fp_result = run_fingerprint_check(clean_text, task, setting)
        if not fp_result["overall_pass"]:
            log(f"  📐 指纹检测：AI嫌疑{fp_result['fingerprint']['ai_score']}分", state)
            if fp_result["fingerprint"]["ai_score"] >= 60:
                # 高风险时追加一次Normalizer LLM处理
                from agents.normalizer_agent import second_pass_llm
                clean_text, cost2 = second_pass_llm(clean_text)
                _add_cost(state, cost2)
    except Exception:
        pass

    log("  🛡️  合规检查...", state)
    comp_result, cost = run_compliance(clean_text, state.get("platform", "fanqie"))
    _add_cost(state, cost)

    if not comp_result["passed"]:
        log(f"  ❌ 合规失败：{comp_result['hard_rejects'][:1]}", state)
        task["_compliance_failed"]   = True
        task["_compliance_feedback"] = comp_result.get("suggestion", "")
        task["_draft_text"]          = clean_text
        state["current_task"]        = task
        return state

    audit_mode = task.get("audit_mode", "full")
    log(f"  🔍 质检（{audit_mode}）...", state)
    checker_result, cost = run_checker(clean_text, task, audit_mode)
    _add_cost(state, cost)

    score = checker_result["score"]
    log(f"  📊 {score:.1f}分 | {checker_result['verdict']} | {checker_result.get('weakest_point','')[:40]}", state)

    task["_draft_text"]        = clean_text
    task["_checker_result"]    = checker_result
    task["_compliance_failed"] = False
    state["current_task"]      = task

    qh = state.get("quality_history", [])
    qh.append(score)
    state["quality_history"] = qh[-100:]
    state["consecutive_low_score"] = (state.get("consecutive_low_score",0)+1 if score < PASS_SCORE
                                      else 0)
    return state


def node_rewrite(state: OrchestratorState) -> OrchestratorState:
    task    = state["current_task"]
    setting = _setting()
    setting["novel_id"] = state.get("novel_id", "renqingzhai_v1")
    memory  = get_l2(state["novel_id"])

    compliance_failed = task.get("_compliance_failed", False)
    checker_result    = task.get("_checker_result", {})
    draft_text        = task.get("_draft_text", "")
    feedback          = (task.get("_compliance_feedback","违规内容需重写") if compliance_failed
                         else checker_result.get("feedback",""))
    rewrite_level     = "P1" if compliance_failed else checker_result.get("rewrite_level","P1")

    state["rewrite_count_current"] = state.get("rewrite_count_current", 0) + 1
    log(f"  ♻️  第{state['rewrite_count_current']}次重写（{rewrite_level}）", state)

    new_text, cost = run_rewriter(draft_text, rewrite_level, feedback, task, checker_result, memory, setting)
    _add_cost(state, cost)

    clean_text, _, cost = run_normalizer(new_text, task)
    _add_cost(state, cost)

    checker_result2, cost = run_checker(clean_text, task, "lite")
    _add_cost(state, cost)

    log(f"  📊 重写后：{checker_result2['score']:.1f}分", state)

    task["_draft_text"]        = clean_text
    task["_checker_result"]    = checker_result2
    task["_compliance_failed"] = False
    state["current_task"]      = task

    qh = state.get("quality_history", [])
    qh.append(checker_result2["score"])
    state["quality_history"] = qh[-100:]
    return state


def node_save_and_track(state: OrchestratorState) -> OrchestratorState:
    task   = state["current_task"]
    text   = task.get("_draft_text", "")
    cr     = task.get("_checker_result", {})
    memory = get_l2(state["novel_id"])

    meta = {
        "chapter_number": task["chapter_number"],
        "chapter_role":   task["chapter_role"],
        "chapter_goal":   task.get("chapter_goal",""),
        "score":          cr.get("score", 0),
        "verdict":        cr.get("verdict", ""),
        "dimensions":     cr.get("dimensions", {}),
        "rewrite_count":  state.get("rewrite_count_current", 0),
        "word_count":     len(text),
    }
    save_chapter(state["novel_id"], task["chapter_number"], text, meta)
    log(f"  💾 已保存（{len(text)}字，{cr.get('score',0):.1f}分）", state)

    updated_mem, cost = run_tracker(text, task, memory, state["novel_id"])
    _add_cost(state, cost)
    log(f"  📍 等级={updated_mem.get('hot',updated_mem).get('protagonist_level','')} "
        f"点数={updated_mem.get('hot',updated_mem).get('protagonist_points',0)}", state)

    # 弧结束检查
    if not state.get("chapter_task_queue"):
        arc_plans = state.get("arc_plans", [])
        arc_idx   = state["current_arc"]
        log(f"🏁 弧{arc_idx+1}完成，触发Summarizer", state)
        if arc_idx < len(arc_plans):
            _, cost = run_summarizer("arc_end", arc_plans[arc_idx], updated_mem, state["novel_id"])
            _add_cost(state, cost)
        state["current_arc"] = arc_idx + 1

    # 预算提醒
    used  = state.get("budget_used_usd", 0)
    limit = state.get("budget_limit_usd", 500)
    if used >= limit * BUDGET_WARN and int(used / (limit*0.01)) % 5 == 0:
        log(f"  💰 预算已用{used/limit:.0%}（${used:.2f}/${limit:.0f}）", state)

    save_state(state, STATE_PATH)
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
            "weakest_point":  cr.get("weakest_point",""),
            "feedback":       cr.get("feedback",""),
        },
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "priority": "must",
    }]
    text = task.get("_draft_text","")
    save_chapter(state["novel_id"], task["chapter_number"], f"[待修订]\n{text}", {
        "chapter_number": task["chapter_number"], "status": "human_required",
        "score": cr.get("score",0), "word_count": len(text),
    })
    save_state(state, STATE_PATH)
    return state


# ══════════════════════════════════════════
# 路由
# ══════════════════════════════════════════
def route_after_pipeline(state) -> Literal["save","rewrite","escalate","budget_stop"]:
    if state.get("current_phase") in ("done","budget_paused"): return "save"
    task = state.get("current_task", {})
    score = task.get("_checker_result", {}).get("score", 0) if task.get("_checker_result") else 0
    rw = state.get("rewrite_count_current", 0)
    if task.get("_compliance_failed"): return "escalate" if rw >= MAX_REWRITE else "rewrite"
    if score >= PASS_SCORE: return "save"
    return "escalate" if rw >= MAX_REWRITE else "rewrite"

def route_after_rewrite(state) -> Literal["save","rewrite","escalate"]:
    task  = state.get("current_task", {})
    score = task.get("_checker_result", {}).get("score", 0) if task.get("_checker_result") else 0
    rw    = state.get("rewrite_count_current", 0)
    if score >= PASS_SCORE: return "save"
    return "escalate" if rw >= MAX_REWRITE else "rewrite"

def route_after_save(state) -> Literal["next_task","done"]:
    if state.get("current_phase") in ("done","budget_paused"): return "done"
    if not state.get("chapter_task_queue") and state.get("current_arc",0) >= len(state.get("arc_plans",[])): return "done"
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
        {"save":"save_and_track","rewrite":"rewrite","escalate":"human_escalation","budget_stop":END})
    g.add_conditional_edges("rewrite", route_after_rewrite,
        {"save":"save_and_track","rewrite":"rewrite","escalate":"human_escalation"})
    g.add_conditional_edges("save_and_track", route_after_save,
        {"next_task":"load_arc_tasks","done":END})
    g.add_edge("human_escalation", END)
    return g.compile()


# ══════════════════════════════════════════
# 对外接口
# ══════════════════════════════════════════
def run_orchestrator(state: OrchestratorState, max_chapters: int = 10) -> OrchestratorState:
    app = build_graph()
    chapters_done = 0
    print(f"\n{'='*60}")
    print(f"🚀 Orchestrator | 目标{max_chapters}章 | 起始Ch{state.get('current_chapter',0)+1}")
    print(f"   {state.get('novel_id')} | 预算${state.get('budget_used_usd',0):.2f}/${state.get('budget_limit_usd',500):.0f}")
    print(f"{'='*60}\n")
    for event in app.stream(state, {"recursion_limit": 250}):
        node_name = list(event.keys())[0]
        new_state = event[node_name]
        if node_name == "save_and_track":
            chapters_done += 1
            print(f"\n✅ [{chapters_done}/{max_chapters}] Ch{new_state.get('current_chapter',0)} "
                  f"完成 | ${new_state.get('budget_used_usd',0):.4f}\n")
            if chapters_done >= max_chapters:
                print(f"⏸  已完成{max_chapters}章，暂停。")
                save_state(new_state, STATE_PATH)
                return new_state
        if node_name == "human_escalation":
            pending = new_state.get("human_pending", [])
            print(f"\n🚨 需要人工介入！{len(pending)}个待处理任务")
            for t in pending[-3:]:
                print(f"   [{t.get('priority','?')}] {t['description']}")
            save_state(new_state, STATE_PATH)
            return new_state
        state = new_state
    save_state(state, STATE_PATH)
    return state
