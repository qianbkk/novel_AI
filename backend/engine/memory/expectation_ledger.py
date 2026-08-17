"""expectation_ledger.py - v1.0 Stage H 期待感兑现台账

每章写作后追加 expectation_status 到 ledger，writer 写下一章时
可以读到'上一章播种了什么 / 兑现了什么 / 还剩什么没兑现'。

落盘位置：<novel_ai_dir>/output/expectation_ledger.json
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger("novel_ai.engine.memory.expectation_ledger")


def _ledger_path(novel_id: str) -> Path:
    """ledger 文件路径（env-aware）。"""
    base = Path(os.environ.get("NOVEL_AI_DIR", "backend/data/engine"))
    return base / "output" / "expectation_ledger.json"


def load_ledger(novel_id: str) -> dict:
    """加载 ledger，不存在返回空结构。"""
    target = _ledger_path(novel_id)
    if not target.is_file():
        return {"chapters": {}}
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"chapters": {}}
        if "chapters" not in data or not isinstance(data["chapters"], dict):
            data["chapters"] = {}
        return data
    except Exception:
        return {"chapters": {}}


def _save_ledger(novel_id: str, ledger: dict) -> None:
    """落盘 ledger。"""
    target = _ledger_path(novel_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    target.write_text(_json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("expectation_ledger 落盘: %s", target)


def append_expectation(novel_id: str, chapter_number: int, expectation_status: dict) -> None:
    """追加一章的 expectation_status 到 ledger。

    Args:
        novel_id: 项目 ID
        chapter_number: 章号
        expectation_status: {seed_X_status, new_seed, ...}
    """
    if not isinstance(expectation_status, dict):
        raise ValueError(f"expectation_status 必须是 dict，实际 {type(expectation_status).__name__}")

    ledger = load_ledger(novel_id)
    ledger["chapters"][str(chapter_number)] = {
        "expectation_status": expectation_status,
    }
    _save_ledger(novel_id, ledger)


def get_pending_seeds(novel_id: str, exclude_chapter: int) -> list[str]:
    """返回所有未兑现的期望种子（key 列表，按播种顺序）。

    Args:
        novel_id: 项目 ID
        exclude_chapter: 排除当前章号（避免把本章自身当 pending）

    Returns:
        list[str]: 所有出现过的 seed_X key 的并集
    """
    ledger = load_ledger(novel_id)
    keys: set[str] = set()
    for ch_str, entry in ledger.get("chapters", {}).items():
        try:
            ch = int(ch_str)
        except (TypeError, ValueError):
            continue
        if ch >= exclude_chapter:
            continue
        status = entry.get("expectation_status") or {}
        for k in status:
            if k == "new_seed":
                continue  # new_seed 是值不是 key
            keys.add(k)
    return sorted(keys)