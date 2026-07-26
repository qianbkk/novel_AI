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
    _format_recent_events,
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


def _merge_character_states(
    existing: dict, updates: dict, *,
    window: int = 50,
) -> dict:
    """2026-07-22 Phase 2 #5：character_states 字段的 fuzzy-key dedup 合并。

    之前 `char_states = dict(...); char_states.update(updates.get(...))` 是
    dict.update() 纯替换——LLM 在不同章节可能用不同叫法指同一角色
    （\"林渊\" / \"逍遥兄\" / \"林兄\"），会新增独立 key 而不更新旧 key。
    长篇（30+ 章）实测里这种碎片化导致 character_states key 数无限
    增长，writer 检索时拿到多份互不同步的角色快照，表现为「某个配角
    的状态/设定突然对不上」且日志里无任何报错。

    修法（与 _merge_threads 同模式）：对每个 update key 在已有 keys 里
    fuzzy-equal 匹配（substring 互相包含视为同义改写）；命中则 merge value
    （更新值优先，未变化保留旧值），未命中则作为新 key 添加。
    window=50 防 LLM 一次性返 N 个新角色时 O(n²) 爆炸。
    """
    result: dict = dict(existing or {})
    existing_keys: list = list(result.keys())
    for new_name, new_state in (updates or {}).items():
        new_name_s = str(new_name).strip()
        new_state_s = str(new_state).strip() if new_state else ""
        if not new_name_s:
            continue
        merged_into = None
        for kept in existing_keys[-window:]:
            kept_s = str(kept).strip()
            # substring 互相包含视为同义（含 \"林渊\"/\"林渊兄\" 类的短叫法）
            if new_name_s == kept_s:
                merged_into = kept
                break
            if new_name_s in kept_s or kept_s in new_name_s:
                merged_into = kept
                break
        if merged_into is not None:
            # 用更新值覆盖（LLM 这次状态比上次新），但保留旧 key 字符串以稳定检索
            old_state = str(result.get(merged_into, "") or "").strip()
            if new_state_s and new_state_s != old_state:
                result[merged_into] = new_state_s
            # 新名作为别名不另存——避免同一角色多 key
        else:
            # 真新角色 / 全新叫法
            if new_state_s:
                result[new_name_s] = new_state_s
            existing_keys.append(new_name_s)
    return result


TRACKER_SYSTEM = """你是叙事状态追踪AI。阅读本章正文，提取状态变化并更新记录。
严格输出JSON，不输出任何其他内容。

【字段】（**只填有变化的字段；没变化就输出 null 或不输出**）
- chapter_summary（必填）：50字以内本章摘要
- protagonist_level（仅境界变化）：新境界名
- protagonist_level_num（仅变化）：新等级数字
- protagonist_points（仅变化）：新点数
- inventory_add / inventory_remove（仅本章有变化）：道具列表
- character_states（**仅本章登场或状态改变的角色**）：{"角色名": "一句话状态"}
- active_threads（**本章涉及的当前活跃剧情线**）：["..."]（不要重复每章已存在的）
- new_closed_threads（仅本章真正关闭）：["..."]
- last_chapter_ending：最后100字核心内容
- scene_location（仅变化）：本章结束时所在地点
- time_context（仅变化）：本章结束时的时间背景
- new_foreshadowing（仅本章明确埋设的）：[{"desc":"…","target_arc":弧ID数字}]
- resolved_foreshadowing（仅本章明确回收的）：["…"]

【关键约束】
1. **宁缺勿滥**：拿不准的字段不要瞎填，没变化就不要输出，让 history 自动延续。
2. **不要复述全文**：每条 30 字以内。
3. **只返回 JSON**，连 markdown fence 也不要。"""


def run_tracker(chapter_text: str, task: dict, current_memory: dict, novel_id: str,
                *, unverified: bool = False) -> tuple[dict, float]:
    """提取章节状态变化，更新 L2 hot/cold/constraints/meta 四层。

    unverified=True 表示这一章没过质量门（human_escalation 路径）。仍然记进
    记忆是有意的折中——完全跳过会让 L2 缺这一章，100+ 章长篇里漂移更严重——
    但摘要会被打上 unverified 标记，让下游能区分「已确认的剧情事实」和
    「待人工修订的草稿内容」，避免低质内容被当成既成事实持续污染后续章节。
    """
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
    # 一期修复（复盘 P5）：tracker 解析失败时**自动 reformat retry 一次**——
    # 把上次 LLM 原始输出 + 重写指令再喂一次，命中率显著高于零样本。
    # 原始目标：96% 失败率 → < 10%（DOC/Re3 验证的通用模式）。
    updates = parse_llm_json_response(resp, None)
    if updates is None:
        retry_prompt = (
            f"{context}\n\n"
            f"【上一次你的输出无法被解析为 JSON，原文如下】\n{resp[:1500]}\n\n"
            "请重新审视并严格按 schema 输出纯 JSON，不要任何解释/markdown fence。"
        )
        try:
            resp2, cost2 = router.call(
                agent_name="tracker",
                system_prompt=TRACKER_SYSTEM,
                user_prompt=retry_prompt,
                max_tokens=1200,
                temperature=0.0,
            )
            cost += cost2
            updates = parse_llm_json_response(resp2, None)
            if updates is not None:
                log.info(
                    "tracker parse retry succeeded for chapter %s",
                    task.get("chapter_number"),
                )
        except Exception as e:
            log.warning("tracker retry LLM call failed: %s", e)

    if updates is None:
        log.warning(
            "tracker LLM JSON parse failed after retry for chapter %s: resp[:200]=%r",
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

    # Phase 2 #5 (2026-07-22)：character_states 走 fuzzy-key dedup，
    # 避免 LLM 在不同章节用不同叫法指同一角色时新增独立 key 导致碎片化。
    if "character_states" in updates:
        hot["character_states"] = _merge_character_states(
            hot.get("character_states", {}),
            updates.get("character_states", {}),
        )

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
        entry = {"chapter": task["chapter_number"], "summary": updates["chapter_summary"]}
        if unverified:
            # 2026-07-26：没过质量门的章节仍然记进记忆（防 L2 缺章），但必须打标。
            # 否则下游把草稿内容当成已确认的剧情事实，低质内容顺着 recent_events
            # 污染后续每一章的写作上下文 —— 质量债会一路传染。
            entry["unverified"] = True
        summaries.append(entry)
        hot["recent_summaries"] = summaries
        hot["recent_events"] = _format_recent_events(summaries[-5:])

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
    # Phase A fix：maybe_compress_hot_to_cold 现在返回 (memory, compress_cost)，
    # 二次摘要的真实 LLM 花费需要累加到本次 run 的 cost 上，否则
    # state["budget_used_usd"] 漏记 → BUDGET_HARD 硬停阈值失效。
    current_memory, compress_cost = maybe_compress_hot_to_cold(current_memory, novel_id)

    save_l2(novel_id, current_memory)
    return current_memory, cost + compress_cost
