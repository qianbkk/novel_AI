"""
Phase 2：10 阶段 worldbuild stages。prompt + mock_payload 全部升级为结构化输出。

升级后 stage_world_basics 输出 7 段世界观 + 故事核心 4 段 + 历史时间线。
升级后 stage_characters 输出 8 段角色卡。
升级后 stage_relations 输出富关系（强度 / 标签 / 演化 / 关键事件）。
升级后 stage_factions_power 让 tiers 含 summary / break_condition / cultivation_time。
升级后 stage_currency_special 让 currencies 含 exchange_rate / issuers / scope。
升级后 stage_consistency_check 追加 3 类新规则。

所有 stage 写入前调用 schema_validator，与 setting_package / chapter_meta 同模式。
"""
from sqlalchemy.orm import Session

from ..models import (
    WorldSetting, Character, Faction, PowerSystem, MapNode,
    Foreshadowing, Currency, EntityRelation,
)
from ..llm_client import call_llm_json
from ..schema_validator import (
    validate_world_view_rich, validate_character_card, validate_entity_relation_rich,
    SchemaError,
)


# ════════════════════════════════════════════════════════════════════════════
# Stage 1: parse_config —— 纯本地处理，不变
# ════════════════════════════════════════════════════════════════════════════
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


# ════════════════════════════════════════════════════════════════════════════
# Stage 2: world_basics —— 结构化 7 段世界观
# ════════════════════════════════════════════════════════════════════════════
WORLD_BASICS_SYSTEM = (
    "你是专业的小说世界构建引擎，擅长把模糊的世界观概念扩展为结构化设定包。"
    "请根据项目配置生成结构化世界观。"
    "返回 JSON："
    "{"
    "  world_view_rich: {cosmos, geography, history, society, technology, races, customs},"
    "  story_core_struct: {goal, conflict, theme, hook},"
    "  history_timeline: [{era, event, impact}, ... 至少 3 个大事件]"
    "}。"
    "world_view_rich 每段至少 60 字，7 段缺一不可；"
    "history_timeline 至少 3 条，每条 era 是年代 / 事件一句话 / impact 是对后续剧情的影响。"
)


async def stage_world_basics(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt=WORLD_BASICS_SYSTEM,
        user_prompt=(
            f"项目配置：{ctx['normalized_config']}\n"
            f"用户方向：{ctx['normalized_config'].get('main_conflict_hint', '')}"
        ),
        mock_payload=_WORLD_BASICS_MOCK,
    )
    rich = payload.get("world_view_rich") or {}
    # 写入前 schema 校验（fail-fast）
    try:
        validate_world_view_rich(rich)
    except SchemaError as e:
        raise RuntimeError(f"stage_world_basics schema 校验失败：{e}") from e

    ws = WorldSetting(
        project_id=ctx["project"].id,
        world_view=payload.get("world_view"),  # legacy 字段，老接口 fallback
        story_core=payload.get("story_core"),  # legacy 字段
    )
    db.add(ws)
    db.flush()
    # 新结构化字段
    ws.world_view_rich_json = rich
    ws.story_core_struct_json = payload.get("story_core_struct")
    ws.history_timeline_json = payload.get("history_timeline")

    # legacy 字段 fallback：如果 world_view 为空，从 rich 拼接
    if not ws.world_view:
        ws.world_view = " | ".join(
            f"【{k}】{v}" for k, v in rich.items() if isinstance(v, str)
        )[:4000]
    if not ws.story_core:
        struct = payload.get("story_core_struct") or {}
        ws.story_core = (
            f"目标：{struct.get('goal','')}｜冲突：{struct.get('conflict','')}"
            f"｜主题：{struct.get('theme','')}｜钩子：{struct.get('hook','')}"
        )[:2000]

    ctx["world_setting_id"] = ws.id
    ctx["world_view_rich"] = rich
    ctx["story_core_struct"] = payload.get("story_core_struct")
    ctx["history_timeline"] = payload.get("history_timeline")
    ctx["world_view"] = ws.world_view
    ctx["story_core"] = ws.story_core


