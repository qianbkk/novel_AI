"""
mock 模式下用字符 bigram 哈希出一个确定性向量，不是真正的语义 embedding，
但对"判断两段文字像不像"这个目的来说足够诚实——意思相近、用词雷同的文字，
bigram 分布天然接近，足够把"重复度检测"这条链路在离线状态下跑通、写测试。
这跟 llm_client 的 mock_payload（完全写死的假数据）不是一回事：
n-gram 向量是真的在算文本相似度，只是没有语义理解能力。

接真实 embedding 模型时（Qwen3-Embedding 走阿里云百炼，或本地部署 BGE-M3），
只需要改 embed_text() 里的网络调用部分，存储和检索逻辑（cosine_similarity、
retrieval.py 里的三个函数）完全不用动——这是把"能不能识别语义相似"和
"怎么存/怎么查向量"这两件事解耦开的好处。
"""
import hashlib
import math

from ..config import settings

MOCK_EMBEDDING_DIMS = 256


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
    if settings.embedding_provider == "mock":
        return _mock_ngram_embedding(text)

    import httpx  # 延迟导入，mock 模式下完全不需要网络库参与

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.embedding_api_base}/embeddings",
            headers={"Authorization": f"Bearer {settings.embedding_api_key}"},
            json={"model": settings.embedding_model, "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a)) or 1.0
    norm_b = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (norm_a * norm_b)
