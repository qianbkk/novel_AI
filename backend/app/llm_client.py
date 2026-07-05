"""
统一的 LLM 调用入口。

设计目的：
1. mock 模式下不需要任何 API key / 网络，整条 worldbuild 流水线
   可以本地跑通、验证数据结构和前端 SSE 联调 —— 这是"快速搭原型"
   最关心的部分。
2. 按角色（role）路由到不同 provider：结构化/逻辑类阶段用 DeepSeek，
   需要"网文味"的细节类阶段用 Kimi，一致性复核类阶段用 MiniMax。
   见 llm_router.py 里的依据说明。
3. DeepSeek/Kimi/MiniMax 都是国内服务，不需要代理；
   如果以后接入需要代理的海外服务，把 settings.llm_proxy_url
   填上 http://127.0.0.1:7890 即可，这里会自动应用到 httpx 客户端。
"""
import json
import asyncio
import httpx

from .config import settings
from .llm_router import resolve_provider


class LLMError(Exception):
    pass


def _build_httpx_client() -> httpx.AsyncClient:
    kwargs = {"timeout": settings.llm_timeout_seconds}
    if settings.llm_proxy_url:
        kwargs["proxy"] = settings.llm_proxy_url
    return httpx.AsyncClient(**kwargs)


async def call_llm_json(
    role: str,
    system_prompt: str,
    user_prompt: str,
    mock_payload: dict | None = None,
) -> dict:
    """
    要求模型只返回 JSON，解析失败会重试。

    role: "structured_logic" | "creative_detail" | "consistency_check"
          决定走哪个 provider，见 llm_router.resolve_provider。
    mock 模式下直接返回 mock_payload，方便在没有 API key 的情况下把整条流水线跑通。
    """
    provider_cfg = resolve_provider(role)
    if provider_cfg is None:  # mock 模式
        await asyncio.sleep(0.3)  # 模拟网络延迟，方便前端进度条联调
        return mock_payload or {}

    last_error = None
    async with _build_httpx_client() as client:
        for attempt in range(settings.llm_max_retries + 1):
            try:
                resp = await client.post(
                    f"{provider_cfg.api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {provider_cfg.api_key}"},
                    json={
                        "model": provider_cfg.model,
                        "messages": [
                            {"role": "system", "content": system_prompt + "\n只返回 JSON，不要任何额外文字。"},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                # 迭代 #62: 之前 catch 只到 KeyError — IndexError（empty choices
                # 或 missing message）会跳出重试循环。LLM 返回 {"choices": []}
                # 或 {"choices": [{"message": null}]} 是真实场景（rate limit
                # 触发的 fallback、模型挂了等）→ IndexError / TypeError 都应该
                # 走重试，而不是直接 LLMError 把最后一次 IndexError 暴露。
                text = data["choices"][0]["message"]["content"]
                return json.loads(text)
            except (httpx.HTTPError, json.JSONDecodeError, KeyError,
                    IndexError, TypeError) as e:
                last_error = e
                continue
    raise LLMError(
        f"LLM 调用失败（provider={provider_cfg.provider}, role={role}），"
        f"已重试 {settings.llm_max_retries} 次: {last_error}"
    )