# Mock 模式全填：7 段 + 故事核心 4 段 + 历史时间线 3 条（每段 ≥60 字）
_WORLD_BASICS_MOCK = {
    "world_view_rich": {
        "cosmos": (
            "蓝星与九天之上并存：人间是科技主导的现代都市，灵气复苏三百年，"
            "修士从江湖退入体制化宗门，明面与暗面并轨运行。天道运转以「债」为根——"
            "人情债、因果债、命数债互相转化，构成不可见的引力场，主角意外觉醒债感能力。"
        ),
        "geography": (
            "云州、临海、苍莽山脉三足鼎立。云州七区，每区驻守一个宗门分舵与商会分部；"
            "临海是港口城市，跨境贸易枢纽，妖族与人类混居；苍莽山脉是妖族祖地，"
            "深处禁入。云州城内「债街」是修士经济命脉。"
        ),
        "history": (
            "1984 年灵气潮汐初现，全球能源结构重塑，修士家族转型为隐性财阀。"
            "1998 年云州商会成立，标志修士经济联盟雏形。"
            "2012 年主角前世破产、债台高筑，是蝴蝶效应的起点。"
            "2024 年债主委员会成立，明面仲裁机构。"
        ),
        "society": (
            "修士与凡人共治，修士内部分九品：一至三品基层，四至六品中坚，"
            "七至九品决策层。修士享有债务豁免权，但需向债主委员会报备。"
            "凡人通过商业、科举、联姻三条路径上升；宗门弟子下山历练须完成最低三笔债务。"
        ),
        "technology": (
            "灵气+科技混合形态：灵力可与电路耦合，催生新型工业体系（灵石电池、灵阵芯片）。"
            "通讯基础是灵网 + 5G 双轨；交通以飞剑公交 + 高铁混运。"
            "医疗领域「丹术西医」已成主流，普通感冒已可一丹治愈。"
        ),
        "races": (
            "人族（约 70%）/ 古妖族（约 18%，含狐族、蛟族等亚族）/ 幽冥族（约 7%，"
            "近年才浮出水面）/ 混血族（约 5%，地位尴尬但偶有奇才）。"
            "三大种族各有宗门体系，互相通婚需报备。"
        ),
        "customs": (
            "祭剑节（每年农历三月初三，新人入门礼）/ 走火大会（宗门年度比武）/ "
            "长辈赐字（成年礼）。新人入门必须经三年苦修方可下山；"
            "下山前须写一封「债书」承诺此后因果。"
        ),
    },
    "story_core_struct": {
        "goal":     "主角利用先知能力打造长青基业，化解林家三代债务",
        "conflict": "先知带来的蝴蝶效应引发多方围剿——前世债主、本世新敌、宗门暗桩轮番出手",
        "theme":    "个体与时代的协商：先知不是万能，但每一次选择都在重塑格局",
        "hook":     "重生后第一次商业决策为何与上一世不同？蝴蝶究竟在哪里折翅？",
    },
    "history_timeline": [
        {"era": "1984", "event": "灵气潮汐初现",      "impact": "全球能源结构重塑，修士家族转型为隐性财阀"},
        {"era": "1998", "event": "云州商会成立",        "impact": "修士经济联盟雏形，主角祖父辈成为早期合伙人"},
        {"era": "2012", "event": "主角前世破产",        "impact": "蝴蝶效应起点；前世债主委员会开始注意到林家"},
        {"era": "2024", "event": "债主委员会成立",      "impact": "明面仲裁机构，平衡修士与凡人利益"},
        {"era": "2026", "event": "主角重生回到这一年",  "impact": "故事开篇，主角开始改写命运"},
    ],
}


# ════════════════════════════════════════════════════════════════════════════
# Stage 3: plot_skeleton —— 卷级骨架
# ════════════════════════════════════════════════════════════════════════════
async def stage_plot_skeleton(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt=(
            "基于已确立的世界观和故事核心，给出卷级（不是章节级）的情节骨架，"
            "返回 volumes 列表（3-5 卷）。"
        ),
        user_prompt=(
            f"世界观摘要：{ctx.get('world_view','')[:500]}\n"
            f"故事核心：{ctx.get('story_core','')[:500]}\n"
            f"主线冲突：{ctx['normalized_config'].get('main_conflict_hint','')}"
        ),
        mock_payload={
            "volumes": [
                {"title": "第1卷 债起云州",  "summary": "主角重生回到 2012，第一桶金与第一次还债"},
                {"title": "第2卷 入局云州",  "summary": "建立第一个商业根据地，被前世债主发现踪迹"},
                {"title": "第3卷 临海风波",  "summary": "业务扩展到港口，与古妖族初次交锋"},
                {"title": "第4卷 苍莽之约",  "summary": "深入妖族祖地，揭开先知能力的真正源头"},
            ]
        },
    )
    ws = db.get(WorldSetting, ctx["world_setting_id"])
    ws.plot_skeleton_json = payload.get("volumes", [])
    ctx["plot_skeleton"] = payload.get("volumes", [])


