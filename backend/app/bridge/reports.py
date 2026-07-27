import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 迭代 #43: orchestrator_state.json（apply_review 等写入）之前直接 write_text
# 半写损坏 → 下次 pull_review / apply_review 失败。改用 atomic_write_json。
from shared.atomic_io import atomic_write_json  # 2026-07-25 抽离（修 P0 双向 import）

VALID_REVIEW_ACTIONS = {"accept", "reject", "edit"}


def _state_path(novel_ai_dir: str) -> Path:
    """解析 state 文件路径。

    历史背景（commit 08a8f02 / 62baf44）：
      engine 写到 NOVEL_AI_DIR env 路径（与 binding.novel_ai_dir 等价时是
      novel_AI/output/，否则是 backend/data/engine/output/）。
      reports.py 之前硬编码 novel_ai_dir/output/ → engine 写到 env 路径时
      reports 读不到，造成 status/pending/budget 显示陈旧。

    解析顺序：
      1. 如果 NOVEL_AI_DIR env 设置了 → 用 env 路径（与 engine 完全一致）
      2. 否则 → 用传进来的 novel_ai_dir（向后兼容）

    效果：bridge endpoint 注入 NOVEL_AI_DIR env 后，subprocess 和主进程 reports
    读同一份 state 文件，不会再出现 "engine 在跑但 status 显示 not_initialized" 的
    假象。
    """
    env_dir = os.environ.get("NOVEL_AI_DIR")
    if env_dir:
        return Path(env_dir) / "output" / "orchestrator_state.json"
    return Path(novel_ai_dir) / "output" / "orchestrator_state.json"


def _chapters_dir(novel_ai_dir: str) -> Path:
    """解析 chapters 目录（与 _state_path 同一规则）。"""
    env_dir = os.environ.get("NOVEL_AI_DIR")
    if env_dir:
        return Path(env_dir) / "output" / "chapters"
    return Path(novel_ai_dir) / "output" / "chapters"


def _budget_log_path(novel_ai_dir: str) -> Path:
    """解析 budget log 路径（与 _state_path 同一规则）。"""
    env_dir = os.environ.get("NOVEL_AI_DIR")
    if env_dir:
        return Path(env_dir) / "logs" / "budget_log.jsonl"
    return Path(novel_ai_dir) / "logs" / "budget_log.jsonl"


def read_status(novel_ai_dir: str) -> dict[str, Any]:
    state_path = _state_path(novel_ai_dir)
    if not state_path.exists():
        return {
            "available": False,
            "status": "not_initialized",
            "message": "orchestrator_state.json not found; run planner/bootstrap first",
        }
    state = _read_json(state_path)
    return {
        "available": True,
        "status": state.get("current_phase", "unknown"),
        "current_arc": state.get("current_arc", 0),
        "current_chapter": state.get("current_chapter", 0),
        "total_chapters_planned": state.get("total_chapters_planned", 0),
        "budget_used_usd": state.get("budget_used_usd", 0.0),
        "budget_limit_usd": state.get("budget_limit_usd"),
        "human_pending_count": len(state.get("human_pending", []) or []),
        "state": state,
    }


def read_pending(novel_ai_dir: str) -> dict[str, Any]:
    state_path = _state_path(novel_ai_dir)
    if not state_path.exists():
        return {"available": False, "pending": [], "message": "orchestrator_state.json not found"}
    state = _read_json(state_path)
    return {"available": True, "pending": state.get("human_pending", []) or []}


def read_budget_log(novel_ai_dir: str) -> dict[str, Any]:
    state = read_status(novel_ai_dir)
    log_path = _budget_log_path(novel_ai_dir)
    records = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"raw": line, "parse_error": True})

    total = sum(float(item.get("cost_usd", 0) or 0) for item in records)
    if not records and state.get("available"):
        total = float(state.get("budget_used_usd") or 0)
    budget_limit = state.get("budget_limit_usd") if state.get("available") else None
    return {
        "available": log_path.exists() or state.get("available", False),
        "budget_limit_usd": budget_limit,
        "total_cost_usd": round(total, 6),
        "record_count": len(records),
        "records": records,
    }


def _memory_path(novel_ai_dir: str, novel_id: str, layer: str) -> Path:
    """解析 L2/L5 记忆文件路径（与 _state_path 同一 env 优先规则）。

    落盘位置由 engine/config/paths.py 定义：
      {NOVEL_AI_DIR}/memory/l2/{novel_id}_memory.json
      {NOVEL_AI_DIR}/memory/l5/{novel_id}_l5.json
    """
    base = Path(os.environ.get("NOVEL_AI_DIR") or novel_ai_dir)
    suffix = "memory" if layer == "l2" else "l5"
    return base / "memory" / layer / f"{novel_id}_{suffix}.json"


# 上浮给前端的列表上限。记忆文件可以到几百条，端点是给人看的仪表盘而不是
# 数据导出口；截断的同时必须在 stats 里报总数，否则前端会把"看到的"当成全部。
_TAIL_SUMMARIES = 20
_TAIL_COLD = 50


