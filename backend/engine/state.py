"""Orchestrator state schema + persistence.

Migrated from novel_AI/orchestrator_state.py. Pure-data module: the
TypedDict shapes and create_initial_state / save_state / load_state
helpers. No import from novel_AI/ — completely standalone.
"""
from __future__ import annotations
from typing import TypedDict, List, Optional, Dict, Any
import json
from datetime import datetime


# ─────────────────────────────────────────────
# Arc planning
# ─────────────────────────────────────────────
class ArcPlan(TypedDict):
    arc_id: int
    arc_name: str
    arc_goal: str
    estimated_chapters: int
    arc_climax_description: str
    arc_climax_chapter_offset: int
    emotion_curve: str
    new_characters_introduced: List[str]
    arc_ending_state: str
    is_final_arc: bool


# ─────────────────────────────────────────────
# Chapter task (an item in the per-arc task queue)
# ─────────────────────────────────────────────
class ChapterTask(TypedDict):
    chapter_number: int
    chapter_role: str           # 铺垫|发展|爽点|弧高潮|过渡
    chapter_goal: str
    main_characters: List[str]
    shuang_type: Optional[str]
    shuang_description: str
    ending_hook_type: str       # 7 种钩子之一
    ending_hook_description: str
    setting_constraints: List[str]
    forbidden_actions: List[str]
    target_length: str          # 如 '2000-2200'
    audit_mode: str             # full|lite|bootstrap
    is_arc_climax: bool


class NarrativeUnit(TypedDict):
    unit_id: str
    unit_type: str              # small_win|setback|investigation|arc_climax|transition
    chapters: List[ChapterTask]
    unit_problem: str
    unit_resolution: str
    emotional_intensity: str    # low|medium|high|peak


class HumanTask(TypedDict):
    task_id: str
    task_type: str              # confirm_setting|confirm_arc|fix_chapter
    description: str
    payload: Any
    created_at: str
    priority: str               # must|recommended


class Action(TypedDict):
    type: str
    payload: Optional[Any]


# ─────────────────────────────────────────────
# Main state container
# ─────────────────────────────────────────────
class OrchestratorState(TypedDict):
    # 基础信息
    novel_id: str
    title: str
    platform: str               # fanqie|qidian|qimao
    genre: str
    setting_concept: str

    # 进度状态
    current_phase: str          # planning|outlining|writing|revising|done
    current_arc: int
    total_arcs_planned: int
    current_chapter: int
    total_chapters_planned: int
    arc_progress_pct: float

    # 任务队列
    arc_plans: List[ArcPlan]
    chapter_task_queue: List[ChapterTask]
    current_task: Optional[ChapterTask]

    # 质量监控
    quality_history: List[float]
    rewrite_count_current: int
    consecutive_low_score: int

    # 成本控制
    budget_limit_usd: float
    budget_used_usd: float
    audit_mode: str             # full|lite

    # 人工介入
    human_pending: List[HumanTask]
    tracker_pending: List[Dict]

    # 大纲模式产物
    outline_candidates: List[Dict]   # card 模式：每弧 3 个候选分支
    talk_questions: List[Dict]       # talk 模式：每弧引导性问题

    # 元数据
    style_samples: List[str]
    style_samples_source: str   # external|internal
    last_p0_chapter: int
    error_log: List[str]
    created_at: str
    last_updated: str


def create_initial_state(
    novel_id: str,
    title: str,
    platform: str,
    genre: str,
    setting_concept: str,
    budget_limit_usd: float = 500.0,
) -> OrchestratorState:
    """Build a fresh OrchestratorState. All progress fields start at zero."""
    now = datetime.now().isoformat()
    return OrchestratorState(
        novel_id=novel_id,
        title=title,
        platform=platform,
        genre=genre,
        setting_concept=setting_concept,
        current_phase="planning",
        current_arc=0,
        total_arcs_planned=0,
        current_chapter=0,
        total_chapters_planned=0,
        arc_progress_pct=0.0,
        arc_plans=[],
        chapter_task_queue=[],
        current_task=None,
        quality_history=[],
        rewrite_count_current=0,
        consecutive_low_score=0,
        budget_limit_usd=budget_limit_usd,
        budget_used_usd=0.0,
        audit_mode="full",
        human_pending=[],
        tracker_pending=[],
        outline_candidates=[],
        talk_questions=[],
        style_samples=[],
        style_samples_source="external",
        last_p0_chapter=0,
        error_log=[],
        created_at=now,
        last_updated=now,
    )


def save_state(state: OrchestratorState, path: str) -> None:
    """Serialize state to JSON file (UTF-8, indented for diff-friendliness).

    自动更新 last_updated 为当前时间——之前不更新导致 state 看起来"冻结"
    （用户视角：bridge/status 显示 last_updated 17 小时前，但实际 engine
    还在跑）。P5 fix。
    """
    from datetime import datetime
    # 复制一份避免修改入参（TypedDict 实际是 dict）
    payload = dict(state)
    payload["last_updated"] = datetime.now().isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_state(path: str) -> OrchestratorState:
    """Load state from JSON. Returns a TypedDict instance."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
