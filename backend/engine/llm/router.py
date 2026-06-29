"""LLM router — multi-provider API client.

Migrated from novel_AI/api_client.py (now gitignored reference). Behavior
preserved 1:1; the only structural change is replacing module-level
globals (ANTHROPIC_API_KEY, MODEL_ROUTES, _stats, _http_clients) with
LLMRouter instance state, so a backend process can have multiple routers
per project (one per NovelAIBinding) without monkey-patching shared
state.

Supported providers: anthropic / deepseek / gemini / kimi / MiniMax / custom
"""
from __future__ import annotations
import os
import threading
from typing import Optional

import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

try:
    from anthropic import Anthropic
except ImportError:  # allow import without anthropic installed
    Anthropic = None  # type: ignore


# ── Defaults (used if RoleAssignment rows are absent) ──
MODEL_ROUTES_DEFAULT = {
    "orchestrator":   ("deepseek",   "deepseek-chat"),
    "planner":        ("anthropic",  "claude-sonnet-4-5"),
    "outline":        ("deepseek",   "deepseek-chat"),
    "writer":         ("anthropic",  "claude-sonnet-4-5"),
    "normalizer":     ("anthropic",  "claude-sonnet-4-5"),
    "compliance":     ("deepseek",   "deepseek-chat"),
    "checker_main":   ("deepseek",   "deepseek-chat"),
    "checker_cross1": ("anthropic",  "claude-sonnet-4-5"),
    "checker_cross2": ("deepseek",   "deepseek-chat"),
    "rewriter":       ("anthropic",  "claude-sonnet-4-5"),
    "tracker":        ("deepseek",   "deepseek-chat"),
    "summarizer":     ("anthropic",  "claude-sonnet-4-5"),
}

TOKEN_BUDGET_DEFAULT = {
    "writer":         (2000, 3000, 4000),
    "rewriter":       (1500, 3500, 4000),
    "checker_main":   (800,  3000, 600),
    "checker_cross1": (800,  3000, 600),
    "checker_cross2": (800,  3000, 600),
    "compliance":     (600,  2000, 600),
    "normalizer":     (500,  4000, 4000),
    "tracker":        (800,  2000, 1200),
    "outline":        (1000, 2000, 8000),
    "planner":        (1500, 3000, 6000),
    "summarizer":     (600,  2000, 1000),
}


# ── Per-process HTTP client pool (process-wide, like the original) ──
_http_clients: dict[int, httpx.Client] = {}
_http_clients_lock = threading.Lock()


