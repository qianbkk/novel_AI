"""novel_AI memory manager V2 — L2 热冷分离 / 按需检索 / 约束过期

Migrated from novel_AI/memory/memory_manager.py.

Layered memory architecture:
  L2 hot        — 近 20 章活跃状态 (hot.character_states, hot.active_threads,
                 hot.recent_summaries, hot.protagonist_*, hot.inventory ...)
  L2 cold       — 远期压缩 (cold.compressed_history, cold.closed_threads,
                 cold.resolved_foreshadowing, cold.world_events)
  L2 constraints — 自动过期约束 / 已确立事实 / 已植入伏笔
  L2 meta       — novel_id / last_updated_chapter / total_chapters_tracked
  L5            — 弧级档案 (arc_summaries / character_arcs /
                 major_revelations / compressed_history)

Storage: JSON files under backend/data/engine/memory/{l2,l5}/.

Entity tracker: 人物状态/道具/伏笔/时间线统一通过 L2 子字段表达
（无独立 entity 表；与原版 novel_AI 一致）。
"""
from __future__ import annotations
import glob
import json
import logging
import os
import time
from typing import Tuple

from ..config.paths import (
    L2_DIR_STR, L5_DIR_STR, STYLE_SAMPLES_DIR_STR, CHAPTERS_DIR_STR,
)
from ..config.power_levels import DEFAULT_POWER_LEVEL
from ..utils import atomic_write_json as _atomic_write_json

# 迭代 #73: module logger 之前缺失 → 4 处 silent `except Exception: continue`
# 完全没人看得见。Writer 拿到残缺上下文没有任何信号。
_log = logging.getLogger("novel_ai.engine.memory.manager")


# ── Thresholds ──
STYLE_SWITCH_THRESHOLD = 20
STYLE_UPDATE_INTERVAL  = 30
INTERNAL_MIN_SCORE     = 7.5
HOT_TO_COLD_THRESHOLD  = 20   # >20 summaries → push oldest 10 to cold


# ══════════════════════════════════════════════
# L2 helpers
# ══════════════════════════════════════════════
def empty_l2() -> dict:
    """Fresh L2 shell with all keys populated with defaults."""
    return {
        "hot": {
            "protagonist_level": DEFAULT_POWER_LEVEL,
            "protagonist_level_num": 1,
            "protagonist_points": 0,
            "inventory": [],
            "character_states": {},
            "active_threads": [],
            "last_chapter_ending": "",
            "recent_summaries": [],
            "scene_location": "",
            "time_context": "",
        },
        "cold": {
            "compressed_history": "",
            "closed_threads": [],
            "resolved_foreshadowing": [],
            "world_events": [],
        },
        "constraints": {
            "forbidden_constraints": [],
            "established_facts": [],
            "foreshadowing_planted": [],
        },
        "meta": {"novel_id": "", "last_updated_chapter": 0, "total_chapters_tracked": 0},
    }


def get_l2(novel_id: str) -> dict:
    """Load L2 from {L2_DIR}/{novel_id}_memory.json; returns empty_l2 if absent.

    迭代 #36：损坏文件不再静默返回空，而是备份为 .corrupted.{ts} 后返回空。
    下次 save_l2 仍能正常工作（写新文件），但损坏的数据被保留在备份里供排查。
    """
    os.makedirs(L2_DIR_STR, exist_ok=True)
    path = os.path.join(L2_DIR_STR, f"{novel_id}_memory.json")
    result = _load_json_or_default(path, empty_l2)
    # 兼容旧调用：补上 novel_id
    if not result.get("meta", {}).get("novel_id"):
        result["meta"]["novel_id"] = novel_id
    return result


def save_l2(novel_id: str, memory: dict) -> None:
    """Atomic write L2 记忆：先 .tmp + os.replace，避免半写文件被下次 load 读到。
    迭代 #36：之前直接 open(path, "w") 写一半进程被杀 → 文件损坏 → get_l2 静默
    返回 empty_l2 → 下次 save 覆盖空数据 → L2 记忆永久丢失。
    跟 engine.state.save_state 同样的 atomic write 模式。
    """
    os.makedirs(L2_DIR_STR, exist_ok=True)
    path = os.path.join(L2_DIR_STR, f"{novel_id}_memory.json")
    _atomic_write_json(path, memory)


def save_l5(novel_id: str, data: dict) -> None:
    """Atomic write L5 弧总结：同 save_l2 的修法。"""
    os.makedirs(L5_DIR_STR, exist_ok=True)
    path = os.path.join(L5_DIR_STR, f"{novel_id}_l5.json")
    _atomic_write_json(path, data)


# ══════════════════════════════════════════════
# L2 maintenance helpers
# ══════════════════════════════════════════════
def expire_constraints(memory: dict, current_chapter: int) -> Tuple[dict, int]:
    """Prune forbidden_constraints whose expires_at_chapter <= current_chapter."""
    forbidden = memory.get("constraints", {}).get("forbidden_constraints", [])
    active = [c for c in forbidden if c.get("expires_at_chapter", 9999) > current_chapter]
    expired = len(forbidden) - len(active)
    if expired:
        memory.setdefault("constraints", {})["forbidden_constraints"] = active
    return memory, expired


