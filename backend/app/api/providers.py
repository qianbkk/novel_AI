from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import Provider, RoleAssignment
from ..schemas import ProviderCreate, ProviderOut
from ..security import encrypt_api_key, decrypt_api_key, key_suffix
from ..config import settings


# 2026-08-18：/providers/health 端点里要查 DB provider 列表，
# 但这个端点签名没有 Depends(get_db)（无 project 上下文，避免把 owner check 拖进来）。
# 直接用 SessionLocal 自己开一个 session，简单可靠。
def SessionLocal_for_health() -> Session:
    return SessionLocal()


router = APIRouter(prefix="/providers", tags=["providers"])

# 审计 #9 (2026-07-20)：本路由族 Phase 1 是"全局共享配置"—— Provider /
# RoleAssignment 两张表无 owner 字段。设计原因：15 个写作角色需要
# 可复用同一组 Provider 配置；本地原型阶段共享是预期行为。
#   - dev 模式（默认）：任意请求均可 list/create/update/delete；
#   - prod 模式（NOVEL_PRODUCTION=1）：仍由 NOVEL_PRODUCTION 启动时的
#     fail-fast 兜底（强制 JWT_SECRET / MASTER_KEY 等），跨用户访问
#     当前没有 owner 校验——属于已知设计现状，README 已声明"原型阶段"。
# 如未来要做 per-user Provider 隔离：加 provider_owners(provider_id, user_id)
# 关联表 + 在 list / update 路径挂 owner_filter，CLAUDE.md 禁止未经
# 任务授权增加表，故本次范围仅做注释。


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


# ════════════════════════════════════════════════════════════════
# Provider / LLM 健康检查（2026-08-18 新增）
# ════════════════════════════════════════════════════════════════

@router.get("/health", summary="LLM provider 整体健康状态")
def provider_health():
    """告诉前端「现在能不能调 LLM」。

    用户报告 #3 + #5：「点开始构建 → 显示生成失败 调用LLM失败」。
    真实原因之一：用户没配 provider，但前端没有任何方式提前知道这件事，
    直到点了开始构建、跑了 30 秒、失败了一次才在 banner 里看到一行英文报错。

    修法：把这个端点暴露给前端；
      - mode = "mock" → 没有真实 provider，但 mock 模式不调真 LLM，世界构建照样跑（返回 mock 数据）
      - mode = "live" + has_usable_provider = true → 可以直接跑
      - mode = "live" + has_usable_provider = false → 必须先去设置供应商

    「usability」定义（详见 app/llm_router.py）：
      1. settings.llm_provider == "mock" → 全局 mock，永远可用
      2. settings.llm_provider != "mock" → 走 env 配置：
         a) 角色对应的 provider 必须有 api_key
         b) 角色 provider 没配 key 时退回 settings.llm_provider（兜底），兜底也必须有 key
         c) 都没有 → 不可用

    角色路由默认（app/llm_router.py ROLE_DEFAULTS）：
      structured_logic → deepseek
      creative_detail   → kimi
      consistency_check → minimax
    """
    from ..llm_router import ROLE_DEFAULTS, _provider_configs
    from ..schemas import ProviderOut

    if settings.llm_provider == "mock":
        return {
            "mode": "mock",
            "can_run_llm": True,
            "message": "当前为 mock 模式 — 无需 API key，将返回内置示例数据",
            "active_provider": "mock",
            "active_model": "(内置示例)",
            "roles": {
                role: {"provider": "mock", "model": "(内置示例)", "ok": True}
                for role in ROLE_DEFAULTS
            },
            "db_providers": [],
        }

    providers = _provider_configs()
    # 拿一个 db session 查 DB providers 列表（不要把整个 Depends(get_db) 搬进嵌套函数，
    # 那样会和 get_db yield 交互更复杂；这里用 SessionLocal 直接开）
    db = SessionLocal_for_health()

    # 按角色评估是否可达
    role_status: dict[str, dict] = {}
    any_role_ok = False
    for role, default_name in ROLE_DEFAULTS.items():
        cfg = providers.get(default_name)
        if cfg and cfg.api_key:
            role_status[role] = {
                "provider": cfg.provider,
                "model": cfg.model,
                "ok": True,
                "reason": None,
            }
            any_role_ok = True
        else:
            # 角色 provider 没配 key → 退回全局默认
            fallback = providers.get("default")
            if fallback and fallback.api_key:
                role_status[role] = {
                    "provider": fallback.provider,
                    "model": fallback.model,
                    "ok": True,
                    "reason": f"角色默认 {default_name} 未配置，已退回全局 {fallback.provider}",
                }
                any_role_ok = True
            else:
                role_status[role] = {
                    "provider": default_name,
                    "model": None,
                    "ok": False,
                    "reason": f"未配置 {default_name} 的 API key，且全局 provider ({settings.llm_provider}) 也未配置 key",
                }

    active = providers.get("default")
    db_providers = [
        {
            "id": str(p.id),
            "name": str(p.name),
            "provider_type": str(p.provider_type),
            "default_model": p.default_model,
            "has_api_key": bool(p.api_key_encrypted),
            "api_key_suffix": p.api_key_suffix,
        }
        for p in db.query(Provider).order_by(Provider.created_at.desc()).all()
    ]
    db.close()
    return {
        "mode": "live",
        "can_run_llm": any_role_ok,
        "message": (
            "LLM 已配置，至少一个角色可达"
            if any_role_ok
            else f"当前 llm_provider={settings.llm_provider} 但其 API key 未配置；worldbuild / outline 等所有需要 LLM 的操作都会失败。"
        ),
        "active_provider": active.provider if active else None,
        "active_model": active.model if active else None,
        "active_api_base": active.api_base if active else None,
        "roles": role_status,
        "db_providers": db_providers,
    }


@router.post("/{provider_id}/test", summary="测试单个 provider 是否能联通")
async def test_provider(provider_id: str, db: Session = Depends(get_db)):
    """给前端「点一下立即测试」按钮用：实际发一条最小请求验证连通性 + key 正确性。

    失败原因透传给前端（不静默吞）：
      - 401/403 → key 无效
      - timeout → 网络不通
      - 404 → api_base 错
      - 400 + 余额不足 → key 有效但没钱
    """
    provider = db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(404, "provider not found")
    if not provider.api_key_encrypted:
        raise HTTPException(400, "未配置 API key，请先在编辑界面填 key")
    api_key = decrypt_api_key(provider.api_key_encrypted)
    import httpx
    url = (provider.api_base or "").rstrip("/") + "/chat/completions"
    if not provider.api_base:
        raise HTTPException(400, "provider.api_base 未配置")
    payload = {
        "model": provider.default_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"网络错误：{e}")
    if resp.status_code == 200:
        return {"ok": True, "status": 200, "message": "连通正常"}
    # 把上游错误原文透传，便于前端排查（不暴露完整 body 以避免泄漏不必要的字段）
    snippet = resp.text[:300] if resp.text else ""
    raise HTTPException(
        resp.status_code,
        f"上游 HTTP {resp.status_code}：{snippet}",
    )
