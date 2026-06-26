from pathlib import Path

from sqlalchemy.orm import Session

from ..models import Provider

SUPPORTED_PROVIDER_TYPES = {"anthropic", "deepseek", "gemini", "kimi", "minimax", "custom"}


def write_provider_env(novel_ai_dir: str, providers: list[Provider]) -> dict:
    env_path = Path(novel_ai_dir, ".env")
    existing = _read_env(env_path)
    written_types = []

    for provider in providers:
        provider_type = provider.provider_type.lower()
        if provider_type not in SUPPORTED_PROVIDER_TYPES:
            continue
        written_types.append(provider_type)
        extra = provider.extra_json or {}

        if provider_type == "anthropic":
            existing["ANTHROPIC_API_KEY"] = provider.api_key
            existing["ANTHROPIC_MODEL"] = provider.default_model
        elif provider_type == "deepseek":
            existing["DEEPSEEK_API_KEY"] = provider.api_key
            existing["DEEPSEEK_MODEL"] = provider.default_model
            if provider.api_base:
                existing["DEEPSEEK_API_BASE"] = provider.api_base
        elif provider_type == "gemini":
            existing["GEMINI_API_KEY"] = provider.api_key
            existing["GEMINI_MODEL"] = provider.default_model
        elif provider_type == "kimi":
            existing["KIMI_API_KEY"] = provider.api_key
            existing["KIMI_MODEL"] = provider.default_model
            if provider.api_base:
                existing["KIMI_API_BASE"] = provider.api_base
        elif provider_type == "minimax":
            existing["MINIMAX_API_KEY"] = provider.api_key
            existing["MINIMAX_MODEL"] = provider.default_model
            if extra.get("group_id"):
                existing["MINIMAX_GROUP_ID"] = str(extra["group_id"])
        elif provider_type == "custom":
            existing["CUSTOM_API_KEY"] = provider.api_key
            existing["CUSTOM_API_BASE"] = provider.api_base or extra.get("api_base", "")
            existing["CUSTOM_MODEL_ID"] = provider.default_model or extra.get("model", "")

        if provider.needs_proxy:
            proxy_url = extra.get("proxy_url") or existing.get("HTTP_PROXY") or existing.get("HTTPS_PROXY")
            if proxy_url:
                existing["HTTP_PROXY"] = proxy_url
                existing["HTTPS_PROXY"] = proxy_url

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(_format_env(existing), encoding="utf-8")
    return {"env_path": str(env_path), "provider_types": sorted(set(written_types))}


def collect_assigned_providers(db: Session, provider_ids: list[str]) -> list[Provider]:
    if not provider_ids:
        return []
    return db.query(Provider).filter(Provider.id.in_(provider_ids)).all()


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _format_env(values: dict[str, str]) -> str:
    return "\n".join(f"{key}={value}" for key, value in sorted(values.items())) + "\n"
