"""
正向不猜 novel_AI 内部字段名，只写已从源码 100% 确认的 config/novel_config.json，
把世界构建结果压成一段结构化文本传给 setting_concept，交给 novel_AI 自己的
Planner 去生成完整设定包。反向回灌时，title_candidates/arc_outline 两个字段
做结构化映射，其余字段用候选 key 做 best-effort 解析，解析不到也不报错——
novel_ai_raw_setting_json 永远完整保留原文件，任何字段都能从那里手动找到。
"""
import json
from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Project, WorldSetting, Character, Faction, PowerSystem

KNOWN_CHARACTER_KEYS = ["characters", "main_characters", "character_list"]
KNOWN_POWER_KEYS = ["power_system", "power_levels", "ability_system"]


async def push_setting_concept(project_id: str, novel_ai_dir: str, db: Session) -> dict:
    project = db.get(Project, project_id)
    world = db.query(WorldSetting).filter_by(project_id=project_id).first()
    characters = db.query(Character).filter_by(project_id=project_id).all()
    factions = db.query(Faction).filter_by(project_id=project_id).all()

    concept = "\n".join([
        f"世界观：{world.world_view}",
        f"故事核心：{world.story_core}",
        "主要人物：" + "；".join(f"{c.name}（{c.role}）" for c in characters),
        "主要势力：" + "；".join(f.name for f in factions),
    ])
    novel_config = {
        "novel_id": project.id,
        "platform": "fanqie",
        "genre": project.genre,
        "setting_concept": concept,
        "budget_limit_usd": project.budget_limit_usd or 500.0,
    }
    config_dir = Path(novel_ai_dir, "config")
    config_dir.mkdir(parents=True, exist_ok=True)
    Path(config_dir, "novel_config.json").write_text(
        json.dumps(novel_config, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    project.novel_ai_status = "concept_pushed"
    db.commit()
    return novel_config


async def pull_setting_package(project_id: str, novel_ai_dir: str, db: Session) -> dict:
    setting_path = Path(novel_ai_dir, "output", "setting_package.json")
    raw = json.loads(setting_path.read_text(encoding="utf-8"))

    world = db.query(WorldSetting).filter_by(project_id=project_id).first()
    world.novel_ai_raw_setting_json = raw

    project = db.get(Project, project_id)
    if raw.get("title_candidates") and not project.title:
        project.title = raw["title_candidates"][0]

    world.plot_skeleton_json = [
        {"title": a.get("arc_name"), "summary": a.get("arc_goal")}
        for a in raw.get("arc_outline", [])
    ]

    imported_characters = 0
    for key in KNOWN_CHARACTER_KEYS:
        if key in raw:
            for item in raw[key]:
                db.add(Character(
                    project_id=project_id, name=item.get("name"),
                    role=item.get("role"), detail_json=item,
                ))
                imported_characters += 1
            break

    imported_power_system = False
    for key in KNOWN_POWER_KEYS:
        if key in raw:
            ps = raw[key]
            db.add(PowerSystem(
                project_id=project_id, name=ps.get("name", "力量体系"),
                description=ps.get("description"), tiers_json=ps.get("tiers"),
            ))
            imported_power_system = True
            break

    project.novel_ai_status = "planner_done"
    db.commit()
    return {
        "arcs_imported": len(world.plot_skeleton_json),
        "characters_imported": imported_characters,
        "power_system_imported": imported_power_system,
    }