# ════════════════════════════════════════════════════════════════════════════
# Stage 4: characters —— 4 个角色，每个完整 8 段角色卡
# ════════════════════════════════════════════════════════════════════════════
CHARACTERS_SYSTEM = (
    "你是网文人物设计专家。基于世界观、故事核心、情节骨架，设计 4-6 个核心人物。"
    "每个人物返回完整 8 段角色卡（character_card），字段定义见 character_card.schema.json。"
    "返回 JSON 数组 characters，每项结构："
    "{ name, role, card: {basic, appearance, personality, background, abilities, catchphrase, props, arc} }。"
    "其中 personality.tags 至少 2 个标签；catchphrase.lines 至少 2 句；"
    "arc 必含 start_state / catalyst / end_state。"
)


async def stage_characters(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="creative_detail",
        system_prompt=CHARACTERS_SYSTEM,
        user_prompt=(
            f"故事核心：{ctx.get('story_core','')[:500]}\n"
            f"情节骨架：{ctx.get('plot_skeleton',[])[:3]}\n"
            f"世界观摘要：{ctx.get('world_view','')[:300]}"
        ),
        mock_payload={"characters": _CHARACTERS_MOCK},
    )
    characters = payload.get("characters") or []
    if not characters:
        raise RuntimeError("stage_characters LLM 返回空 characters 数组")

    ctx["characters"] = []
    for c in characters:
        name = c.get("name") or "未命名"
        role = c.get("role") or "配角"
        card = c.get("card") or {}

        # schema 校验
        try:
            validate_character_card(card)
        except SchemaError as e:
            raise RuntimeError(f"角色 {name} 角色卡 schema 校验失败：{e}") from e

        row = Character(
            project_id=ctx["project"].id,
            name=name,
            role=role,
            # detail_json 保留完整 card（API 一次性取全）+ legacy 字段
            detail_json={
                "card": card,
                # legacy 字段保留（老接口仍能读）
                "background": (card.get("background") or {}).get("origin", ""),
                "ability":    ((card.get("abilities") or {}).get("power_name", "") +
                               " · " + (card.get("abilities") or {}).get("current_tier", "")),
            },
        )
        # 8 段分别写列
        row.card_basic_json       = card.get("basic")
        row.card_appearance_json  = card.get("appearance")
        row.card_personality_json = card.get("personality")
        row.card_background_json  = card.get("background")
        row.card_abilities_json   = card.get("abilities")
        row.card_catchphrase_json = card.get("catchphrase")
        row.card_props_json       = card.get("props")
        row.card_arc_json         = card.get("arc")

        db.add(row)
        db.flush()
        ctx["characters"].append({
            "id": row.id, "name": name, "role": role,
            "card": card,
        })


