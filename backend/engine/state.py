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

    并发保护（迭代 #9）：用 fcntl/msvcrt 文件锁，避免两个 engine 进程
    同时写 state.json 互相覆盖（last-write-wins 导致数据丢失）。
    """
    from datetime import datetime
    import os
    # 复制一份避免修改入参（TypedDict 实际是 dict）
    payload = dict(state)
    payload["last_updated"] = datetime.now().isoformat()

    # atomic write: 先写 .tmp，再 rename（避免半写文件被读）
    # + 文件锁防并发（Windows 用 msvcrt，POSIX 用 fcntl）
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        _acquire_lock(f)  # no-op if not supported on platform
        try:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        finally:
            _release_lock(f)
    # atomic rename（Windows 上并发 rename 可能 WinError 32 — 文件被另一进程锁）
    # 重试 3 次：第一次失败后等 50ms 让另一边的 msvcrt.locking 释放
    last_exc = None
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return
        except OSError as e:
            last_exc = e
            import time
            time.sleep(0.05 * (attempt + 1))
    # 3 次都失败：最后一次 raise（不让数据静默丢失）
    raise last_exc  # type: ignore


def load_state(path: str) -> OrchestratorState:
    """Load state from JSON. Returns a TypedDict instance."""
    with open(path, "r", encoding="utf-8") as f:
        _acquire_lock(f)
        try:
            return json.load(f)
        finally:
            _release_lock(f)


# ─────────────────────────────────────────────
# 文件锁辅助（跨平台）
# ─────────────────────────────────────────────
def _acquire_lock(file_obj) -> bool:
    """获取文件锁（独占写 / 共享读）。

    Returns:
        True: 锁成功（POSIX）或不适用（Windows / 锁库不可用）
        False: 锁失败（OS 不可重入等）

    Windows 用 msvcrt.locking（短时锁，配合 with 立即释放）：
      - msvcrt.locking(fd, mode, nbytes)
      - mode=2 = LK_LOCK, mode=8 = LK_UNLCK
    POSIX 用 fcntl.flock（多进程间文件锁）：
      - LOCK_SH (1) = 共享读 / LOCK_EX (2) = 独占写 / LOCK_UN (8) = 释放
    """
    try:
        import fcntl  # type: ignore
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX)
        return True
    except (ImportError, AttributeError):
        # Windows: 退化到 msvcrt
        try:
            import msvcrt  # type: ignore
            # 锁住当前文件指针后的 1 个字节（写场景，文件已有内容）
            msvcrt.locking(file_obj.fileno(), msvcrt.LK_LOCK, 1)
            return True
        except Exception:
            # 锁库不可用（罕见）：跳过锁而非 crash
            return False
    except OSError:
        return False


def _release_lock(file_obj) -> None:
    """释放文件锁（与 _acquire_lock 配对）。失败不抛（避免掩盖原异常）。"""
    try:
        import fcntl  # type: ignore
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)
    except (ImportError, AttributeError):
        try:
            import msvcrt  # type: ignore
            msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
        except Exception:
            pass
    except OSError:
        pass
