"""
角色路由：不同生成任务交给不同模型，而不是整条流水线写死一个 provider。

依据（2026.6 横评结论，详见 README「模型选型」一节）：
- structured_logic（配置解析/世界观骨架/情节骨架/势力力量体系/货币特殊设定/地图）：
  这些本质是"结构化抽取 + 逻辑推演"，DeepSeek 性价比和逻辑严密度最高，
  但文风偏理工科，不适合直接出文学性正文。
- creative_detail（人物细节/伏笔措辞/实体关系）：
  需要"网文味"和人物语感，Kimi 文风偏文学性，长篇创作有惊喜。
- consistency_check（一致性复核）：
  如果以后要让模型而不是纯规则做复核，适合用长窗口、价格便宜的 MiniMax，
  但要注意"窗口大≠多跳推理准"，复核应该是"喂结构化实体卡片"而不是"喂全文"。

mock 模式下角色路由完全不生效——所有角色统一走 mock，方便离线开发。
"""
from dataclasses import dataclass

from .config import settings


@dataclass
class ProviderConfig:
    provider: str
    api_base: str
    api_key: str
    model: str


# 角色 -> 默认 provider 名称。可以按需调整，不代表"必须"这么分工。
ROLE_DEFAULTS: dict[str, str] = {
    "structured_logic": "deepseek",
    "creative_detail": "kimi",
    "consistency_check": "minimax",
}


def _db_provider_configs() -> dict[str, ProviderConfig] | None:
    """从 Provider DB 表 + RoleAssignment 读 provider 配置。
    返回 None 表示 DB 没配（mock 模式或用户没建 provider）。

    2026-08-19 修（用户报告 401）：之前 resolve_provider 只读 settings/env，
    但 .env 文件里 key 可能过期（用户在 Provider 页更新过 key，但 .env 没同步）→
    settings.minimax_api_key 是无效 key → 401 但 Provider 页 test_provider 通过。
    修法：DB Provider 表优先，env 只作 fallback。
    """
    from .database import SessionLocal
    from .models import Provider, RoleAssignment
    from .security import decrypt_api_key

    db = SessionLocal()
    try:
        rows = db.query(Provider).all()
        if not rows:
            return None
        result: dict[str, ProviderConfig] = {}
        for p in rows:
            key = ""
            if p.api_key_encrypted:
                try:
                    key = decrypt_api_key(p.api_key_encrypted)
                except Exception:
                    pass
            if not key or not p.api_base:
                continue
            cfg = ProviderConfig(
                provider=p.provider_type,
                api_base=p.api_base,
                api_key=key,
                model=p.default_model or "",
            )
            # 同一 provider_type 可能多个，**第一个有效的覆盖**之前的（保持简单）。
            if p.provider_type not in result:
                result[p.provider_type] = cfg
        # RoleAssignment 映射：role → provider type
        ra_rows = db.query(RoleAssignment).all()
        for ra in ra_rows:
            if ra.provider_id and ra.provider_id in [p.id for p in rows]:
                p = db.query(Provider).filter_by(id=ra.provider_id).first()
                if p and p.provider_type in result:
                    # 让该 role 直接指向配过的 provider（覆盖 ROLE_DEFAULTS 默认）
                    result[ra.role_key] = ProviderConfig(
                        provider=p.provider_type,
                        api_base=p.api_base,
                        api_key=result[p.provider_type].api_key,
                        model=ra.model_override or p.default_model or "",
                    )
        return result if result else None
    finally:
        db.close()


def _provider_configs() -> dict[str, ProviderConfig]:
    """优先级：DB Provider 表 > env/settings。"""
    db_cfg = _db_provider_configs()
    if db_cfg:
        return db_cfg
    return {
        "deepseek": ProviderConfig("deepseek", settings.deepseek_api_base, settings.deepseek_api_key, settings.deepseek_model),
        "kimi": ProviderConfig("kimi", settings.kimi_api_base, settings.kimi_api_key, settings.kimi_model),
        "minimax": ProviderConfig("minimax", settings.minimax_api_base, settings.minimax_api_key, settings.minimax_model),
        # 兜底：用全局默认 provider 配置（适配只想用一个 provider 跑通全流程的情况）
        "default": ProviderConfig(settings.llm_provider, settings.llm_api_base, settings.llm_api_key, settings.llm_model),
    }


def resolve_provider(role: str) -> ProviderConfig | None:
    """mock 模式返回 None（调用方据此走 mock_payload）；否则返回该角色应该用的 provider 配置。"""
    if settings.llm_provider == "mock":
        return None

    providers = _provider_configs()
    # 优先看 RoleAssignment 是否直接把该 role 映射到了具体 provider（即便 provider_type 不在 ROLE_DEFAULTS 里）
    cfg = providers.get(role)

    # 角色对应的 provider 没配 key，退回全局默认 provider，而不是直接报错——
    # 这样用户只配一个 provider 也能先跑起来，模型路由是"锦上添花"不是"硬依赖"。
    if cfg and not cfg.api_key:
        cfg = providers.get("default")
    return cfg
