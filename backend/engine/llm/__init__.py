"""LLM router package — moved here from novel_AI/api_client.py to remove the
subprocess / sys.path dependency on the gitignored novel_AI/ reference.
Public surface kept stable: LLMRouter.call(agent_name, system, user) is the
unified entry point agents use."""
from .router import LLMRouter, MODEL_ROUTES_DEFAULT, TOKEN_BUDGET_DEFAULT

__all__ = ["LLMRouter", "MODEL_ROUTES_DEFAULT", "TOKEN_BUDGET_DEFAULT"]
