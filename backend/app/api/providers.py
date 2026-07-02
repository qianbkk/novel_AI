from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import Provider, RoleAssignment
from ..schemas import ProviderCreate, ProviderOut
from ..security import encrypt_api_key, decrypt_api_key, key_suffix

router = APIRouter(prefix="/providers", tags=["providers"])


def _to_out(provider) -> ProviderOut:
    """ORM Provider 或 ProviderOut → ProviderOut。

    支持两种输入：
      - Provider ORM 实例（列表查询场景）：从加密列构造
      - ProviderOut pydantic 实例（POST/PUT 刚返回）：直接复制

    注意：必须用 str(...) 显式转换 ORM InstrumentedAttribute 为纯值，
    否则 pydantic v2 校验时会触发 `_sa_instance_state` 等 ORM 内部属性访问。
    """
    if isinstance(provider, ProviderOut):
        # 已经构造好的 ProviderOut —— POST/PUT 路径里 create/update 返回的
        return provider
    return ProviderOut(
        id=str(provider.id),
        name=str(provider.name),
        provider_type=str(provider.provider_type),
        api_base=provider.api_base,
        default_model=provider.default_model,
        extra_json=dict(provider.extra_json) if provider.extra_json else None,
        needs_proxy=bool(provider.needs_proxy),
        api_key_suffix=provider.api_key_suffix,
        api_key_set=bool(provider.api_key_encrypted),
        created_at=provider.created_at,
    )


@router.get("", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db)):
    providers = db.query(Provider).order_by(Provider.created_at.desc()).all()
    return [_to_out(p) for p in providers]


@router.post("", response_model=ProviderOut)
def create_provider(payload: ProviderCreate, db: Session = Depends(get_db)):
    # api_key 明文 → 加密存 DB
    encrypted = encrypt_api_key(payload.api_key)
    suffix = key_suffix(payload.api_key)
    data = payload.model_dump()
    data.pop("api_key")  # 不存明文
    provider = Provider(
        **data,
        api_key_encrypted=encrypted,
        api_key_suffix=suffix,
    )
    db.add(provider)
    db.commit()
    db.refresh(provider)
    return _to_out(provider)


@router.put("/{provider_id}", response_model=ProviderOut)
def update_provider(provider_id: str, payload: ProviderCreate, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    # 普通字段直接赋值
    provider.name = payload.name
    provider.provider_type = payload.provider_type
    provider.api_base = payload.api_base
    provider.default_model = payload.default_model
    provider.extra_json = payload.extra_json
    provider.needs_proxy = payload.needs_proxy
    # api_key 重新加密（每次 PUT 都换 ciphertext + suffix）
    provider.api_key_encrypted = encrypt_api_key(payload.api_key)
    provider.api_key_suffix = key_suffix(payload.api_key)
    db.commit()
    db.refresh(provider)
    return _to_out(provider)


@router.delete("/{provider_id}")
def delete_provider(provider_id: str, db: Session = Depends(get_db)):
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    db.query(RoleAssignment).filter_by(provider_id=provider_id).update({"provider_id": None})
    db.delete(provider)
    db.commit()
    return {"deleted": True}


def get_decrypted_api_key(provider_id: str, db: Session) -> str:
    """给 engine 用的内部接口：解密 API key 用于实际 LLM 调用。

    只在 engine / llm_router 内部调用，绝不暴露给 HTTP API。
    """
    provider = db.get(Provider, provider_id)
    if not provider or not provider.api_key_encrypted:
        raise ValueError(f"provider {provider_id} 未配置 api_key")
    return decrypt_api_key(provider.api_key_encrypted)
