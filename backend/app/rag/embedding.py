"""
Embedding provider — 选择真模型 or mock:

  - 真模型：默认 Qwen3-Embedding（阿里云百炼 dashscope OpenAI 兼容接口），
    也支持 bge_m3 / openai_compatible / 自定义 endpoint。
  - 自动 fallback：若 API key 未设，自动回退到 mock（offline dev）。
    这样无需任何 env 改动即可本地跑通，但配了 key 就直接用真 embedding。

接真实 embedding 模型时的实战选项：
  - 阿里云百炼 Qwen3-Embedding（text-embedding-v3）：国内，无需代理，按量付费。
    settings.embedding_api_base 默认已设为 DashScope OpenAI 兼容端点。
  - BGE-M3（本地部署）：自家机器跑，0 成本，延迟稳定；需要在 settings 设
    api_base 指向本地服务（如 http://localhost:11434/v1）。
  - OpenAI / Azure：openai_compatible 通用。

接真 embedding 时只需配置 settings.embedding_api_key + model 名，
存储和检索逻辑（add_chapter / search / cosine_similarity）不变。

mock 模式：用字符 bigram 哈希出一个确定性向量，不是真语义 embedding，但
"判断两段文字像不像"bigram 分布天然接近。这跟 llm_client 的 mock_payload
（写死的假数据）不是一回事——mock 是真在算文本相似度，没语义理解能力。
"""
import hashlib
import math

from .. import config as _config_mod

MOCK_EMBEDDING_DIMS = 256

# Qwen3-Embedding 默认 endpoint（阿里云百炼 OpenAI 兼容接口，国内无需代理）。
QWEN3_DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN3_DEFAULT_MODEL    = "text-embedding-v3"


def _settings():
    """通过模块级查 settings，方便测试时 monkeypatch _config_mod.settings。

    实际生产代码直接 `from ..config import settings`，跟这个等价——
    这个间接层是为了 test_alignment_smoke 里能临时替换 settings 而不污染
    全局缓存。
    """
    return _config_mod.settings


def _resolved_provider() -> str:
    """解析真正的 embedding provider，自动 fallback 到 mock。

    规则：
      1) 用户显式设 embedding_provider="mock" → mock
      2) 用户没设 api_key → mock（offline dev 友好）
      3) 否则按用户设的 provider 用真模型
    """
    s = _settings()
    explicit = (s.embedding_provider or "qwen3").strip().lower()
    if explicit == "mock":
        return "mock"
    if not (s.embedding_api_key or "").strip():
        return "mock"
    return explicit


def _api_base_for(provider: str) -> str:
    return _settings().embedding_api_base or QWEN3_DEFAULT_API_BASE


def _model_for(provider: str) -> str:
    return _settings().embedding_model or QWEN3_DEFAULT_MODEL


def _mock_ngram_embedding(text: str, dims: int = MOCK_EMBEDDING_DIMS) -> list[float]:
    vec = [0.0] * dims
    text = text or ""
    for i in range(len(text) - 1):
        bigram = text[i : i + 2]
        h = int(hashlib.md5(bigram.encode("utf-8")).hexdigest(), 16)
        vec[h % dims] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


async def embed_text(text: str) -> list[float]:
    """把一段文本转成向量。返回维度由 provider 决定。

    注意事项：
      - mock 模式：256 维字符 bigram 哈希（确定性，便于测试 + 离线 dev）
      - 真模式：取决于 embedding_model（Qwen3 默认 1024 维）
    维度不同时 cosine_similarity 自动归一化，不影响排序；如需严格对齐
    待所有历史 chunks re-embed 完后再换维度。
    """
    provider = _resolved_provider()
    if provider == "mock":
        return _mock_ngram_embedding(text)

    import httpx  # 延迟导入，mock 模式下完全不需要网络库参与
    api_base = _api_base_for(provider)
    model    = _model_for(provider)
    api_key  = _settings().embedding_api_key

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{api_base.rstrip('/')}/embeddings",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """对两个 embedding 向量算余弦相似度。维度不一致（256 vs 1024）会被
    长度不等直接判定为 0.0，返回不相似 —— 这是 fail-safe：跨维度数据宁可
    当"不像"也不要错配。生产用真 embedding 时建议全量 re-embed 历史 chunks
    或用同一 provider。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)
