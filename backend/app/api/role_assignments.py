from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..bridge.role_registry import ROLE_KEYS, ROLE_REGISTRY
from ..database import get_db
from ..models import Provider, RoleAssignment
from ..schemas import RoleAssignmentOut, RoleAssignmentUpdate

router = APIRouter(prefix="/role-assignments", tags=["role-assignments"])

# 审计 #9 (2026-07-20)：RoleAssignment 决定"某个 Agent 角色用哪个
# Provider"，是全局共享配置。15 个角色通常共用 1-2 个 Provider 账号，
# 没有"per-user 隔离"的合理动机。Phase 1 不带 owner 字段是设计现状。
#   - dev 模式：seed_role_assignments 启动时种入 15 行；任意请求可改；
#   - prod 模式：仍由 NOVEL_PRODUCTION 启动校验兜底。
# 跨用户可见/可写是已知妥协。如未来要 per-user：加 user_role_overrides
# 表（user_id, role_key, provider_id, model_override），不在本次范围。


def seed_role_assignments(db: Session) -> None:
    existing = {row.role_key for row in db.query(RoleAssignment).all()}
    for item in ROLE_REGISTRY:
        if item["role_key"] not in existing:
            db.add(RoleAssignment(role_key=item["role_key"]))
    db.commit()


@router.get("", response_model=list[RoleAssignmentOut])
def list_role_assignments(db: Session = Depends(get_db)):
    seed_role_assignments(db)
    rows = {
        row.role_key: row
        for row in db.query(RoleAssignment).filter(RoleAssignment.role_key.in_(ROLE_KEYS)).all()
    }
    provider_ids = [row.provider_id for row in rows.values() if row.provider_id]
    providers = {
        row.id: row
        for row in db.query(Provider).filter(Provider.id.in_(provider_ids)).all()
    } if provider_ids else {}

    result = []
    for item in ROLE_REGISTRY:
        assignment = rows.get(item["role_key"])
        provider = providers.get(assignment.provider_id) if assignment else None
        result.append({
            "role_key": item["role_key"],
            "label": item["label"],
            "provider_id": assignment.provider_id if assignment else None,
            "provider_name": provider.name if provider else None,
            "provider_type": provider.provider_type if provider else None,
            "model_override": assignment.model_override if assignment else None,
        })
    return result


@router.put("/{role_key}", response_model=RoleAssignmentOut)
def update_role_assignment(role_key: str, payload: RoleAssignmentUpdate, db: Session = Depends(get_db)):
    if role_key not in ROLE_KEYS:
        raise HTTPException(404, "role not found")
    if payload.provider_id and not db.get(Provider, payload.provider_id):
        raise HTTPException(404, "provider not found")

    assignment = db.query(RoleAssignment).filter_by(role_key=role_key).first()
    if not assignment:
        assignment = RoleAssignment(role_key=role_key)
        db.add(assignment)
    assignment.provider_id = payload.provider_id
    assignment.model_override = payload.model_override
    db.commit()
    db.refresh(assignment)

    provider = db.get(Provider, assignment.provider_id) if assignment.provider_id else None
    label = next(item["label"] for item in ROLE_REGISTRY if item["role_key"] == role_key)
    return {
        "role_key": assignment.role_key,
        "label": label,
        "provider_id": assignment.provider_id,
        "provider_name": provider.name if provider else None,
        "provider_type": provider.provider_type if provider else None,
        "model_override": assignment.model_override,
    }
