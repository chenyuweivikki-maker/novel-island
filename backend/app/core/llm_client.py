"""
LLM 客户端 — OpenAI 兼容格式（Phase 0：多 Provider 支持）

各家模型（DeepSeek / Moonshot Kimi / 腾讯混元）都是 OpenAI 兼容协议，
这里按"模型名 → Provider"维护一个客户端注册表：
  get_client(model) 根据模型名选对应 Provider 的客户端（base_url + key 不同）。

模型名由 model_router.get_model_for_task 按任务级别路由决定
（缺 key 时已在路由层回退 DeepSeek，这里只负责"用哪个客户端"）。
"""
import json
from openai import OpenAI
from .config import settings
from .model_router import (
    get_model_for_task,
    record_llm_cost,
    record_model_fallback,
    mark_provider_failure,
    MODEL_PROVIDERS,
)

# Provider → OpenAI 客户端缓存（每家一个，懒加载）
_clients: dict[str, OpenAI] = {}

# 推理模型（Moonshot kimi-k2.6）：默认关闭思考模式（thinking=disabled）
#   - 开启思考时 API 强制 temperature=1，且思考会吃掉全部输出 token（实测 4096 token 全被思考消耗、正文为空）
#   - 关闭思考后 API 强制 temperature=0.6，响应快、确定性高、正文正常
#   - 需要深度推理的付费场景（如深度灵感）后续可单独开思考，这里保证默认可靠
REASONING_MODELS = {"kimi-k2.6", "kimi-k2.7-code"}
REASONING_TEMPERATURE = 0.6
# 推理模型输出 token 下限（保险丝：防止长任务被截断）
REASONING_MIN_OUTPUT_TOKENS = 4096


def _effective_temperature(model: str, temperature: float) -> float:
    """推理模型（关闭思考后）API 强制 temperature=0.6，其余按调用方传值"""
    if model in REASONING_MODELS:
        return REASONING_TEMPERATURE
    return temperature


def _effective_max_tokens(model: str, max_tokens: int) -> int:
    """推理模型保留输出 token 下限（保险丝），其余按调用方传值"""
    if model in REASONING_MODELS:
        return max(max_tokens, REASONING_MIN_OUTPUT_TOKENS)
    return max_tokens


def _effective_extra_body(model: str) -> dict | None:
    """推理模型默认关闭思考；非推理模型不加额外参数"""
    if model in REASONING_MODELS:
        return {"thinking": {"type": "disabled"}}
    return None


def _provider_spec(provider: str) -> tuple[str, str]:
    """返回 (api_key, base_url)。未知 Provider 一律按 DeepSeek 处理"""
    if provider == "moonshot":
        return settings.MOONSHOT_API_KEY, settings.MOONSHOT_BASE_URL
    if provider == "hunyuan":
        return settings.HUNYUAN_API_KEY, settings.HUNYUAN_BASE_URL
    return settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_BASE_URL


def get_client(model: str | None = None) -> OpenAI:
    """按模型名取对应 Provider 的客户端；不传模型时默认 DeepSeek（向后兼容）"""
    provider = MODEL_PROVIDERS.get(model or settings.DEEPSEEK_MODEL, "deepseek")
    if provider not in _clients:
        api_key, base_url = _provider_spec(provider)
        _clients[provider] = OpenAI(api_key=api_key, base_url=base_url)
    return _clients[provider]


def chat(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    task: str = 'qa',
    model: str | None = None,
) -> str:
    """调用 LLM 生成回答（里程碑7：按任务路由模型 + 记录成本）

    模型名由 get_model_for_task 决定（model=None），也可由调用方显式指定（Agent 设置覆盖）。
    客户端按模型名选 Provider。
    Phase 0 降级：高阶模型调用失败（余额不足/超时/限流）→ 自动回退 DeepSeek，
    保证主流程不崩（PRD 模型降级/熔断，埋点 model_fallback）。
    """
    if model is None:
        model = get_model_for_task(task)
    try:
        return _chat_once(model, system_prompt, user_prompt, temperature, max_tokens, task)
    except Exception:
        if model == "deepseek-chat":
            raise  # 主力模型都挂了，往上抛让上层兜底
        # 降级：高阶模型故障 → 回退 DeepSeek 主力模型
        mark_provider_failure(model)  # 熔断计数（PRD：连续失败自动熔断该 provider）
        record_model_fallback(model, "deepseek-chat", reason="provider_error")
        print(f"[model_fallback] {model} 调用失败，回退 deepseek-chat（task={task}）")
        return _chat_once("deepseek-chat", system_prompt, user_prompt, temperature, max_tokens, task)