def add_constraint(memory: dict, desc: str, expires_at_chapter: int, reason: str = "") -> dict:
    """Append a forbidden constraint; returns the mutated memory."""
    forbidden = memory.setdefault("constraints", {}).setdefault("forbidden_constraints", [])
    forbidden.append({"id": f"c{len(forbidden)+1}", "desc": desc,
                      "expires_at_chapter": expires_at_chapter, "reason": reason})
    return memory


def maybe_compress_hot_to_cold(memory: dict, novel_id: str) -> dict:
    """If recent_summaries > 20, push oldest 10 into cold.compressed_history
    (truncated to last 3000 chars)."""
    summaries = memory.get("hot", {}).get("recent_summaries", [])
    if len(summaries) <= HOT_TO_COLD_THRESHOLD:
        return memory
    to_compress, keep = summaries[:10], summaries[10:]
    new_lines = "\n".join(f"Ch{s['chapter']}: {s['summary']}" for s in to_compress)
    cold = memory.get("cold", {})
    existing = cold.get("compressed_history", "")
    cold["compressed_history"] = (existing + "\n" + new_lines if existing else new_lines)[-3000:]
    memory["hot"]["recent_summaries"] = keep
    memory["cold"] = cold
    return memory


def get_chapter_relevant_context(memory: dict, task: dict) -> dict:
    """Filter hot/cold/constraints down to only what's relevant to the current task.

    Returns a ~1500-token context object the Writer prompt expects.
    """
    hot = memory.get("hot", {})
    constraints = memory.get("constraints", {})
    main_chars = set(task.get("main_characters", []) or [])
    all_states = hot.get("character_states", {})
    rel_states = {k: v for k, v in all_states.items()
                  if any(k in c or c in k for c in main_chars) or k in main_chars}
    recent = hot.get("recent_summaries", [])[-5:]
    recent_events = " | ".join(s["summary"] for s in recent) if recent else ""
    ch_num = task.get("chapter_number", 0)
    forbidden = constraints.get("forbidden_constraints", [])
    rel_forbidden = [c["desc"] for c in forbidden
                     if any(ch in c.get("desc", "") for ch in main_chars)
                     or c.get("expires_at_chapter", 9999) > ch_num][:5]
    planted = constraints.get("foreshadowing_planted", [])
    due_soon = [f["desc"] for f in planted
                if isinstance(f.get("target_arc"), int) and f.get("target_arc") <= ch_num + 30][:3]
    total_tracked = memory.get("meta", {}).get("total_chapters_tracked", 0)
    cold_summary = memory.get("cold", {}).get("compressed_history", "")[-500:] if total_tracked > 20 else ""
    return {
        "protagonist_level": hot.get("protagonist_level", "感债者"),
        "protagonist_level_num": hot.get("protagonist_level_num", 1),
        "protagonist_points": hot.get("protagonist_points", 0),
        "inventory": hot.get("inventory", []),
        "scene_location": hot.get("scene_location", ""),
        "time_context": hot.get("time_context", ""),
        "character_states": rel_states,
        "active_threads": hot.get("active_threads", []),
        "recent_events": recent_events,
        "last_chapter_ending": hot.get("last_chapter_ending", ""),
        "relevant_forbidden": rel_forbidden,
        "foreshadowing_due_soon": due_soon,
        "cold_summary": cold_summary,
    }


# ══════════════════════════════════════════════
# L5 helpers
# ══════════════════════════════════════════════
def get_l5(novel_id: str) -> dict:
    """Load L5 from {L5_DIR}/{novel_id}_l5.json。损坏文件备份后返回默认（迭代 #36）。"""
    os.makedirs(L5_DIR_STR, exist_ok=True)
    path = os.path.join(L5_DIR_STR, f"{novel_id}_l5.json")
    return _load_json_or_default(path, lambda: {
        "arc_summaries": [], "character_arcs": {},
        "major_revelations": [], "compressed_history": ""
    })


def _load_json_or_default(path: str, default_factory):
    """读 JSON，损坏时返回 default_factory()（不抛）。与 get_l2/get_l5 共享。"""
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            # 迭代 #36：损坏文件备份到 .corrupted（不静默丢失），
            # 让用户能事后取回数据
            try:
                corrupted = path + f".corrupted.{int(time.time())}"
                os.replace(path, corrupted)
                import logging
                logging.getLogger("novel_ai.memory").warning(
                    "memory file corrupted, backed up to %s: %s", corrupted, e
                )
            except Exception:
                pass
    return default_factory()


# ══════════════════════════════════════════════
# Style samples (few-shot for writer)
# ══════════════════════════════════════════════
def get_style_samples(current_chapter: int, max_chars: int = 1500) -> Tuple[list, str]:
    """Returns (samples, source). Source: 'internal' or 'external'."""
    use_internal = current_chapter >= STYLE_SWITCH_THRESHOLD
    samples, source = [], "external"
    if use_internal:
        samples = _get_internal_samples()
        if samples:
            source = "internal"
    if not samples:
        samples = _get_external_samples()
        source = "external"
    return [s[:max_chars] for s in samples[:3]], source


