"""Tracker Agent V2 — 叙事状态追踪

使用新版 L2 热冷分离 schema，支持约束自动过期。
LLM 单次调用提取状态变化，然后合并到 L2 的 hot/cold/constraints/meta
四层。

Migrated from novel_AI/agents/tracker_agent.py.
"""
from __future__ import annotations
import json
import logging

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..utils import parse_llm_json_response, truncate_preserving_ends
from ..memory.manager import (
    save_l2, expire_constraints, maybe_compress_hot_to_cold,
)


log = logging.getLogger("novel_ai.engine.tracker")


# Phase 8 simplify: 抽出 fuzzy-dedup 子例程，3 个调用点共用。
# 之前 substring 循环在多处复制粘贴; window 由调用方决定
# (threads 用 10，cold 三件套用 50 防 O(n²) 爆炸)。
def _is_fuzzy_dup(s: str, existing: list, window: int = 10) -> bool:
    """substring 互相包含视为「同义改写」同一项。扫最近 window 条已有项。"""
    for kept in existing[-window:]:
        ks = str(kept).strip()
        if s in ks or ks in s:
            return True
    return False


def _merge_threads(existing: list, llm_returned: list) -> list:
    """Phase 8 fix #8: active_threads 的 dedup-aware 合并。

    之前 hot["active_threads"] = updates["active_threads"] 直接破坏性赋值
    —— 一个 chapter 没提某条线，LLM 漏列，这一条就永久消失。Arc-level 剧情线
    不可逆丢失，writer 后续章节脱节。

    修法: 1) LLM 当前顺序优先; 2) existing 兜底防漏列; 3) fuzzy dedup;
    4) cap 50 防 LLM 全漏列时孤儿线无限堆。
    """
    def _norm(x) -> str:
        return x.strip() if isinstance(x, str) else ""

    result: list[str] = []
    # Pass 1: LLM 当前顺序
    for t in llm_returned or []:
        s = _norm(t)
        if not s or _is_fuzzy_dup(s, result):
            continue
        result.append(s)
    # Pass 2: existing 兜底
    for t in existing or []:
        s = _norm(t)
        if not s or _is_fuzzy_dup(s, result):
            continue
        result.append(s)
    return result[:50]


def _append_dedup(existing: list, additions: list) -> list:
    """Phase 8 fix #10：cold 三件套通用 dedup append。

    同一事件 / 同条伏笔 / 同条 closed thread 在多章节被 LLM 反复提及时，
    也只记一次（substring fuzzy dedup，window=50 限制 O(n²) 上界）。
    """
    result = list(existing or [])
    for item in additions or []:
        s = str(item).strip() if item else ""
        if not s or _is_fuzzy_dup(s, result, window=50):
            continue
        result.append(item)
    return result


TRACKER_SYSTEM = """你是叙事状态追踪AI。阅读本章正文，提取状态变化并更新记录。
严格输出JSON，不输出任何其他内容：
{
  "protagonist_level": "（仅境界变化时填写）",
  "protagonist_level_num": 数字（仅变化时），
  "protagonist_points": 数字（仅变化时），
  "inventory_add": ["新增道具"],
  "inventory_remove": ["消耗道具"],
  "character_states": {"角色名": "一句话状态"},
  "active_threads": ["完整的当前剧情线列表（包含旧的未关闭线）"],
  "new_closed_threads": ["本章关闭的线"],
  "new_world_events": ["重要世界事件"],
  "last_chapter_ending": "最后100字核心内容",
  "chapter_summary": "50字以内摘要",
  "scene_location": "本章结束时所在地点",
  "time_context": "本章结束时的时间背景",
  "new_foreshadowing": [{"desc":"伏笔描述","target_arc":目标弧ID数字}],
  "resolved_foreshadowing": ["已揭开的伏笔描述"],
  "new_constraints": [{"desc":"新约束","expires_at_chapter":过期章节数,"reason":"原因"}],
  "new_facts": ["本章确立的重要事实"]
}"""


