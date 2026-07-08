"""
正向不猜 novel_AI 内部字段名，只写已从源码 100% 确认的 config/novel_config.json，
把世界构建结果压成一段结构化文本传给 setting_concept，交给 novel_AI 自己的
Planner 去生成完整设定包。反向回灌时，把 setting_package.json 里的全部字段
按 schema 落到 WorldSetting / Character / Faction / PowerSystem / Currency /
MapNode / Foreshadowing / RuleConfig 等表，novel_ai_raw_setting_json 仍然
完整保留原文件，任何字段都能从那里手动找到。

为什么这里要广撒网：之前的版本只写了 plot_skeleton_json + novel_ai_raw_setting_json，
结果 WorldSetting.world_view 全空、世界立法（人物/势力/伏笔/地图/货币）表全空，
前端 WorldBuild 页"世界观/人物阵营/世界立法"三个 Tab 全渲染不出东西。
这次按 setting_package.json 实际字段全量灌一次，导入幂等（重复调用会先清旧行）。
"""
import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..models import (
    Project, WorldSetting, Character, Faction, PowerSystem,
    Currency, MapNode, Foreshadowing, RuleConfig, EntityRelation,
    Chapter, ChapterCharacter,
)
from ..logging_setup import get_logger
# 迭代 #43: novel_config.json 之前直接 .write_text(json.dumps(...)) —
# 半写损坏 → 下次 push concept 失败 / 整个 worldbuild 流卡住。
# 改用 engine.utils.atomic_write_json 统一 atomic write 模式。
from engine.utils import atomic_write_json

log = get_logger("novel_ai.setting_sync")

KNOWN_CHARACTER_KEYS = ["key_characters", "characters", "main_characters", "character_list"]
KNOWN_POWER_KEYS = ["power_system", "power_levels", "ability_system"]


# ─────────────────────────────────────────────
# 正向：concept → novel_config.json
# ─────────────────────────────────────────────
async def push_setting_concept(project_id: str, novel_ai_dir: str, db: Session) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"project {project_id} 不存在")
    world = db.query(WorldSetting).filter_by(project_id=project_id).first()
    characters = db.query(Character).filter_by(project_id=project_id).all()
    factions = db.query(Faction).filter_by(project_id=project_id).all()

    if world is None:
        world_view_text = ""
        story_core_text = ""
    else:
        world_view_text = world.world_view or ""
        story_core_text = world.story_core or ""

    cfg = project.config_json or {}
    tropes = cfg.get("tropes", [])
    length_range = cfg.get("length_range", "200-400万字（长篇）")
    main_conflict = cfg.get("main_conflict", "")
    # platform 字段：来自 project.config_json.platform（前端 /api/projects POST 时
    # 已经支持 config_json.platform）。支持的值：
    #   fanqie | qidian | qimao —— 走对应平台合规
    #   personal | none | internal —— 跳过平台合规（个人原型 / 自存档用）
    # 默认 fanqie 保持向后兼容。
    platform = cfg.get("platform", "fanqie")

    if not world_view_text and not story_core_text:
        concept = "\n".join([
            f"题材：{project.genre}",
            f"受众：{project.audience or '男频·青年向'}",
            f"篇幅：{length_range}",
            f"叙事套路：{'、'.join(tropes) if tropes else '系统流'}",
            f"主要冲突/方向：{main_conflict or '主角在力量体系下崛起，经历多弧冲突，最终抵达力量巅峰。'}",
            f"风格调性：番茄爽文，节奏紧凑、爽点密集、对话口语化",
        ])
    else:
        concept = "\n".join([
            f"世界观：{world_view_text}",
            f"故事核心：{story_core_text}",
            "主要人物：" + "；".join(f"{c.name}（{c.role}）" for c in characters) or "（未设定）",
            "主要势力：" + "；".join(f.name for f in factions) or "（未设定）",
        ])
    novel_config = {
        "novel_id": project.id,
        "platform": platform,
        "genre": project.genre,
        "setting_concept": concept,
        "budget_limit_usd": project.budget_limit_usd or 500.0,
    }
    config_dir = Path(novel_ai_dir, "config")
    config_dir.mkdir(parents=True, exist_ok=True)
    # 迭代 #43: 改用 atomic_write_json，避免半写损坏
    atomic_write_json(
        str(Path(config_dir, "novel_config.json")),
        novel_config,
    )
    project.novel_ai_status = "concept_pushed"
    db.commit()
    log.info("push-concept project=%s, concept_len=%d", project_id, len(concept))
    return novel_config


