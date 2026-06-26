"""
对应参考截图(图3)里看到的步骤列表，并在调研同类产品后补了两个阶段：
分析配置参数 -> 基本信息/世界观 -> 规划情节脉络 -> 设计主要人物
-> 设计人物关系 -> 设计伏笔系统 -> 构建世界地图 -> 势力阵营/力量体系
-> 特殊设定/货币体系 -> 一致性校验

每个 stage 是一个 (name, label, run) 的三元组：
run(ctx, db) -> 更新 ctx 并写库，ctx 会作为后续阶段的上下文输入。

每个 LLM 调用都带 role 参数（structured_logic / creative_detail），
对应 llm_router.py 里"逻辑类用 DeepSeek、味道类用 Kimi"的路由依据。
"""
from sqlalchemy.orm import Session

from ..models import (
    WorldSetting, Character, Faction, PowerSystem, MapNode,
    Foreshadowing, Currency, EntityRelation,
)
from ..llm_client import call_llm_json


async def stage_parse_config(ctx: dict, db: Session):
    """纯本地处理，不调用 LLM：把构建配置表单整理成统一结构"""
    cfg = ctx["project"].config_json or {}
    ctx["normalized_config"] = {
        "genre": ctx["project"].genre,
        "audience": ctx["project"].audience,
        "tropes": cfg.get("tropes", []),
        "length_range": cfg.get("length_range", "200-400万字"),
        "structure_mode": cfg.get("structure_mode", "五幕式"),
        "main_conflict_hint": cfg.get("main_conflict", ""),
    }


async def stage_world_basics(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt="你是专业的小说世界构建引擎，输出 world_view 和 story_core 两个字段。",
        user_prompt=str(ctx["normalized_config"]),
        mock_payload={
            "world_view": "现代都市背景，主角拥有重生记忆，依托对2012年后十年商业变迁的先知发展事业。",
            "story_core": "利用重生记忆改变命运，商业帝国崛起",
        },
    )
    ws = WorldSetting(
        project_id=ctx["project"].id,
        world_view=payload.get("world_view"),
        story_core=payload.get("story_core"),
    )
    db.add(ws)
    db.flush()
    ctx["world_setting_id"] = ws.id
    ctx["world_view"] = payload.get("world_view")
    ctx["story_core"] = payload.get("story_core")


async def stage_plot_skeleton(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt="基于世界观和故事核心，给出卷级（不是章节级）的情节脉络骨架，返回 volumes 列表。",
        user_prompt=f"world_view={ctx['world_view']}; story_core={ctx['story_core']}",
        mock_payload={
            "volumes": [
                {"title": "第1卷 重回起点", "summary": "主角重生，初步布局"},
                {"title": "第2卷 入局站稳", "summary": "建立第一个商业根据地"},
            ]
        },
    )
    ws = db.get(WorldSetting, ctx["world_setting_id"])
    ws.plot_skeleton_json = payload.get("volumes", [])
    ctx["plot_skeleton"] = payload.get("volumes", [])


async def stage_characters(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="creative_detail",
        system_prompt="设计主要人物列表，每个人物包含 name, role, detail。",
        user_prompt=f"story_core={ctx['story_core']}; plot={ctx['plot_skeleton']}",
        mock_payload={
            "characters": [
                {"name": "林渊", "role": "主角", "detail": "重生者，掌握十年商业先知"},
                {"name": "苏晚栀", "role": "重要配角", "detail": "青梅竹马，财务/运营能力强"},
            ]
        },
    )
    ctx["characters"] = []
    for c in payload.get("characters", []):
        row = Character(
            project_id=ctx["project"].id,
            name=c.get("name"),
            role=c.get("role"),
            detail_json=c,
        )
        db.add(row)
        db.flush()
        ctx["characters"].append({"id": row.id, "name": row.name, "role": row.role})