# Mock：主角 + 3 个配角，每人完整 8 段
_CHARACTERS_MOCK = [
    {
        "name": "林渊", "role": "主角",
        "card": {
            "basic":      {"gender": "男", "age": 32, "identity": "云州林氏长子",
                           "faction_id": None},
            "appearance": {"height": "182cm", "hair": "短黑", "outfit": "深灰风衣",
                           "distinguishing_feature": "左眉尾一道陈年刀疤"},
            "personality":{"tags": ["克制", "精算", "冷面热心"],
                           "summary": "外表冷峻内心压着火，行动前必先算三步。看似无情，"
                                     "实则把所有柔软都留给身边人。偶有少年意气，但绝不冲动。"},
            "background": {"origin": "云州林氏，家道中落前是云州三大商号之一",
                           "motivation": "改写林家破产命运，偿还父亲欠下的三笔人情债",
                           "secret": "前世是 2024 年崛起的商业老兵，重生回到 2012"},
            "abilities":  {"power_name": "先知回响", "current_tier": "感债者（一品）",
                           "growth_potential": "识债者（九品封顶）"},
            "catchphrase":{"lines": ["这局我来开局。", "记住，你是来学习的，不是来算计的。",
                                     "债还不完，就别想下桌。"]},
            "props":      {"signature_item": "老旧铜怀表（母亲遗物）",
                           "companion": "瘸腿狼狗「阿斗」"},
            "arc":        {"start_state": "破产边缘的小商人，负债累累",
                           "catalyst":   "意外重生回到 2012，且觉醒债感能力",
                           "end_state":  "云州新一代商盟领袖，债主委员会实权长老"},
        },
    },
    {
        "name": "苏晚栀", "role": "重要配角",
        "card": {
            "basic":      {"gender": "女", "age": 28, "identity": "云州苏氏财务总监",
                           "faction_id": None},
            "appearance": {"height": "168cm", "hair": "乌黑长直", "outfit": "改良旗袍",
                           "distinguishing_feature": "左耳一颗红痣"},
            "personality":{"tags": ["精明", "倔强", "外冷内热"],
                           "summary": "数字敏感度极高，谈判时目光如刀；幼年丧母，"
                                     "养成独立自主的硬壳，但面对在意的人会瞬间软化。"},
            "background": {"origin": "云州苏氏旁支，自幼被嫡支排挤",
                           "motivation": "证明旁支不比嫡支差，夺回应得的继承权",
                           "secret": "前世曾暗恋林渊，但因林家破产而分离"},
            "abilities":  {"power_name": "账心通（无修为，靠商业技能）",
                           "current_tier": "商道一品",
                           "growth_potential": "商道九品"},
            "catchphrase":{"lines": ["账上说话。", "林渊，你别太自信。"]},
            "props":      {"signature_item": "一枚祖母绿胸针",
                           "companion": "无"},
            "arc":        {"start_state": "云州商圈默默无闻的旁支女",
                           "catalyst":   "被林渊拉入创业团队，逐渐成为合伙人",
                           "end_state":  "苏氏实际掌权人，与林渊形成商业帝国双核"},
        },
    },
    {
        "name": "孟浩", "role": "反派",
        "card": {
            "basic":      {"gender": "男", "age": 35, "identity": "债主委员会首席调查官",
                           "faction_id": None},
            "appearance": {"height": "178cm", "hair": "板寸灰白", "outfit": "黑色西装",
                           "distinguishing_feature": "右手无名指缺半截"},
            "personality":{"tags": ["冷血", "执着", "守序邪恶"],
                           "summary": "债主委员会的死忠，把追债视为天道；"
                                     "对林家有特殊的怨念——前世逼死林父的正是他。"},
            "background": {"origin": "债主委员会世家，三代追债人",
                           "motivation": "把林家「彻底了结」，不留任何翻身机会",
                           "secret": "前世与林父是结拜兄弟，因林父赖账而反目"},
            "abilities":  {"power_name": "债锁术（黑道修士技能）",
                           "current_tier": "识债者（七品）",
                           "growth_potential": "识债者巅峰"},
            "catchphrase":{"lines": ["欠债还钱，天经地义。", "林家，躲到哪里去？"]},
            "props":      {"signature_item": "一本黑色账簿（死者名录）",
                           "companion": "无"},
            "arc":        {"start_state": "债主委员会首席调查官，林家头号追债人",
                           "catalyst":   "发现林渊「先知」能力的存在",
                           "end_state":  "被林渊反将一军，欠下三笔新债，沦为阶下囚"},
        },
    },
    {
        "name": "顾青锋", "role": "重要配角",
        "card": {
            "basic":      {"gender": "男", "age": 30, "identity": "苍茫山脉妖族少主",
                           "faction_id": None},
            "appearance": {"height": "185cm", "hair": "银白长发", "outfit": "玄色裘袍",
                           "distinguishing_feature": "瞳孔金色"},
            "personality":{"tags": ["狂傲", "重诺", "少年感"],
                           "summary": "妖族少主，外表放荡不羁，实则重情重义；"
                                     "和林渊不打不相识，是少数知道主角秘密的盟友。"},
            "background": {"origin": "苍莽山脉狐族分支，母亲是人族",
                           "motivation": "证明混血也能继任族长，重振苍茫山脉",
                           "secret": "母亲被债主委员会所杀，立誓复仇"},
            "abilities":  {"power_name": "九尾天心诀（妖族功法）",
                           "current_tier": "六品化形",
                           "growth_potential": "九品大圣"},
            "catchphrase":{"lines": ["林兄，喝酒去！", "我顾青锋认的兄弟，谁敢动？"]},
            "props":      {"signature_item": "母亲遗下的玉佩",
                           "companion": "无"},
            "arc":        {"start_state": "苍茫山脉妖族少主，被排挤到边境",
                           "catalyst":   "与林渊在临海一战不打不相识",
                           "end_state":  "苍茫山脉新任族长，与林渊结为异姓兄弟"},
        },
    },
]


# ════════════════════════════════════════════════════════════════════════════
# Stage 5: relations —— 富关系
# ════════════════════════════════════════════════════════════════════════════
RELATIONS_SYSTEM = (
    "你是人物关系图谱专家。基于已有角色列表，设计角色之间的关系边。"
    "返回 JSON 数组 relations，每项结构："
    "{ from_name, to_name, relation, description, mutual(是否双向), "
    "intensity(0-10), tags([关系标签，如敌对/师徒/暧昧/家族/知己]), "
    "evolution([{phase, state}]，关系随剧情演化), "
    "key_events([{chapter_hint, event}]，关系的关键转折) }。"
    "至少 5 条关系，覆盖：亲密 / 师徒 / 敌对 / 宿敌 / 知己 / 暧昧 等。"
)