def _as_dict(v) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _as_list(v) -> list:
    return v if isinstance(v, list) else []


def _foreshadow_target(f: dict, chapters_per_arc: int) -> int:
    """伏笔应回收章号。口径必须与 engine.memory.manager._foreshadow_target_chapter
    一致，否则前端显示的"逾期数"和引擎催收的不是同一件事。"""
    tc = f.get("target_chapter")
    if isinstance(tc, int) and tc > 0:
        return tc
    ta = f.get("target_arc")
    if isinstance(ta, int) and ta > 0:
        arc_len = chapters_per_arc if isinstance(chapters_per_arc, int) and chapters_per_arc > 0 else 30
        return ta * arc_len
    planted = f.get("planted_at_chapter")
    return (planted + 30) if isinstance(planted, int) else 10 ** 9


def read_memory(novel_ai_dir: str, novel_id: str | None) -> dict[str, Any]:
    """只读地暴露分层记忆（L2 热/冷/约束/meta + L5 弧归档）。

    存在的理由：长篇写作里记忆漂移是头号杀手，而在此之前记忆状态既没有 API
    也没有界面——伏笔逾期、质量债传染、tracker 解析失败这些信号全都埋在绑定
    目录的 JSON 里，无法验收也无法调优。

    与 read_status/read_pending/read_budget_log 同构：读绑定目录、NOVEL_AI_DIR
    env 优先、缺文件不抛。损坏文件显式报错（不冒充"没有记忆"）。
    """
    if not novel_id:
        return {"available": False, "l2_available": False, "l5_available": False,
                "l2": {}, "l5": {}, "stats": {},
                "message": "binding.novel_id 未配置，无法定位记忆文件"}

    l2_raw: dict[str, Any] = {}
    l5_raw: dict[str, Any] = {}
    errors: list[str] = []
    l2_ok = l5_ok = False
    for layer, target in (("l2", "l2_raw"), ("l5", "l5_raw")):
        path = _memory_path(novel_ai_dir, novel_id, layer)
        if not path.exists():
            continue
        try:
            data = _read_json(path)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            errors.append(f"{layer} corrupted: {e}")
            continue
        if not isinstance(data, dict):
            errors.append(f"{layer} corrupted: 顶层不是对象（{type(data).__name__}）")
            continue
        if layer == "l2":
            l2_raw, l2_ok = data, True
        else:
            l5_raw, l5_ok = data, True

    hot = _as_dict(l2_raw.get("hot"))
    cold = _as_dict(l2_raw.get("cold"))
    constraints = _as_dict(l2_raw.get("constraints"))
    meta = _as_dict(l2_raw.get("meta"))

    summaries = [s for s in _as_list(hot.get("recent_summaries")) if isinstance(s, dict)]
    world_events = _as_list(cold.get("world_events"))
    planted = [f for f in _as_list(constraints.get("foreshadowing_planted"))
               if isinstance(f, dict)]

    # 计数基于全量，截断只作用于上浮的列表——否则统计会随截断失真。
    unverified = [s.get("chapter") for s in summaries if s.get("unverified")]
    current_ch = meta.get("last_updated_chapter")
    current_ch = current_ch if isinstance(current_ch, int) else 0
    chapters_per_arc = meta.get("chapters_per_arc")
    overdue = sum(1 for f in planted
                  if _foreshadow_target(f, chapters_per_arc) < current_ch)

    stats = {
        "last_updated_chapter": current_ch,
        "total_chapters_tracked": meta.get("total_chapters_tracked", 0),
        "chapters_per_arc": chapters_per_arc,
        "unverified_chapter_count": len(unverified),
        "unverified_chapters": [c for c in unverified if c is not None],
        "foreshadowing_planted_count": len(planted),
        "foreshadowing_resolved_count": len(_as_list(cold.get("resolved_foreshadowing"))),
        "foreshadowing_overdue_count": overdue,
        "active_thread_count": len(_as_list(hot.get("active_threads"))),
        "character_state_count": len(_as_dict(hot.get("character_states"))),
        "constraint_count": len(_as_list(constraints.get("forbidden_constraints"))),
        "established_fact_count": len(_as_list(constraints.get("established_facts"))),
        "recent_summaries_total": len(summaries),
        "world_events_total": len(world_events),
        "tracker_parse_failure_count": meta.get("tracker_parse_failure_count", 0),
        "arc_count": len(_as_list(l5_raw.get("arc_summaries"))),
    }

    l2_out = {
        "hot": {
            "protagonist_level": hot.get("protagonist_level"),
            "protagonist_level_num": hot.get("protagonist_level_num"),
            "protagonist_points": hot.get("protagonist_points"),
            "inventory": _as_list(hot.get("inventory")),
            "active_threads": _as_list(hot.get("active_threads")),
            "character_states": _as_dict(hot.get("character_states")),
            "recent_summaries": summaries[-_TAIL_SUMMARIES:],
            "recent_events": hot.get("recent_events", ""),
            "scene_location": hot.get("scene_location"),
            "time_context": hot.get("time_context"),
            "last_chapter_ending": hot.get("last_chapter_ending", ""),
        },
        "cold": {
            "world_events": world_events[-_TAIL_COLD:],
            "closed_threads": _as_list(cold.get("closed_threads"))[-_TAIL_COLD:],
            "resolved_foreshadowing":
                _as_list(cold.get("resolved_foreshadowing"))[-_TAIL_COLD:],
        },
        "constraints": {
            "forbidden_constraints":
                _as_list(constraints.get("forbidden_constraints"))[-_TAIL_COLD:],
            "established_facts":
                _as_list(constraints.get("established_facts"))[-_TAIL_COLD:],
            "foreshadowing_planted": [
                {**f, "due_chapter": _foreshadow_target(f, chapters_per_arc),
                 "overdue": _foreshadow_target(f, chapters_per_arc) < current_ch}
                for f in planted[-_TAIL_COLD:]
            ],
        },
        # meta 是引擎自己写的运行记账，白名单上浮：既避免夹带意外字段，
        # 也让前端字段契约稳定。
        "meta": {
            "last_updated_chapter": meta.get("last_updated_chapter"),
            "total_chapters_tracked": meta.get("total_chapters_tracked"),
            "chapters_per_arc": chapters_per_arc,
            "tracker_parse_failure_count": meta.get("tracker_parse_failure_count", 0),
            "last_tracker_parse_failure_chapter":
                meta.get("last_tracker_parse_failure_chapter"),
        },
    } if l2_ok else {}

    l5_out = {
        "arc_summaries": _as_list(l5_raw.get("arc_summaries")),
        "character_arcs": _as_dict(l5_raw.get("character_arcs")),
        "major_revelations": _as_list(l5_raw.get("major_revelations")),
        "compressed_history": l5_raw.get("compressed_history", ""),
    } if l5_ok else {}

    message = "; ".join(errors)
    if not message and not (l2_ok or l5_ok):
        message = "记忆文件尚未生成（引擎还没跑过 tracker）"
    return {
        "available": l2_ok or l5_ok,
        "l2_available": l2_ok,
        "l5_available": l5_ok,
        "novel_id": novel_id,
        "l2": l2_out,
        "l5": l5_out,
        "stats": stats,
        "message": message,
    }


