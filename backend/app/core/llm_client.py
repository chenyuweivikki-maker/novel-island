"""
DeepSeek LLM 客户端 — OpenAI 兼容格式

使用 DeepSeek-V3 (deepseek-chat) 做：
1. 基于检索结果的问答生成
2. 实体/人设抽取（Phase 2 预留）
"""
from openai import OpenAI
from .config import settings

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=settings.DEEPSEEK_API_KEY,
            base_url=settings.DEEPSEEK_BASE_URL,
        )
    return _client


def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 1024,
) -> str:
    """调用 DeepSeek 生成回答"""
    client = get_client()
    resp = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=False,
    )
    return resp.choices[0].message.content


def chat_stream(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 1024,
):
    """流式输出 — 逐 token 返回"""
    client = get_client()
    resp = client.chat.completions.create(
        model=settings.DEEPSEEK_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
        stream=True,
    )
    for chunk in resp:
        if chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content


# ===== Prompt 模板 =====

RAG_SYSTEM_PROMPT = """你是「小说岛」的文学助手，专门帮助小说作者回顾和查询自己作品的设定与情节。

规则：
1. 只根据提供的「原文片段」回答问题，不要编造原文中没有的信息。
2. 如果原文片段中没有相关内容，明确说"在当前知识库中未找到相关内容"。
3. 回答要简洁、准确，用自然语言组织，不要简单复述原文。
4. 涉及人物关系、情节发展时，可以适度整合多个片段的信息。
5. 回答末尾标注参考了哪些片段（格式：[参考：片段#1, 片段#3]）。"""


def build_rag_prompt(query: str, retrieved_chunks: list[dict]) -> str:
    """构建 RAG 用户 prompt"""
    context_parts = []
    for r in retrieved_chunks:
        c = r["chunk"]
        context_parts.append(f"【片段#{c.id + 1}】(相似度:{r['score']:.2f})\n{c.text}")

    context = "\n\n---\n\n".join(context_parts)
    return f"""以下是检索到的原文片段：

{context}

---

作者的问题：{query}

请根据以上原文片段回答。如果片段中没有足够信息，请说明。"""