async def stage_relations(ctx: dict, db: Session):
    characters = ctx.get("characters") or []
    char_summary = [{"name": c["name"], "role": c["role"]} for c in characters]

    payload = await call_llm_json(
        role="creative_detail",
        system_prompt=RELATIONS_SYSTEM,
        user_prompt=(
            f"已有角色：{char_summary}\n"
            f"故事核心：{ctx.get('story_core','')[:300]}"
        ),
        mock_payload={"relations": _RELATIONS_MOCK},
    )
    name_to_id = {c["name"]: c["id"] for c in characters}

    ctx["relations"] = []
    for r in payload.get("relations") or []:
        from_name = r.get("from_name", "")
        to_name   = r.get("to_name", "")
        from_id = name_to_id.get(from_name)
        to_id   = name_to_id.get(to_name)
        if not from_id or not to_id:
            continue

        # schema 校验富关系（intensity/tags/evolution/key_events）
        rich_part = {
            "mutual":     r.get("mutual", False),
            "intensity":  r.get("intensity", 5),
            "tags":       r.get("tags", []),
            "evolution":  r.get("evolution", []),
            "key_events": r.get("key_events", []),
        }
        try:
            validate_entity_relation_rich(rich_part)
        except SchemaError as e:
            # 富关系校验失败不让阻断主流程（降级为老接口）
            rich_part = {k: v for k, v in rich_part.items() if v}

        edge = EntityRelation(
            project_id=ctx["project"].id,
            from_type="character",
            from_id=from_id,
            to_type="character",
            to_id=to_id,
            relation=r.get("relation", ""),
            description=r.get("description", ""),
            mutual=rich_part.get("mutual", False),
            intensity=rich_part.get("intensity"),
            tags_json=rich_part.get("tags"),
            evolution_json=rich_part.get("evolution"),
            key_events_json=rich_part.get("key_events"),
        )
        db.add(edge)
        db.flush()
        ctx["relations"].append({
            "from": from_name, "to": to_name,
            "relation": edge.relation,
            "description": edge.description,
        })


# Mock：5 条关系
_RELATIONS_MOCK = [
    {
        "from_name": "苏晚栀", "to_name": "林渊",
        "relation": "青梅竹马",
        "description": "上一世曾陪伴主角创业，今世重逢后逐渐成为合伙人",
        "mutual": True, "intensity": 9,
        "tags": ["亲密", "信任", "暧昧"],
        "evolution": [
            {"phase": "开端", "state": "互有好感但各自隐忍"},
            {"phase": "中段", "state": "因债务问题产生隔阂"},
            {"phase": "结尾", "state": "重逢后深度绑定，共建商业帝国"},
        ],
        "key_events": [
            {"chapter_hint": "第3章",  "event": "主角劝阻她投资失利项目"},
            {"chapter_hint": "第18章", "event": "破产后她仍守在身边"},
            {"chapter_hint": "第80章", "event": "公开商业帝国双核身份"},
        ],
    },
    {
        "from_name": "顾青锋", "to_name": "林渊",
        "relation": "异姓兄弟",
        "description": "不打不相识，从对手到盟友",
        "mutual": True, "intensity": 8,
        "tags": ["知己", "兄弟"],
        "evolution": [
            {"phase": "开端", "state": "临海一战后互相敬重"},
            {"phase": "中段", "state": "共同对抗债主委员会"},
            {"phase": "结尾", "state": "结为异姓兄弟，互为后背"},
        ],
        "key_events": [
            {"chapter_hint": "第35章", "event": "临海一战不打不相识"},
            {"chapter_hint": "第60章", "event": "主角帮顾青锋夺回族长之位"},
        ],
    },
    {
        "from_name": "孟浩", "to_name": "林渊",
        "relation": "宿敌",
        "description": "前世逼死林父，今世继续追杀林家",
        "mutual": True, "intensity": 10,
        "tags": ["敌对", "仇恨"],
        "evolution": [
            {"phase": "开端", "state": "孟浩主动追债，林渊被动应付"},
            {"phase": "中段", "state": "孟浩发现林渊先知能力，倾全力追杀"},
            {"phase": "结尾", "state": "孟浩反被林渊反将一军，欠下三笔新债"},
        ],
        "key_events": [
            {"chapter_hint": "第5章",  "event": "孟浩第一次登门追债"},
            {"chapter_hint": "第45章", "event": "孟浩暗杀林渊失败反被擒"},
            {"chapter_hint": "第120章", "event": "孟浩在债主委员会被公开审判"},
        ],
    },
    {
        "from_name": "顾青锋", "to_name": "孟浩",
        "relation": "复仇者",
        "description": "母亲被债主委员会所杀，立誓向孟浩复仇",
        "mutual": False, "intensity": 9,
        "tags": ["敌对", "复仇"],
        "evolution": [
            {"phase": "开端", "state": "暗中观察孟浩"},
            {"phase": "中段", "state": "正面对峙过一次"},
            {"phase": "结尾", "state": "联手林渊击败孟浩"},
        ],
        "key_events": [
            {"chapter_hint": "第50章", "event": "顾青锋发现母亲死亡真相"},
        ],
    },
    {
        "from_name": "苏晚栀", "to_name": "孟浩",
        "relation": "商业对手",
        "description": "孟浩试图通过打压苏氏逼林渊就范",
        "mutual": False, "intensity": 6,
        "tags": ["敌对", "商业"],
        "evolution": [
            {"phase": "开端", "state": "互不干涉"},
            {"phase": "中段", "state": "孟浩利用苏氏施压林渊"},
            {"phase": "结尾", "state": "苏晚栀联手林渊反制孟浩"},
        ],
        "key_events": [
            {"chapter_hint": "第25章", "event": "孟浩打压苏氏股价"},
        ],
    },
]


