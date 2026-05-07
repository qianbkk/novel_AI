"""
api_client.py — 多模型 API 路由层 V3
支持：Anthropic (Claude) / DeepSeek / Gemini / Kimi / MiniMax / Custom
新增：Prompt Cache / Token 预算 / 调用统计 / 自定义 Provider
"""
import os, json
import httpx
from typing import Optional
from anthropic import Anthropic

# ── API Keys ──
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
DEEPSEEK_API_KEY  = os.getenv("DEEPSEEK_API_KEY", "")
GEMINI_API_KEY    = os.getenv("GEMINI_API_KEY", "")
KIMI_API_KEY      = os.getenv("KIMI_API_KEY", "")
MINIMAX_API_KEY   = os.getenv("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID  = os.getenv("MINIMAX_GROUP_ID", "")
CUSTOM_API_KEY    = os.getenv("CUSTOM_API_KEY", "")
CUSTOM_API_BASE   = os.getenv("CUSTOM_API_BASE", "")   # 例：https://your-proxy.com/v1
CUSTOM_MODEL_ID   = os.getenv("CUSTOM_MODEL_ID", "")   # 例：gpt-4o / qwen-max / 任意

# ── 模型路由表 ──
# 格式：agent_name -> (provider, model_id)
# 修改此表即可切换任意 Agent 使用的模型
MODEL_ROUTES = {
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

# ── Token 预算上限表（system_max, user_max, output_max）──
TOKEN_BUDGET = {
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

# ── 调用统计（进程内累计）──
_stats = {"total_calls": 0, "total_cost_usd": 0.0, "by_agent": {}}

def get_stats() -> dict:
    return dict(_stats)

def reset_stats():
    _stats.update({"total_calls": 0, "total_cost_usd": 0.0, "by_agent": {}})

def _record(agent: str, cost: float, in_tok: int, out_tok: int):
    _stats["total_calls"] += 1
    _stats["total_cost_usd"] += cost
    s = _stats["by_agent"].setdefault(agent, {"calls": 0, "cost": 0.0, "in_tokens": 0, "out_tokens": 0})
    s["calls"] += 1; s["cost"] += cost
    s["in_tokens"] += in_tok; s["out_tokens"] += out_tok


# ══════════════════════════════════════════════
# 统一调用入口
# ══════════════════════════════════════════════
def call_llm(
    agent_name: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    override_provider: Optional[str] = None,
    override_model: Optional[str] = None,
    use_cache: bool = False,
    cached_system: Optional[str] = None,
) -> tuple:
    """
    统一 LLM 调用入口。
    返回 (response_text, cost_usd)

    参数：
      agent_name       — 决定使用哪个模型路由（见 MODEL_ROUTES）
      use_cache        — 是否启用 Prompt Cache（仅 Anthropic 支持）
      cached_system    — 需要缓存的系统提示前缀（可缓存的慢变部分）
      override_provider / override_model — 临时覆盖路由，不修改路由表
    """
    provider, model = MODEL_ROUTES.get(agent_name, ("anthropic", "claude-sonnet-4-5"))
    if override_provider: provider = override_provider
    if override_model:    model    = override_model

    # 应用 Token 预算上限
    budget = TOKEN_BUDGET.get(agent_name)
    if budget:
        max_tokens = min(max_tokens, budget[2])

    dispatch = {
        "anthropic": _anthropic,
        "deepseek":  _deepseek,
        "gemini":    _gemini,
        "kimi":      _kimi,
        "minimax":   _minimax,
        "custom":    _custom,
    }
    fn = dispatch.get(provider)
    if fn is None:
        raise ValueError(f"未知 Provider：{provider}。支持：{list(dispatch.keys())}")

    if provider == "anthropic":
        return fn(agent_name, system_prompt, user_prompt, model, max_tokens, temperature, use_cache, cached_system)
    return fn(agent_name, system_prompt, user_prompt, model, max_tokens, temperature)


# ══════════════════════════════════════════════
# Anthropic（含 Prompt Cache）
# ══════════════════════════════════════════════
def _anthropic(agent, system_prompt, user_prompt, model, max_tokens, temperature,
               use_cache=False, cached_system=None):
    client = Anthropic(api_key=ANTHROPIC_API_KEY)

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
    _record(agent, cost, in_tok, out_tok)
    return text, cost


# ══════════════════════════════════════════════
# DeepSeek
# ══════════════════════════════════════════════
def _deepseek(agent, system_prompt, user_prompt, model, max_tokens, temperature):
    if not DEEPSEEK_API_KEY:
        raise ValueError("DEEPSEEK_API_KEY 未设置，请在 .env 文件中配置")
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens, "temperature": temperature,
    }
    with httpx.Client(timeout=120) as c:
        r = c.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    text    = data["choices"][0]["message"]["content"]
    u       = data.get("usage", {})
    in_tok  = u.get("prompt_tokens", 0)
    out_tok = u.get("completion_tokens", 0)
    cost    = (in_tok * 0.27 + out_tok * 1.10) / 1_000_000
    _record(agent, cost, in_tok, out_tok)
    return text, cost


# ══════════════════════════════════════════════
# Gemini
# ══════════════════════════════════════════════
def _gemini(agent, system_prompt, user_prompt, model, max_tokens, temperature):
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY 未设置")
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={GEMINI_API_KEY}")
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"text": user_prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }
    with httpx.Client(timeout=180) as c:
        r = c.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    _record(agent, 0.002, 0, 0)   # Gemini Flash 成本极低，近似
    return text, 0.002


# ══════════════════════════════════════════════
# Kimi（Moonshot AI）
# ══════════════════════════════════════════════
def _kimi(agent, system_prompt, user_prompt, model, max_tokens, temperature):
    if not KIMI_API_KEY:
        raise ValueError("KIMI_API_KEY 未设置")
    headers = {"Authorization": f"Bearer {KIMI_API_KEY}", "Content-Type": "application/json"}
    payload = {
        "model": model or "moonshot-v1-32k",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens, "temperature": temperature,
    }
    with httpx.Client(timeout=120) as c:
        r = c.post("https://api.moonshot.cn/v1/chat/completions", headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()
    text    = data["choices"][0]["message"]["content"]
    u       = data.get("usage", {})
    in_tok  = u.get("prompt_tokens", 0)
    out_tok = u.get("completion_tokens", 0)
    # Kimi moonshot-v1-32k 计费约 ¥0.024/千token，折算约 $0.0033/千token
    cost = (in_tok + out_tok) * 0.0033 / 1000
    _record(agent, cost, in_tok, out_tok)
    return text, cost


# ══════════════════════════════════════════════
# MiniMax（abab 系列）
# ══════════════════════════════════════════════
def _minimax(agent, system_prompt, user_prompt, model, max_tokens, temperature):
    """
    MiniMax ChatCompletion Pro API
    需要设置：MINIMAX_API_KEY 和 MINIMAX_GROUP_ID
    模型推荐：abab6.5s-chat（性价比高）或 abab6.5-chat（质量高）
    文档：https://platform.minimaxi.com/document/ChatCompletion%20Pro
    """
    if not MINIMAX_API_KEY or not MINIMAX_GROUP_ID:
        raise ValueError("MINIMAX_API_KEY 和 MINIMAX_GROUP_ID 均需在 .env 中设置")

    url = f"https://api.minimax.chat/v1/text/chatcompletion_pro?GroupId={MINIMAX_GROUP_ID}"
    headers = {
        "Authorization": f"Bearer {MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model or "abab6.5s-chat",
        "tokens_to_generate": max_tokens,
        "temperature": temperature,
        "messages": [{"sender_type": "USER", "sender_name": "用户", "text": user_prompt}],
        "bot_setting": [
            {
                "bot_name": "AI助手",
                "content": system_prompt,
            }
        ],
    }
    with httpx.Client(timeout=120) as c:
        r = c.post(url, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    # MiniMax 响应结构
    choices = data.get("choices", [{}])
    text = choices[0].get("messages", [{}])[-1].get("text", "") if choices else ""
    if not text:
        # 兼容简化响应结构
        text = data.get("reply", "")

    usage   = data.get("usage", {})
    in_tok  = usage.get("prompt_tokens", 0)
    out_tok = usage.get("completion_tokens", 0)
    # abab6.5s 约 ¥0.01/千token，折算约 $0.0014/千token
    cost = (in_tok + out_tok) * 0.0014 / 1000
    _record(agent, cost, in_tok, out_tok)
    return text, cost


# ══════════════════════════════════════════════
# Custom（兼容 OpenAI API 格式的任意服务）
# ══════════════════════════════════════════════
def _custom(agent, system_prompt, user_prompt, model, max_tokens, temperature):
    """
    自定义 Provider，兼容任何 OpenAI Chat Completions 格式的 API。
    支持：本地 Ollama、中转代理、阿里云百炼（Qwen）、字节豆包、智谱 GLM 等。

    配置方式（在 .env 中设置）：
      CUSTOM_API_KEY=your-key
      CUSTOM_API_BASE=https://your-endpoint/v1
      CUSTOM_MODEL_ID=your-model-name

    可在 MODEL_ROUTES 中将任意 agent 指向 "custom"，
    或在调用时用 override_provider="custom", override_model="xxx" 临时覆盖。
    """
    if not CUSTOM_API_BASE:
        raise ValueError("CUSTOM_API_BASE 未设置，请在 .env 中配置自定义 Provider 的 API 地址")

    actual_model = model or CUSTOM_MODEL_ID
    if not actual_model:
        raise ValueError("CUSTOM_MODEL_ID 未设置，请在 .env 中或调用时通过 override_model 指定模型名称")

    headers = {"Content-Type": "application/json"}
    if CUSTOM_API_KEY:
        headers["Authorization"] = f"Bearer {CUSTOM_API_KEY}"

    payload = {
        "model": actual_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    base = CUSTOM_API_BASE.rstrip("/")
    endpoint = f"{base}/chat/completions"

    with httpx.Client(timeout=180) as c:
        r = c.post(endpoint, headers=headers, json=payload)
        r.raise_for_status()
        data = r.json()

    text    = data["choices"][0]["message"]["content"]
    u       = data.get("usage", {})
    in_tok  = u.get("prompt_tokens", 0)
    out_tok = u.get("completion_tokens", 0)
    # 自定义模型成本未知，记录为 0（用户可在 budget_manager 中手动调整）
    cost = 0.0
    _record(agent, cost, in_tok, out_tok)
    return text, cost


# ══════════════════════════════════════════════
# 便捷函数：临时切换单次调用的模型
# ══════════════════════════════════════════════
def call_with_model(
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 2000,
    temperature: float = 0.7,
) -> tuple:
    """
    不经过路由表，直接指定 provider 和 model 调用。
    适用于测试或一次性调用特定模型。

    示例：
      text, cost = call_with_model("minimax", "abab6.5s-chat", system, user)
      text, cost = call_with_model("custom", "qwen-max", system, user)
    """
    return call_llm(
        agent_name="__adhoc__",
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        override_provider=provider,
        override_model=model,
    )
