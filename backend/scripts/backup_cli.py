"""CLI wrapper for app.backup_db.take_all_snapshots, callable from dev.bat.

Why this exists: dev.bat (Windows cmd) can't reliably pass `python -c "..."`
for multi-line f-strings because cmd mangles `%` and quotes. So we ship a
small launcher script instead.

Exits 0 if at least one snapshot was created, 1 otherwise.
"""
from __future__ import annotations

import logging
import sys

from app.backup_db import _backup_dir, _data_dir, take_all_snapshots


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )
    print()
    print(f"[backup] data_dir  = {_data_dir()}")
    print(f"[backup] backup_dir = {_backup_dir()}")
    print()
    result = take_all_snapshots()
    for label, path in result.items():
        status = "OK " if path is not None else "SKIP"
        print(f"[backup] {status} {label:18s} -> {path}")
    print()
    return 0 if any(result.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
