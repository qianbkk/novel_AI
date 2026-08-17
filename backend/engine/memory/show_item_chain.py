"""show_item_chain.py - v1.0 Stage H show-item 接力链

每章写作后追加 show_item_used 到 chain，writer 写下一章时
可以读到'上一章/上 3 章 哪些物件/动作出现过了'，便于接力强化。

落盘位置：<novel_ai_dir>/output/show_item_chain.json
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger("novel_ai.engine.memory.show_item_chain")


def _chain_path(novel_id: str) -> Path:
    base = Path(os.environ.get("NOVEL_AI_DIR", "backend/data/engine"))
    return base / "output" / "show_item_chain.json"


def load_chain(novel_id: str) -> dict:
    """加载 chain，不存在返回空结构。"""
    target = _chain_path(novel_id)
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


def _save_chain(novel_id: str, chain: dict) -> None:
    target = _chain_path(novel_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    target.write_text(_json.dumps(chain, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("show_item_chain 落盘: %s", target)


def append_show_item(novel_id: str, chapter_number: int, items: list[str]) -> None:
    """追加一章的 show_item_used。"""
    if not isinstance(items, list):
        raise ValueError(f"items 必须是 list，实际 {type(items).__name__}")

    chain = load_chain(novel_id)
    chain["chapters"][str(chapter_number)] = list(items)
    _save_chain(novel_id, chain)


def get_recent_items(novel_id: str, last_n: int = 3) -> dict[int, list[str]]:
    """返回最近 last_n 章的 items。

    Returns:
        {chapter_number: items_list} 字典（按章号升序）
    """
    chain = load_chain(novel_id)
    chapters = chain.get("chapters", {})
    # 解析章号 → 排序 → 取最后 last_n 个
    parsed: list[tuple[int, list[str]]] = []
    for ch_str, items in chapters.items():
        try:
            ch = int(ch_str)
        except (TypeError, ValueError):
            continue
        parsed.append((ch, list(items) if isinstance(items, list) else []))
    parsed.sort(key=lambda x: x[0])
    return dict(parsed[-last_n:])