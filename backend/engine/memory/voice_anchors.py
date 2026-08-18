"""voice_anchors.py - v1.0 Stage H 角色口癖锚点

每章写作后追踪该角色的口癖使用情况，便于后续章节做一致性检查。

落盘位置：<novel_ai_dir>/output/voice_anchors.json
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

_log = logging.getLogger("novel_ai.engine.memory.voice_anchors")


def _anchors_path(novel_id: str) -> Path:
    base = Path(os.environ.get("NOVEL_AI_DIR", "backend/data/engine"))
    return base / "output" / "voice_anchors.json"


def load_anchors(novel_id: str) -> dict:
    """加载 voice_anchors。"""
    target = _anchors_path(novel_id)
    if not target.is_file():
        return {}
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        # 2026-08-18 修复（CLAUDE.md「失败要响亮」）：之前 return {} 完全无声
        # — 磁盘损坏 / 权限错误 / 编码错误都看不到。
        _log.exception("load_anchors 读取失败（将视作无 anchors）: %s", target)
        return {}


def _save_anchors(novel_id: str, anchors: dict) -> None:
    target = _anchors_path(novel_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    import json as _json
    target.write_text(_json.dumps(anchors, ensure_ascii=False, indent=2), encoding="utf-8")
    _log.info("voice_anchors 落盘: %s", target)


def record_voice(novel_id: str, character_name: str, speech_quirks: list[str]) -> None:
    """记录某个角色的口癖（设置 / 刷新）。"""
    if not isinstance(speech_quirks, list):
        raise ValueError(f"speech_quirks 必须是 list，实际 {type(speech_quirks).__name__}")

    anchors = load_anchors(novel_id)
    anchors[character_name] = list(speech_quirks)
    _save_anchors(novel_id, anchors)


def get_voice_anchors(novel_id: str) -> dict[str, list[str]]:
    """返回 {character_name: [quirk1, quirk2, ...]}。"""
    return load_anchors(novel_id)


def check_voice_consistency(
    novel_id: str,
    character_name: str,
    chapter_text: str,
) -> dict:
    """检查角色在 chapter_text 中是否使用了已记录的口癖。

    Returns:
        {used_anchors: [...], unused_anchors: [...]}
    """
    anchors = load_anchors(novel_id)
    char_quirks = anchors.get(character_name) or []
    if not char_quirks:
        return {"used_anchors": [], "unused_anchors": []}

    used = [q for q in char_quirks if q in chapter_text]
    unused = [q for q in char_quirks if q not in chapter_text]
    return {"used_anchors": used, "unused_anchors": unused}