"""
Phase 3：世界构建板块结构化数据的 5 个新 endpoint。

- GET /projects/{pid}/worldview/rich     — 7 段结构化世界观 + 故事核心 + 历史时间线
- GET /projects/{pid}/characters         — 角色列表（之前缺失）
- GET /projects/{pid}/characters/{cid}   — 角色卡详情（8 段）
- GET /projects/{pid}/characters/{cid}/relations — 角色的关系边
- GET /projects/{pid}/relations/graph    — 完整关系图谱数据（供前端 SVG）

老数据 fallback：所有 rich/card 字段 nullable，前端读不到时回退到 legacy 字段。
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth_scope import require_owned_project
from ..database import get_db
from ..models import WorldSetting, Character, EntityRelation, Faction
from ..schemas import (
    WorldviewRichOut, CharacterSummaryOut, CharacterCardOut,
    CharacterRelationOut, RelationGraphOut,
)

router = APIRouter(prefix="/projects/{project_id}", tags=["world"])


def _owner_check(request: Request, project_id: str, db: Session = Depends(get_db)):
    """Phase 4：所有 project-scoped 路由统一 owner 校验。

    角色卡、世界观、关系图谱都是真正敏感的内容（创作笔记、人物设定），
    这些资源也是泄漏重灾区。
    """
    from ..auth import get_current_user_optional
    from ..auth_scope import is_production_mode
    user = get_current_user_optional(request)
    if user is None and is_production_mode():
        raise HTTPException(401, "authentication required")
    require_owned_project(db, project_id, user)
    return user


def _build_card_dict(c: Character) -> dict | None:
    """聚合 8 个 card_*_json 列为统一 card dict。任一段为空则整段为 None。"""
    if not any([
        c.card_basic_json, c.card_appearance_json, c.card_personality_json,
        c.card_background_json, c.card_abilities_json, c.card_catchphrase_json,
        c.card_props_json, c.card_arc_json,
    ]):
        return None
    return {
        "basic":       c.card_basic_json,
        "appearance":  c.card_appearance_json,
        "personality": c.card_personality_json,
        "background":  c.card_background_json,
        "abilities":   c.card_abilities_json,
        "catchphrase": c.card_catchphrase_json,
        "props":       c.card_props_json,
        "arc":         c.card_arc_json,
    }


@router.get("/worldview/rich", response_model=WorldviewRichOut)
def get_worldview_rich(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """返回 7 段结构化世界观 + 故事核心 4 段 + 历史时间线。
    老项目（world_view_rich_json=null）回退到 legacy 字段。"""
    ws = db.query(WorldSetting).filter_by(project_id=project_id).first()
    if not ws:
        raise HTTPException(404, "WorldSetting not found for project")
    return WorldviewRichOut(
        rich=ws.world_view_rich_json,
        story_core=ws.story_core_struct_json,
        history_timeline=ws.history_timeline_json,
        # 老项目 fallback
        fallback_text=ws.world_view,
        fallback_story_core=ws.story_core,
    )


@router.get("/characters", response_model=list[CharacterSummaryOut])
def list_characters(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """角色列表（之前缺失）。返回 name/role + 卡片摘要。"""
    rows = db.query(Character).filter_by(project_id=project_id).order_by(Character.name).all()
    out = []
    for c in rows:
        basic = c.card_basic_json if isinstance(c.card_basic_json, dict) else {}
        out.append(CharacterSummaryOut(
            id=c.id,
            name=c.name,
            role=c.role,
            identity=basic.get("identity") if isinstance(basic, dict) else None,
            age=basic.get("age") if isinstance(basic, dict) else None,
            gender=basic.get("gender") if isinstance(basic, dict) else None,
        ))
    return out


@router.get("/characters/{character_id}", response_model=CharacterCardOut)
def get_character_card(
    project_id: str,
    character_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """角色卡详情（8 段）。老项目（card=null）回退到 detail_json 摘要。"""
    c = db.get(Character, character_id)
    if not c or c.project_id != project_id:
        raise HTTPException(404, "Character not found in this project")

    card = _build_card_dict(c)

    # 查角色的势力归属（to_type='faction' 的关系）
    faction_rel = db.query(EntityRelation).filter(
        EntityRelation.project_id == project_id,
        EntityRelation.from_id == character_id,
        EntityRelation.from_type == "character",
        EntityRelation.to_type == "faction",
    ).first()
    faction = None
    if faction_rel:
        f = db.get(Faction, faction_rel.to_id)
        if f:
            faction = {"id": f.id, "name": f.name}

    return CharacterCardOut(
        id=c.id, name=c.name, role=c.role,
        card=card,
        faction=faction,
    )


@router.get("/characters/{character_id}/relations", response_model=list[CharacterRelationOut])
def get_character_relations(
    project_id: str,
    character_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """角色相关的所有关系边（含出向 + 入向）。"""
    rels = db.query(EntityRelation).filter(
        EntityRelation.project_id == project_id,
    ).filter(
        (EntityRelation.from_id == character_id) | (EntityRelation.to_id == character_id)
    ).all()

    # 预加载目标 character（避免 N+1）
    target_ids = set()
    for r in rels:
        other_id = r.to_id if r.from_id == character_id else r.from_id
        target_ids.add(other_id)
    char_map = {
        ch.id: ch for ch in db.query(Character).filter(Character.id.in_(target_ids)).all()
    } if target_ids else {}

    out = []
    for r in rels:
        other_id = r.to_id if r.from_id == character_id else r.from_id
        other = char_map.get(other_id)
        out.append(CharacterRelationOut(
            id=r.id,
            relation=r.relation,
            description=r.description,
            target={
                "id": other.id if other else other_id,
                "name": other.name if other else other_id,
                "role": other.role if other else None,
            },
            mutual=bool(r.mutual),
            intensity=r.intensity,
            tags=r.tags_json,
            evolution=r.evolution_json,
            key_events=r.key_events_json,
        ))
    return out


@router.get("/relations/graph", response_model=RelationGraphOut)
def get_relations_graph(
    project_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _user=Depends(_owner_check),
):
    """完整关系图谱数据（供前端 SVG 渲染）。"""
    chars = db.query(Character).filter_by(project_id=project_id).all()
    rels = db.query(EntityRelation).filter_by(project_id=project_id).all()

    return RelationGraphOut(
        nodes=[
            {
                "id": c.id,
                "name": c.name,
                "role": c.role,
                "role_kind": "character",
            }
            for c in chars
        ],
        edges=[
            {
                "from_id":   r.from_id,
                "to_id":     r.to_id,
                "relation":  r.relation,
                "mutual":    bool(r.mutual),
                "intensity": r.intensity,
                "tags":      r.tags_json,
            }
            for r in rels
        ],
    )