import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 迭代 #43: orchestrator_state.json（apply_review 等写入）之前直接 write_text
# 半写损坏 → 下次 pull_review / apply_review 失败。改用 atomic_write_json。
from engine.utils import atomic_write_json

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