def _get_client(timeout: int = 120) -> httpx.Client:
    """Reuse one httpx.Client per timeout value. Module-level so the
    connection pool survives across multiple router instances."""
    with _http_clients_lock:
        if timeout not in _http_clients:
            _http_clients[timeout] = httpx.Client(
                timeout=timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return _http_clients[timeout]


class _HTTPClientError(httpx.HTTPError):
    """4xx business error — marked non-retryable by _post_with_retry."""
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=10),
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    reraise=True,
)
def _post_with_retry(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """POST + auto-retry on network errors and 5xx (3 attempts, exp backoff 1-10s).
    4xx errors are NOT retried (auth/quota fail-fast)."""
    r = client.post(url, **kwargs)
    if 500 <= r.status_code < 600:
        r.raise_for_status()  # HTTPStatusError → caught by tenacity
    elif 400 <= r.status_code < 500:
        raise _HTTPClientError(r.status_code, f"HTTP {r.status_code}: {r.text[:200]}")
    return r


# ══════════════════════════════════════════════
# LLMRouter
# ══════════════════════════════════════════════
class LLMRouter:
    """Stateful multi-provider LLM client.

    One instance per project (or per process). Holds the API keys, the
    model routes, the token budget, and the per-agent call stats. The
    `configure(...)` method is the bridge that backend.engine.llm_router
    uses to push DB-driven RoleAssignment rows into this client.
    """

    def __init__(self, project_id: str | None = None):
        self.project_id = project_id
        # API keys (read from env at construction; configure() can override)
        self.api_keys: dict[str, str] = {
            "anthropic": os.getenv("ANTHROPIC_API_KEY", ""),
            "deepseek":  os.getenv("DEEPSEEK_API_KEY", ""),
            "gemini":    os.getenv("GEMINI_API_KEY", ""),
            "kimi":      os.getenv("KIMI_API_KEY", ""),
            "minimax":   os.getenv("MINIMAX_API_KEY", ""),
            "minimax_group_id": os.getenv("MINIMAX_GROUP_ID", ""),
            "custom":    os.getenv("CUSTOM_API_KEY", ""),
            "custom_api_base":   os.getenv("CUSTOM_API_BASE", ""),
            "custom_model_id":   os.getenv("CUSTOM_MODEL_ID", ""),
        }
        self.routes: dict[str, tuple[str, str]] = dict(MODEL_ROUTES_DEFAULT)
        self.budget: dict[str, tuple[int, int, int]] = dict(TOKEN_BUDGET_DEFAULT)
        self._stats: dict = {"total_calls": 0, "total_cost_usd": 0.0, "by_agent": {}}
        self._stats_lock = threading.Lock()

    # ---------- DB-driven configuration ----------
    def configure(
        self,
        *,
        routes: Optional[dict[str, tuple[str, str]]] = None,
        api_keys: Optional[dict[str, str]] = None,
        budget: Optional[dict[str, tuple[int, int, int]]] = None,
    ) -> None:
        """Apply DB-driven settings. Called by backend.engine.llm_router once
        it has read RoleAssignment × Provider rows."""
        if routes:
            self.routes.update(routes)
        if budget:
            self.budget.update(budget)
        if api_keys:
            self.api_keys.update(api_keys)

    def get_stats(self) -> dict:
        with self._stats_lock:
            return dict(self._stats)

    def reset_stats(self) -> None:
        with self._stats_lock:
            self._stats.update({"total_calls": 0, "total_cost_usd": 0.0, "by_agent": {}})

    def _record(self, agent: str, cost: float, in_tok: int, out_tok: int) -> None:
        with self._stats_lock:
            self._stats["total_calls"] += 1
            self._stats["total_cost_usd"] += cost
            s = self._stats["by_agent"].setdefault(
                agent, {"calls": 0, "cost": 0.0, "in_tokens": 0, "out_tokens": 0}
            )
            s["calls"] += 1
            s["cost"] += cost
            s["in_tokens"] += in_tok
            s["out_tokens"] += out_tok

    # ---------- Unified entry point ----------
    def call(
        self,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        *,
        override_provider: Optional[str] = None,
        override_model: Optional[str] = None,
        use_cache: bool = False,
        cached_system: Optional[str] = None,
    ) -> tuple[str, float]:
        """Unified LLM call. Returns (text, cost_usd)."""
        provider, model = self.routes.get(agent_name, ("anthropic", "claude-sonnet-4-5"))
        if override_provider:
            provider = override_provider
        if override_model:
            model = override_model

        # Apply token budget cap
        budget = self.budget.get(agent_name)
        if budget:
            max_tokens = min(max_tokens, budget[2])

        dispatch = {
            "anthropic": self._anthropic,
            "deepseek":  self._deepseek,
            "gemini":    self._gemini,
            "kimi":      self._kimi,
            "minimax":   self._minimax,
            "custom":    self._custom,
        }
        fn = dispatch.get(provider)
        if fn is None:
            raise ValueError(f"未知 Provider：{provider}。支持：{list(dispatch.keys())}")

        if provider == "anthropic":
            return fn(agent_name, system_prompt, user_prompt, model,
                      max_tokens, temperature, use_cache, cached_system)
        return fn(agent_name, system_prompt, user_prompt, model, max_tokens, temperature)

    # ---------- Provider implementations ----------
    def _anthropic(self, agent, system_prompt, user_prompt, model, max_tokens,
                   temperature, use_cache=False, cached_system=None):
        if Anthropic is None:
            raise RuntimeError("anthropic package not installed; pip install anthropic")
        api_key = self.api_keys.get("anthropic", "")
        client = Anthropic(api_key=api_key)
        if use_cache and cached_system:
            system = [
                {"type": "text", "text": cached_system, "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": system_prompt},
            ]
        else:
            system = system_prompt
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature,
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
        text    = resp.content[0].text
        in_tok  = resp.usage.input_tokens
        out_tok = resp.usage.output_tokens
        cache_read  = getattr(resp.usage, "cache_read_input_tokens", 0)
        cache_write = getattr(resp.usage, "cache_creation_input_tokens", 0)
        regular_in  = in_tok - cache_read - cache_write
        cost = (regular_in * 3 + cache_write * 3.75 + cache_read * 0.3 + out_tok * 15) / 1_000_000
        self._record(agent, cost, in_tok, out_tok)
        return text, cost

    def _deepseek(self, agent, system_prompt, user_prompt, model, max_tokens, temperature):
        api_key = self.api_keys.get("deepseek", "")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens, "temperature": temperature,
        }
        c = _get_client(120)
        r = _post_with_retry(c, "https://api.deepseek.com/chat/completions",
                             headers=headers, json=payload)
        data = r.json()
        text    = data["choices"][0]["message"]["content"]
        u       = data.get("usage", {})
        in_tok  = u.get("prompt_tokens", 0)
        out_tok = u.get("completion_tokens", 0)
        cost    = (in_tok * 0.27 + out_tok * 1.10) / 1_000_000
        self._record(agent, cost, in_tok, out_tok)
        return text, cost

    def _gemini(self, agent, system_prompt, user_prompt, model, max_tokens, temperature):
        api_key = self.api_keys.get("gemini", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY 未设置")
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={api_key}")
        payload = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": user_prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
        }
        c = _get_client(180)
        r = _post_with_retry(c, url, json=payload)
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        self._record(agent, 0.002, 0, 0)
        return text, 0.002

    def _kimi(self, agent, system_prompt, user_prompt, model, max_tokens, temperature):
        api_key = self.api_keys.get("kimi", "")
        if not api_key:
            raise ValueError("KIMI_API_KEY 未设置")
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model or "moonshot-v1-32k",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens, "temperature": temperature,
        }
        c = _get_client(120)
        r = _post_with_retry(c, "https://api.moonshot.cn/v1/chat/completions",
                             headers=headers, json=payload)
        data = r.json()
        text    = data["choices"][0]["message"]["content"]
        u       = data.get("usage", {})
        in_tok  = u.get("prompt_tokens", 0)
        out_tok = u.get("completion_tokens", 0)
        cost = (in_tok + out_tok) * 0.0033 / 1000
        self._record(agent, cost, in_tok, out_tok)
        return text, cost

    def _minimax(self, agent, system_prompt, user_prompt, model, max_tokens, temperature):
        api_key = self.api_keys.get("minimax", "")
        group_id = self.api_keys.get("minimax_group_id", "")
        if not api_key or not group_id:
            raise ValueError("MINIMAX_API_KEY 和 MINIMAX_GROUP_ID 均需在 .env 中设置")
        url = f"https://api.minimax.chat/v1/text/chatcompletion_pro?GroupId={group_id}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model or "abab6.5s-chat",
            "tokens_to_generate": max_tokens,
            "temperature": temperature,
            "messages": [{"sender_type": "USER", "sender_name": "用户", "text": user_prompt}],
            "bot_setting": [{"bot_name": "AI助手", "content": system_prompt}],
        }
        c = _get_client(120)
        r = _post_with_retry(c, url, headers=headers, json=payload)
        data = r.json()
        choices = data.get("choices", [{}])
        text = choices[0].get("messages", [{}])[-1].get("text", "") if choices else ""
        if not text:
            text = data.get("reply", "")
        usage   = data.get("usage", {})
        in_tok  = usage.get("prompt_tokens", 0)
        out_tok = usage.get("completion_tokens", 0)
        cost = (in_tok + out_tok) * 0.0014 / 1000
        self._record(agent, cost, in_tok, out_tok)
        return text, cost

    def _custom(self, agent, system_prompt, user_prompt, model, max_tokens, temperature):
        api_key = self.api_keys.get("custom", "")
        api_base = self.api_keys.get("custom_api_base", "")
        custom_model = self.api_keys.get("custom_model_id", "")
        if not api_base:
            raise ValueError("CUSTOM_API_BASE 未设置")
        actual_model = model or custom_model
        if not actual_model:
            raise ValueError("CUSTOM_MODEL_ID 未设置")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": actual_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens, "temperature": temperature,
        }
        endpoint = f"{api_base.rstrip('/')}/chat/completions"
        c = _get_client(180)
        r = _post_with_retry(c, endpoint, headers=headers, json=payload)
        data = r.json()
        text    = data["choices"][0]["message"]["content"]
        u       = data.get("usage", {})
        in_tok  = u.get("prompt_tokens", 0)
        out_tok = u.get("completion_tokens", 0)
        self._record(agent, 0.0, in_tok, out_tok)
        return text, 0.0
