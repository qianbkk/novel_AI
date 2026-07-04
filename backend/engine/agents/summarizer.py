"""Summarizer Agent — 弧末/每50章摘要 (L5 长期记忆层)

Migrated from novel_AI/agents/summarizer_agent.py. Two triggers:
  - "arc_end"   → 弧档案 (summary_100 / key_events / ...)
  - "every_50"  → 历史压缩 (compressed_history 字符串)

Both update L5 JSON files on disk via backend.engine.memory.manager.
"""
from __future__ import annotations
import json
import logging

from ..llm.router import LLMRouter
from ..llm_router import get_active_router
from ..utils import parse_llm_json_response
from ..memory.manager import get_l5, save_l5


log = logging.getLogger("novel_ai.engine.summarizer")


ARC_SUMMARY_SYSTEM = """你是网文编辑，负责对已完成的弧进行档案整理。
输出严格JSON格式，不加任何说明：
{
  "arc_id": 弧ID,
  "arc_name": "弧名",
  "summary_100": "100字以内的弧摘要",
  "key_events": ["最重要的3-5个事件"],
  "protagonist_growth": "主角在本弧的成长变化",
  "relationships_changed": ["关系变化描述"],
  "unresolved_threads": ["遗留到下一弧的剧情线"],
  "foreshadowing_planted": ["本弧植入的主要伏笔"],
  "ending_state": "本弧结束时的整体状态描述"
}"""


def summarize_arc(arc: dict, chapter_summaries: list, memory: dict, novel_id: str) -> tuple[dict, float]:
    """生成弧级摘要，更新 L5。"""
    chapters_text = "\n".join(
        f"第{s['chapter']}章：{s['summary']}"
        for s in chapter_summaries
    )
    prompt = f"""【弧信息】
弧ID：{arc.get('arc_id','?')}  弧名：{arc.get('arc_name','?')}
预定目标：{arc.get('arc_goal','')}

【本弧各章摘要】
{chapters_text}

【当前记忆状态】
主角等级：{memory.get('protagonist_level')}
活跃剧情线：{json.dumps(memory.get('active_threads', []), ensure_ascii=False)}

请生成本弧档案："""

    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    resp, cost = router.call(
        agent_name="summarizer",
        system_prompt=ARC_SUMMARY_SYSTEM,
        user_prompt=prompt,
        max_tokens=1000,
        temperature=0.3,
    )
    arc_summary = parse_llm_json_response(resp, None)
    if arc_summary is None:
        # 跟 tracker.py iter #40 同型：parse 失败时 log warning 让运维知道。
        # 用户拿到的是 placeholder（"（摘要生成失败，见L2记忆）"），但日志有
        # resp[:200] 可以让配置 bug 暴露（不需要重新跑 run 才知道哪次失败）。
        log.warning(
            "summarizer.弧档案 JSON parse failed for arc %s: resp[:200]=%r",
            arc.get("arc_id"),
            (resp or "")[:200],
        )
        arc_summary = {
            "arc_id": arc.get("arc_id"),
            "arc_name": arc.get("arc_name"),
            "summary_100": "（摘要生成失败，见L2记忆）",
            "key_events": [],
            "unresolved_threads": memory.get("active_threads", []),
            # iter #47: 加 _parse_failed=True 标记，让 UI / 后续审计能识别
            "_parse_failed": True,
        }

    # 更新 L5
    l5 = get_l5(novel_id)
    l5["arc_summaries"].append(arc_summary)
    save_l5(novel_id, l5)
    return arc_summary, cost


COMPRESS_SYSTEM = """你是记忆压缩AI。将多个章节摘要压缩为简洁的长程记忆。
规则：
- 保留所有关键事件、重要人物状态变化、未解决的伏笔
- 删除过渡细节
- 输出纯文本，500字以内"""


def compress_history(chapter_summaries: list, novel_id: str) -> tuple[str, float]:
    """压缩章节摘要为长程记忆文本，更新 L5.compressed_history。"""
    chapters_text = "\n".join(
        f"第{s['chapter']}章：{s['summary']}"
        for s in chapter_summaries
    )
    router: LLMRouter | None = get_active_router()
    if router is None:
        router = LLMRouter()
    compressed, cost = router.call(
        agent_name="summarizer",
        system_prompt=COMPRESS_SYSTEM,
        user_prompt=f"请压缩以下章节记录：\n{chapters_text}",
        max_tokens=700,
        temperature=0.2,
    )

    l5 = get_l5(novel_id)
    l5["compressed_history"] = compressed
    save_l5(novel_id, l5)
    return compressed, cost


def run_summarizer(trigger: str, arc: dict, memory: dict, novel_id: str) -> tuple[dict, float]:
    """统一摘要入口。trigger: "arc_end" | "every_50"."""
    total_cost = 0.0
    result = {}

    chapter_summaries = memory.get("hot", {}).get("recent_summaries", [])

    if trigger == "arc_end":
        arc_summary, cost = summarize_arc(arc, chapter_summaries, memory, novel_id)
        total_cost += cost
        result["arc_summary"] = arc_summary
        print(f"  📚 [Summarizer] 弧{arc.get('arc_id','?')}档案完成，成本：${cost:.4f}")

    if trigger == "every_50" or (trigger == "arc_end" and len(chapter_summaries) >= 30):
        compressed, cost = compress_history(chapter_summaries, novel_id)
        total_cost += cost
        result["compressed"] = compressed
        print(f"  📚 [Summarizer] 历史压缩完成，成本：${cost:.4f}")

    return result, total_cost