def _get_internal_samples() -> list:
    """Pick top-3 highest-scoring chapters from disk."""
    if not os.path.exists(CHAPTERS_DIR_STR):
        return []
    scored = []
    for mf in sorted(glob.glob(os.path.join(CHAPTERS_DIR_STR, "ch_*_meta.json"))):
        try:
            with open(mf, encoding="utf-8") as f:
                meta = json.load(f)
            if meta.get("score", 0) >= INTERNAL_MIN_SCORE:
                scored.append((meta["score"], meta["chapter_number"]))
        except Exception:  # 迭代 #73: 之前静默 continue, 改为 log.exception
            _log.exception("读取章节 meta 失败: %s", mf)
            continue
    scored.sort(reverse=True)
    result = []
    for _, ch in scored[:3]:
        p = os.path.join(CHAPTERS_DIR_STR, f"ch_{ch:04d}.txt")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    t = f.read()
                if not t.startswith("[待修订]"):
                    result.append(t[:1500])
            except Exception:  # 迭代 #73
                _log.exception("读取章节正文失败: %s", p)
                continue
    return result


def _get_external_samples() -> list:
    """Load any style_samples/*.txt (skipping auto-extracted ones)."""
    result = []
    if not os.path.exists(STYLE_SAMPLES_DIR_STR):
        return result
    for fp in sorted(glob.glob(os.path.join(STYLE_SAMPLES_DIR_STR, "*.txt")))[:3]:
        if "int_auto_" in os.path.basename(fp):
            continue
        try:
            with open(fp, encoding="utf-8") as f:
                content = f.read()
            lines = [l for l in content.split("\n") if not l.startswith("#")]
            result.append("\n".join(lines).strip()[:1500])
        except Exception:  # 迭代 #73
            _log.exception("读取外部风格样本失败: %s", fp)
            continue
    return result


def maybe_update_style_samples(current_chapter: int, novel_id: str) -> bool:
    """Auto-extract style samples every 30 chapters after ch 20."""
    if current_chapter < STYLE_SWITCH_THRESHOLD:
        return False
    if current_chapter % STYLE_UPDATE_INTERVAL != 0:
        return False
    samples = _get_internal_samples()
    if not samples:
        return False
    os.makedirs(STYLE_SAMPLES_DIR_STR, exist_ok=True)
    for i, s in enumerate(samples):
        with open(os.path.join(STYLE_SAMPLES_DIR_STR, f"int_auto_ch{current_chapter}_{i+1}.txt"),
                  "w", encoding="utf-8") as f:
            f.write(f"# 自动提取 Ch{current_chapter}\n\n{s}")
    # 删除旧的 auto 文件
    for fp in glob.glob(os.path.join(STYLE_SAMPLES_DIR_STR, "int_auto_ch*")):
        try:
            ch_in = int(os.path.basename(fp).split("ch")[1].split("_")[0])
            if ch_in < current_chapter:
                os.remove(fp)
        except Exception:  # 迭代 #73: 之前静默 pass
            _log.exception("清理旧 auto 风格文件失败: %s", fp)
            continue
    return True


# ══════════════════════════════════════════════
# Top-level writer context
# ══════════════════════════════════════════════
def get_writer_context(novel_id: str, task: dict) -> dict:
    """Top-level entry point used by Writer agent.

    Order: get_l2 → expire_constraints → get_chapter_relevant_context → append style_samples.
    """
    memory = get_l2(novel_id)
    current_chapter = task.get("chapter_number", 0)
    memory, _ = expire_constraints(memory, current_chapter)
    ctx = get_chapter_relevant_context(memory, task)
    samples, source = get_style_samples(current_chapter)
    ctx["style_samples"] = samples
    ctx["style_samples_source"] = source
    return ctx


def check_memory_health(novel_id: str) -> dict:
    """Returns {ok, issues, stats} for diagnostics."""
    m = get_l2(novel_id)
    hot = m.get("hot", {})
    constraints = m.get("constraints", {})
    issues = []
    if len(hot.get("recent_summaries", [])) > 25:
        issues.append("热层摘要过多")
    if len(hot.get("active_threads", [])) > 8:
        issues.append("活跃剧情线过多")
    if len(constraints.get("forbidden_constraints", [])) > 20:
        issues.append("约束过多")
    return {
        "ok": len(issues) == 0,
        "issues": issues,
        "stats": {
            "protagonist_level": hot.get("protagonist_level"),
            "protagonist_points": hot.get("protagonist_points", 0),
            "tracked_chapters": m.get("meta", {}).get("total_chapters_tracked", 0),
            "active_constraints": len(constraints.get("forbidden_constraints", [])),
            "foreshadowing_planted": len(constraints.get("foreshadowing_planted", [])),
        },
    }