"""
Summarizer Agent — 摘要生成（L5长期记忆层）
触发时机：每弧结束时 / 每50章
功能：① 弧级摘要 ② 压缩近期章节摘要为长程记忆 ③ 更新角色弧光档案
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import call_llm

L2_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "l2")
L5_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "memory", "l5")

def load_l5(novel_id: str) -> dict:
    os.makedirs(L5_DIR, exist_ok=True)
    path = os.path.join(L5_DIR, f"{novel_id}_l5.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"arc_summaries": [], "character_arcs": {}, "major_revelations": [], "compressed_history": ""}

def save_l5(novel_id: str, l5: dict):
    path = os.path.join(L5_DIR, f"{novel_id}_l5.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(l5, f, ensure_ascii=False, indent=2)

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

def summarize_arc(
    arc: dict,
    chapter_summaries: list,
    memory: dict,
    novel_id: str,
) -> tuple[dict, float]:
    """生成弧级摘要"""
    chapters_text = "\n".join(
        f"第{s['chapter']}章：{s['summary']}"
        for s in chapter_summaries
    )
    prompt = f"""【弧信息】
弧ID：{arc['arc_id']}  弧名：{arc['arc_name']}
预定目标：{arc['arc_goal']}

【本弧各章摘要】
{chapters_text}

【当前记忆状态】
主角等级：{memory.get('protagonist_level')}
活跃剧情线：{json.dumps(memory.get('active_threads', []), ensure_ascii=False)}

请生成本弧档案："""

    resp, cost = call_llm(
        agent_name="summarizer",
        system_prompt=ARC_SUMMARY_SYSTEM,
        user_prompt=prompt,
        max_tokens=1000,
        temperature=0.3,
    )
    resp = resp.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        arc_summary = json.loads(resp)
    except:
        arc_summary = {
            "arc_id": arc["arc_id"],
            "arc_name": arc["arc_name"],
            "summary_100": "（摘要生成失败，见L2记忆）",
            "key_events": [],
            "unresolved_threads": memory.get("active_threads", []),
        }

    # 更新L5
    l5 = load_l5(novel_id)
    l5["arc_summaries"].append(arc_summary)
    save_l5(novel_id, l5)

    return arc_summary, cost


COMPRESS_SYSTEM = """你是记忆压缩AI。将多个章节摘要压缩为简洁的长程记忆。
规则：
- 保留所有关键事件、重要人物状态变化、未解决的伏笔
- 删除过渡细节
- 输出纯文本，500字以内"""

def compress_history(chapter_summaries: list, novel_id: str) -> tuple[str, float]:
    """压缩章节摘要为长程记忆文本"""
    chapters_text = "\n".join(
        f"第{s['chapter']}章：{s['summary']}"
        for s in chapter_summaries
    )
    compressed, cost = call_llm(
        agent_name="summarizer",
        system_prompt=COMPRESS_SYSTEM,
        user_prompt=f"请压缩以下章节记录：\n{chapters_text}",
        max_tokens=700,
        temperature=0.2,
    )

    # 更新L5
    l5 = load_l5(novel_id)
    l5["compressed_history"] = compressed
    save_l5(novel_id, l5)

    return compressed, cost


def run_summarizer(
    trigger: str,   # "arc_end" | "every_50"
    arc: dict,
    memory: dict,
    novel_id: str,
) -> tuple[dict, float]:
    """统一摘要入口"""
    total_cost = 0.0
    result = {}

    chapter_summaries = memory.get("chapter_summaries", [])

    if trigger == "arc_end":
        arc_summary, cost = summarize_arc(arc, chapter_summaries, memory, novel_id)
        total_cost += cost
        result["arc_summary"] = arc_summary
        print(f"  📚 [Summarizer] 弧{arc['arc_id']}档案完成，成本：${cost:.4f}")

    if trigger == "every_50" or (trigger == "arc_end" and len(chapter_summaries) >= 30):
        compressed, cost = compress_history(chapter_summaries, novel_id)
        total_cost += cost
        result["compressed"] = compressed
        print(f"  📚 [Summarizer] 历史压缩完成，成本：${cost:.4f}")

    return result, total_cost
