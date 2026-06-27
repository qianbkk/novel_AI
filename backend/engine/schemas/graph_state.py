"""Pydantic V2 state definitions for LangGraph Orchestrator.
Replaces novel_AI/orchestrator_state.py TypedDict with runtime-validated models."""
from __future__ import annotations
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel


class WritingPhase(str, Enum):
    planning = "planning"
    outlining = "outlining"
    writing = "writing"
    revising = "revising"
    done = "done"
    paused = "paused"


class AuditMode(str, Enum):
    full = "full"
    lite = "lite"
    bootstrap = "bootstrap"


class ArcPlan(BaseModel):
    arc_id: int
    arc_name: str
    arc_goal: str
    estimated_chapters: int
    arc_climax_description: str = ""
    arc_climax_chapter_offset: int = 0
    emotion_curve: str = ""
    new_characters_introduced: list[str] = []
    arc_ending_state: str = ""
    is_final_arc: bool = False


class ChapterTask(BaseModel):
    chapter_number: int
    chapter_role: str
    chapter_goal: str
    main_characters: list[str] = []
    shuang_type: Optional[str] = None
    shuang_description: str = ""
    ending_hook_type: str = ""
    ending_hook_description: str = ""
    setting_constraints: list[str] = []
    forbidden_actions: list[str] = []
    target_length: str = "2000-2200"
    audit_mode: str = "full"
    is_arc_climax: bool = False


class HumanPendingItem(BaseModel):
    task_id: str
    task_type: str = "fix_chapter"
    description: str = ""
    priority: str = "recommended"
    payload: dict[str, Any] = {}
    created_at: str = ""


class L2Memory(BaseModel):
    protagonist_level: str = "凡人"
    protagonist_level_num: int = 1
    protagonist_points: int = 0
    inventory: list[dict[str, Any]] = []
    character_states: dict[str, Any] = {}
    active_threads: list[str] = []
    last_chapter_ending: str = ""
    recent_summaries: list[str] = []
    scene_location: str = ""
    time_context: str = ""
    closed_threads: list[str] = []
    resolved_foreshadowing: list[str] = []
    established_facts: list[str] = []


class OrchestratorStatePydantic(BaseModel):
    novel_id: str = ""
    project_id: str = ""
    title: str = ""
    platform: str = "fanqie"
    genre: str = ""
    setting_concept: str = ""
    current_phase: WritingPhase = WritingPhase.planning
    current_arc: int = 0
    total_arcs_planned: int = 0
    current_chapter: int = 0
    total_chapters_planned: int = 0
    arc_plans: list[ArcPlan] = []
    chapter_task_queue: list[ChapterTask] = []
    current_task: Optional[ChapterTask] = None
    quality_history: list[float] = []
    rewrite_count_current: int = 0
    consecutive_low_score: int = 0
    budget_limit_usd: float = 500.0
    budget_used_usd: float = 0.0
    audit_mode: AuditMode = AuditMode.full
    human_pending: list[HumanPendingItem] = []
    protagonist_level: str = "凡人"
    protagonist_level_num: int = 1
    protagonist_points: int = 0
    inventory: list[dict[str, Any]] = []
    character_states: dict[str, Any] = {}
    recent_summaries: list[str] = []
    scene_location: str = ""
    time_context: str = ""
    established_facts: list[str] = []
    style_samples: list[str] = []
    last_p0_chapter: int = 0
    error_log: list[str] = []
    created_at: str = ""
    last_updated: str = ""

    @classmethod
    def from_typed_dict(cls, td: dict) -> "OrchestratorStatePydantic":
        return cls(**{k: v for k, v in td.items() if k in cls.model_fields})

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")
