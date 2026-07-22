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


def _strip_think_blocks(text: str) -> str:
    """去掉 LLM 返回里常见的包装层（MiniMax-M3 等推理模型 + Markdown 围栏）。

    三种常见包装：
      - <think>...</think>  推理痕迹
      - ```json ... ```    Markdown code fence
      - ``` ... ```         Markdown code fence（无语言标签）
      - 两侧裸 ``` 围栏也吃

    实现选择：手写循环而不是 regex。Python regex 的非贪婪 + 嵌套 `>`
    在 multi-line + 跨块场景下会回溯失败，simple replace + while 循环
    行为更可预期。
    """
    while True:
        start = text.find("<think>")
        if start < 0:
            break
        end = text.find("</think>", start)
        if end < 0:
            break
        text = text[:start] + text[end + len("</think>"):]

    # Markdown 围栏：去掉成对的 ``` 块（语言标签可有可无），保留内部内容
    while True:
        start = text.find("```")
        if start < 0:
            break
        end = text.find("```", start + 3)
        if end < 0:
            break
        # 去掉开头 ``` + 可能的语言标签（json / python / 等） + 换行
        fence_open_end = start + 3
        # 跳到语言标签后的换行
        newline = text.find("\n", fence_open_end)
        if newline != -1 and newline - fence_open_end < 20:
            inner_start = newline + 1
        else:
            inner_start = fence_open_end
        # 保留内部内容
        text = text[:start] + text[inner_start:end]

    return text.strip()


def _unwrap_list_response(parsed):
    """真实 LLM (2026-07-20)：MiniMax-M3 等推理模型对 prompt 模糊时常把
    「返回 volumes 列表」解释为「直接返回 list of dicts」，跳过外层 dict。
    这种情况会让下游 `payload.get("volumes", [])` 报 `'list' object has no
    attribute 'get'`。

    容错策略：当 response 是 list 且至少有一个 dict 元素时，包成
    `{"_list": [...]}` 形式，让下游 stage 自己的 `payload.get(key, [])`
    仍然按 dict 访问，但 list 内的数据完整保留。下游 stage 在 .get 拿不到
    key 时仍能 fallback 到空 list，不会再抛 attribute error。
    """
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        # 选择一个合理的 key 名。优先用 items，否则 _items。
        return {"_items": parsed}
    return parsed


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
                # 真实 LLM 接入 (2026-07-20)：加 response_format=json_object 强制
                # 走 JSON 模式（MiniMax-M3 / DeepSeek / OpenAI 兼容端点都支持）。
                # 之前裸调 + prompt 软约束"只返回 JSON"对推理模型不够强，模型
                # 经常写一段散文再写 JSON，json.loads 失败。强制 json_object 模式
                # 后模型仍可能加 <think> 段或 ```json``` 包装，但 _strip_think_blocks
                # 会剥掉。
                resp = await client.post(
                    f"{provider_cfg.api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {provider_cfg.api_key}"},
                    json={
                        "model": provider_cfg.model,
                        "messages": [
                            {"role": "system", "content": system_prompt + "\n只返回 JSON，不要任何额外文字。"},
                            {"role": "user", "content": user_prompt},
                        ],
                        "response_format": {"type": "json_object"},
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
                # MiniMax-M3 等推理模型会把链式思考用 <think>...</think> 包起来，
                # 后面才是真正的 JSON；json_object 模式有时仍包 ```json```。
                # strip 掉再 parse，否则一直 JSONDecodeError 走到重试上限仍 fail。
                text = _strip_think_blocks(text)
                return _unwrap_list_response(json.loads(text))
            except (httpx.HTTPError, json.JSONDecodeError, KeyError,
                    IndexError, TypeError) as e:
                last_error = e
                continue
    raise LLMError(
        f"LLM 调用失败（provider={provider_cfg.provider}, role={role}），"
        f"已重试 {settings.llm_max_retries} 次: {last_error}"
    )
