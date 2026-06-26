from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Provider, RoleAssignment
from ..schemas import ProviderCreate, ProviderOut

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get("", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db)):
    return db.query(Provider).order_by(Provider.created_at.desc()).all()


@router.post("", response_model=ProviderOut)
def create_provider(payload: ProviderCreate, db: Session = Depends(get_db)):
    provider = Provider(**payload.model_dump())
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return provider


@router.put("/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: str, payload: ProviderCreate, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    for key, value in payload.model_dump().items():
        setattr(provider, key, value)
    db.commit()
    db.refresh(provider)
    return provider


@router.delete("/{provider_id}")
def delete_provider(provider_id: str, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    db.query(RoleAssignment).filter_by(provider_id=provider_id).update({"provider_id": None})
    db.delete(provider)
    db.commit()
    return {"deleted": True}