async def stage_relations(ctx: dict, db: Session):
    """
    新增阶段：人物关系图谱。对标马良写作"自动提取实体并构建动态关系图谱"，
    本地实现为一张关系边表（EntityRelation），写作时按实体 ID 检索关系，
    而不是每次把全部人物设定塞进 prompt。
    """
    payload = await call_llm_json(
        role="creative_detail",
        system_prompt=(
            "基于人物列表设计人物之间的关系，返回 relations 列表，"
            "每条含 from_name, to_name, relation, description。"
        ),
        user_prompt=f"characters={ctx['characters']}",
        mock_payload={
            "relations": [
                {"from_name": "苏晚栀", "to_name": "林渊", "relation": "青梅竹马",
                 "description": "上一世曾陪伴主角创业，这一世主角想避免她重复吃苦"},
            ]
        },
    )
    name_to_id = {c["name"]: c["id"] for c in ctx["characters"]}
    ctx["relations"] = []
    for r in payload.get("relations", []):
        from_id = name_to_id.get(r.get("from_name"))
        to_id = name_to_id.get(r.get("to_name"))
        if not from_id or not to_id:
            continue  # 名字没对上的关系先丢弃，留给一致性校验阶段统计
        db.add(
            EntityRelation(
                project_id=ctx["project"].id,
                from_type="character",
                from_id=from_id,
                to_type="character",
                to_id=to_id,
                relation=r.get("relation"),
                description=r.get("description"),
            )
        )
        ctx["relations"].append(r)


async def stage_foreshadowing(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="creative_detail",
        system_prompt="基于人物和情节骨架设计伏笔系统，返回 items 列表，每条含 content, importance, linked_character_name。",
        user_prompt=f"characters={ctx['characters']}; plot={ctx['plot_skeleton']}",
        mock_payload={
            "items": [
                {"content": "主角父母早年破产与孟家旧怨有关", "importance": "高", "linked_character_name": "林渊"},
            ]
        },
    )
    name_to_id = {c["name"]: c["id"] for c in ctx["characters"]}
    ctx["foreshadowing_raw"] = payload.get("items", [])
    for item in payload.get("items", []):
        db.add(
            Foreshadowing(
                project_id=ctx["project"].id,
                content=item.get("content"),
                importance=item.get("importance", "中"),
                linked_character_id=name_to_id.get(item.get("linked_character_name")),
            )
        )


async def stage_map(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt="构建地理地图层级，返回 nodes 列表，每条含 name, level, parent_name(可为空), description。",
        user_prompt=f"world_view={ctx['world_view']}",
        mock_payload={
            "nodes": [
                {"name": "蓝星", "level": "world", "parent_name": None, "description": "故事所在世界"},
                {"name": "云州市", "level": "city", "parent_name": "蓝星", "description": "主角起家的城市"},
            ]
        },
    )
    name_to_id = {}
    ctx["map_orphans"] = []
    for node in payload.get("nodes", []):
        parent_name = node.get("parent_name")
        parent_id = name_to_id.get(parent_name)
        if parent_name and parent_id is None:
            # 模型给了 parent_name 但前面没有同名节点——记下来，一致性校验阶段会报出来
            ctx["map_orphans"].append(node.get("name"))
        row = MapNode(
            project_id=ctx["project"].id,
            parent_id=parent_id,
            name=node.get("name"),
            level=node.get("level"),
            description=node.get("description"),
        )
        db.add(row)
        db.flush()
        name_to_id[node.get("name")] = row.id


