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
import json
import logging
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


log = logging.getLogger("novel_ai.engine.llm")


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


# ── Proxy mount map ──
# P3 wiring: Provider.needs_proxy=True → engine.llm.router.LLMRouter.set_proxy_map()
# lets a configured proxy URL apply to a specific provider's HTTP client.
_proxy_mounts: dict[str, httpx.Client] = {}  # proxy_url -> mounted client
_proxy_lock = threading.Lock()


def _get_proxied_client(provider: str, base_url: str, timeout: int = 120) -> httpx.Client:
    """If a proxy is configured for `provider`, return an httpx.Client with
    proxy mounts that intercept calls to that provider's host.

    Falls back to the regular pool client if no proxy is configured.

    迭代 #46: 之前 `_proxy_mounts.get(provider)` 期望拿到 URL 字符串，但
    `_proxy_mounts` 实际是 `dict[str, httpx.Client]`（用作 client 缓存）。
    真 URL 在 `_PROVIDER_PROXY`（由 set_proxy_map 写入）。
    后果：proxy URL 配置了但 httpx.Client 永远不挂代理——用户以为是网络问题
    实际是代码 bug。
    修法：从 `_PROVIDER_PROXY` 读 URL。
    """
    proxy_url = _PROVIDER_PROXY.get(provider)
    if not proxy_url:
        return _get_client(timeout)

    # Key proxied client by (provider, proxy_url, timeout) to avoid re-init
    key = (provider, proxy_url, timeout)
    with _proxy_lock:
        if key not in _proxy_mounts:
            # httpx.Client with proxy via mounts (httpx doesn't support 'proxies=' kwarg)
            client = httpx.Client(
                timeout=timeout,
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
            # Mount proxy for the host portion of base_url
            try:
                from urllib.parse import urlparse
                host = urlparse(base_url).netloc
                if host:
                    client.mount(f"https://{host}", httpx.Client(proxy=proxy_url, timeout=timeout))
                    client.mount(f"http://{host}", httpx.Client(proxy=proxy_url, timeout=timeout))
            except Exception as e:
                # 迭代 #76: 之前 bare except + pass，proxy 配置失败时
                # caller 完全看不到信号——以为是网络问题实际是代码 bug。
                # 修法：log.warning 带 base_url + exception type 让运维快速定位。
                log.warning(
                    "_get_proxied_client: mount proxy 失败 for provider=%s base_url=%r (%s: %s); "
                    "proxied client 仍可用，但 proxy mounts 缺失——回退到直连",
                    provider, base_url, type(e).__name__, e,
                )
                # 仍然继续 — client 在没 proxy 情况下能工作；log 让运维知道
            _proxy_mounts[key] = client
        return _proxy_mounts[key]  # type: ignore[return-value]


# Module-level proxy map exposed to LLMRouter.set_proxy_map
_PROVIDER_PROXY: dict[str, str] = {}


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
            # 旧版 XiyuTech MiniMax 才需要 GroupId；新版 MiniMax-M3 不需要
            "minimax_group_id": os.getenv("MINIMAX_GROUP_ID", ""),
            "custom":    os.getenv("CUSTOM_API_KEY", ""),
            "custom_api_base":   os.getenv("CUSTOM_API_BASE", ""),
            "custom_model_id":   os.getenv("CUSTOM_MODEL_ID", ""),
        }
        self.routes: dict[str, tuple[str, str]] = dict(MODEL_ROUTES_DEFAULT)
        self.budget: dict[str, tuple[int, int, int]] = dict(TOKEN_BUDGET_DEFAULT)
        self._stats: dict = {"total_calls": 0, "total_cost_usd": 0.0, "by_agent": {}}
        self._stats_lock = threading.Lock()
        # Mock 模式：如果设置了 NOVEL_ENGINE_MOCK=1 或调用方显式 use_mock()，
        # 把所有 routes 切到 mock provider（无需任何 API key 即可端到端跑通）。
        if os.getenv("NOVEL_ENGINE_MOCK") == "1":
            self.use_mock()

    # ---------- DB-driven configuration ----------
    def use_mock(self) -> None:
        """把全部 agent routes 切到 mock provider。

        适用场景：
          - CI / 单元测试（无需 API key 即可端到端跑通）
          - 引擎机制验证（schema 校验、字数 budget、orchestrator 编排）
          - demo / 本地开发（没配 API key 时仍能跑起来）

        调用方式：
          - 设 env NOVEL_ENGINE_MOCK=1 → 构造时自动调
          - 显式 r.use_mock() → 任何时候切
        """
        # 把所有 9 个 agent 切到 mock
        for agent in MODEL_ROUTES_DEFAULT:
            self.routes[agent] = ("mock", "mock-model")
        log.info("LLMRouter.use_mock: 全部 agent routes 切到 mock provider")

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

    def set_proxy_map(self, provider_proxy: dict[str, str]) -> None:
        """Wire per-provider proxy URLs (P3). Keys: 'anthropic' | 'deepseek' |
        'gemini' | 'kimi' | 'minimax' | 'custom'. Values: full proxy URL like
        'http://127.0.0.1:7890'. Driven by Provider.needs_proxy=True."""
        global _PROVIDER_PROXY
        _PROVIDER_PROXY = dict(provider_proxy)

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
            "mock":      self._mock,   # 无需 API key 的测试 provider
        }
        fn = dispatch.get(provider)
        if fn is None:
            raise ValueError(f"未知 Provider：{provider}。支持：{list(dispatch.keys())}")

        if provider == "anthropic":
            return fn(agent_name, system_prompt, user_prompt, model,
                      max_tokens, temperature, use_cache, cached_system)
        return fn(agent_name, system_prompt, user_prompt, model, max_tokens, temperature)

    # ---------- Provider implementations ----------
    def _mock(self, agent, system_prompt, user_prompt, model, max_tokens, temperature):
        """Mock LLM provider：无需 API key，返回 schema 化固定响应。

        历史背景（独立审查标记的中危点）：
          之前要验证 engine 端到端机制（schema 校验、字数 budget、orchestrator
          编排、tools 调用）必须真花钱调 LLM。Mock provider 让这一切离线跑：
          - 单元测试 / 集成测试不依赖外部 API
          - CI 不需要 secret
          - 引擎质量验证（schema 契约）独立于生成质量（LLM 内容）

        返回内容：根据 agent_name 给 schema 化 JSON/文本，每个 agent 走不同
        结构以模拟真实生成场景。writer 模拟生成 ~2000 字章节文本（满足
        call_with_length_budget 区间），其他 agent 返回符合 schema 的 JSON。

        注意：Mock 模式**只是引擎机制测试**，不验证生成内容质量。生产
        生成质量仍要走真 provider。
        """
        text = _MOCK_RESPONSES.get(agent, _MOCK_DEFAULT_TEXT)
        # writer 模拟接近目标字数的章节（满足 call_with_length_budget）
        if agent == "writer":
            text = _mock_chapter_text(agent_name=agent, target_chars=max_tokens)
        # 计费：模拟 $0.001/调用
        cost = 0.001
        self._record(agent, cost, len(user_prompt) // 4, len(text) // 4)
        return text, cost

    def _anthropic(self, agent, system_prompt, user_prompt, model, max_tokens,
                   temperature, use_cache=False, cached_system=None):
        if Anthropic is None:
            raise RuntimeError("anthropic package not installed; pip install anthropic")
        api_key = self.api_keys.get("anthropic", "")
        # P3: 通过 ANTHROPIC_BASE_URL 反代
        base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
        # 迭代 #51: 之前 Anthropic() 直接调用，没传 http_client —— 即使
        # _PROVIDER_PROXY["anthropic"] 配了，proxy 永远不生效（GFW 区域
        # 用户没法用 anthropic）。现在 if needs_proxy → 构造 proxied httpx.Client。
        proxy_url = _PROVIDER_PROXY.get("anthropic")
        http_client = None
        if proxy_url:
            http_client = httpx.Client(proxy=proxy_url, timeout=120)
        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        if http_client is not None:
            client_kwargs["http_client"] = http_client
        client = Anthropic(**client_kwargs)
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
        c = (_get_proxied_client("deepseek", "https://api.deepseek.com")
             if _PROVIDER_PROXY.get("deepseek") else _get_client(120))
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
        c = (_get_proxied_client("gemini", url) if _PROVIDER_PROXY.get("gemini")
             else _get_client(180))
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
        c = (_get_proxied_client("kimi", "https://api.moonshot.cn")
             if _PROVIDER_PROXY.get("kimi") else _get_client(120))
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
        # 2026.6 update: 切换到新版 MiniMax endpoint（api.minimaxi.com）。
        # 鉴权方式：Bearer <MINIMAX_API_KEY>，不再需要 GroupId。
        # Payload/响应格式：标准 OpenAI chat.completions。
        api_key = self.api_keys.get("minimax", "")
        if not api_key:
            raise ValueError("MINIMAX_API_KEY 未设置")
        # 允许用 MINIMAX_BASE_URL env 覆盖默认 endpoint
        base_url = os.environ.get("MINIMAX_BASE_URL") or "https://api.minimaxi.com/v1"
        url = f"{base_url.rstrip('/')}/text/chatcompletion_v2"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        actual_model = model or "MiniMax-M3"
        payload = {
            "model": actual_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            # M3 默认开启深度思考，会把 reasoning_content 当成 content 返回但实际 content 为空。
            # 显式禁用：让模型直接给最终回答。
            "thinking": {"type": "disabled"},
        }
        c = (_get_proxied_client("minimax", base_url)
             if _PROVIDER_PROXY.get("minimax") else _get_client(120))
        r = _post_with_retry(c, url, headers=headers, json=payload)
        data = r.json()
        # chat.completions 格式
        choices = data.get("choices", [])
        if not choices:
            raise ValueError(f"MiniMax 返回无 choices: {data}")
        msg = choices[0].get("message", {}) or {}
        text = msg.get("content", "") or ""
        if not text and msg.get("reasoning_content"):
            # 迭代 #32: 检测到 M3 思考模式被意外开启。
            # 之前 (line 456-458) 是死代码 —— content 已空，重新赋 msg.get("content", "")
            # 还是空，text 仍是 ""，caller 把空文本当正常生成继续 pipeline。
            # 我们的 payload 显式设了 "thinking": {"type": "disabled"}，如果还触发说明：
            #   - 服务端配置变了
            #   - 用户覆盖了 MINIMAX_BASE_URL 指向旧版 endpoint
            #   - 别的代理把 thinking 字段剥掉
            # 此时显式 raise 让配置 bug 暴露，避免静默空文本污染下游章节。
            raise ValueError(
                f"MiniMax M3 返回了 reasoning_content 但 content 为空——"
                f"思考模式被意外开启（payload 已显式 disabled）。"
                f"请检查 MINIMAX_BASE_URL 是否覆盖到旧版 endpoint。"
                f"raw choices[0].message: {msg}"
            )
        if not text:
            # 兜底：有些 M 系列字段在 delta 或 text 字段
            text = choices[0].get("text", "") or data.get("reply", "")
        usage   = data.get("usage", {}) or {}
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
        c = (_get_proxied_client("custom", api_base) if _PROVIDER_PROXY.get("custom")
             else _get_client(180))
        r = _post_with_retry(c, endpoint, headers=headers, json=payload)
        data = r.json()
        text    = data["choices"][0]["message"]["content"]
        u       = data.get("usage", {})
        in_tok  = u.get("prompt_tokens", 0)
        out_tok = u.get("completion_tokens", 0)
        self._record(agent, 0.0, in_tok, out_tok)
        return text, 0.0

    # ─────────────────────────────────────────────
    # Length-budget call (写入路径 length fix)
    # ─────────────────────────────────────────────
    def call_with_length_budget(
        self,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        target_chars: int,
        tolerance: int = 200,
        max_tokens: int | None = None,
        temperature: float = 0.4,
        *,
        max_continues: int = 2,
    ) -> tuple[str, float]:
        """**写入路径**的字数控制：调 LLM 生成 + 强制 truncate 到 budget + 续写到 target。

        为什么有这个方法：
          `call()` 只让 LLM 自己写到哪算哪，事后校验几乎必然超 / 不足。
          这套机制是**生成时**控长度：
            1. 第一次调 LLM，让它"先写一稿"
            2. 超过 `target + tolerance` 字符 → 截断 + ask_continuation
            3. 续写时 prompt 明确写"你前一篇被截断了，请从截断点继续写，剩余 N 字以内"
            4. 最多 max_continues 次（默认 2），最后一次不够也接受

        Returns: (full_text, total_cost_usd)
        """
        budget = target_chars
        soft_max = target_chars + tolerance
        accumulated = ""
        total_cost = 0.0
        already_written = 0

        for i in range(max_continues + 1):
            remaining = budget - already_written
            if remaining <= 0:
                break
            # 第一次按全目标发；之后只发「剩余 N 字」
            if i == 0:
                sys_p = system_prompt
                user_p = user_prompt
                cap = max_tokens or int(target_chars * 1.4)  # 写入路径不超太多
            else:
                sys_p = system_prompt
                tail = accumulated[-600:]  # 给 LLM 续写上下文
                user_p = (
                    f"【你上一次写了 {already_written} 字（截至上一段末尾）。"
                    f"本章共需 {budget} 字（允许 {budget-tolerance}~{budget+tolerance}）。"
                    f"还剩约 {remaining} 字要写。\n"
                    f"以下是上次最后 600 字：\n{tail}\n\n"
                    f"请**接上**上文最后一句续写，**不要再重写**已有内容，"
                    f"写够约 {remaining} 字后停。\n"
                    f"要求：① 直接续写，不要前言 ② 不要写「第N章」/「【卷名】」/'# 标题'/「---」开头"
                )
                cap = int(remaining * 1.4)

            text, cost = self.call(
                agent_name=agent_name,
                system_prompt=sys_p,
                user_prompt=user_p,
                max_tokens=cap,
                temperature=temperature,
            )
            total_cost += cost
            text = text.strip()

            if i == 0:
                accumulated = text
            else:
                # 续写：append 到已有文本
                accumulated = accumulated.rstrip() + "\n\n" + text

            already_written = len(accumulated)

            # 第一次如果已经在 budget 内，直接返回
            if already_written >= budget - tolerance:
                # 截断到 soft_max（避免超太多），用句号边界感知避免切在字中间
                if already_written > soft_max:
                    accumulated = _truncate_at_sentence_boundary(accumulated, soft_max)
                return accumulated, total_cost

        return accumulated, total_cost


def _truncate_at_sentence_boundary(text: str, max_chars: int) -> str:
    """在 max_chars 之内截断 text，**优先停在句末标点处**。

    为什么不直接 `text[:max_chars]`：
      LLM 不会在字中间停——它会自然停在「。」上。
      但我们写完后硬切到 max_chars 经常会切在字中间，
      留下「...林尘走进药铺，林」这种半句话。
      章节结尾半句话会被前端 / 出版平台直接拒绝。

    策略：往回找最近的「句末标点 + 引号」（支持。"！？"等成对标点）。
    如果在 [max_chars - 200, max_chars] 范围内找不到，就 fallback 到硬切
    （总比无限回退好）。
    """
    if len(text) <= max_chars:
        return text

    # 句末标点：中英文 + 配对引号
    sentence_end_chars = "。！？.!?\"」』"

    # 搜索窗口：从 max_chars 往回搜 200 字（容许一点弹性）
    search_start = max(0, max_chars - 200)
    candidate = text[search_start:max_chars]

    # 找最靠右的句末标点
    last_end = -1
    for i, ch in enumerate(candidate):
        if ch in sentence_end_chars:
            last_end = i

    if last_end == -1:
        # 找不到句末标点 → 硬切（fallback）
        return text[:max_chars]

    # last_end 是 candidate 内的索引，转回 text 全局索引
    cut_pos = search_start + last_end + 1  # +1 是要包含这个标点
    return text[:cut_pos]


# ══════════════════════════════════════════════
# Mock provider 数据（不依赖外部 API）
# ══════════════════════════════════════════════
# 每个 agent 的固定响应模板：让 orchestrator / agent 拿到符合 schema 的内容
# 而不是空字符串 / 假 pass。Mock 模式**只是引擎机制测试**。

_MOCK_RESPONSES: dict[str, str] = {
    "planner": json.dumps({
        # ─── Phase 2 修复（iter #85）：mock_payload 必须满足
        # backend/schema/setting_package.schema.json 的 7 个 required 字段，
        # 否则 schema_validator fail-fast 阻止 orchestrator 继续。
        # 之前 mock 只有 5 个 legacy 字段（title/world_view/...），缺 7 个
        # required 字段，mock 模式跑 planner 直接报 SchemaError 退出。──
        "novel_id": "（Mock 由 router 自动注入）",
        "platform": "fanqie",
        "genre": "都市",
        "budget_limit_usd": 500.0,
        "title_candidates": ["（Mock）测试标题A", "（Mock）测试标题B", "（Mock）测试标题C"],
        "tagline": "（Mock）一句话简介：测试 schema 校验、字数 budget、orchestrator 编排的端到端链路。",
        "protagonist": {
            "name": "（Mock）主角",
            "age": 25,
            "background": "（Mock）主角背景：测试场景中的验证角色",
            "personality": "（Mock）克制、谨慎、善于复盘",
            "speech_quirks": ["（Mock）口癖：先看再说"],
            "awakening_trigger": "（Mock）觉醒触发：进入测试 pipeline",
            "initial_power_level": "（Mock）一品",
        },
        "world_setting": {
            "hidden_world_name": "（Mock）九霄",
            "hidden_world_history": "（Mock）一段至少 50 字符的隐秘世界历史。设定于 1984 年灵气潮汐初现，全球能源结构重塑，修士家族转型为隐性财阀，奠定了网文世界的基础格局。",
            "surface_world_name": "（Mock）云州",
            "unique_elements": ["（Mock）灵网与电路耦合", "（Mock）债感能力", "（Mock）九品修炼体系"],
        },
        "power_system": {
            "name": "（Mock）债感修炼体系",
            "currency": "（Mock）人情点",
            "description": "（Mock）通过感知、回应、积累他人对你的债来修炼。",
            "levels": [
                {"level": 1, "name": "（Mock）感债者", "point_threshold": 0,    "ability": "（Mock）模糊感知周围人的债"},
                {"level": 2, "name": "（Mock）识债者", "point_threshold": 500,  "ability": "（Mock）精确识别债务人/债权人"},
                {"level": 3, "name": "（Mock）操债者", "point_threshold": 2000, "ability": "（Mock）主动结债/解债"},
            ],
        },
        "key_characters": [
            {"name": "（Mock）主角",  "role": "主角",     "speech_quirks": ["（Mock）口癖"], "background": "（Mock）测试用"},
            {"name": "（Mock）配角A", "role": "重要配角", "speech_quirks": ["（Mock）口癖"], "background": "（Mock）测试用"},
            {"name": "（Mock）反派B", "role": "反派",     "speech_quirks": ["（Mock）口癖"], "background": "（Mock）测试用"},
        ],
        "arc_outline": [
            {
                "arc_id": 1, "arc_name": "（Mock）第 1 弧",
                "arc_goal": "（Mock）弧目标：跑通 orchestrator 7 节点",
                "estimated_chapters": 10,
                "arc_climax_description": "（Mock）弧高潮：测试章节峰值",
                "arc_climax_chapter_offset": 7,
                "emotion_curve": "低开→持续上升→高潮→收尾",
                "new_characters_introduced": ["（Mock）配角A", "（Mock）反派B"],
                "arc_ending_state": "（Mock）弧结束状态",
                "is_final_arc": False,
            },
            {
                "arc_id": 2, "arc_name": "（Mock）第 2 弧",
                "arc_goal": "（Mock）弧目标：跑通第二弧",
                "estimated_chapters": 10,
                "arc_climax_description": "（Mock）弧高潮",
                "arc_climax_chapter_offset": 7,
                "emotion_curve": "低开→持续上升→高潮→收尾",
                "new_characters_introduced": [],
                "arc_ending_state": "（Mock）",
                "is_final_arc": True,
            },
        ],
        "foreshadowing_seeds": [
            {"content": "（Mock）伏笔种子 1：埋下去等后续章节回收", "target_arc": 2, "linked_character": "（Mock）主角",  "importance": "high"},
            {"content": "（Mock）伏笔种子 2：早期埋下的钩子",                "target_arc": 2, "linked_character": "（Mock）配角A", "importance": "medium"},
        ],
        "golden_chapter_hooks": {
            "chapter_1_opening":      "（Mock）第 1 章开篇方向：测试场景",
            "chapter_1_shuang_point": "（Mock）第 1 章爽点：第一次打脸",
            "chapter_3_cliffhanger":  "（Mock）第 3 章结尾钩子",
        },
    }, ensure_ascii=False),

    # run_outline 期待「章节任务 JSON 数组」（见 outline.py OUTLINE_SYSTEM
    # schema）。修订 2026-07-19：之前是 {"arcs":...,"chapters":...} 对象，
    # mock 模式下 outline 阶段必然解析失败，全链路 mock run 从未跑通。
    "outline": json.dumps([
        {
            "chapter_number": 1, "chapter_role": "铺垫",
            "chapter_goal": "（Mock）主角遭遇异常事件，确立本弧目标",
            "core_conflict": "（Mock）主角 vs 未知异象",
            "main_characters": ["（Mock）主角"],
            "shuang_type": None, "shuang_description": "",
            "ending_hook_type": "悬念钩",
            "ending_hook_description": "（Mock）结尾抛出未解之谜",
            "foreshadowing_ops": [
                {"op": "plant", "desc": "（Mock）伏笔：神秘符号", "target_chapter": 3},
            ],
            "emotion_shift": "平静→紧张",
            "plot_progression": "（Mock）主线启动",
            "setting_constraints": ["（Mock）遵守世界观基本设定"],
            "forbidden_actions": [],
            "target_length": "2000-2200",
            "audit_mode": "full",
            "is_arc_climax": False,
        },
        {
            "chapter_number": 2, "chapter_role": "爽点",
            "chapter_goal": "（Mock）主角首次运用能力化解危机",
            "core_conflict": "（Mock）主角 vs 反派B",
            "main_characters": ["（Mock）主角", "（Mock）反派B"],
            "shuang_type": "打脸", "shuang_description": "（Mock）当众反杀质疑者",
            "ending_hook_type": "危机钩",
            "ending_hook_description": "（Mock）更大的威胁浮出水面",
            "foreshadowing_ops": [
                {"op": "reinforce", "desc": "（Mock）伏笔：神秘符号", "target_chapter": 3},
            ],
            "emotion_shift": "紧张→释然",
            "plot_progression": "（Mock）能力首秀",
            "setting_constraints": ["（Mock）遵守世界观基本设定"],
            "forbidden_actions": [],
            "target_length": "2200-2500",
            "audit_mode": "full",
            "is_arc_climax": False,
        },
        {
            "chapter_number": 3, "chapter_role": "弧高潮",
            "chapter_goal": "（Mock）主角揭开符号真相，击败本弧反派",
            "core_conflict": "（Mock）主角 vs 反派B（决战）",
            "main_characters": ["（Mock）主角", "（Mock）配角A", "（Mock）反派B"],
            "shuang_type": "碾压", "shuang_description": "（Mock）实力碾压收尾",
            "ending_hook_type": "升级钩",
            "ending_hook_description": "（Mock）新地图/新层级展开",
            "foreshadowing_ops": [
                {"op": "resolve", "desc": "（Mock）伏笔：神秘符号", "target_chapter": 3},
            ],
            "emotion_shift": "压抑→爆发",
            "plot_progression": "（Mock）本弧收束",
            "setting_constraints": ["（Mock）遵守世界观基本设定"],
            "forbidden_actions": [],
            "target_length": "3000-3300",
            "audit_mode": "full",
            "is_arc_climax": True,
        },
    ], ensure_ascii=False),

    # run_tracker（agents/tracker.py TRACKER_SYSTEM）消费的字段是
    # chapter_summary / character_states / active_threads / last_chapter_ending
    # / new_foreshadowing 等。旧 mock 用的 entity_updates/foreshadow_updates
    # 在代码库里没有任何消费方——能 parse 但全部被静默忽略，mock 闭环跑完
    # 记忆热层仍是空（writer 拿不到上章结尾/近期事件），2026-07-19 e2e 发现。
    "tracker": json.dumps({
        "chapter_summary": "（Mock）本章摘要：主角初次触发系统，埋下神秘符号伏笔。",
        "character_states": {"（Mock）主角": "获得系统，尚未向外界暴露"},
        "active_threads": ["（Mock）主线：查明系统来历"],
        "last_chapter_ending": "（Mock）上章结尾：主角盯着掌心浮现的神秘符号，听见系统提示音再次响起。",
        "scene_location": "（Mock）主角出租屋",
        "time_context": "（Mock）第一天深夜",
        "new_foreshadowing": [
            {"desc": "（Mock）伏笔：神秘符号", "target_arc": 1},
        ],
    }, ensure_ascii=False),

    "summarizer": "（Mock）本章摘要：跑通 mock LLM → parse_llm_json_response → orchestrator 状态更新整条链路。",

    "compliance": json.dumps({
        "violations": [],
        "risk_level": "low",
        "risk_score": 0.1,
        "rationale": "（Mock）合规通过：mock 文本不触发任何禁忌词。",
    }, ensure_ascii=False),

    # score_chapter 的真实 schema 是 dimensions + overall_score（见
    # checker.py CHECKER_SYSTEM / default）。修订 2026-07-19：之前只有
    # {"score":...,"verdict":...} → calculate_weighted_score 读不到
    # dimensions → 全维度兜底 6 分 → 加权 6.0 < 6.5 → mock 模式每章
    # 必然重写 3 次后升级人工，mock run 永远写不出一章。
    "checker_main": json.dumps({
        "dimensions": {"pacing": 8, "character_voice": 8, "plot_logic": 8,
                       "consistency": 8, "writing_naturalness": 7, "hook_power": 8},
        "overall_score": 7.9,
        "strongest_point": "（Mock）checker_main：节奏与钩子到位。",
        "weakest_point": "",
        "specific_feedback": "（Mock）checker_main 通过：mock 章节符合目标区间。",
    }, ensure_ascii=False),

    "checker_cross1": json.dumps({
        "dimensions": {"pacing": 8, "character_voice": 7, "plot_logic": 8,
                       "consistency": 8, "writing_naturalness": 7, "hook_power": 8},
        "overall_score": 7.7,
        "strongest_point": "（Mock）checker_cross1：与前情一致性无矛盾。",
        "weakest_point": "",
        "specific_feedback": "（Mock）checker_cross1 通过。",
    }, ensure_ascii=False),

    "checker_cross2": json.dumps({
        "dimensions": {"pacing": 7, "character_voice": 8, "plot_logic": 8,
                       "consistency": 8, "writing_naturalness": 7, "hook_power": 7},
        "overall_score": 7.5,
        "strongest_point": "（Mock）checker_cross2：人物动机连贯。",
        "weakest_point": "",
        "specific_feedback": "（Mock）checker_cross2 通过。",
    }, ensure_ascii=False),

    "rewriter": "（Mock）改写后章节：与原章节同义但用词略有调整，验证 rewriter 路径可正常返回。",

    "normalizer": "（Mock）normalized_text: 测试文本已通过 normalizer schema 校验。",
}

_MOCK_DEFAULT_TEXT = "（Mock）默认响应：未识别的 agent，请检查 routes 配置。"


def _mock_chapter_text(agent_name: str, target_chars: int) -> str:
    """生成接近 target_chars 字数的章节文本（满足 call_with_length_budget）。

    中文字符，每个字占 1 字符；句末用「。」保证 _truncate_at_sentence_boundary
    能找到句子边界。
    """
    sentence_unit = "（Mock）这是 mock writer 生成的测试章节，用于验证 engine 端到端机制，包括 schema 校验、字数 budget 截断 + 续写、orchestrator 编排。"
    # 重复直到接近 target_chars
    out = ""
    while len(out) < target_chars:
        out += sentence_unit
    # 截到 target_chars 附近 + 句末标点
    cut = _truncate_at_sentence_boundary(out, target_chars)
    return cut if cut else out[:target_chars]


def _truncate_at_sentence_boundary_for_mock(text: str, max_chars: int) -> str:
    """_truncate_at_sentence_boundary 的公共可见别名（mock 调用）。"""
    return _truncate_at_sentence_boundary(text, max_chars)

