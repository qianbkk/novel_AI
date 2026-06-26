from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..bridge.role_registry import ROLE_KEYS, ROLE_REGISTRY
from ..database import get_db
from ..models import Provider, RoleAssignment
from ..schemas import RoleAssignmentOut, RoleAssignmentUpdate

router = APIRouter(prefix="/role-assignments", tags=["role-assignments"])


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
