"""novel_AI memory package.

P1 scope: only in-memory stub. P2 will add:
  - L2 hot/cold分层 (近 20 章 / 远期压缩)
  - L5 弧摘要
  - 人物/道具/伏笔/时间线 实体追踪
  - 自动 hot→cold 压缩
"""
from .stub import get_l2, get_writer_context, save_l2, maybe_update_style_samples

__all__ = ["get_l2", "get_writer_context", "save_l2", "maybe_update_style_samples"]