def _chat_once(model, system_prompt, user_prompt, temperature, max_tokens, task) -> str:
    """单次非流式调用（不降级，供 chat 复用）"""
    client = get_client(model)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=_effective_temperature(model, temperature),
        max_tokens=_effective_max_tokens(model, max_tokens),
        extra_body=_effective_extra_body(model),
        stream=False,
    )
    # 里程碑7：记录成本（token数来自API响应）
    record_llm_cost(model, task, resp.usage.prompt_tokens, resp.usage.completion_tokens)
    content = resp.choices[0].message.content
    if not content:
        # 推理模型 max_tokens 可能全被思考过程吃掉 → 正文为空，视为失败
        raise ValueError(f"模型 {model} 返回空内容（推理模型可能 max_tokens 不足）")
    return content


def chat_stream(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 1024,
    task: str = 'qa',
    model: str | None = None,
):
    """流式输出 — 逐 token 返回（高阶模型故障时降级回退 DeepSeek）"""
    if model is None:
        model = get_model_for_task(task)
    try:
        yield from _stream_once(model, system_prompt, user_prompt, temperature, max_tokens)
    except Exception:
        if model == "deepseek-chat":
            raise
        mark_provider_failure(model)
        record_model_fallback(model, "deepseek-chat", reason="provider_error")
        print(f"[model_fallback] {model} 流式调用失败，回退 deepseek-chat（task={task}）")
        yield from _stream_once("deepseek-chat", system_prompt, user_prompt, temperature, max_tokens)


def _stream_once(model, system_prompt, user_prompt, temperature, max_tokens):
    """单次流式调用（不降级）"""
    client = get_client(model)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=_effective_temperature(model, temperature),
        max_tokens=_effective_max_tokens(model, max_tokens),
        extra_body=_effective_extra_body(model),
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
    task: str = 'qa',
    tool_context: dict | None = None,
    tool_log: list | None = None,
) -> str:
    """带工具调用的对话 — 里程碑3+6

    流程（tool calling 4步）：
      1. 把问题 + 工具清单发给 LLM
      2. LLM 决定：直接回答，还是调用工具（输出 tool_call）
      3. 如果调用工具：代码执行 → 把结果作为新消息还给 LLM → LLM 继续
      4. 直到 LLM 给出最终回答

    里程碑6：use_messages=True 时，user_prompt 参数是完整的消息列表
    （含对话历史），而不是单个用户问题。

    里程碑17：tool_context 是在执行工具时注入的上下文（如 novel_id），
    与 LLM 生成的 args 合并后传给执行函数——让工具知道当前在哪个项目。

    tools:          工具说明书列表（给 LLM 看的 JSON Schema）
    tool_executors: {工具名: 执行函数}，LLM 说调哪个，代码就调哪个
    """
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
        model = get_model_for_task(task)
        # 每次按模型名取客户端（工具调用可能跨 Provider 换模型）
        client = get_client(model)
        # 高阶模型故障（余额不足/超时）→ 本轮回退 DeepSeek 重试一次
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools,
                temperature=_effective_temperature(model, temperature),
                max_tokens=_effective_max_tokens(model, max_tokens),
                extra_body=_effective_extra_body(model),
                stream=False,
            )
        except Exception:
            if model == "deepseek-chat":
                raise
            mark_provider_failure(model)
            record_model_fallback(model, "deepseek-chat", reason="provider_error")
            print(f"[model_fallback] {model} 工具调用失败，回退 deepseek-chat（task={task}）")
            client = get_client("deepseek-chat")
            resp = client.chat.completions.create(
                model="deepseek-chat",
                messages=messages,
                tools=tools,
                temperature=_effective_temperature("deepseek-chat", temperature),
                max_tokens=_effective_max_tokens("deepseek-chat", max_tokens),
                extra_body=_effective_extra_body("deepseek-chat"),
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

                # 里程碑17：注入工具上下文（如 novel_id），工具才知道当前项目
                if tool_context:
                    args = {**args, **tool_context}

                # 从映射表找执行函数（找不到就报错给 LLM，让它换路）
                executor = tool_executors.get(tool_name)
                if executor is None:
                    result = f"错误：未知工具 {tool_name}"
                else:
                    try:
                        result = executor(**args)
                    except Exception as e:
                        result = f"工具执行失败：{e}"

                # 工具调用可见性：记录（名字 + 参数摘要 + 结果摘要）
                if tool_log is not None:
                    tool_log.append({
                        "tool": tool_name,
                        "args": json.dumps(args, ensure_ascii=False)[:120],
                        "result": str(result)[:120],
                    })

                # PRD 埋点：tool_usage
                try:
                    from .tracking import tracking
                    tracking.record("tool_usage", tool_name=tool_name,
                                    output_success=not str(result).startswith(("错误", "工具执行失败")),
                                    novel_id=(tool_context or {}).get("novel_id"))
                except Exception:
                    pass

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
