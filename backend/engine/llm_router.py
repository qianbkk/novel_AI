"""Runtime LLM router — reads Provider + RoleAssignment from DB at call time.
Replaces novel_AI/api_client.py's import-time env var approach.
ponytail: sets module globals on api_client. Single-user, no concurrent runs."""
from __future__ import annotations
from typing import Optional

_ACTIVE_ROUTER: Optional[LLMRouter] = None


def set_active_router(router: LLMRouter):
    global _ACTIVE_ROUTER
    _ACTIVE_ROUTER = router


def get_active_router() -> Optional[LLMRouter]:
    return _ACTIVE_ROUTER


def call_llm(
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    override_provider: str | None = None,
    override_model: str | None = None,
    use_cache: bool = False,
    cached_system: str | None = None,
) -> tuple:
    """Same signature as api_client.call_llm — dispatches through active router."""
    router = _ACTIVE_ROUTER
    if router:
        return router.call_llm(
            agent_name, system_prompt, user_prompt,
            max_tokens, temperature,
            override_provider, override_model,
            use_cache, cached_system,
        )
    from api_client import call_llm as fallback
    return fallback(
        agent_name, system_prompt, user_prompt,
        max_tokens, temperature,
        override_provider, override_model,
        use_cache, cached_system,
    )


class LLMRouter:
    """Builds provider config from DB, monkey-patches into api_client globals.
    ponytail: safe because graph runs sequentially in single-user mode."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self._routes: dict[str, dict] | None = None

    def load_routes(self):
        """Fetch all RoleAssignment + Provider rows from DB."""
        from app.database import SessionLocal
        from app.models import Provider, RoleAssignment

        db = SessionLocal()
        try:
            rows = db.query(RoleAssignment, Provider).join(
                Provider, RoleAssignment.provider_id == Provider.id
            ).all()
            routes = {}
            for ra, p in rows:
                routes[ra.role_key] = {
                    "type": p.provider_type,
                    "key": p.api_key,
                    "base": p.api_base or "",
                    "model": ra.model_override or p.default_model,
                    "extra": p.extra_json or {},
                    "proxy": p.needs_proxy,
                }
            self._routes = routes
        finally:
            db.close()

    def install(self):
        """Push DB config into api_client module globals.
        Call once before running the graph for this project."""
        if self._routes is None:
            self.load_routes()
        import api_client as ac

        # Override MODEL_ROUTES so agent→model routing uses DB
        for role_key, r in (self._routes or {}).items():
            ac.MODEL_ROUTES[role_key] = (r["type"], r["model"])

        # Override API key constants — Python resolves globals at call time,
        # so setting module attrs works even though they were initialized
        # from env vars at import time.
        seen: set[str] = set()
        for r in (self._routes or {}).values():
            pt = r["type"]
            if pt in seen:
                continue
            seen.add(pt)
            if pt == "anthropic":
                ac.ANTHROPIC_API_KEY = r["key"]
            elif pt == "deepseek":
                ac.DEEPSEEK_API_KEY = r["key"]
                if r["base"]:
                    ac.DEEPSEEK_API_BASE = r["base"]
            elif pt == "gemini":
                ac.GEMINI_API_KEY = r["key"]
            elif pt == "kimi":
                ac.KIMI_API_KEY = r["key"]
                if r["base"]:
                    ac.KIMI_API_BASE = r["base"]
            elif pt == "minimax":
                ac.MINIMAX_API_KEY = r["key"]
                gid = r["extra"].get("group_id")
                if gid:
                    ac.MINIMAX_GROUP_ID = str(gid)
            elif pt == "custom":
                ac.CUSTOM_API_KEY = r["key"]
                ac.CUSTOM_API_BASE = r["base"]
                ac.CUSTOM_MODEL_ID = r["model"]

    def call_llm(
        self,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        override_provider: str | None = None,
        override_model: str | None = None,
        use_cache: bool = False,
        cached_system: str | None = None,
    ) -> tuple:
        """Delegate to api_client.call_llm after ensuring config is installed.
        ponytail: on each call, push the route config for this agent.
        A full install() is done once in graph.py before graph execution."""
        import api_client as ac
        return ac.call_llm(
            agent_name, system_prompt, user_prompt,
            max_tokens, temperature,
            override_provider, override_model,
            use_cache, cached_system,
        )

    def build_runnable_config(self) -> dict:
        """Build LangGraph RunnableConfig for this project.
        thread_id = project_id enables SqliteSaver checkpoint isolation."""
        return {
            "configurable": {
                "thread_id": self.project_id,
                "project_id": self.project_id,
            }
        }
