"""
memory/memory_manager.py — 统一记忆管理 V2
L2热冷分离 / 按需检索 / 约束自动过期 / 风格样本动态切换
"""
import os, sys, json, glob, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
L2_DIR     = os.path.join(BASE_DIR, "memory", "l2")
L5_DIR     = os.path.join(BASE_DIR, "memory", "l5")
STYLE_DIR  = os.path.join(BASE_DIR, "style_samples")
CHAPTERS_DIR = os.path.join(BASE_DIR, "output", "chapters")
from config.power_levels import DEFAULT_POWER_LEVEL

STYLE_SWITCH_THRESHOLD = 20
STYLE_UPDATE_INTERVAL  = 30
INTERNAL_MIN_SCORE     = 7.5

def empty_l2() -> dict:
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
        },
        "constraints": {
            "forbidden_constraints": [],
            "established_facts": [],
            "foreshadowing_planted": [],
        },
        "meta": {"novel_id": "", "last_updated_chapter": 0, "total_chapters_tracked": 0}
    }

def get_l2(novel_id: str) -> dict:
    os.makedirs(L2_DIR, exist_ok=True)
    path = os.path.join(L2_DIR, f"{novel_id}_memory.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    m = empty_l2(); m["meta"]["novel_id"] = novel_id
    return m

def save_l2(novel_id: str, memory: dict):
    os.makedirs(L2_DIR, exist_ok=True)
    with open(os.path.join(L2_DIR, f"{novel_id}_memory.json"), "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

def expire_constraints(memory: dict, current_chapter: int) -> tuple:
    forbidden = memory.get("constraints", {}).get("forbidden_constraints", [])
    active = [c for c in forbidden if c.get("expires_at_chapter", 9999) > current_chapter]
    expired = len(forbidden) - len(active)
    if expired: memory["constraints"]["forbidden_constraints"] = active
    return memory, expired

def add_constraint(memory: dict, desc: str, expires_at_chapter: int, reason: str = "") -> dict:
    forbidden = memory.setdefault("constraints", {}).setdefault("forbidden_constraints", [])
    forbidden.append({"id": f"c{len(forbidden)+1}", "desc": desc,
                      "expires_at_chapter": expires_at_chapter, "reason": reason})
    return memory

def maybe_compress_hot_to_cold(memory: dict, novel_id: str) -> dict:
    summaries = memory.get("hot", {}).get("recent_summaries", [])
    if len(summaries) <= 20: return memory
    to_compress, keep = summaries[:10], summaries[10:]
    new_lines = "\n".join(f"Ch{s['chapter']}: {s['summary']}" for s in to_compress)
    cold = memory.get("cold", {})
    existing = cold.get("compressed_history", "")
    cold["compressed_history"] = (existing + "\n" + new_lines if existing else new_lines)[-3000:]
    memory["hot"]["recent_summaries"] = keep
    memory["cold"] = cold
    return memory

def get_chapter_relevant_context(memory: dict, task: dict) -> dict:
    hot = memory.get("hot", {})
    constraints = memory.get("constraints", {})
    main_chars = set(task.get("main_characters", []))
    all_states = hot.get("character_states", {})
    rel_states = {k:v for k,v in all_states.items()
                  if any(k in c or c in k for c in main_chars) or k in main_chars}
    recent = hot.get("recent_summaries", [])[-5:]
    recent_events = " | ".join(s["summary"] for s in recent) if recent else ""
    ch_num = task.get("chapter_number", 0)
    forbidden = constraints.get("forbidden_constraints", [])
    rel_forbidden = [c["desc"] for c in forbidden
                     if any(ch in c.get("desc","") for ch in main_chars)
                     or c.get("expires_at_chapter",9999) > ch_num][:5]
    planted = constraints.get("foreshadowing_planted", [])
    due_soon = [f["desc"] for f in planted
                if isinstance(f.get("target_arc"), int) and f.get("target_arc") <= ch_num+30][:3]
    total_tracked = memory.get("meta", {}).get("total_chapters_tracked", 0)
    cold_summary = memory.get("cold",{}).get("compressed_history","")[-500:] if total_tracked > 20 else ""
    return {
        "protagonist_level": hot.get("protagonist_level","感债者"),
        "protagonist_level_num": hot.get("protagonist_level_num",1),
        "protagonist_points": hot.get("protagonist_points",0),
        "inventory": hot.get("inventory",[]),
        "scene_location": hot.get("scene_location",""),
        "time_context": hot.get("time_context",""),
        "character_states": rel_states,
        "active_threads": hot.get("active_threads",[]),
        "recent_events": recent_events,
        "last_chapter_ending": hot.get("last_chapter_ending",""),
        "relevant_forbidden": rel_forbidden,
        "foreshadowing_due_soon": due_soon,
        "cold_summary": cold_summary,
    }

def get_l5(novel_id: str) -> dict:
    os.makedirs(L5_DIR, exist_ok=True)
    path = os.path.join(L5_DIR, f"{novel_id}_l5.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f: return json.load(f)
    return {"arc_summaries":[],"character_arcs":{},"major_revelations":[],"compressed_history":""}

def save_l5(novel_id: str, data: dict):
    os.makedirs(L5_DIR, exist_ok=True)
    with open(os.path.join(L5_DIR, f"{novel_id}_l5.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_style_samples(current_chapter: int, max_chars: int = 1500) -> tuple:
    use_internal = current_chapter >= STYLE_SWITCH_THRESHOLD
    samples, source = [], "external"
    if use_internal:
        samples = _get_internal_samples()
        if samples: source = "internal"
    if not samples:
        samples = _get_external_samples(); source = "external"
    return [s[:max_chars] for s in samples[:3]], source

def _get_internal_samples() -> list:
    if not os.path.exists(CHAPTERS_DIR): return []
    scored = []
    for mf in sorted(glob.glob(os.path.join(CHAPTERS_DIR, "ch_*_meta.json"))):
        with open(mf, encoding="utf-8") as f: meta = json.load(f)
        if meta.get("score",0) >= INTERNAL_MIN_SCORE:
            scored.append((meta["score"], meta["chapter_number"]))
    scored.sort(reverse=True)
    result = []
    for _, ch in scored[:3]:
        p = os.path.join(CHAPTERS_DIR, f"ch_{ch:04d}.txt")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f: t = f.read()
            if not t.startswith("[待修订]"): result.append(t[:1500])
    return result

def _get_external_samples() -> list:
    result = []
    for fp in sorted(glob.glob(os.path.join(STYLE_DIR, "*.txt")))[:3]:
        with open(fp, encoding="utf-8") as f: content = f.read()
        lines = [l for l in content.split("\n") if not l.startswith("#")]
        result.append("\n".join(lines).strip()[:1500])
    return result

def maybe_update_style_samples(current_chapter: int, novel_id: str) -> bool:
    if current_chapter < STYLE_SWITCH_THRESHOLD: return False
    if current_chapter % STYLE_UPDATE_INTERVAL != 0: return False
    samples = _get_internal_samples()
    if not samples: return False
    os.makedirs(STYLE_DIR, exist_ok=True)
    for i, s in enumerate(samples):
        with open(os.path.join(STYLE_DIR, f"int_auto_ch{current_chapter}_{i+1}.txt"), "w", encoding="utf-8") as f:
            f.write(f"# 自动提取 Ch{current_chapter}\n\n{s}")
    for fp in glob.glob(os.path.join(STYLE_DIR, "int_auto_ch*")):
        try:
            ch_in = int(os.path.basename(fp).split("ch")[1].split("_")[0])
            if ch_in < current_chapter: os.remove(fp)
        except: pass
    return True

def get_writer_context(novel_id: str, task: dict) -> dict:
    memory = get_l2(novel_id)
    current_chapter = task.get("chapter_number", 0)
    memory, _ = expire_constraints(memory, current_chapter)
    ctx = get_chapter_relevant_context(memory, task)
    samples, source = get_style_samples(current_chapter)
    ctx["style_samples"] = samples
    ctx["style_samples_source"] = source
    return ctx

def check_memory_health(novel_id: str) -> dict:
    m = get_l2(novel_id)
    hot = m.get("hot", {}); constraints = m.get("constraints", {})
    issues = []
    if len(hot.get("recent_summaries",[])) > 25: issues.append("热层摘要过多")
    if len(hot.get("active_threads",[])) > 8: issues.append("活跃剧情线过多")
    if len(constraints.get("forbidden_constraints",[])) > 20: issues.append("约束过多")
    return {"ok": len(issues)==0, "issues": issues,
            "stats": {"protagonist_level": hot.get("protagonist_level"),
                      "protagonist_points": hot.get("protagonist_points",0)}}
