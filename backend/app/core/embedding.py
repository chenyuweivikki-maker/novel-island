"""
Embedding 客户端 — 里程碑9：BGE-M3 向量化

把文本转成向量（语义），用于向量检索。
用 SiliconFlow 的 BGE-M3（兼容 OpenAI API），免费额度内可用。

用途：
  VectorStore 用它把文本块向量化，检索时按语义匹配。
"""
from openai import OpenAI
from .config import settings

_client: OpenAI | None = None


def get_embedding_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.SILICONFLOW_API_KEY,
            base_url=settings.SILICONFLOW_BASE_URL,
        )
    return _client


def embed_texts(texts: list[str]) -> list[list[float]]:
    """把一批文本转成向量（BGE-M3）"""
    client = get_embedding_client()
    resp = client.embeddings.create(
        model=settings.EMBEDDING_MODEL,
        input=texts,
    )
    # 按输入顺序返回向量
    vectors = [item.embedding for item in resp.data]
    return vectors


def embed_query(text: str) -> list[float]:
    """把单个查询文本转成向量"""
    return embed_texts([text])[0]
