"""In-memory memory stub for P1.

Real memory manager (L2 hot/cold + L5 arc summaries + entity tracker)
is a P2 deliverable. This stub returns sensible empty/default shapes so
the orchestrator can run end-to-end without persistence.

Each novel_id has its own _MEM dict, process-wide. NOT persistent across
backend restarts. Real L2 should live in DB / sqlite file under
backend/data/engine/memory/.
"""
from __future__ import annotations
import threading

_LOCK = threading.Lock()
_MEM: dict[str, dict] = {}


def _default(novel_id: str) -> dict:
    return {
        "meta": {"novel_id": novel_id, "version": 1},
        "hot": {
            "protagonist_level": "凡人",
            "protagonist_points": 0,
            "inventory": [],
            "scene_location": "未指定",
            "time_context": "未指定",
            "last_chapter_ending": "",
            "recent_events": "",
            "active_threads": [],
            "character_states": {},
            "foreshadowing_due_soon": [],
            "relevant_forbidden": [],
        },
        "cold": {"summary": ""},
        "l5_arc_summaries": [],
    }


def get_l2(novel_id: str) -> dict:
    with _LOCK:
        if novel_id not in _MEM:
            _MEM[novel_id] = _default(novel_id)
        # Return a shallow copy so callers can't mutate our cache by accident
        m = _MEM[novel_id]
        return {
            "meta": dict(m["meta"]),
            "hot": dict(m["hot"]),
            "cold": dict(m["cold"]),
            "l5_arc_summaries": list(m["l5_arc_summaries"]),
        }


def save_l2(novel_id: str, mem: dict) -> None:
    with _LOCK:
        _MEM[novel_id] = mem


def get_writer_context(novel_id: str, task: dict) -> dict:
    """Return the ~1500-token context object the writer prompt expects.
    P1: returns the hot layer as-is. P2: should rank by relevance to
    current task (recency + character match + thread match)."""
    mem = get_l2(novel_id)
    hot = mem.get("hot", {})
    return {
        "protagonist_level":     hot.get("protagonist_level", "凡人"),
        "protagonist_points":    hot.get("protagonist_points", 0),
        "inventory":             hot.get("inventory", []),
        "scene_location":        hot.get("scene_location", "未指定"),
        "time_context":          hot.get("time_context", "未指定"),
        "last_chapter_ending":   hot.get("last_chapter_ending", ""),
        "recent_events":         hot.get("recent_events", ""),
        "active_threads":        hot.get("active_threads", []),
        "character_states":      hot.get("character_states", {}),
        "foreshadowing_due_soon": hot.get("foreshadowing_due_soon", []),
        "relevant_forbidden":    hot.get("relevant_forbidden", []),
        "cold_summary":          mem.get("cold", {}).get("summary", ""),
        "style_samples":         [],
        "style_samples_source":  "external",
    }


def maybe_update_style_samples(chapter_number: int, novel_id: str) -> None:
    """P1: no-op. P2: every 20/30 chapters, extract style samples from
    freshly-written chapters and store as few-shot examples for Writer."""
    return None
