"""Runtime LLM router — reads Provider + RoleAssignment from DB and
configures a backend.engine.llm.router.LLMRouter.

P1-E rewrite: no more monkey-patching the old api_client module globals.
The router is a fully-fledged backend.engine.llm.router.LLMRouter that
holds all state internally; configure() pushes DB-driven config into it.

P3 add: needs_proxy wiring — when Provider.needs_proxy=True, the
configured proxy URL (from env: <PROVIDER>_PROXY) is mounted onto the
provider-specific httpx.Client. This makes region-restricted providers
(deepseek / anthropic behind GFW) usable through a proxy.
"""
from __future__ import annotations
import os
from typing import Optional

from .llm.router import LLMRouter as _EngineRouter


_ACTIVE_ROUTER: Optional[_EngineRouter] = None


def set_active_router(router: _EngineRouter) -> None:
    global _ACTIVE_ROUTER
    _ACTIVE_ROUTER = router


def get_active_router() -> Optional[_EngineRouter]:
    return _ACTIVE_ROUTER


def reset_active_router() -> None:
    """Drop the active router. Used by tests and after-process teardown."""
    global _ACTIVE_ROUTER
    _ACTIVE_ROUTER = None


class LLMRouter:
    """Reads Provider + RoleAssignment from the database and pushes the
    resulting config into a backend.engine.llm.router.LLMRouter instance.

    The constructor does NOT touch the DB — call load_routes() + install()
    explicitly. This keeps the side effect surface small and lets tests
    bypass the DB by passing in a pre-configured router directly.
    """

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._routes: dict[str, dict] | None = None
        # The actual provider-aware router that does the calls.
        self._engine = _EngineRouter(project_id=project_id)

    # ---- DB-driven configuration ----
    def load_routes(self) -> None:
        """Read RoleAssignment × Provider rows. Sets self._routes."""
        from app.database import SessionLocal
        from app.models import Provider, RoleAssignment
        from app.security import decrypt_api_key

        db = SessionLocal()
        try:
            rows = (
                db.query(RoleAssignment, Provider)
                .join(Provider, RoleAssignment.provider_id == Provider.id)
                .all()
            )
            routes: dict[str, dict] = {}
            for ra, p in rows:
                # api_key 加密存储（commit 加密修复）→ 读时解密
                key = ""
                if p.api_key_encrypted:
                    try:
                        key = decrypt_api_key(p.api_key_encrypted)
                    except Exception:
                        # 解密失败（MASTER_KEY 变了等）→ 该 provider 暂时不可用
                        key = ""
                routes[ra.role_key] = {
                    "type":  p.provider_type,
                    "key":   key,
                    "base":  p.api_base or "",
                    "model": ra.model_override or p.default_model or "",
                    "extra": p.extra_json or {},
                    "proxy": p.needs_proxy,
                }
            self._routes = routes
        finally:
            db.close()

    def install(self) -> _EngineRouter:
        """Push DB config into the engine router and make it the active one.

        Returns the configured engine router so callers can hold a reference.
        """
        if self._routes is None:
            self.load_routes()

        routes: dict[str, tuple[str, str]] = {}
        api_keys: dict[str, str] = {}
        for role_key, r in (self._routes or {}).items():
            routes[role_key] = (r["type"], r["model"])
            # Map provider type → the env-style key the engine router stores.
            if r["type"] == "anthropic":
                api_keys["anthropic"] = r["key"]
            elif r["type"] == "deepseek":
                api_keys["deepseek"] = r["key"]
                if r["base"]:
                    api_keys.setdefault("deepseek_api_base", r["base"])
            elif r["type"] == "gemini":
                api_keys["gemini"] = r["key"]
            elif r["type"] == "kimi":
                api_keys["kimi"] = r["key"]
            elif r["type"] == "minimax":
                api_keys["minimax"] = r["key"]
                gid = r["extra"].get("group_id") if r["extra"] else None
                if gid:
                    api_keys["minimax_group_id"] = str(gid)
            elif r["type"] == "custom":
                api_keys["custom"] = r["key"]
                if r["base"]:
                    api_keys["custom_api_base"] = r["base"]
                if r["model"]:
                    api_keys["custom_model_id"] = r["model"]

        self._engine.configure(routes=routes, api_keys=api_keys)

        # Wire provider.needs_proxy → engine LLMRouter proxy URL
        # （anthropic / deepseek / kimi 等可能因地区需要走代理；HTTP 客户端按 provider 设置）
        if self._routes:
            proxy_by_provider: dict[str, str] = {}
            for role_key, r in self._routes.items():
                if r.get("proxy") and r.get("base"):
                    # base 字段在 anthropic/deepseek 不直接用，但 provider 的 proxy=True
                    # 表示 "此 provider 需要走代理"。代理 URL 从环境变量读：
                    # ANTHROPIC_PROXY / DEEPSEEK_PROXY / GEMINI_PROXY / KIMI_PROXY / MINIMAX_PROXY
                    env_key = f"{r['type'].upper()}_PROXY"
                    proxy_url = os.environ.get(env_key, "").strip()
                    if proxy_url:
                        proxy_by_provider[r["type"]] = proxy_url
            if proxy_by_provider:
                self._engine.set_proxy_map(proxy_by_provider)

        set_active_router(self._engine)
        # Wire all agents that hold their own active-router reference.
        # Each agent reads from `get_active_router()` on every call, so this
        # is mainly to ensure the writer / future agent modules that capture
        # the router at module load still work.
        for _name, _modname in (
            ("writer",     "writer"),
            ("normalizer", "normalizer"),
            ("compliance", "compliance"),
            ("checker",    "checker"),
            ("rewriter",   "rewriter"),
            ("tracker",    "tracker"),
            ("summarizer", "summarizer"),
            ("outline",    "outline"),
        ):
            try:
                _mod = __import__(f"backend.engine.agents.{_modname}",
                                  fromlist=["set_active_router"])
                if hasattr(_mod, "set_active_router"):
                    _mod.set_active_router(self._engine)
            except Exception:
                # Agent modules may not have set_active_router — that's fine,
                # they read get_active_router() per-call anyway.
                pass
        return self._engine

    @property
    def engine(self) -> _EngineRouter:
        return self._engine

    def get_stats(self) -> dict:
        return self._engine.get_stats()

    def reset_stats(self) -> None:
        self._engine.reset_stats()

    def build_runnable_config(self) -> dict:
        """LangGraph RunnableConfig with thread_id = project_id."""
        return {
            "configurable": {
                "thread_id":  self.project_id,
                "project_id": self.project_id,
            }
        }