# ════════════════════════════════════════════════════════════════════════════
# Stage 6: foreshadowing
# ════════════════════════════════════════════════════════════════════════════
async def stage_foreshadowing(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="creative_detail",
        system_prompt="基于人物和情节骨架设计伏笔系统，返回 items 列表，每条含 content, importance, linked_character_name。",
        user_prompt=f"characters={ctx['characters']}; plot={ctx['plot_skeleton']}",
        mock_payload={
            "items": [
                {"content": "主角父母早年破产与孟家旧怨有关", "importance": "高", "linked_character_name": "林渊"},
                {"content": "苏晚栀手里有一份神秘的债务文书", "importance": "高", "linked_character_name": "苏晚栀"},
                {"content": "顾青锋母亲遗下的玉佩藏有苍茫山脉的惊天秘密", "importance": "中", "linked_character_name": "顾青锋"},
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


# ════════════════════════════════════════════════════════════════════════════
# Stage 7: map —— 地理地图
# ════════════════════════════════════════════════════════════════════════════
async def stage_map(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt="构建地理地图层级，返回 nodes 列表，每条含 name, level, parent_name(可为空), description。",
        user_prompt=f"world_view={ctx.get('world_view','')}",
        mock_payload={
            "nodes": [
                {"name": "蓝星", "level": "world",    "parent_name": None,           "description": "故事所在世界"},
                {"name": "云州", "level": "continent", "parent_name": "蓝星",        "description": "故事主要舞台"},
                {"name": "云州内城", "level": "city", "parent_name": "云州",        "description": "商业中心"},
                {"name": "债街", "level": "district", "parent_name": "云州内城",  "description": "修士经济命脉"},
                {"name": "临海城", "level": "city",   "parent_name": "云州",        "description": "港口，妖族混居"},
                {"name": "苍茫山脉", "level": "continent", "parent_name": "蓝星", "description": "妖族祖地"},
            ]
        },
    )
    name_to_id = {}
    ctx["map_orphans"] = []
    for node in payload.get("nodes", []):
        parent_name = node.get("parent_name")
        parent_id = name_to_id.get(parent_name)
        if parent_name and parent_id is None:
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


# ════════════════════════════════════════════════════════════════════════════
# Stage 8: factions_power —— 结构化 tiers
# ════════════════════════════════════════════════════════════════════════════
FACTIONS_POWER_SYSTEM = (
    "你是力量体系 + 势力阵营设计专家。"
    "返回 JSON：{ factions: [...], power_system: {name, currency, description, levels: [...]} }。"
    "factions 每项：name + detail + structure + goals + territory + allies + enemies。"
    "power_system.levels 每项必含 level, name, summary, break_condition, cultivation_time。"
)


async def stage_factions_power(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt=FACTIONS_POWER_SYSTEM,
        user_prompt=(
            f"world_view={ctx.get('world_view','')}\n"
            f"characters={ctx.get('characters',[])[:3]}"
        ),
        mock_payload=_FACTIONS_POWER_MOCK,
    )
    ctx["factions"] = []
    for f in payload.get("factions", []):
        row = Faction(
            project_id=ctx["project"].id,
            name=f.get("name"),
            detail_json=f,  # 完整结构存 detail_json
        )
        db.add(row)
        db.flush()
        ctx["factions"].append({"id": row.id, "name": f.get("name")})

    ps = payload.get("power_system")
    if ps:
        # levels 结构化（含 summary / break_condition / cultivation_time）
        db.add(PowerSystem(
            project_id=ctx["project"].id,
            name=ps.get("name"),
            description=ps.get("description"),
            tiers_json=ps.get("levels", []),  # 完整结构存 tiers_json
        ))


_FACTIONS_POWER_MOCK = {
    "factions": [
        {
            "name": "云州商会",
            "detail": "云州最大商业联盟，整合本地七成修士与凡人经济",
            "structure": "议长 1 人 + 长老 7 人 + 七区分舵",
            "goals": "维持云州经济秩序，扩大修士与凡人合作",
            "territory": "云州七区",
            "allies": ["苏氏", "苍茫狐族"],
            "enemies": ["债主委员会"],
        },
        {
            "name": "债主委员会",
            "detail": "跨州修士仲裁机构，垄断债务追讨",
            "structure": "首席调查官 1 人 + 十二调查员 + 区域代表",
            "goals": "把每一笔旧债追到底，不允许任何赖账",
            "territory": "全国 + 部分跨境",
            "allies": ["部分边缘宗门"],
            "enemies": ["云州商会", "苍茫狐族"],
        },
        {
            "name": "苍茫狐族",
            "detail": "妖族三大族之一，主支盘踞苍茫山脉",
            "structure": "族长 + 长老会 + 九支脉",
            "goals": "重振妖族地位，与人族平起平坐",
            "territory": "苍茫山脉",
            "allies": ["云州商会（部分）"],
            "enemies": ["债主委员会", "激进人族宗门"],
        },
    ],
    "power_system": {
        "name": "债感修炼体系",
        "currency": "人情点（修士可量化的人情/因果）",
        "description": "通过感知、回应、积累他人对你的「债」（人情/因果/命数）来修炼。",
        "levels": [
            {"level": 1, "name": "感债者", "summary": "初觉醒，能模糊感知周围人的债",
             "break_condition": "需主动回应至少 3 笔小额人情债",
             "cultivation_time": "3-12 个月"},
            {"level": 2, "name": "识债者", "summary": "能精确识别债务人/债权人及债的类型",
             "break_condition": "累计还清 10 笔旧债",
             "cultivation_time": "1-2 年"},
            {"level": 3, "name": "操债者", "summary": "能主动结债/解债，影响因果线",
             "break_condition": "在公平交易中完成 3 笔大额债务转化",
             "cultivation_time": "2-3 年"},
            {"level": 4, "name": "断债者", "summary": "可斩断指定因果线",
             "break_condition": "完成一次生死级因果斩断",
             "cultivation_time": "3-5 年"},
            {"level": 5, "name": "化债者", "summary": "可将旧债转化为新机缘",
             "break_condition": "主动化解一段历史仇怨",
             "cultivation_time": "5-8 年"},
            {"level": 6, "name": "债仙", "summary": "超脱债务轮回",
             "break_condition": "完成天道级债务结算",
             "cultivation_time": "10 年以上"},
        ],
    },
}


# ════════════════════════════════════════════════════════════════════════════
# Stage 9: currency_special —— 结构化 currencies
# ════════════════════════════════════════════════════════════════════════════
CURRENCY_SYSTEM = (
    "你是货币与特殊设定设计专家。"
    "返回 JSON：{ currencies: [...], special_settings: {...} }。"
    "currencies 每项必含 name, detail, exchange_rate, issuers, scope。"
)


async def stage_currency_special(ctx: dict, db: Session):
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt=CURRENCY_SYSTEM,
        user_prompt=f"world_view={ctx.get('world_view','')}",
        mock_payload={
            "currencies": [
                {"name": "人情点",
                 "detail": "修士经济的基础单位，可量化人情/因果",
                 "exchange_rate": "1 人情点 ≈ 100 元人民币（修士间内部汇率）",
                 "issuers": ["债主委员会", "各宗门"],
                 "scope": "修士与凡人混合经济圈"},
                {"name": "人民币",
                 "detail": "凡人社会的法定货币",
                 "exchange_rate": "1 RMB = 100 元面额",
                 "issuers": ["国家央行"],
                 "scope": "凡人日常生活"},
                {"name": "灵石",
                 "detail": "修士硬通货，用于阵法、丹药炼制",
                 "exchange_rate": "1 灵石 ≈ 10000 人情点",
                 "issuers": ["苍茫山脉矿脉", "国家储备"],
                 "scope": "修士法器/阵法市场"},
            ],
            "special_settings": {"golden_finger": "重生记忆 + 债感能力"},
        },
    )
    for c in payload.get("currencies", []):
        db.add(Currency(
            project_id=ctx["project"].id,
            name=c.get("name"),
            detail_json=c,  # 完整结构
        ))
    ws = db.get(WorldSetting, ctx["world_setting_id"])
    ws.special_settings_json = payload.get("special_settings", {})


# ════════════════════════════════════════════════════════════════════════════
# Stage 10: consistency_check —— 追加 3 类新规则
# ════════════════════════════════════════════════════════════════════════════
async def stage_consistency_check(ctx: dict, db: Session):
    """
    一致性校验：纯规则 + 3 类新规则（防 LLM 漏检）。
    - 人物/势力是否有重名
    - 地图节点是否有"声明了 parent 但没建上"的孤儿节点
    - 伏笔是否有"声明了关联角色但没匹配到角色 ID"的悬空记录
    - NEW: relation_cardinality（同一对角色 >3 条关系）
    - NEW: factionless_character（角色未归属任何 faction）
    - NEW: power_orphan（力量体系无角色归属）
    """
    from collections import Counter

    warnings = []

    # ── 规则 1：人物重名（保持原行为）──
    names = [c["name"] for c in ctx.get("characters", [])]
    dup_names = {n for n in names if names.count(n) > 1}
    if dup_names:
        warnings.append({"type": "duplicate_character_name", "detail": list(dup_names)})

    # ── 规则 2：地图孤儿（保持原行为）──
    if ctx.get("map_orphans"):
        warnings.append({"type": "orphan_map_node", "detail": ctx["map_orphans"]})

    # ── 规则 3：伏笔悬空（保持原行为）──
    name_to_id = {c["name"]: c["id"] for c in ctx.get("characters", [])}
    unresolved_foreshadowing = [
        item.get("content") for item in ctx.get("foreshadowing_raw", [])
        if item.get("linked_character_name") and item.get("linked_character_name") not in name_to_id
    ]
    if unresolved_foreshadowing:
        warnings.append({"type": "unresolved_foreshadowing_link", "detail": unresolved_foreshadowing})

    # ── 规则 4 NEW：relation_cardinality（同一对角色 >3 条关系）──
    pair_count = Counter()
    for r in ctx.get("relations", []):
        # relations 当前 stage 5 写入的是 {from, to, relation, description}
        pair = tuple(sorted([r.get("from", ""), r.get("to", "")]))
        pair_count[pair] += 1
    for (a, b), n in pair_count.items():
        if n > 3:
            warnings.append({
                "type": "relation_cardinality",
                "detail": [f"{a} ↔ {b}: {n} 条关系（建议合并去重）"]
            })

    # ── 规则 5 NEW：factionless_character（角色未归属任何 faction）──
    faction_chars = set()
    faction_relations = db.query(EntityRelation).filter(
        EntityRelation.project_id == ctx["project"].id,
        EntityRelation.to_type == "faction",
    ).all()
    for r in faction_relations:
        faction_chars.add(r.to_id)
    for c in ctx.get("characters", []):
        if c.get("id") not in faction_chars:
            warnings.append({
                "type": "factionless_character",
                "detail": [f"{c.get('name','')} 未归属任何势力"]
            })

    # ── 规则 6 NEW：power_orphan（力量体系无角色归属）──
    power_chars = set()
    power_relations = db.query(EntityRelation).filter(
        EntityRelation.project_id == ctx["project"].id,
        EntityRelation.relation == "修炼同体系",
    ).all()
    for r in power_relations:
        power_chars.add(r.from_id)
        power_chars.add(r.to_id)
    if ctx.get("characters") and not power_chars and len(ctx["characters"]) >= 2:
        warnings.append({
            "type": "power_orphan",
            "detail": ["力量体系无任何角色归属（建议在 relations 阶段加'修炼同体系'关系）"]
        })

    ctx["consistency_warnings"] = warnings


# ════════════════════════════════════════════════════════════════════════════
# Stage 顺序与展示名
# ════════════════════════════════════════════════════════════════════════════
STAGES: list[tuple[str, str, callable]] = [
    ("parse_config",       "分析配置参数",       stage_parse_config),
    ("world_basics",       "基本信息·世界观",    stage_world_basics),
    ("plot_skeleton",      "规划情节脉络",       stage_plot_skeleton),
    ("characters",         "设计主要人物",       stage_characters),
    ("relations",          "设计人物关系",       stage_relations),
    ("foreshadowing",      "设计伏笔系统",       stage_foreshadowing),
    ("map",                "构建世界地图",       stage_map),
    ("factions_power",     "势力阵营·力量体系",  stage_factions_power),
    ("currency_special",   "特殊设定·货币体系",  stage_currency_special),
    ("consistency_check",  "一致性校验",         stage_consistency_check),
]