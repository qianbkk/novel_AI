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


# ── Proxy mount map ──
# P3 wiring: Provider.needs_proxy=True → engine.llm.router.LLMRouter.set_proxy_map()
# lets a configured proxy URL apply to a specific provider's HTTP client.
_proxy_mounts: dict[str, httpx.Client] = {}  # proxy_url -> mounted client
_proxy_lock = threading.Lock()


def _get_proxied_client(provider: str, base_url: str, timeout: int = 120) -> httpx.Client:
    """If a proxy is configured for `provider`, return an httpx.Client with
    proxy mounts that intercept calls to that provider's host.

    Falls back to the regular pool client if no proxy is configured.
    """
    proxy_url = _proxy_mounts.get(provider)
    if not proxy_url:
        return _get_client(timeout)

    # Key proxied client by (proxy_url, timeout) to avoid re-init
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
            except Exception:
                pass
            _proxy_mounts[key] = client
        return _proxy_mounts[key]


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
        # P3: 通过 ANTHROPIC_BASE_URL 反代（如 anthropic provider.needs_proxy=True + ANTHROPIC_PROXY 设置）
        client = Anthropic(
            api_key=api_key,
            base_url=os.environ.get("ANTHROPIC_BASE_URL") or None,
        )
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
        if not text and "reasoning_content" in msg:
            # M3 思考模型：reasoning_content 不算正文，找 content
            text = msg.get("content", "") or ""
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

