import json
from datetime import datetime
from pathlib import Path
from typing import Any

VALID_REVIEW_ACTIONS = {"accept", "reject", "edit"}


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
    log_path = Path(novel_ai_dir, "logs", "budget_log.jsonl")
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
        chapter_path = Path(novel_ai_dir, "output", "chapters", f"ch_{chapter_number:04d}.txt")
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
        "reviewed_at": datetime.utcnow().isoformat(),
    })
    state["last_updated"] = datetime.utcnow().isoformat()
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"available": True, "action": action, "task": task, "remaining": len(pending)}


def _state_path(novel_ai_dir: str) -> Path:
    return Path(novel_ai_dir, "output", "orchestrator_state.json")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_task_index(
    pending: list[dict[str, Any]],
    task_id: str | None,
    task_index: int | None,
    chapter_number: int | None,
) -> int | None:
    if task_index is not None and 0 <= task_index < len(pending):
        return task_index
    if task_id:
        for idx, item in enumerate(pending):
            if str(item.get("id") or item.get("task_id") or "") == task_id:
                return idx
    if chapter_number is not None:
        for idx, item in enumerate(pending):
            payload = item.get("payload", {}) or {}
            if item.get("chapter_number") == chapter_number or payload.get("chapter_number") == chapter_number:
                return idx
    return 0 if pending else None
