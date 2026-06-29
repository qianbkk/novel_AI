"""novel_AI memory package — L2/L5/manager.

P1-E scope: stub returned in-memory dicts with the right shape.
P2 (current): full L2 hot/cold + L5 arc summaries + constraint expiry +
  style samples + entity tracking via L2 sub-fields (人物/道具/伏笔/时间线).
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