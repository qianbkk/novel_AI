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


def _provider_configs() -> dict[str, ProviderConfig]:
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
    provider_name = ROLE_DEFAULTS.get(role, "default")
    cfg = providers.get(provider_name)

    # 角色对应的 provider 没配 key，退回全局默认 provider，而不是直接报错——
    # 这样用户只配一个 provider 也能先跑起来，模型路由是"锦上添花"不是"硬依赖"。
    if cfg and not cfg.api_key:
        cfg = providers["default"]
    return cfg