def run_tracker(chapter_text: str, task: dict, current_memory: dict, novel_id: str) -> tuple[dict, float]:
    """提取章节状态变化，更新 L2 hot/cold/constraints/meta 四层。"""
    hot = current_memory.get("hot", {})
    constraints = current_memory.get("constraints", {})

    # Phase 8 fix #7：原代码 `chapter_text[:2000]` 把弧高潮（3000-3300）截掉尾段。
    # tracker 提取的是事实（last_chapter_ending / scene_location / world_events），
    # 比 checker 主观打分更严重 — 看到错位置会直接记错事实。
    # 策略：≤4000 全送；>4000 保留头 1500 + 尾 2000（保尾段，状态多在结尾）。
    text_sample = truncate_preserving_ends(
        chapter_text, head_chars=1500, tail_chars=2000,
    )

    context = f"""【当前状态】
主角等级：{hot.get('protagonist_level','感债者')}（Lv{hot.get('protagonist_level_num',1)}）
主角点数：{hot.get('protagonist_points',0)}
道具：{json.dumps(hot.get('inventory',[]), ensure_ascii=False)}
活跃剧情线：{json.dumps(hot.get('active_threads',[]), ensure_ascii=False)}
角色状态：{json.dumps(hot.get('character_states',{}), ensure_ascii=False)[:400]}
当前约束数：{len(constraints.get('forbidden_constraints',[]))}条

【第{task['chapter_number']}章正文】
{text_sample}"""

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    resp, cost = router.call(
        agent_name="tracker",
        system_prompt=TRACKER_SYSTEM,
        user_prompt=context,
        max_tokens=1200,
        temperature=0.1,
    )
    # 迭代 #40: 之前用 parse_llm_json_response(resp, {}) — parse 失败时
    # 返回 {}，下游所有 `updates.get(...)` 都是默认值（空 list / 空 dict），
    # chapter_summary / world_events / constraints / foreshadowing **全部
    # 静默丢失**。后果：50 章跑下来 meta.tracked_chapters=50 但
    # recent_summaries=[]、world_events=[]、character_states={}——writer
    # 拿到的 memory 永远是"第 0 章状态"，文章脱节。
    # 修法：用 None 作为 default 检测 parse 失败；失败时 log warning
    # + 在 meta 里记 last_tracker_parse_failure_chapter，**不静默丢失
    # 信号**。runs / UI 可以通过 meta 看到「哪一章 tracker 失败了」。
    updates = parse_llm_json_response(resp, None)
    if updates is None:
        log.warning(
            "tracker LLM JSON parse failed for chapter %s: resp[:200]=%r",
            task.get("chapter_number"),
            (resp or "")[:200],
        )
        # meta 标记一下，下次 save_l2 写入
        meta_early = current_memory.get("meta", {})
        meta_early["last_tracker_parse_failure_chapter"] = task["chapter_number"]
        meta_early["tracker_parse_failure_count"] = meta_early.get("tracker_parse_failure_count", 0) + 1
        current_memory["meta"] = meta_early
        # 把 updates 当空 dict 处理——下面代码所有 `if "X" in updates` 走 False 分支
        updates = {}

    # 过期约束
    current_memory, _ = expire_constraints(current_memory, task["chapter_number"])

    # 更新热层
    hot = current_memory.get("hot", {})
    if "protagonist_level" in updates:
        hot["protagonist_level"] = updates["protagonist_level"]
    if "protagonist_level_num" in updates:
        hot["protagonist_level_num"] = updates["protagonist_level_num"]
    if "protagonist_points" in updates:
        hot["protagonist_points"] = updates["protagonist_points"]

    inv = list(hot.get("inventory", []))
    for item in updates.get("inventory_add", []):
        if item not in inv:
            inv.append(item)
    for item in updates.get("inventory_remove", []):
        if item in inv:
            inv.remove(item)
    hot["inventory"] = inv

    char_states = dict(hot.get("character_states", {}))
    char_states.update(updates.get("character_states", {}))
    hot["character_states"] = char_states

    # Phase 8 fix #8：active_threads 不能 LLM 一旦漏列就被静默删除。
    # 之前 `hot["active_threads"] = updates["active_threads"]` 是破坏性替换。
    # 用 _merge_threads 收下 LLM 列表 + 保留旧线（防止 LLM 漏列）。
    if "active_threads" in updates:
        hot["active_threads"] = _merge_threads(
            hot.get("active_threads", []),
            updates.get("active_threads", []),
        )

    if "last_chapter_ending" in updates:
        hot["last_chapter_ending"] = updates["last_chapter_ending"]
    # Phase 8 fix #9：scene_location / time_context 不能破坏性替换。
    # 一章节不写地点 = 主角位置未变，用旧值；不应当归零。
    if "scene_location" in updates:
        new_loc = str(updates["scene_location"] or "").strip()
        if new_loc:
            hot["scene_location"] = new_loc
    if "time_context" in updates:
        new_t = str(updates["time_context"] or "").strip()
        if new_t:
            hot["time_context"] = new_t

    # 章节摘要进热层
    if "chapter_summary" in updates:
        summaries = hot.get("recent_summaries", [])
        summaries.append({"chapter": task["chapter_number"], "summary": updates["chapter_summary"]})
        hot["recent_summaries"] = summaries
        hot["recent_events"] = " | ".join(s["summary"] for s in summaries[-5:])

    # 世界事件进冷层
    # Phase 8 fix #10：cold 三件套 append-only 但加 dedup。同一事件跨章节被
    # LLM 重提时只记一次，substring fuzzy 去重（跟 _merge_threads 同样的弱判断）。
    cold = current_memory.get("cold", {})
    world_events_deduped = _append_dedup(
        cold.get("world_events", []),
        updates.get("new_world_events", []),
    )
    cold["world_events"] = world_events_deduped[-50:]  # cap
    closed_deduped = _append_dedup(
        cold.get("closed_threads", []),
        updates.get("new_closed_threads", []),
    )
    cold["closed_threads"] = closed_deduped
    resolved_deduped = _append_dedup(
        cold.get("resolved_foreshadowing", []),
        updates.get("resolved_foreshadowing", []),
    )
    cold["resolved_foreshadowing"] = resolved_deduped

    # 约束与伏笔进 constraints 层
    constr = current_memory.get("constraints", {})
    for c in updates.get("new_constraints", []):
        # P3 fix: 模型可能返回 dict 也可能直接返回字符串，统一处理
        if isinstance(c, dict):
            desc = c.get("desc", str(c))
            exp  = c.get("expires_at_chapter", task["chapter_number"] + 20)
            reason = c.get("reason", "")
        else:
            desc = str(c)
            exp  = task["chapter_number"] + 20
            reason = ""
        fb = constr.setdefault("forbidden_constraints", [])
        fb.append({"id": f"c{len(fb)+1}", "desc": desc,
                   "expires_at_chapter": exp, "reason": reason})
    for fact in updates.get("new_facts", []):
        if isinstance(fact, dict):
            fact_text = fact.get("fact", str(fact))
        else:
            fact_text = str(fact)
        facts = constr.setdefault("established_facts", [])
        facts.append({"fact": fact_text, "established_at_chapter": task["chapter_number"]})
    for fp in updates.get("new_foreshadowing", []):
        if isinstance(fp, dict):
            desc = fp.get("desc", "")
            target_arc = fp.get("target_arc")
        else:
            desc = str(fp)
            target_arc = None
        planted = constr.setdefault("foreshadowing_planted", [])
        planted.append({"desc": desc,
                        "planted_at_chapter": task["chapter_number"],
                        "target_arc": target_arc})

    # 更新 meta
    meta = current_memory.get("meta", {})
    meta["last_updated_chapter"] = task["chapter_number"]
    meta["total_chapters_tracked"] = meta.get("total_chapters_tracked", 0) + 1

    current_memory["hot"]         = hot
    current_memory["cold"]        = cold
    current_memory["constraints"] = constr
    current_memory["meta"]        = meta

    # 热冷分离压缩
    current_memory = maybe_compress_hot_to_cold(current_memory, novel_id)

    save_l2(novel_id, current_memory)
    return current_memory, cost