# ─────────────────────────────────────────────
# 反向：setting_package.json → DB 全量
# ─────────────────────────────────────────────
async def pull_setting_package(project_id: str, novel_ai_dir: str, db: Session) -> dict:
    setting_path = Path(novel_ai_dir, "output", "setting_package.json")
    if not setting_path.exists():
        raise FileNotFoundError(
            f"setting_package.json 不存在：{setting_path}。"
            "请先 POST /bridge/run command=planner。"
        )
    # 迭代 #35: catch JSON 解析错误 + 编码错误，throw 清晰 ValueError
    # 而不是让原始 traceback 暴露给前端（之前损坏文件 → 500 + 几百行 Python traceback）
    try:
        raw = json.loads(setting_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        log.error("pull-setting: %s 解析失败：%s", setting_path, e)
        raise ValueError(
            f"setting_package.json 损坏（{type(e).__name__}）：{e}。"
            f"请重新跑 POST /bridge/run command=planner 重新生成。"
        ) from e
    log.info("pull-setting project=%s, top_keys=%s", project_id, list(raw.keys()))

    # v3: 校验 setting_package.json 是否符合 schema。fail-fast，
    # 否则「LLM 漏字段」会让 DB 静默缺失（之前 world_view=0 字 / 伏笔=0
    # 的根因之一）。planner 端已经校验过一次，这里再守一道防止手工改文件。
    try:
        from ..schema_validator import validate_setting_package, SchemaError
        validate_setting_package(raw)
    except SchemaError as e:
        log.error("pull-setting: %s", e)
        raise

    # 0. WorldSetting 行
    world = db.query(WorldSetting).filter_by(project_id=project_id).first()
    if world is None:
        world = WorldSetting(project_id=project_id)
        db.add(world)
        db.flush()
    world.novel_ai_raw_setting_json = raw

    project = db.get(Project, project_id)
    if raw.get("title_candidates") and (not project.title or project.title == project.id):
        project.title = raw["title_candidates"][0]

    # 1. world_view + story_core + plot_skeleton
    ws = raw.get("world_setting", {}) or {}
    world_lines = []
    if ws.get("hidden_world_name"):
        world_lines.append(f"【隐秘世界】{ws['hidden_world_name']}")
    if ws.get("surface_world_name"):
        world_lines.append(f"【表世界】{ws['surface_world_name']}")
    if ws.get("hidden_world_history"):
        world_lines.append(f"【历史】{ws['hidden_world_history']}")
    if ws.get("unique_elements"):
        world_lines.append("【独特元素】" + "；".join(ws["unique_elements"]))
    world_view_text = "\n".join(world_lines)
    if world_view_text:
        world.world_view = world_view_text

    proto = raw.get("protagonist", {}) or {}
    protagonist_line = (
        f"主角：{proto.get('name','未命名')}（{proto.get('age','?')}岁），"
        f"{proto.get('background','')}；性格：{proto.get('personality','')}；"
        f"觉醒：{proto.get('awakening_trigger','')}；初始境界：{proto.get('initial_power_level','')}"
    )
    tagline = raw.get("tagline", "")
    story_core_text = (tagline + "\n" if tagline else "") + protagonist_line
    if story_core_text.strip():
        world.story_core = story_core_text.strip()

    arcs = raw.get("arc_outline", []) or []
    world.plot_skeleton_json = [
        {
            "arc_id": a.get("arc_id"),
            "arc_name": a.get("arc_name"),
            "arc_goal": a.get("arc_goal"),
            "estimated_chapters": a.get("estimated_chapters"),
            "arc_climax_description": a.get("arc_climax_description"),
            "emotion_curve": a.get("emotion_curve"),
            "new_characters_introduced": a.get("new_characters_introduced", []),
            "arc_ending_state": a.get("arc_ending_state"),
            "is_final_arc": a.get("is_final_arc", False),
        }
        for a in arcs
    ]
    # 特殊设定：金手指 / 套路
    world.special_settings_json = {
        "protagonist": proto,
        "tagline": tagline,
        "golden_chapter_hooks": raw.get("golden_chapter_hooks", {}),
    }

    # 2. 幂等：先清掉旧的关联行（保留 novel_ai_raw_setting_json 已有内容）
    # P0 修复（iter #85）：删除顺序必须先删子表再删父表，否则 FK 约束失败：
    #   - ChapterCharacter → Character（chapter_characters 表存 character_id FK）
    #   - EntityRelation → Character（from_id/to_id 都可能指向 character）
    # 之前 7 个 delete 不级联 → 第 1 个 Character.delete() 报
    #   FOREIGN KEY constraint failed（重 pull setting 时）
    db.query(ChapterCharacter).filter(
        ChapterCharacter.chapter_id.in_(
            db.query(Chapter.id).filter_by(project_id=project_id).subquery()
        )
    ).delete(synchronize_session=False)
    db.query(EntityRelation).filter_by(project_id=project_id).delete()
    db.query(Foreshadowing).filter_by(project_id=project_id).delete()
    db.query(MapNode).filter_by(project_id=project_id).delete()
    db.query(Currency).filter_by(project_id=project_id).delete()
    db.query(PowerSystem).filter_by(project_id=project_id).delete()
    db.query(Faction).filter_by(project_id=project_id).delete()
    db.query(Character).filter_by(project_id=project_id).delete()

    # 3. 人物：从 key_characters + protagonist
    imported_characters = 0
    char_id_by_name: dict[str, str] = {}

    def _add_character(name: str, role: str | None, detail: dict) -> str:
        c = Character(
            project_id=project_id,
            name=name or "未命名",
            role=role,
            detail_json=detail,
        )
        db.add(c)
        db.flush()
        return c.id

    if proto.get("name"):
        cid = _add_character(proto["name"], "主角", proto)
        char_id_by_name[proto["name"]] = cid
        imported_characters += 1
    for key in KNOWN_CHARACTER_KEYS:
        if key in raw:
            for item in raw[key] or []:
                if not item.get("name"):
                    continue
                cid = _add_character(item["name"], item.get("role"), item)
                char_id_by_name[item["name"]] = cid
                imported_characters += 1
            break

    # 4. 力量体系
    imported_power = False
    for key in KNOWN_POWER_KEYS:
        if key in raw:
            ps = raw[key] or {}
            # key 不同时：power_system.tiers vs power_levels 列表项
            tiers = ps.get("levels") or ps.get("tiers")
            if not tiers and isinstance(ps.get("power_levels"), list):
                tiers = ps["power_levels"]
            db.add(PowerSystem(
                project_id=project_id,
                name=ps.get("name") or "力量体系",
                description=ps.get("description"),
                tiers_json=tiers,
            ))
            imported_power = True
            break

    # 5. 货币（来自 power_system.currency + unique_elements 推断）
    imported_currency = False
    ps = raw.get("power_system", {}) or {}
    cur = ps.get("currency")
    if cur:
        # 优先使用 LLM 直出的结构化 currency_detail，否则回退到旧 shape
        # 但旧 shape 也补充 detail 字符串字段，保证前端始终有 desc 渲染。
        cur_detail = ps.get("currency_detail") if isinstance(ps.get("currency_detail"), dict) else None
        detail_json = cur_detail or {
            "detail": f"货币：{cur}，所属力量体系：{ps.get('name', '')}",
            "exchange_rate": ps.get("currency_exchange_rate"),
            "issuers": ps.get("currency_issuers") if isinstance(ps.get("currency_issuers"), list) else [],
            "scope": ps.get("currency_scope"),
            "source": "power_system.currency",
            "power_system_name": ps.get("name"),
        }
        db.add(Currency(
            project_id=project_id,
            name=cur,
            detail_json=detail_json,
        ))
        imported_currency = True

    # 6. 势力：每弧的"new_characters_introduced"+ world unique_elements 视为线索；
    #    真正的势力名要从 unique_elements 提（人/妖/魔/灵/神/鬼族等）
    imported_factions = 0
    faction_set = set()
    for el in ws.get("unique_elements", []) or []:
        # 抓出现的人/妖/魔/灵/神/鬼族/古族/宗门等关键词
        for kw in ("人族", "妖族", "魔族", "灵族", "神族", "鬼族", "古族"):
            if kw in el and kw not in faction_set:
                faction_set.add(kw)
                db.add(Faction(
                    project_id=project_id,
                    name=kw,
                    detail_json={"source": "world_setting.unique_elements", "raw": el},
                ))
                imported_factions += 1
    # 弧标题里出现"宗"/"族"/"门"/"殿"/"盟"/"城"等也补一刀
    for a in arcs:
        aname = a.get("arc_name", "") or ""
        for suffix in ("宗", "族", "门", "殿", "盟", "城", "域"):
            if suffix in aname and aname not in faction_set:
                faction_set.add(aname)
                db.add(Faction(
                    project_id=project_id,
                    name=aname,
                    detail_json={"source": "arc_outline.arc_name", "arc_id": a.get("arc_id")},
                ))
                imported_factions += 1
                break

    # 7. 地图节点：surface_world_name 作为根大陆，
    #    arc_outline.arc_name + 隐藏世界名作为子节点
    imported_maps = 0
    surface = ws.get("surface_world_name")
    hidden = ws.get("hidden_world_name")
    root_id = None
    if surface:
        m = MapNode(project_id=project_id, name=surface, level="world",
                    description=ws.get("hidden_world_history", "")[:200])
        db.add(m); db.flush()
        root_id = m.id
        imported_maps += 1
    if hidden:
        db.add(MapNode(
            project_id=project_id, name=hidden, level="continent",
            description="隐秘世界",
        ))
        imported_maps += 1
    for a in arcs:
        aname = a.get("arc_name", "") or ""
        if aname:
            db.add(MapNode(
                project_id=project_id, name=aname, level="province",
                description=a.get("arc_goal", ""),
                parent_id=root_id,
            ))
            imported_maps += 1

    # 8. 伏笔：来自 foreshadowing_seeds
    imported_fs = 0
    for f in raw.get("foreshadowing_seeds", []) or []:
        if isinstance(f, dict):
            content = f.get("content") or f.get("desc") or "（未描述）"
            target_ch = f.get("target_arc")
            linked_name = f.get("linked_character")
        else:
            content = str(f)
            target_ch = None
            linked_name = None
        linked_id = char_id_by_name.get(linked_name) if linked_name else None
        importance = "高" if (isinstance(f, dict) and f.get("importance") == "high") else "中"
        db.add(Foreshadowing(
            project_id=project_id,
            content=content,
            linked_character_id=linked_id,
            importance=importance,
            status="未铺垫",
            planted_chapter_hint=f"第{target_ch}弧" if target_ch else None,
            payoff_chapter_hint=None,
        ))
        imported_fs += 1

    # 9. 规则中心（默认 webnovel 风格 + 套路 taboos）
    rule = db.query(RuleConfig).filter_by(project_id=project_id).first()
    if rule is None:
        rule = RuleConfig(project_id=project_id)
        db.add(rule)
    rule.style = "webnovel"
    rule.taboos_json = [
        "不出现现实国家/品牌名",
        "主角不主动投敌/背叛",
        "不允许色情/政治敏感细节",
    ]
    rule.template = (
        f"作品：{raw.get('title_candidates', ['未命名'])[0]}\n"
        f"流派：{raw.get('genre','玄幻')} | 套路："
        + "、".join((raw.get('protagonist') or {}).get('speech_quirks', []) or ["系统流"])
    )
    rule.extra_json = {"source": "pull_setting_package", "tagline": tagline}

    project.novel_ai_status = "planner_done"
    db.commit()

    log.info(
        "pull-setting OK: characters=%d factions=%d maps=%d power=%s currency=%s foreshadowing=%d",
        imported_characters, imported_factions, imported_maps,
        imported_power, imported_currency, imported_fs,
    )
    return {
        "arcs_imported": len(arcs),
        "characters_imported": imported_characters,
        "factions_imported": imported_factions,
        "map_nodes_imported": imported_maps,
        "power_system_imported": imported_power,
        "currency_imported": imported_currency,
        "foreshadowings_imported": imported_fs,
        "world_view_len": len(world_view_text),
    }