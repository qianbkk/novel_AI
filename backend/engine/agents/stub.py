"""Stub agents — placeholders for the 7 agents that are NOT in P1 scope.

Each stub returns a shape that satisfies the orchestrator's call site so
the graph can be exercised end-to-end. P2 will replace these one at a
time with the real implementations ported from novel_AI/agents/.

Conventions for stub return values:
  - Normalizer:  (clean_text, issues_list, cost)
  - Compliance:  ({passed: bool, hard_rejects: list, suggestion: str}, cost)
  - Checker:     ({score: float, verdict: str, rewrite_level: str,
                   feedback: str, weakest_point: str, dimensions: dict}, cost)
  - Rewriter:    (text, cost)
  - Tracker:     (memory_dict, cost)
  - Summarizer:  (summary, cost)
  - Outline:     (tasks_list, cost)
"""
from __future__ import annotations
from typing import Any


def run_normalizer(text: str, task: dict) -> tuple[str, list, float]:
    """P1 stub: pass-through with a tiny cleanup. Real version strips
    AI-tic phrases, normalizes punctuation, runs fingerprint second-pass."""
    if not text:
        return text, [], 0.0
    cleaned = text.replace("  ", " ").replace("\n\n\n", "\n\n").strip()
    return cleaned, [], 0.0


def run_compliance(text: str, platform: str = "fanqie") -> tuple[dict, float]:
    """P1 stub: always passes. Real version checks 番茄 / 起点 / 七猫
    specific content rules (暴力/政治/未成年/低俗等)."""
    return {"passed": True, "hard_rejects": [], "suggestion": ""}, 0.0


def run_checker(text: str, task: dict, mode: str = "full") -> tuple[dict, float]:
    """P1 stub: 3-vendor vote degenerated to single-vendor, always PASS
    with a generous score so the graph flows into save_and_track. Real
    version calls 3 different LLMs (DeepSeek / Gemini / Anthropic)."""
    return {
        "score": 7.5,
        "verdict": "PASS",
        "rewrite_level": "P0",
        "feedback": "P1 stub: no real check performed.",
        "weakest_point": "",
        "dimensions": {"plot": 7.5, "writing": 7.5, "engagement": 7.5},
    }, 0.0


def run_rewriter(text: str, level: str, feedback: str, task: dict,
                 checker_result: dict, memory: dict, setting: dict) -> tuple[str, float]:
    """P1 stub: returns draft unchanged. Real version calls LLM with
    targeted rewrite prompt based on feedback."""
    return text, 0.0


def run_tracker(text: str, task: dict, memory: dict, novel_id: str) -> tuple[dict, float]:
    """P1 stub: bumps protagonist_points by 1 if it exists, else no-op.
    Real version extracts entities / relationships / state deltas."""
    if not isinstance(memory, dict):
        return memory or {}, 0.0
    memory.setdefault("hot", {})
    memory["hot"].setdefault("protagonist_points", 0)
    memory["hot"]["protagonist_points"] += 1
    return memory, 0.0


def run_summarizer(phase: str, arc: dict, memory: dict, novel_id: str) -> tuple[str, float]:
    """P1 stub: writes a one-line arc-end placeholder. Real version
    calls LLM to compress the full arc into 500-800 chars."""
    summary = f"[P1 stub] Arc {arc.get('arc_id', '?')}「{arc.get('arc_name','?')}」completed."
    return summary, 0.0


def run_outline(arc: dict, start_chapter: int, setting: dict,
                memory: dict) -> tuple[list, float]:
    """P1 stub: generates N placeholder chapter tasks where N is
    arc.estimated_chapters (default 30). Real version calls LLM to
    produce structured ChapterTask dicts with seven-arc story beats."""
    n = int(arc.get("estimated_chapters", 30))
    tasks = []
    roles = ["铺垫", "铺垫", "发展", "发展", "发展", "爽点", "发展", "爽点", "发展", "发展",
             "爽点", "发展", "发展", "发展", "发展", "爽点", "发展", "发展", "发展", "发展",
             "爽点", "发展", "发展", "发展", "发展", "发展", "发展", "发展", "发展", "弧高潮"]
    for i in range(n):
        tasks.append({
            "chapter_number":      start_chapter + i,
            "chapter_role":        roles[i % len(roles)] if i < len(roles) else "发展",
            "chapter_goal":        f"第{start_chapter + i}章：{arc.get('arc_name','')}剧情推进",
            "main_characters":     ["主角"],
            "shuang_type":         "实力展示" if i % 5 == 0 else None,
            "shuang_description":  "展示主角当前境界的压倒性优势" if i % 5 == 0 else "",
            "ending_hook_type":    ["悬念钩", "信息钩", "反转钩", "期待钩"][i % 4],
            "ending_hook_description": "下一章揭示关键信息",
            "setting_constraints": [],
            "forbidden_actions":   [],
            "target_length":       "2000-2200",
            "audit_mode":          "full",
            "is_arc_climax":       (i == n - 1),
        })
    return tasks, 0.0
