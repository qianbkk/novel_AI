"""Lightweight SQLite backup for the single-tenant local prototype.

Why this exists:
  backend/data/novel_assistant.db + backend/data/checkpoints.sqlite hold all
  creative work (worldbuilding / character cards / chapter drafts / LangGraph
  checkpoints). Single-tenant local prototype -> biggest real risk is disk
  corruption / accidental deletion / process crash mid-write. No production
  infra, so the cheap mitigation is: take a timestamped snapshot at startup,
  keep last N (default 10). Use sqlite3.Connection.backup() (not cp) so we
  never capture a half-written page.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from .logging_setup import get_logger

log = get_logger("novel_ai.backup")

DEFAULT_KEEP_N = 10
ENV_SKIP = "NOVEL_AI_SKIP_BACKUP"
ENV_KEEP_N = "NOVEL_AI_BACKUP_KEEP_N"


def _data_dir() -> Path:
    # Match what backend/app/database.py uses (DATA_DIR = backend/data)
    return Path(__file__).resolve().parents[1] / "data"


def _backup_dir() -> Path:
    p = _data_dir() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _should_skip() -> bool:
    return os.environ.get(ENV_SKIP, "").strip() in ("1", "true", "yes")


def _keep_n() -> int:
    try:
        return max(1, int(os.environ.get(ENV_KEEP_N, str(DEFAULT_KEEP_N))))
    except ValueError:
        return DEFAULT_KEEP_N


def _rotate(snapshot_dir: Path, prefix: str, keep_n: int) -> int:
    """Delete oldest snapshots beyond keep_n. Returns number deleted."""
    files = sorted(snapshot_dir.glob(f"{prefix}-*.db*"), key=lambda p: p.stat().st_mtime)
    excess = len(files) - keep_n
    deleted = 0
    for f in files[: max(0, excess)]:
        try:
            f.unlink()
            deleted += 1
            log.info("backup-rotate deleted=%s", f.name)
        except OSError as e:
            log.warning("backup-rotate failed name=%s err=%s", f.name, e)
    return deleted


def take_snapshot(db_path: Path, *, label: str | None = None) -> Path | None:
    """Take a consistent snapshot of db_path into backups/<label>-<ts>.db.

    Returns the snapshot path, or None if skipped/failed.
    Never raises -- logs and returns None on error so startup can continue.
    """
    if _should_skip():
        log.info("backup skipped (NOVEL_AI_SKIP_BACKUP=1)")
        return None
    if not db_path.exists():
        log.warning("backup skipped: source db missing path=%s", db_path)
        return None
    prefix = label or db_path.stem  # novel_assistant or checkpoints
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = _backup_dir() / f"{prefix}-{ts}{db_path.suffix}"
    try:
        # sqlite3 online backup API -- consistent snapshot even with concurrent writers
        src = sqlite3.connect(str(db_path))
        try:
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        deleted = _rotate(_backup_dir(), prefix, _keep_n())
        log.info(
            "backup OK src=%s dest=%s deleted_old=%d",
            db_path.name, dest.name, deleted,
        )
        return dest
    except Exception as e:
        log.error("backup FAILED src=%s err=%s", db_path, e, exc_info=True)
        # Best-effort cleanup of half-written file
        if dest.exists():
            try:
                dest.unlink()
            except OSError:
                pass
        return None


def take_all_snapshots() -> dict[str, Path | None]:
    """Take snapshots of both DBs. Returns {label: path or None}."""
    data_dir = _data_dir()
    return {
        "novel_assistant": take_snapshot(data_dir / "novel_assistant.db"),
        "checkpoints": take_snapshot(data_dir / "checkpoints.sqlite"),
    }


__all__ = [
    "take_snapshot",
    "take_all_snapshots",
    "_data_dir",
    "_backup_dir",
    "_rotate",
    "_should_skip",
    "_keep_n",
    "DEFAULT_KEEP_N",
    "ENV_SKIP",
    "ENV_KEEP_N",
]
