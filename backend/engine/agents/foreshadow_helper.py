"""backend/engine/agents/foreshadow_helper.py — 二期伏笔/细纲辅助

提供三个工具函数供 outline 和 writer 共享：
  1. normalize_foreshadow_ops：把 LLM 返回的 foreshadowing_ops 标准化为
     [{op, desc, target_chapter}, ...]，op 限定 plant/reinforce/resolve。
  2. plant_seeds_from_tasks：把 outline 里 plant 操作的伏笔灌进 L2 记忆
     （与 tracker 动态写入的伏笔去重）。
  3. format_foreshadow_ops_for_prompt：把伏笔 ops 转成 writer prompt 可读的中文。
"""
from __future__ import annotations

from typing import Any


VALID_OPS = ("plant", "reinforce", "resolve")


def normalize_foreshadow_ops(ops: Any) -> list[dict]:
    """把 LLM 的 foreshadowing_ops 规整为统一结构。LLM 可能返 str / dict / list，
    兼容 3 种情况。任何异常条目丢弃。"""
    if not ops:
        return []
    out: list[dict] = []
    if not isinstance(ops, list):
        ops = [ops]
    for item in ops:
        if isinstance(item, dict):
            op = str(item.get("op") or "").lower().strip()
            desc = str(item.get("desc") or item.get("content") or "").strip()
            tc = item.get("target_chapter")
            if op not in VALID_OPS:
                # 启发式：含「回收/解决/揭开」→ resolve；含「强化/提及」→ reinforce；其余 plant
                if any(k in desc for k in ("回收", "揭开", "解决", "明白")):
                    op = "resolve"
                elif any(k in desc for k in ("强化", "再次", "重提")):
                    op = "reinforce"
                else:
                    op = "plant"
            if not desc:
                continue
            try:
                tc = int(tc) if tc is not None else None
            except (ValueError, TypeError):
                tc = None
            out.append({"op": op, "desc": desc[:200], "target_chapter": tc})
        elif isinstance(item, str):
            desc = item.strip()
            if desc:
                out.append({"op": "plant", "desc": desc[:200], "target_chapter": None})
    return out


def plant_seeds_from_tasks(tasks: list[dict], novel_id: str, save_l2=None,
                           get_l2=None) -> int:
    """把 outline 阶段 plan 出去的伏笔种进 L2.constraints.foreshadowing_planted。

    复用 seed_foreshadowing_from_setting 的幂等模式：按 desc 去重。
    返回本次新增条数。

    save_l2 / get_l2 / add_to_constraints 是依赖注入，便于单元测试。
    """
    if get_l2 is None:
        from ..memory.manager import get_l2
    if save_l2 is None:
        from ..memory.manager import save_l2

    memory = get_l2(novel_id)
    constr = memory.setdefault("constraints", {})
    planted = constr.setdefault("foreshadowing_planted", [])
    existing = {p.get("desc") for p in planted if isinstance(p, dict)}

    added = 0
    for t in tasks:
        for op in t.get("foreshadowing_ops", []):
            if op.get("op") != "plant":
                continue
            desc = op.get("desc")
            if not desc or desc in existing:
                continue
            planted.append({
                "desc": desc,
                "planted_at_chapter": t.get("chapter_number", 0),
                "target_chapter": op.get("target_chapter"),
                "target_arc": None,
                "source": "outline",
            })
            existing.add(desc)
            added += 1

    if added:
        save_l2(novel_id, memory)
    return added


def format_foreshadow_ops_for_prompt(tasks_for_chapter: list[dict]) -> str:
    """把 outline 在第 N 章前若干章里规划的所有伏笔操作，按 op 分组输出
    中文片段，给 writer 当「本章伏笔工作单」。"""
    if not tasks_for_chapter:
        return ""
    plants, reinforces, resolves = [], [], []
    for t in tasks_for_chapter:
        ch = t.get("chapter_number", "?")
        for op in t.get("foreshadowing_ops", []):
            entry = f"  [Ch{ch}] {op['desc']}"
            if op.get("op") == "plant":
                plants.append(entry)
            elif op.get("op") == "reinforce":
                reinforces.append(entry)
            elif op.get("op") == "resolve":
                resolves.append(entry)
    if not (plants or reinforces or resolves):
        return ""
    parts = ["【本章伏笔工作单】（写作时必须落实）"]
    if plants:
        parts.append("  ◆ 埋设：" + "\n".join(plants))
    if reinforces:
        parts.append("  ◇ 强化：" + "\n".join(reinforces))
    if resolves:
        parts.append("  ★ 回收：" + "\n".join(resolves))
    return "\n".join(parts) + "\n"
