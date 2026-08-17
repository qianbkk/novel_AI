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

from sqlalchemy.orm import Session

# 迭代 #43: novel_config.json 之前直接 .write_text(json.dumps(...)) —
# 半写损坏 → 下次 push concept 失败 / 整个 worldbuild 流卡住。
# 改用 engine.utils.atomic_write_json 统一 atomic write 模式。
from shared.atomic_io import atomic_write_json  # 2026-07-25 抽离（修 P0 双向 import）

from ..logging_setup import get_logger
from ..models import (
    Chapter,
    ChapterCharacter,
    Character,
    Currency,
    EntityRelation,
    Faction,
    Foreshadowing,
    MapNode,
    PowerSystem,
    Project,
    RuleConfig,
    WorldSetting,
)

log = get_logger("novel_ai.setting_sync")

KNOWN_CHARACTER_KEYS = ["key_characters", "characters", "main_characters", "character_list"]
KNOWN_POWER_KEYS = ["power_system", "power_levels", "ability_system"]


# ─────────────────────────────────────────────
# 正向：concept → novel_config.json
# ─────────────────────────────────────────────
def _build_worldbuild_snapshot(project_id: str, db: Session) -> dict:
    """一期修复（根因 #3：推送压扁）：把 worldbuild 的结构化产出打包成快照。

    之前 push 只传一段拼接文本（世界观概要+人物名+势力名），7 段世界观/
    角色卡/关系/伏笔/力量体系全部丢弃，planner 拿概念从头重编一套设定——
    两套世界观只共享一段模糊文字。现在把结构化数据随 novel_config.json
    一起传给 planner，planner 降级为「补全者」（沿用实体，只补缺失字段）。
    """
    world = db.query(WorldSetting).filter_by(project_id=project_id).first()
    characters = db.query(Character).filter_by(project_id=project_id).all()
    factions = db.query(Faction).filter_by(project_id=project_id).all()
    powers = db.query(PowerSystem).filter_by(project_id=project_id).all()
    foreshadowings = db.query(Foreshadowing).filter_by(project_id=project_id).all()

    snapshot: dict = {}
    if world:
        if world.world_view_rich_json:
            snapshot["world_view_rich"] = world.world_view_rich_json
        if world.story_core_struct_json:
            snapshot["story_core_struct"] = world.story_core_struct_json
        if world.history_timeline_json:
            snapshot["history_timeline"] = world.history_timeline_json
        if world.plot_skeleton_json:
            snapshot["plot_skeleton"] = world.plot_skeleton_json
    if characters:
        snapshot["characters"] = [
            {
                "name": c.name, "role": c.role or "配角",
                "basic": c.card_basic_json,
                "personality": c.card_personality_json,
                "background": c.card_background_json,
                "abilities": c.card_abilities_json,
                "catchphrase": c.card_catchphrase_json,
                "arc": c.card_arc_json,
            }
            for c in characters
        ]
    if factions:
        snapshot["factions"] = [
            {"name": f.name, "detail": f.detail_json} for f in factions
        ]
    if powers:
        snapshot["power_systems"] = [
            {"name": p.name, "description": p.description, "tiers": p.tiers_json}
            for p in powers
        ]
    if foreshadowings:
        snapshot["foreshadowings"] = [
            {
                "content": fs.content, "importance": fs.importance,
                "status": fs.status,
                "planted_chapter_hint": fs.planted_chapter_hint,
                "payoff_chapter_hint": fs.payoff_chapter_hint,
            }
            for fs in foreshadowings
        ]
    return snapshot


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
            "风格调性：番茄爽文，节奏紧凑、爽点密集、对话口语化",
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
        # 一期修复：结构化世界观快照随行（planner 有则沿用，无则自行生成）
        "worldbuild_snapshot": _build_worldbuild_snapshot(project_id, db),
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

    # 2026-07-25 软验证（修 P0-6 短板核心链路 Pydantic 化）：
    # 在 jsonschema 校验后用 Pydantic SettingPackage model_validate 验证
    # 一遍 — 给下游代码提供 pkg.protagonist.name 类型的强类型访问（替代
    # 30+ 处 dict.get 裸访问）。
    #
    # P2-14（2026-08-17）：Pydantic 失败必须 raise，不能再静默 fallback 裸 dict。
    # 之前 fallback 让 schema 漂移（planner LLM 输出 "keyCharacter" 而不是
    # "key_characters"）藏起来 → 下游 8 段角色卡全空 → writer 拿到的【世界观
    # 速览】全空 → 角色硬编名字。
    #
    # 与 jsonschema（log.error 不 raise）不同语义：jsonschema 失败常因老项目
    # setting 字段漂移，raise 会让所有旧项目 bootstrap 失败（向后兼容优先）；
    # Pydantic 失败是 schema 严重漂移，必须 raise 阻断污染下游。
    try:
        from shared.setting_models import SettingPackage
        pkg = SettingPackage.model_validate(raw)
        log.info(
            "pull-setting: Pydantic SettingPackage 验证通过 — "
            "protagonist=%r, arc_count=%d, char_count=%d",
            pkg.protagonist.name, len(pkg.arc_outline), len(pkg.key_characters),
        )
    except Exception as pyd_err:
        log.error(
            "pull-setting: Pydantic SettingPackage 验证失败（schema 漂移阻断）: %s",
            pyd_err,
        )
        raise  # 不让裸 dict 路径兜底污染下游 writer

    # v3: 校验 setting_package.json 是否符合 schema。fail-fast，
    # 否则「LLM 漏字段」会让 DB 静默缺失（之前 world_view=0 字 / 伏笔=0
    # 的根因之一）。planner 端已经校验过一次，这里再守一道防止手工改文件。
    try:
        from ..schema_validator import SchemaError, validate_setting_package
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
    # 2026-07-24 修复（pull-setting 重复人物根因）：之前先 add protagonist 再 add key_characters，
    # 当 planner 同时把 protagonist.name 放进 key_characters 时 → 同一 name 写 2 行
    # （2026-07-24 real30ch-16862056 跑出来 7 个 character 含 2 个林渊）。
    # 修法：用 seen_names set 守门，已见名字直接 skip。
    #
    # 2026-07-25 修复（角色卡 8 段丢失根因）：之前 _add_character 只写 detail_json，
    # 重建时 stage_characters 写好的 card_*_json 8 段全丢，前端 CharacterCard.tsx 看不到
    # 「基础信息/外貌/性格/背景/能力/口癖/道具/成长弧」8 个分段，只能看到 detail_json 的旧
    # 拼接文本。修法：detail 里有完整 8 段（card=...）时把 8 段拆开分别写 card_*_json 列。
    # planner 输出的 key_characters[] 项通常没有 card 结构（只有 name/role/background），
    # 此时仍只写 detail_json — 这是正常的；但 pull-setting 完整路径里 worldbuild_snapshot
    # 已经把 8 段带过来，detail 里有 card 段时优先写 8 段列。
    imported_characters = 0
    char_id_by_name: dict[str, str] = {}
    seen_names: set[str] = set()

    def _split_card_fields(detail: dict) -> dict:
        """把 detail 里的 card 段拆成 8 段独立字段，stage_characters 写过的 8 段才能回填。
        2026-07-25 增强：如果 detail 里没有 card 段（key_characters 只有 name/role/background），
        从 detail 现有字段推演一个最简 card 8 段，避免前端 CharacterCard 看到空白。
        """
        if not isinstance(detail, dict):
            return {}
        card = detail.get("card")
        if not isinstance(card, dict):
            card = _build_minimal_card(detail)
        return {
            "card_basic_json":       card.get("basic"),
            "card_appearance_json":  card.get("appearance"),
            "card_personality_json": card.get("personality"),
            "card_background_json":  card.get("background"),
            "card_abilities_json":   card.get("abilities"),
            "card_catchphrase_json": card.get("catchphrase"),
            "card_props_json":       card.get("props"),
            "card_arc_json":         card.get("arc"),
        }

    def _build_minimal_card(detail: dict) -> dict:
        """从 key_characters 的扁平字段构造 8 段 card 兜底版本。

        key_characters 通常只有 name/role/background/speech_quirks，
        把它们映射到 8 段让 CharacterCard.tsx 至少有内容展示，
        缺哪段都填合理默认（让前端显示"待补全"而不是空白）。
        """
        _name = detail.get("name") or "未命名"  # 暂时未使用，前端 basic.identity 用 role
        role = detail.get("role") or "配角"
        background = detail.get("background") or ""
        personality_text = detail.get("personality") or ""
        speech_quirks = detail.get("speech_quirks") or []
        # 处理 speech_quirks 是 list 或 str
        if isinstance(speech_quirks, str):
            speech_quirks = [speech_quirks]
        speech_lines = [s for s in speech_quirks if isinstance(s, str) and s.strip()]
        if not speech_lines:
            speech_lines = ["（待补全）"]
        return {
            "basic": {
                "gender": "未知",
                "age": detail.get("age") if isinstance(detail.get("age"), (int, float)) else 0,
                "identity": role,
                "faction_id": None,
            },
            "appearance": {
                "height": "",
                "hair": "",
                "outfit": "",
                "distinguishing_feature": "",
            },
            "personality": {
                "tags": [role] if role else ["配角"],
                "summary": personality_text[:200] if personality_text else f"{role}，性格待补全",
            },
            "background": {
                "origin": background,
                "motivation": "",
                "secret": "",
            },
            "abilities": {
                "power_name": detail.get("initial_power_level", "") or "",
                "current_tier": "",
                "growth_potential": "",
            },
            "catchphrase": {
                "lines": speech_lines[:3],
            },
            "props": {
                "signature_item": "",
                "companion": "无",
            },
            "arc": {
                "start_state": f"登场：{role}",
                "catalyst": "（待补全）",
                "end_state": "（待补全）",
            },
        }

    def _add_character(name: str, role: str | None, detail: dict) -> str | None:
        if not name or name in seen_names:
            return None
        seen_names.add(name)
        card_fields = _split_card_fields(detail or {})
        c = Character(
            project_id=project_id,
            name=name or "未命名",
            role=role,
            detail_json=detail,
            **card_fields,
        )
        db.add(c)
        db.flush()
        return c.id

    # protagonist 先 add（更权威）
    if proto.get("name"):
        cid = _add_character(proto["name"], "主角", proto)
        if cid:
            char_id_by_name[proto["name"]] = cid
            imported_characters += 1
    for key in KNOWN_CHARACTER_KEYS:
        if key in raw:
            for item in raw[key] or []:
                cid = _add_character(item.get("name", ""), item.get("role"), item)
                if cid:
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
    # 2026-07-25 修复（货币简陋根因）：之前只从 power_system.currency 抓**单个**货币名字符串，
    # 跑完 real30ch 后 currencies 表只有 1 行「（来自快照）」，detail_json 是 placeholder。
    # 现代商战/修真题材常有"灵石+灵币+信用点"等多货币，或者"凡币+商券"双层币。
    # 修法：1) 接受 currency 是 dict 或 string；2) 接受 currencies: [...] 多货币列表；
    #       3) 从 unique_elements 文本里再扫"灵石/法币/灵币/灵石币/信用点/商券"等关键词。
    imported_currency = 0
    ps = raw.get("power_system", {}) or {}
    ps_name = ps.get("name") or "（无）"
    added_currency_names: set[str] = set()

    def _add_currency(name: str, detail: dict) -> None:
        if not name or name in added_currency_names:
            return
        added_currency_names.add(name)
        db.add(Currency(
            project_id=project_id,
            name=name,
            detail_json=detail,
        ))
        # locals 计数在闭包内累加
        nonlocal imported_currency
        imported_currency += 1

    # 5.1 优先取结构化 currency_detail 字段
    cur_detail_obj = ps.get("currency_detail")
    if isinstance(cur_detail_obj, dict) and cur_detail_obj.get("name"):
        _add_currency(cur_detail_obj["name"], {
            "detail": cur_detail_obj.get("description", ""),
            "exchange_rate": cur_detail_obj.get("exchange_rate"),
            "issuers": cur_detail_obj.get("issuers", []),
            "scope": cur_detail_obj.get("scope"),
            "source": "power_system.currency_detail",
            "power_system_name": ps_name,
        })

    # 5.2 currencies: [...] 多货币列表
    if isinstance(ps.get("currencies"), list):
        for c in ps["currencies"]:
            if isinstance(c, dict):
                _add_currency(c.get("name", ""), {
                    "detail": c.get("description", ""),
                    "exchange_rate": c.get("exchange_rate"),
                    "issuers": c.get("issuers", []),
                    "scope": c.get("scope"),
                    "source": "power_system.currencies[]",
                    "power_system_name": ps_name,
                })
            elif isinstance(c, str) and c:
                _add_currency(c, {
                    "detail": f"货币：{c}，所属力量体系：{ps_name}",
                    "exchange_rate": ps.get("currency_exchange_rate"),
                    "issuers": ps.get("currency_issuers") if isinstance(ps.get("currency_issuers"), list) else [],
                    "scope": ps.get("currency_scope"),
                    "source": "power_system.currencies[].name",
                    "power_system_name": ps_name,
                })

    # 5.3 currency 字符串（旧 shape）
    cur = ps.get("currency")
    if isinstance(cur, str) and cur and cur not in added_currency_names:
        _add_currency(cur, {
            "detail": f"货币：{cur}，所属力量体系：{ps_name}",
            "exchange_rate": ps.get("currency_exchange_rate"),
            "issuers": ps.get("currency_issuers") if isinstance(ps.get("currency_issuers"), list) else [],
            "scope": ps.get("currency_scope"),
            "source": "power_system.currency",
            "power_system_name": ps_name,
        })
    elif isinstance(cur, dict) and cur.get("name") and cur["name"] not in added_currency_names:
        _add_currency(cur["name"], {
            "detail": cur.get("description", ""),
            "exchange_rate": cur.get("exchange_rate"),
            "issuers": cur.get("issuers", []),
            "scope": cur.get("scope"),
            "source": "power_system.currency{name:...}",
            "power_system_name": ps_name,
        })

    # 5.4 从 unique_elements 文本里再扫"灵石/法币/灵币/信用点/商券/金票/银票/铜币"等
    currency_keywords = [
        "灵石", "灵币", "法币", "信用点", "商券", "金票", "银票", "铜币",
        "金币", "银币", "铜板", "法器币", "元宝", "银两", "铜钱",
        "法印", "印记", "命币", "信物", "债点",
    ]
    for el in ws.get("unique_elements", []) or []:
        if not isinstance(el, str):
            continue
        for kw in currency_keywords:
            if kw in el and kw not in added_currency_names:
                _add_currency(kw, {
                    "detail": f"从 unique_elements 文本识别的货币：「{kw}」",
                    "exchange_rate": None,
                    "issuers": [],
                    "scope": None,
                    "source": "world_setting.unique_elements",
                    "power_system_name": ps_name,
                    "raw": el[:120],
                })
                break  # 一段文本只取一个

    # 6. 势力：每弧的"new_characters_introduced"+ world unique_elements 视为线索；
    #    真正的势力名要从 unique_elements 提（人/妖/魔/灵/神/鬼族等）
    # 2026-07-25 修复（factions=0 行根因）：之前只识别"人/妖/魔/灵/神/鬼族"6 个种族关键词，
    # 对云州商道这类现代都市题材（势力是"周氏/陈家/苏氏/林家/商会"家族）一个都匹配不到，
    # 导致 31 章 real30ch 跑完后 factions 表 0 行，前端"人物阵营"tab 显示空。
    # 修法：扩展关键词覆盖「家族/门派/商会/宗门/世家」共 4 类关键词；用正则把"X氏/X家/X宗/X门/
    # X殿/X盟/X派"提取出来当 faction 名。同时把 character role 文本里的"X家长子"等也收集。
    imported_factions = 0
    faction_set: set[str] = set()

    # 6.1 从 unique_elements 文本中识别势力名
    # 现代都市/商战题材：「X氏」「X家」「X集团」「X商会」「X商号」「X堂」
    # 玄幻/修真题材：「X宗」「X门」「X殿」「X教」「X派」「X宫」「X域」「X盟」
    #
    # 2026-07-25 修：纯正则总是出问题（贪婪吃整段 / 非贪婪匹配到 0 字）。
    # 改用手动扫描：定位后缀位置（X氏/X家/X宗/...），从后缀向前找 1-2 字
    # （但不超过"地名/州/市"边界）。这样"云州林家"识别为"林家"（不是"云州林家"），
    # "云州书香门第苏家"识别为"苏家"，"云州大学任教"完全不匹配。
    faction_suffixes = (
        "氏", "家", "堂", "商号", "集团", "商会", "商行", "总号",
        "宗", "门", "教", "派", "宫", "殿", "盟", "山", "谷",
        "岛", "国", "朝", "帮", "会",
    )
    # 候选过滤：包含这些词的不是势力名
    faction_blacklist = (
        "大学", "学院", "中学", "小学", "医院", "学校", "学堂",
        "任教", "就任", "任职", "出任", "出生", "出身",
        "本书", "本作", "本卷", "本章", "本剧", "本篇",
        "家少", "家长", "家主", "家人", "家族", "家父", "家母",
        "少主", "少女", "少爷", "少妇", "少年", "少壮",
        "全家", "你家", "我家", "他家", "她家",
        "本门", "本宗", "本派", "本教", "本会",
        "掌门", "帮主", "教主", "门主", "宗主", "派主",
        "反派", "正派", "反派", "好派",
        "第十二", "第十", "第N", "N代", "代家", "代堂", "代宗", "代门",
        "前任", "现任", "初代", "末代",
        "我会", "你会", "他会", "她会", "它会", "人会",
        "你知", "我知", "他知", "她知", "它知",
        "商会总", "商会副", "集团军",
        # 误识别率高
        "香门", "书香", "掌家", "接家", "候学", "委以", "代家", "接手", "学会",
        "手家", "足家", "脚家", "嘴家", "脑家", "眼家", "耳家",
        "以集团", "以堂", "以号", "以会", "以宗", "以门", "以派",
        # 文本切分碎片
        "/", "·", "—", "-", "。", ",", "、", " ", "\t", "\n",
    )

    def _scan_faction_names(text: str) -> set[str]:
        """扫一段文字里的势力名。后缀定位 + 前缀长度 1-2 字。"""
        out: set[str] = set()
        i = 0
        while i < len(text):
            # 找最近的 suffix
            best = None
            for suf in faction_suffixes:
                if text.startswith(suf, i):
                    if best is None or len(suf) > len(best[1]):
                        best = (i, suf)
            if best is None:
                i += 1
                continue
            suf_start, suf = best
            # 向前找 1-2 个汉字（但排除"长/次/主/家/族"等强势力特征词，
            # 它们是 X 后的修饰词，不是 X 本身）
            prefix_start = suf_start
            for k in range(2, 0, -1):  # 优先 2 字前缀
                cand_start = suf_start - k
                if cand_start < 0:
                    continue
                _cand = text[cand_start:suf_start]  # noqa: F841 — 占位声明给 prefix 锚定逻辑
                # 前 1 字符（如果不是字符串开头）必须是"X州/X市"等地名前缀，
                # 或者 cand 前一字符是空格/标点/数字（边界）
                if cand_start > 0:
                    prev_char = text[cand_start - 1]
                    if prev_char in " \t\n，。、；：！？（）《》「」『』【】":
                        prefix_start = cand_start
                        break
                    # 如果 cand 是单字（k=1），且 prev 是汉字且 prev 也是势力名的一部分
                    # → 取 k=1（避免跨词吃整段"云州林家"）
                    if k == 1:
                        prefix_start = cand_start
                        break
                else:
                    # 字符串开头
                    prefix_start = cand_start
                    break
            else:
                # 2 字和 1 字都没匹配，2 字优先
                cand_start = suf_start - 2
                if cand_start < 0:
                    cand_start = max(0, suf_start - 1)
                prefix_start = cand_start
            name = text[prefix_start:suf_start + len(suf)]
            if any(bad in name for bad in faction_blacklist):
                i = suf_start + len(suf)
                continue
            if len(name) >= 2:
                out.add(name)
            i = suf_start + len(suf)
        return out
    for el in ws.get("unique_elements", []) or []:
        if not isinstance(el, str):
            continue
        for name in _scan_faction_names(el):
            if name not in faction_set:
                faction_set.add(name)
                db.add(Faction(
                    project_id=project_id,
                    name=name,
                    detail_json={"source": "world_setting.unique_elements", "raw": el[:120]},
                ))
                imported_factions += 1
        # 兼容旧 6 个种族关键词
        for kw in ("人族", "妖族", "魔族", "灵族", "神族", "鬼族", "古族"):
            if kw in el and kw not in faction_set:
                faction_set.add(kw)
                db.add(Faction(
                    project_id=project_id,
                    name=kw,
                    detail_json={"source": "world_setting.unique_elements", "raw": el},
                ))
                imported_factions += 1

    # 6.2 从 character role 推断势力（如「周氏少主」「林家长子」→ 周氏/林家）
    for char_item in (raw.get("key_characters") or []) + ([proto] if proto.get("name") else []):
        if not isinstance(char_item, dict):
            continue
        text = " ".join([
            str(char_item.get("role") or ""),
            str(char_item.get("identity") or ""),
            str(char_item.get("background") or ""),
            str(char_item.get("speech_quirks") or "") if isinstance(char_item.get("speech_quirks"), str) else " ".join(char_item.get("speech_quirks") or []),
        ])
        for name in _scan_faction_names(text):
            if name not in faction_set:
                faction_set.add(name)
                db.add(Faction(
                    project_id=project_id,
                    name=name,
                    detail_json={"source": "character_role_inferred", "raw": text[:120]},
                ))
                imported_factions += 1

    # 6.3 弧标题里出现"宗"/"族"/"门"/"殿"/"盟"/"城"等也补一刀
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
        db.add(m)
        db.flush()
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

    # 8.5 关系重建：2026-07-25 修复（entity_relations=0 行根因）：
    # 之前 pull-setting 在 step 2 删了 EntityRelation 表但 step 3-7 都没重建，
    # 导致 real30ch-16862056 跑完后 EntityRelation 表 0 行，
    # 前端 RelationGraph 显示空、CharacterCard 关系栏空。
    # 修法：从 character role / identity 文本推演"X 是 Y 的 Z"关系，写入 EntityRelation。
    imported_relations = 0
    relation_keywords = [
        ("父亲", "父子"), ("母亲", "母子"),
        ("弟弟", "兄弟"), ("哥哥", "兄弟"),
        ("妹妹", "姐妹"), ("姐姐", "姐妹"),
        ("前妻", "前夫妻"), ("妻子", "夫妻"),
        ("伯父", "伯侄"), ("叔父", "叔侄"),
        ("长子", "嫡孙"),
        ("盟友", "盟友"), ("宿敌", "宿敌"), ("对手", "对手"),
    ]
    proto_name = proto.get("name", "")
    if proto_name:
        proto_id = char_id_by_name.get(proto_name, "")
        for item in (raw.get("key_characters") or []):
            if not isinstance(item, dict) or item.get("name") == proto_name:
                continue
            other_id = char_id_by_name.get(item.get("name", ""))
            if not other_id or not proto_id or other_id == proto_id:
                continue
            item_text = " ".join([
                str(item.get("role") or ""),
                str(item.get("identity") or ""),
            ])
            for kw, rel_label in relation_keywords:
                if kw in item_text:
                    # 幂等：同 from_id+to_id 已存在就不加
                    already = db.query(EntityRelation).filter(
                        EntityRelation.project_id == project_id,
                        EntityRelation.from_id == proto_id,
                        EntityRelation.to_id == other_id,
                    ).first()
                    if not already:
                        db.add(EntityRelation(
                            project_id=project_id,
                            from_type="character", from_id=proto_id,
                            to_type="character", to_id=other_id,
                            relation=rel_label,
                            description=f"由 role='{item_text}' 推演",
                            mutual=rel_label in ("夫妻", "前夫妻", "兄弟", "姐妹", "盟友", "兄弟"),
                            intensity=7,
                        ))
                        imported_relations += 1
                    break

    # 8.6 把 characters 与 factions 关联：character role 里有"X氏少主"时建 to_type=faction 边
    faction_id_by_name: dict[str, str] = {
        f.name: f.id for f in db.query(Faction).filter_by(project_id=project_id).all()
    }
    char_rows = db.query(Character).filter(Character.project_id == project_id).all()
    for c in char_rows:
        text = " ".join([c.role or "", c.name or ""])
        for fname, fid in faction_id_by_name.items():
            if len(fname) >= 2 and fname in text and c.name != fname:
                already = db.query(EntityRelation).filter(
                    EntityRelation.project_id == project_id,
                    EntityRelation.from_id == c.id,
                    EntityRelation.to_id == fid,
                    EntityRelation.to_type == "faction",
                ).first()
                if not already:
                    db.add(EntityRelation(
                        project_id=project_id,
                        from_type="character", from_id=c.id,
                        to_type="faction", to_id=fid,
                        relation="归属",
                        description=f"由 role='{c.role}' 含 '{fname}' 推演",
                        mutual=False,
                        intensity=5,
                    ))
                    imported_relations += 1
                break  # 每个角色只归属第一个匹配势力

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
        "pull-setting OK: characters=%d factions=%d relations=%d maps=%d power=%s currency=%d foreshadowing=%d",
        imported_characters, imported_factions, imported_relations, imported_maps,
        imported_power, imported_currency, imported_fs,
    )
    return {
        "arcs_imported": len(arcs),
        "characters_imported": imported_characters,
        "factions_imported": imported_factions,
        "relations_imported": imported_relations,
        "map_nodes_imported": imported_maps,
        "power_system_imported": imported_power,
        "currency_imported": imported_currency,
        "foreshadowings_imported": imported_fs,
        "world_view_len": len(world_view_text),
    }
