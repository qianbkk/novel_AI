"""从已有小说正文反向提取世界观/人物/关系/势力/力量体系/伏笔。

属于 goal 2026-07-19 授权的「已有小说上传」特性族第三步（在
novel_import 切章 + import-text API 之后）。与 worldbuild stages.py
的关键差异：**best-effort 降级** —— 提取端原文是既定事实，提多少算多少，
不能 fail-fast；校验失败的角色仍入库、world_view_rich 拼 legacy 文本兜底。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .llm_client import LLMError, call_llm_json
from .logging_setup import get_logger
from .models import (
    Character, Chapter, ChapterCharacter, EntityRelation, Faction,
    Foreshadowing, PowerSystem, WorldSetting,
)
from .schema_validator import (
    SchemaError, validate_character_card, validate_entity_relation_rich,
    validate_world_view_rich,
)

log = get_logger("novel_ai.novel_extract")

# ────────────── 取材限额 ──────────────
_PER_CHAPTER_CHAR_BUDGET = 1500   # 每章正文截断上限
_TOTAL_CORPUS_CHAR_BUDGET = 30000 # 拼起来后再截的总量上限


class ExtractConflictError(Exception):
    """已有设定且 caller 未授权 replace 时抛 —— API 层转 409。
    与 chapters.py 的 DuplicateChapterError 同一处理风格。"""


# ═══════════════════════════════════════════════════════════════════════════════
# 素材构造（纯函数，单测友好）
# ═══════════════════════════════════════════════════════════════════════════════
def _build_corpus(chapters: list[Chapter]) -> str:
    """把章节正文拼成 LLM 友好的语料，按 chapter_no 升序截断。"""
    pieces: list[str] = []
    total = 0
    for ch in chapters:
        chunk = (ch.content or "").strip()[:_PER_CHAPTER_CHAR_BUDGET]
        if not chunk:
            continue
        snippet = f"第{ch.chapter_no}章 {ch.title or ''}\n{chunk}"
        if total + len(snippet) > _TOTAL_CORPUS_CHAR_BUDGET:
            remaining = max(_TOTAL_CORPUS_CHAR_BUDGET - total, 0)
            snippet = snippet[:remaining]
            pieces.append(snippet)
            break
        pieces.append(snippet)
        total += len(snippet)
    return "\n\n".join(pieces)


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt 与 mock payload（mock payload 必须过消费者 schema —— §3.6 红线）
# ═══════════════════════════════════════════════════════════════════════════════
WORLD_SYSTEM = (
    "你是小说设定提取助手。基于用户提供的已有小说正文，提取结构化世界观。"
    "返回 JSON：{ "
    "world_view_rich: {cosmos, geography, history, society, technology, races, customs}, "
    "story_core_struct: {goal, conflict, theme, hook}, "
    "history_timeline: [{era, event, impact}, ... 至少 2 条] "
    "}。"
    "world_view_rich 每段必须 ≥30 字；原文没有依据时可以合理推断并显式标注"
    "「（推断）」前缀；history_timeline 至少 2 条。"
)

CHARACTERS_SYSTEM = (
    "你是人物提取助手。从已有小说正文里识别所有有名有姓的人物及其关系。"
    "返回 JSON：{ characters: [...], relations: [...] }。"
    "characters 每项：{ name, role, card: {basic, appearance, personality, "
    "background, abilities, catchphrase, props, arc} }，role ∈ {主角, "
    "重要配角, 配角, 反派}，personality 必须含 tags（≥1）与 summary（≥20 字），"
    "catchphrase 必须含 lines（≥1），arc 必须含 start_state / catalyst / "
    "end_state（≥10 字）。人物上限 10 个。"
    "relations 每项：{ from_name, to_name, relation, description, mutual, "
    "intensity(0-10), tags([...]) }。"
    "原文无依据的字段填空字符串或空数组，不要凭空捏造内容。"
)

MISC_SYSTEM = (
    "你是势力 / 力量体系 / 伏笔提取助手。"
    "返回 JSON：{ factions: [...], power_system: {name, description, levels: "
    "[{level, name, summary, break_condition, cultivation_time}]} | null, "
    "foreshadowings: [{content, importance(高|中|低), linked_character_name, "
    "status(未铺垫|已铺垫|已回收)}] }。"
    "factions 每项：{ name, detail, structure, goals, territory, allies, "
    "enemies }；levels 每项必含 level, name, summary 三段。"
)

# ── MOCK payload ──
# 7 段全 ≥30 字；character card 含合法 personality/catchphrase/arc；
# relations 的 from_name/to_name 必须是 characters 里出现的名字。
_WORLD_MOCK: dict[str, Any] = {
    "world_view_rich": {
        "cosmos":      "（Mock提取）灵气与天道并行运转的世界，修士通过捕捉灵气波动"
                       "进行修炼，凡人借助科技维持日常；两者共生于云州与苍莽山脉"
                       "两片主要舞台。",
        "geography":   "（Mock提取）云州城是凡人聚居之地，七区各有职能；苍莽山脉"
                       "横亘北方，是修士与妖族争锋之所；临海港为对外贸易枢纽。",
        "history":     "（Mock提取）百年前灵气复苏引发社会重组，修士家族逐步演变为"
                       "隐性财阀，与凡人商业体系形成微妙的契约关系。",
        "society":     "（Mock提取）云州实行修士与凡人共治，修士享有债务豁免权"
                       "但需向债主委员会报备；凡人通过科举、商会、联姻三条路径上升。",
        "technology":  "（Mock提取）灵气与电路耦合催生灵石电池、灵阵芯片等新型工业；"
                       "通讯基础为灵网+5G 双轨；飞剑公交与高铁混运承担主要交通。",
        "races":       "（Mock提取）人族约占七成，古妖族与幽冥族混居其间，混血族"
                       "地位尴尬但偶有奇才，三大种族各有宗法体系。",
        "customs":     "（Mock提取）新人入门需经三年苦修方可下山，下山前须写"
                       "「债书」承诺此后因果；祭剑节与走火大会为两大年度盛事。",
    },
    "story_core_struct": {
        "goal":     "主角借先知之力改写家族破产命运",
        "conflict": "前世债主与今世新敌轮番围剿",
        "theme":    "个体与时代的协商——选择即格局",
        "hook":     "重生后第一次商业决策为什么与上一世不同？",
    },
    "history_timeline": [
        {"era": "灵气复苏", "event": "全球能源结构重塑",
         "impact": "修士家族转型为隐性财阀"},
        {"era": "债主委员会成立", "event": "建立明面仲裁机构",
         "impact": "平衡修士与凡人债务关系"},
    ],
}

_CHARACTERS_MOCK: list[dict[str, Any]] = [
    {
        "name": "林渊", "role": "主角",
        "card": {
            "basic":      {"gender": "男", "age": 32, "identity": "云州林氏长子"},
            "appearance": {"height": "182cm", "hair": "短黑",
                           "outfit": "深灰风衣",
                           "distinguishing_feature": "左眉尾一道陈年刀疤"},
            "personality": {"tags": ["克制", "精算", "冷面热心"],
                            "summary": "外表冷峻内心压着火，行动前必先算三步，"
                                      "把柔软都留给身边人，偶有少年意气但绝不冲动。"},
            "background": {"origin": "云州林氏，家道中落前是云州三大商号之一",
                           "motivation": "改写林家破产命运",
                           "secret": "前世是 2024 年崛起的商业老兵"},
            "abilities":  {"power_name": "先知回响",
                           "current_tier": "感债者（一品）",
                           "growth_potential": "识债者（九品）"},
            "catchphrase": {"lines": ["这局我来开局。", "债还不完，就别想下桌。"]},
            "props":      {"signature_item": "老旧铜怀表",
                           "companion": "瘸腿狼狗「阿斗」"},
            "arc":        {"start_state": "破产边缘的小商人",
                           "catalyst":   "意外重生回到 2012",
                           "end_state":  "云州新一代商盟领袖"},
        },
    },
    {
        "name": "苏晚栀", "role": "重要配角",
        "card": {
            "basic":      {"gender": "女", "age": 28, "identity": "云州苏氏财务总监"},
            "appearance": {"height": "168cm", "hair": "乌黑长直",
                           "outfit": "改良旗袍",
                           "distinguishing_feature": "左耳一颗红痣"},
            "personality": {"tags": ["精明", "倔强", "外冷内热"],
                            "summary": "数字敏感度极高，谈判时目光如刀；"
                                      "面对在意的人会瞬间软化。"},
            "background": {"origin": "云州苏氏旁支",
                           "motivation": "证明旁支不比嫡支差",
                           "secret": "前世曾暗恋林渊"},
            "abilities":  {"power_name": "账心通（无修为）",
                           "current_tier": "商道一品",
                           "growth_potential": "商道九品"},
            "catchphrase": {"lines": ["账上说话。", "林渊，你别太自信。"]},
            "props":      {"signature_item": "一枚祖母绿胸针", "companion": "无"},
            "arc":        {"start_state": "云州商圈默默无闻的旁支女",
                           "catalyst":   "被林渊拉入团队",
                           "end_state":  "苏氏实际掌权人"},
        },
    },
]

_RELATIONS_MOCK: list[dict[str, Any]] = [
    {
        "from_name": "苏晚栀", "to_name": "林渊",
        "relation": "青梅竹马",
        "description": "上一世曾陪伴主角创业，今世重逢后逐渐成为合伙人",
        "mutual": True, "intensity": 9,
        "tags": ["亲密", "信任", "暧昧"],
    },
]

_MISC_MOCK: dict[str, Any] = {
    "factions": [
        {
            "name": "（Mock提取）云州商会",
            "detail": "云州最大商业联盟，整合本地七成修士与凡人经济",
            "structure": "议长 1 人 + 长老 7 人 + 七区分舵",
            "goals": "维持云州经济秩序，扩大修士与凡人合作",
            "territory": "云州七区",
            "allies": ["苏氏"],
            "enemies": ["债主委员会"],
        },
    ],
    "power_system": {
        "name": "（Mock提取）债感修炼体系",
        "description": "通过感知、回应、积累他人对你的「债」来修炼",
        "levels": [
            {"level": 1, "name": "感债者",
             "summary": "初觉醒，能模糊感知周围人的债",
             "break_condition": "需主动回应至少 3 笔小额人情债",
             "cultivation_time": "3-12 个月"},
            {"level": 2, "name": "识债者",
             "summary": "能精确识别债务人/债权人及债的类型",
             "break_condition": "累计还清 10 笔旧债",
             "cultivation_time": "1-2 年"},
        ],
    },
    "foreshadowings": [
        {
            "content": "（Mock提取）主角父母早年破产与孟家旧怨有关",
            "importance": "高",
            "linked_character_name": "林渊",
            "status": "已铺垫",
        },
    ],
}


# ═══════════════════════════════════════════════════════════════════════════════
# LLM 调用 + 降级
# ═══════════════════════════════════════════════════════════════════════════════
async def _extract_world(corpus: str) -> tuple[dict, list[str]]:
    """返回 (payload, warnings)。world_view_rich 校验失败 → 拼 legacy 文本。"""
    warnings: list[str] = []
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt=WORLD_SYSTEM,
        user_prompt=("已有小说正文（已截断）：\n" + corpus[:_TOTAL_CORPUS_CHAR_BUDGET]),
        mock_payload=_WORLD_MOCK,
    )
    rich = payload.get("world_view_rich") or {}
    try:
        validate_world_view_rich(rich)
    except SchemaError as e:
        warnings.append(f"world_view_rich 未通过 schema：{e}；已降级为纯文本")
    return payload, warnings


async def _extract_characters(corpus: str) -> tuple[list[dict], list[dict], list[str]]:
    """返回 (characters, relations, warnings)。character card 校验失败者标 warn。"""
    warnings: list[str] = []
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt=CHARACTERS_SYSTEM,
        user_prompt=("已有小说正文（已截断）：\n" + corpus[:_TOTAL_CORPUS_CHAR_BUDGET]),
        mock_payload={
            "characters": _CHARACTERS_MOCK,
            "relations": _RELATIONS_MOCK,
        },
    )
    characters = payload.get("characters") or []
    relations = payload.get("relations") or []
    for c in characters:
        try:
            validate_character_card(c.get("card") or {})
        except SchemaError as e:
            warnings.append(f"角色「{c.get('name', '?')}」卡片未通过 schema：{e}")
    return characters, relations, warnings


async def _extract_misc(corpus: str) -> tuple[dict, list[str]]:
    """返回 (payload, warnings)。伏笔 importance 非法值归一化为「中」。"""
    warnings: list[str] = []
    payload = await call_llm_json(
        role="structured_logic",
        system_prompt=MISC_SYSTEM,
        user_prompt=("已有小说正文（已截断）：\n" + corpus[:_TOTAL_CORPUS_CHAR_BUDGET]),
        mock_payload=_MISC_MOCK,
    )
    for fs in payload.get("foreshadowings") or []:
        if fs.get("importance") not in ("高", "中", "低"):
            fs["importance"] = "中"
            warnings.append(f"伏笔 importance 非法值已归一为「中」: {fs.get('content', '')[:30]}")
        if fs.get("status") not in ("未铺垫", "已铺垫", "已回收"):
            fs["status"] = "已铺垫"  # 已写入正文的伏笔默认就是已铺垫
    return payload, warnings


# ═══════════════════════════════════════════════════════════════════════════════
# 持久化（单事务，replace 子先于父）
# ═══════════════════════════════════════════════════════════════════════════════
def _check_conflict_and_prepare(project_id: str, db: Session, replace: bool) -> None:
    """查询是否已有设定；有则按 replace 决定抛错或删旧。"""
    has_world = db.query(WorldSetting).filter_by(project_id=project_id).first() is not None
    has_chars = db.query(Character).filter_by(project_id=project_id).first() is not None
    if (has_world or has_chars) and not replace:
        raise ExtractConflictError(
            f"project {project_id} 已有设定（world={has_world}, characters={has_chars}），"
            f"重跑请带 replace=true"
        )
    if not replace:
        return
    # 子先于父删除（仿 setting_sync.py:255-272）
    char_ids_subq = select(Character.id).where(Character.project_id == project_id)
    db.query(ChapterCharacter).filter(
        ChapterCharacter.character_id.in_(char_ids_subq)
    ).delete(synchronize_session=False)
    db.query(EntityRelation).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.query(Foreshadowing).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.query(PowerSystem).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.query(Faction).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.query(Character).filter_by(project_id=project_id).delete(synchronize_session=False)
    db.query(WorldSetting).filter_by(project_id=project_id).delete(synchronize_session=False)


def _persist_world(project_id: str, payload: dict, warnings: list[str], db: Session) -> bool:
    rich = payload.get("world_view_rich") or {}
    struct = payload.get("story_core_struct") or {}
    timeline = payload.get("history_timeline") or []

    ws = db.query(WorldSetting).filter_by(project_id=project_id).first()
    if ws is None:
        ws = WorldSetting(project_id=project_id)
        db.add(ws)
        db.flush()

    rich_passed = True
    try:
        validate_world_view_rich(rich)
    except SchemaError:
        rich_passed = False

    if rich_passed and rich:
        ws.world_view_rich_json = rich
    if struct:
        ws.story_core_struct_json = struct
    if timeline:
        ws.history_timeline_json = timeline

    # legacy 字段兜底：把 rich 拼成一段文本，方便 push-concept 的"无 rich 就用 legacy"分支
    if rich:
        legacy = " | ".join(f"【{k}】{v}" for k, v in rich.items() if isinstance(v, str))
        if legacy:
            ws.world_view = legacy[:4000]
    if struct:
        ws.story_core = (
            f"目标：{struct.get('goal','')}｜冲突：{struct.get('conflict','')}"
            f"｜主题：{struct.get('theme','')}｜钩子：{struct.get('hook','')}"
        )[:2000]

    return bool(rich) and rich_passed


def _persist_characters(
    project_id: str, characters: list[dict], relations: list[dict], warnings: list[str], db: Session,
) -> tuple[int, int]:
    """返回 (角色写入数, 关系写入数)。"""
    written_chars = 0
    name_to_id: dict[str, str] = {}
    for c in characters:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        card = c.get("card") or {}
        row = Character(
            project_id=project_id,
            name=name,
            role=c.get("role") or "配角",
            detail_json={
                "card": card,
                "background": (card.get("background") or {}).get("origin", ""),
                "ability":    ((card.get("abilities") or {}).get("power_name", "") +
                               " · " + (card.get("abilities") or {}).get("current_tier", "")),
            },
        )
        try:
            validate_character_card(card)
            row.card_basic_json       = card.get("basic")
            row.card_appearance_json  = card.get("appearance")
            row.card_personality_json = card.get("personality")
            row.card_background_json  = card.get("background")
            row.card_abilities_json   = card.get("abilities")
            row.card_catchphrase_json = card.get("catchphrase")
            row.card_props_json       = card.get("props")
            row.card_arc_json         = card.get("arc")
        except SchemaError:
            # 校验失败的角色仍入库，但 8 段列留空 —— card 内容在 detail_json["card"] 备查
            pass
        db.add(row)
        db.flush()
        name_to_id[name] = row.id
        written_chars += 1

    written_rels = 0
    for r in relations:
        from_id = name_to_id.get(r.get("from_name", ""))
        to_id = name_to_id.get(r.get("to_name", ""))
        if not from_id or not to_id:
            warnings.append(
                f"关系「{r.get('from_name','')}→{r.get('to_name','')}」"
                f"找不到对应人物，已跳过"
            )
            continue
        rich_part = {
            "mutual":     r.get("mutual", False),
            "intensity":  r.get("intensity", 5),
            "tags":       r.get("tags", []),
            "evolution":  r.get("evolution", []),
            "key_events": r.get("key_events", []),
        }
        try:
            validate_entity_relation_rich(rich_part)
        except SchemaError:
            rich_part = {k: v for k, v in rich_part.items() if v}
        edge = EntityRelation(
            project_id=project_id,
            from_type="character",
            from_id=from_id,
            to_type="character",
            to_id=to_id,
            relation=r.get("relation", "") or "关联",
            description=r.get("description", "") or "",
            mutual=rich_part.get("mutual", False),
            intensity=rich_part.get("intensity"),
            tags_json=rich_part.get("tags"),
            evolution_json=rich_part.get("evolution"),
            key_events_json=rich_part.get("key_events"),
        )
        db.add(edge)
        written_rels += 1
    return written_chars, written_rels


def _persist_misc(
    project_id: str, payload: dict, name_to_id: dict[str, str], warnings: list[str], db: Session,
) -> tuple[int, int, int]:
    written_factions = 0
    written_powers = 0
    written_fs = 0
    for f in payload.get("factions") or []:
        if not f.get("name"):
            continue
        db.add(Faction(project_id=project_id, name=f.get("name"), detail_json=f))
        written_factions += 1
    ps = payload.get("power_system")
    if ps and ps.get("name"):
        db.add(PowerSystem(
            project_id=project_id,
            name=ps.get("name"),
            description=ps.get("description"),
            tiers_json=ps.get("levels") or [],
        ))
        written_powers = 1
    for fs in payload.get("foreshadowings") or []:
        if not fs.get("content"):
            continue
        linked_name = fs.get("linked_character_name") or ""
        db.add(Foreshadowing(
            project_id=project_id,
            content=fs.get("content"),
            importance=fs.get("importance", "中"),
            status=fs.get("status", "已铺垫"),
            linked_character_id=name_to_id.get(linked_name),
        ))
        written_fs += 1
    return written_factions, written_powers, written_fs


def _rebuild_chapter_character_edges(project_id: str, db: Session) -> int:
    """重灌后重建 章节-人物 边：与 add_chapter 字符串匹配逻辑一致。"""
    chapters = (
        db.query(Chapter)
        .filter_by(project_id=project_id)
        .order_by(Chapter.chapter_no.asc())
        .all()
    )
    characters = db.query(Character).filter_by(project_id=project_id).all()
    written = 0
    for ch in chapters:
        for c in characters:
            if c.name and c.name in (ch.content or ""):
                # 避免重复（同章同人多条边没意义）
                exists = (
                    db.query(ChapterCharacter)
                    .filter_by(chapter_id=ch.id, character_id=c.id)
                    .first()
                )
                if exists is None:
                    db.add(ChapterCharacter(chapter_id=ch.id, character_id=c.id))
                    written += 1
    return written


# ═══════════════════════════════════════════════════════════════════════════════
# 顶层入口
# ═══════════════════════════════════════════════════════════════════════════════
async def extract_setting_from_chapters(
    project_id: str,
    db: Session,
    max_chapters: int = 20,
    replace: bool = False,
) -> dict:
    """主入口：返回 dict 包含写入计数 + warnings + chapters_used。"""
    chapters = (
        db.query(Chapter)
        .filter_by(project_id=project_id)
        .order_by(Chapter.chapter_no.asc())
        .limit(max_chapters)
        .all()
    )
    if not chapters:
        raise ValueError("project 没有可提取的章节，请先导入")

    corpus = _build_corpus(chapters)
    warnings: list[str] = []

    world_payload, w_warn = await _extract_world(corpus)
    warnings.extend(w_warn)

    characters, relations, c_warn = await _extract_characters(corpus)
    warnings.extend(c_warn)

    misc_payload, m_warn = await _extract_misc(corpus)
    warnings.extend(m_warn)

    # 进入持久化阶段（单事务）
    try:
        _check_conflict_and_prepare(project_id, db, replace)
        world_ok = _persist_world(project_id, world_payload, warnings, db)
        written_chars, written_rels = _persist_characters(
            project_id, characters, relations, warnings, db,
        )
        # 重新读 name_to_id 用于伏笔关联
        name_to_id = {
            c.name: c.id
            for c in db.query(Character).filter_by(project_id=project_id).all()
        }
        written_factions, written_powers, written_fs = _persist_misc(
            project_id, misc_payload, name_to_id, warnings, db,
        )
        rebuilt_edges = _rebuild_chapter_character_edges(project_id, db)
        db.commit()
    except ExtractConflictError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return {
        "world_setting_written": world_ok,
        "characters": written_chars,
        "relations": written_rels,
        "factions": written_factions,
        "power_systems": written_powers,
        "foreshadowings": written_fs,
        "chapter_character_edges_rebuilt": rebuilt_edges,
        "chapters_used": len(chapters),
        "warnings": warnings,
    }


__all__ = [
    "extract_setting_from_chapters",
    "ExtractConflictError",
    "_build_corpus",
    "LLMError",
]