def apply_review(
    novel_ai_dir: str,
    action: str,
    task_id: str | None = None,
    task_index: int | None = None,
    chapter_number: int | None = None,
    content: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if action not in VALID_REVIEW_ACTIONS:
        raise ValueError(f"unsupported review action: {action}")

    state_path = _state_path(novel_ai_dir)
    if not state_path.exists():
        return {"available": False, "message": "orchestrator_state.json not found"}

    state = _read_json(state_path)
    pending = state.get("human_pending", []) or []
    idx = _find_task_index(pending, task_id, task_index, chapter_number)
    task = pending[idx] if idx is not None else None

    if action == "edit" and chapter_number and content is not None:
        chapter_path = _chapters_dir(novel_ai_dir) / f"ch_{chapter_number:04d}.txt"
        chapter_path.parent.mkdir(parents=True, exist_ok=True)
        chapter_path.write_text(content, encoding="utf-8")

    if idx is not None:
        pending.pop(idx)
    state["human_pending"] = pending
    state.setdefault("review_history", []).append({
        "action": action,
        "task_id": task_id,
        "task_index": task_index,
        "chapter_number": chapter_number,
        "note": note,
        "task": task,
        "matched": idx is not None,  # 显式标记是否匹配（前端可显示 "未匹配"）
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    })
    state["last_updated"] = datetime.now(timezone.utc).isoformat()
    # 迭代 #43: 改用 atomic_write_json
    atomic_write_json(str(state_path), state)
    return {
        "available": True,
        "action": action,
        "task": task,
        "matched": idx is not None,  # 重复一份在顶层方便前端判断
        "remaining": len(pending),
    }


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_task_index(
    pending: list[dict[str, Any]],
    task_id: str | None,
    task_index: int | None,
    chapter_number: int | None,
) -> int | None:
    """在 pending 列表里找匹配 task 的 index。

    返回：
      - 找到：返回 0..len(pending)-1
      - 没找到：返回 None（不 pop 任何任务，避免静默 pop 错任务）

    历史 bug（迭代 #29）：
      之前"没找到"时 fallback 到 0，silently pop 第一条 pending 任务。
      用户提交 review with task_id="X" 但 X 不存在 → 第一条 pending 被静默
      移除，review_history 记的是 "X" 但实际 pop 的是另一条 → 数据完整性破坏。
    """
    if task_index is not None and 0 <= task_index < len(pending):
        return task_index
    if task_id:
        for idx, item in enumerate(pending):
            if str(item.get("id") or item.get("task_id") or "") == task_id:
                return idx
        return None  # 显式 None（不 fallback 到 0）
    if chapter_number is not None:
        for idx, item in enumerate(pending):
            payload = item.get("payload", {}) or {}
            if item.get("chapter_number") == chapter_number or payload.get("chapter_number") == chapter_number:
                return idx
        return None  # 显式 None（不 fallback 到 0）
    # 三个 identifier 都没传：没线索，不 pop
    return None
