"""
Tracker Agent V2 — 叙事状态追踪
使用新版L2热冷分离Schema，支持约束自动过期
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from api_client import call_llm
from memory.memory_manager import get_l2, save_l2, empty_l2, expire_constraints, maybe_compress_hot_to_cold

# 兼容旧接口
def load_memory(novel_id: str) -> dict:
    return get_l2(novel_id)

def save_memory(novel_id: str, memory: dict):
    save_l2(novel_id, memory)

def _init_memory() -> dict:
    return empty_l2()

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
    hot = current_memory.get("hot", {})
    constraints = current_memory.get("constraints", {})

    context = f"""【当前状态】
主角等级：{hot.get('protagonist_level','感债者')}（Lv{hot.get('protagonist_level_num',1)}）
主角点数：{hot.get('protagonist_points',0)}
道具：{json.dumps(hot.get('inventory',[]), ensure_ascii=False)}
活跃剧情线：{json.dumps(hot.get('active_threads',[]), ensure_ascii=False)}
角色状态：{json.dumps(hot.get('character_states',{}), ensure_ascii=False)[:400]}
当前约束数：{len(constraints.get('forbidden_constraints',[]))}条

【第{task['chapter_number']}章正文（前2000字）】
{chapter_text[:2000]}"""

    resp, cost = call_llm(
        agent_name="tracker",
        system_prompt=TRACKER_SYSTEM,
        user_prompt=context,
        max_tokens=1200,
        temperature=0.1,
    )
    resp = resp.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
    try:
        updates = json.loads(resp)
    except:
        updates = {}

    # 先清理过期约束
    current_memory, expired = expire_constraints(current_memory, task["chapter_number"])

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
        if item not in inv: inv.append(item)
    for item in updates.get("inventory_remove", []):
        if item in inv: inv.remove(item)
    hot["inventory"] = inv

    char_states = dict(hot.get("character_states", {}))
    char_states.update(updates.get("character_states", {}))
    hot["character_states"] = char_states

    if "active_threads" in updates:
        hot["active_threads"] = updates["active_threads"]

    if "last_chapter_ending" in updates:
        hot["last_chapter_ending"] = updates["last_chapter_ending"]
    if "scene_location" in updates:
        hot["scene_location"] = updates["scene_location"]
    if "time_context" in updates:
        hot["time_context"] = updates["time_context"]

    # 章节摘要进热层
    if "chapter_summary" in updates:
        summaries = hot.get("recent_summaries", [])
        summaries.append({"chapter": task["chapter_number"], "summary": updates["chapter_summary"]})
        hot["recent_summaries"] = summaries
        hot["recent_events"] = " | ".join(s["summary"] for s in summaries[-5:])

    # 世界事件进冷层
    cold = current_memory.get("cold", {})
    world_events = cold.get("world_events", [])
    world_events.extend(updates.get("new_world_events", []))
    cold["world_events"] = world_events[-50:]
    new_closed = updates.get("new_closed_threads", [])
    cold["closed_threads"] = cold.get("closed_threads", []) + new_closed
    new_resolved = updates.get("resolved_foreshadowing", [])
    cold["resolved_foreshadowing"] = cold.get("resolved_foreshadowing", []) + new_resolved

    # 约束与伏笔进constraints层
    constr = current_memory.get("constraints", {})
    for c in updates.get("new_constraints", []):
        fb = constr.setdefault("forbidden_constraints", [])
        fb.append({"id": f"c{len(fb)+1}", "desc": c.get("desc",""),
                   "expires_at_chapter": c.get("expires_at_chapter", task["chapter_number"]+20),
                   "reason": c.get("reason","")})
    for fact in updates.get("new_facts", []):
        facts = constr.setdefault("established_facts", [])
        facts.append({"fact": fact, "established_at_chapter": task["chapter_number"]})
    for fp in updates.get("new_foreshadowing", []):
        planted = constr.setdefault("foreshadowing_planted", [])
        planted.append({"desc": fp.get("desc","") if isinstance(fp,dict) else str(fp),
                        "planted_at_chapter": task["chapter_number"],
                        "target_arc": fp.get("target_arc") if isinstance(fp,dict) else None})

    # 更新meta
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