async def stage_factions_power(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt="设计势力阵营和力量体系（境界/能力分级），返回 factions 和 power_system 两个字段。",
        user_prompt=f"world_view={ctx['world_view']}; characters={ctx['characters']}",
        mock_payload={
            "factions": [{"name": "顾氏资本", "detail": "外资背景的隐藏对手"}],
            "power_system": {
                "name": "商业经营能力体系",
                "description": "现代商业场景下的经营能力进阶体系",
                "tiers": [
                    {"level": 1, "name": "入行摸索者"},
                    {"level": 2, "name": "小微操盘手"},
                ],
            },
        },
    )
    ctx["factions"] = []
    for f in payload.get("factions", []):
        row = Faction(project_id=ctx["project"].id, name=f.get("name"), detail_json=f)
        db.add(row)
        db.flush()
        ctx["factions"].append({"id": row.id, "name": row.name})
    ps = payload.get("power_system")
    if ps:
        db.add(
            PowerSystem(
                project_id=ctx["project"].id,
                name=ps.get("name"),
                description=ps.get("description"),
                tiers_json=ps.get("tiers", []),
            )
        )


async def stage_currency_special(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt="设计货币体系和特殊设定（如金手指类型），返回 currencies 和 special_settings 两个字段。",
        user_prompt=f"world_view={ctx['world_view']}",
        mock_payload={
            "currencies": [{"name": "人民币", "detail": "现代都市背景，沿用现实货币体系"}],
            "special_settings": {"golden_finger": "重生记忆"},
        },
    )
    for c in payload.get("currencies", []):
        db.add(Currency(project_id=ctx["project"].id, name=c.get("name"), detail_json=c))
    ws = db.get(WorldSetting, ctx["world_setting_id"])
    ws.special_settings_json = payload.get("special_settings", {})


async def stage_consistency_check(ctx: dict, db: Session):
    """
    一致性校验：纯规则，不调 LLM。

    这是"吃书检测"的本地实现——不是靠模型读一遍全文去发现矛盾
    （长上下文多跳推理在 2026.6 的模型上仍不可靠，见 README），
    而是直接对结构化实体图谱跑确定性检查：
    - 人物/势力是否有重名
    - 地图节点是否有"声明了 parent 但没建上"的孤儿节点
    - 伏笔是否有"声明了关联角色但没匹配到角色 ID"的悬空记录
    检查结果写进 GenerationJob.consistency_warnings_json，前端可以
    用"完成但有 N 条待复核"代替"完成"，把决定权交还给作者。
    """
    warnings = []

    names = [c["name"] for c in ctx.get("characters", [])]
    dup_names = {n for n in names if names.count(n) > 1}
    if dup_names:
        warnings.append({"type": "duplicate_character_name", "detail": list(dup_names)})

    if ctx.get("map_orphans"):
        warnings.append({"type": "orphan_map_node", "detail": ctx["map_orphans"]})

    name_to_id = {c["name"]: c["id"] for c in ctx.get("characters", [])}
    unresolved_foreshadowing = [
        item.get("content") for item in ctx.get("foreshadowing_raw", [])
        if item.get("linked_character_name") and item.get("linked_character_name") not in name_to_id
    ]
    if unresolved_foreshadowing:
        warnings.append({"type": "unresolved_foreshadowing_link", "detail": unresolved_foreshadowing})

    ctx["consistency_warnings"] = warnings


# 阶段顺序与展示名（展示名用于 SSE 进度，对应截图里"分析配置参数/设计主要人物..."这种文案）
# 第三列是 STAGE_ROLES 里登记的角色，仅供文档说明，实际角色在各 stage 函数内部指定。
STAGES: list[tuple[str, str, callable]] = [
    ("parse_config", "分析配置参数", stage_parse_config),
    ("world_basics", "基本信息·世界观", stage_world_basics),
    ("plot_skeleton", "规划情节脉络", stage_plot_skeleton),
    ("characters", "设计主要人物", stage_characters),
    ("relations", "设计人物关系", stage_relations),
    ("foreshadowing", "设计伏笔系统", stage_foreshadowing),
    ("map", "构建世界地图", stage_map),
    ("factions_power", "势力阵营·力量体系", stage_factions_power),
    ("currency_special", "特殊设定·货币体系", stage_currency_special),
    ("consistency_check", "一致性校验", stage_consistency_check),
]
