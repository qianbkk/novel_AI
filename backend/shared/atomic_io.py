"""shared.atomic_io — 跨 app ↔ engine 的原子写工具。

2026-07-25 抽离（修 P0 短板双向 import）。

之前 app 3 处（bridge/reports.py, bridge/setting_sync.py, chapter_rewrite.py）
从 `engine.utils` import atomic_write_json；engine utils 本身是 Agent 工具集合，
但 atomic_write_json 是个纯 IO 工具，跟 Agent 无关。把它从 engine 提到 shared
后，app 不再 import engine。

兼容：engine.utils 仍然 re-export atomic_write_json，老代码不破。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

log = logging.getLogger("novel_ai.shared.atomic_io")


def atomic_write_text(path: str, content: str) -> None:
    """原子写 UTF-8 文本，并在 Windows 文件占用时做有限重试。"""
    p = os.fspath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass

    last_err: OSError | None = None
    for attempt in range(3):
        try:
            os.replace(tmp, p)
            return
        except OSError as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.1 * (2 ** attempt))
    if last_err is not None:
        raise last_err


def atomic_write_json(path: str, data: Any) -> None:
    """原子写 JSON：先写 .tmp 再 os.replace，避免半写文件被下次读到。

    模式来自 engine.state.save_state，被 save_l2 / save_l5 复用，
    现在推广到所有需要写 JSON 到磁盘的地方（setting_package.json 等）。

    - 写 .tmp + flush + best-effort fsync
    - os.replace 重试 3 次（Windows 上并发 rename 可能 WinError 32）
    - 全部失败才抛

    抽出到 shared.atomic_io（2026-07-25）后逻辑不变；engine.utils re-export
    保持向后兼容。
    """
    p = os.fspath(path)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            # Windows 文件系统不总是支持 fsync（尤其是非本地盘）—— 静默继续
            pass
    # Windows 上 os.replace 可能撞 WinError 32（被另一个进程/线程持有）。
    # 重试 3 次，间隔 100/200/400ms。
    last_err: OSError | None = None
    for attempt in range(3):
        try:
            os.replace(tmp, p)
            return
        except OSError as e:
            last_err = e
            if attempt < 2:
                time.sleep(0.1 * (2 ** attempt))
    if last_err is not None:
        raise last_err
