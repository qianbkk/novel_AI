"""集中日志：让所有运行结果落盘到 backend/logs/，便于事后排查。

设计要点：
  - 启动时把根 logger 配好：控制台 INFO + 文件 INFO（轮转 5MB×5）。
  - 暴露 module-level `get_logger(name)`，调用方直接 import 使用。
  - 不阻断 stdout：sse-starlette / print() 都照常工作。
"""
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_CONFIGURED = False


def configure_root() -> None:
    """配置根 logger。可重入——重复调用不会重复挂 handler。"""
    global _CONFIGURED
    if _CONFIGURED:
        return
    root = logging.getLogger()
    # 把已有的弱 handler 清掉，避免重复挂载
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler(stream=sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    fh = RotatingFileHandler(
        _LOG_DIR / "novel_ai.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    configure_root()
    return logging.getLogger(name)


__all__ = ["get_logger", "configure_root", "_LOG_DIR"]