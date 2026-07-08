"""novel_AI memory package — L2/L5/manager.

当前实现（manager.py）：full L2 hot/cold + L5 arc summaries + constraint expiry +
  style samples + entity tracking via L2 sub-fields (人物/道具/伏笔/时间线).

历史：早期 P1-E 阶段的 in-memory stub (memory/stub.py) 已被 manager.py
完整取代并删除；进程内 dict 不再使用，所有记忆落盘到 backend/data/engine/memory/。
"""
from .manager import (
    get_l2, save_l2, empty_l2,
    expire_constraints, add_constraint, maybe_compress_hot_to_cold,
    get_chapter_relevant_context,
    get_l5, save_l5,
    get_style_samples, maybe_update_style_samples,
    get_writer_context,
    check_memory_health,
)

__all__ = [
    "get_l2", "save_l2", "empty_l2",
    "expire_constraints", "add_constraint", "maybe_compress_hot_to_cold",
    "get_chapter_relevant_context",
    "get_l5", "save_l5",
    "get_style_samples", "maybe_update_style_samples",
    "get_writer_context",
    "check_memory_health",
]