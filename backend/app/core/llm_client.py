"""
DeepSeek LLM 客户端 — OpenAI 兼容格式

使用 DeepSeek-V3 (deepseek-chat) 做：
1. 基于检索结果的问答生成
2. 实体/人设抽取（Phase 2 预留）
"""
import json
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


def chat_with_tools(
    system_prompt: str,
    user_prompt: str,
    tools: list[dict],
    tool_executors: dict,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    use_messages: bool = False,
) -> str:
    """带工具调用的对话 — 里程碑3+6

    流程（tool calling 4步）：
      1. 把问题 + 工具清单发给 LLM
      2. LLM 决定：直接回答，还是调用工具（输出 tool_call）
      3. 如果调用工具：代码执行 → 把结果作为新消息还给 LLM → LLM 继续
      4. 直到 LLM 给出最终回答

    里程碑6：use_messages=True 时，user_prompt 参数是完整的消息列表
    （含对话历史），而不是单个用户问题。

    tools:        工具说明书列表（给 LLM 看的 JSON Schema）
    tool_executors: {工具名: 执行函数}，LLM 说调哪个，代码就调哪个
    """
    client = get_client()

    # 里程碑6：如果传的是完整消息列表，直接用它；否则构造系统+用户
    if use_messages:
        messages = list(user_prompt)
    else:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    # 最多允许 LLM 连续调 3 次工具（防止它陷入工具循环）
    for _ in range(3):
        resp = client.chat.completions.create(
            model=settings.DEEPSEEK_MODEL,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
        )
        msg = resp.choices[0].message

        # 情况A：LLM 决定调用工具
        if msg.tool_calls:
            # 把 LLM 的 tool_call 追加到消息历史（必须带上，OpenAI协议要求）
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # 逐个执行 LLM 要调的工具
            for tc in msg.tool_calls:
                tool_name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")

                # 从映射表找执行函数（找不到就报错给 LLM，让它换路）
                executor = tool_executors.get(tool_name)
                if executor is None:
                    result = f"错误：未知工具 {tool_name}"
                else:
                    try:
                        result = executor(**args)
                    except Exception as e:
                        result = f"工具执行失败：{e}"

                # 工具执行结果作为新消息还回给 LLM
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

            # 继续循环：LLM 拿到工具结果，决定是继续调还是最终回答
            continue

        # 情况B：LLM 直接给出最终回答
        return msg.content or ""

    # 超过 3 次工具调用还没给回答（防御）
    return "工具调用次数过多，已中止。"


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
