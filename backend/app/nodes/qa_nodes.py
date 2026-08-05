"""
问答链路节点 — 里程碑1+2：LangGraph状态机

里程碑1：RetrieveNode（检索）+ GenerateNode（事实问答）
里程碑2：IntentRouterNode（意图路由）+ InspireNode（灵感分支）

每个节点：输入整个 State（背包）→ 返回要改写的字段（部分更新）
"""
from typing import Any, Dict

from ..core.retriever import retriever
from ..core.llm_client import chat, chat_with_tools, RAG_SYSTEM_PROMPT, build_rag_prompt
from ..core.memory import memory
from ..models.state import NovelIslandState
from ..tools.kb_tools import AVAILABLE_TOOLS, TOOL_EXECUTORS


class RetrieveNode:
    """检索节点：从知识库召回 Top-K 片段，写入 state['retrieved_chunks']"""

    name = "retrieve"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        top_k = state.get("top_k", 5)

        # 检索（核心检索逻辑还是复用 retriever，状态机只是把它包成节点）
        results = retriever.search(query, top_k)

        # 把检索结果写回背包 —— 这就是 Node 的"返回值=要改写的字段"
        return {
            "retrieved_chunks": results,
            "current_step": self.name,
        }


class IntentRouterNode:
    """意图路由节点：根据问题关键词判断意图，写入 state['current_intent']

    里程碑2用规则（关键词）判断——便宜、可解释、零依赖。
    里程碑7升级为 LLM 意图分类时，只改这一个节点，不影响图结构。
    """

    name = "intent_router"

    # 灵感类关键词：命中则走灵感分支，否则走事实问答
    INSPIRATION_KEYWORDS = [
        "卡文", "灵感", "剧情", "怎么发展", "后面", "写不下去",
        "设计", "方案", "反转", "建议", "下一个情节", "后续",
    ]

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")

        # 规则判断：命中灵感关键词 → inspiration，否则 → fact_qa
        intent = "inspiration" if any(
            kw in query for kw in self.INSPIRATION_KEYWORDS
        ) else "fact_qa"

        return {
            "current_intent": intent,
            "current_step": self.name,
        }


def route_by_intent(state: NovelIslandState) -> str:
    """条件边路由函数：返回下一个节点的名字

    这个函数不是 Node，是给 add_conditional_edges 用的"指路牌"。
    它不读写 State（不返回 dict），只读 State 返回节点名。
    """
    return state.get("current_intent", "fact_qa")


class GenerateNode:
    """生成节点：事实问答 —— 拼 prompt → 调 LLM → 回答写回背包"""

    name = "generate"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        results = state.get("retrieved_chunks", [])

        # 没有检索到内容，直接给兜底回答（不让LLM编）
        if not results:
            return {
                "agent_response": "在当前知识库中未找到与问题相关的内容。",
                "sources": [],
                "current_step": self.name,
            }

        # 拼 prompt（复用现有逻辑）
        user_prompt = build_rag_prompt(query, results)

        # 调 LLM
        answer = chat(RAG_SYSTEM_PROMPT, user_prompt)

        # 整理来源，写回背包
        sources = [
            {"chunk_id": r["chunk"].id, "score": round(r["score"], 4)}
            for r in results
        ]

        return {
            "agent_response": answer,
            "sources": sources,
            "current_step": self.name,
        }


# AgentNode 的系统提示词：告诉 LLM 它有哪些工具可用、怎么用
AGENT_SYSTEM_PROMPT = """你是「小说岛」的智能助手，帮助小说作者查询作品设定、拓展剧情。

你有以下工具可用：
- search_kb(query, top_k)：搜索小说知识库，找到与问题相关的原文片段。

使用规则：
1. 当用户询问小说中的人物、情节、设定等具体内容时，先调用 search_kb 检索原文。
2. 基于工具返回的原文片段回答，不要编造原文没有的信息。
3. 如果工具返回的片段不足以回答，如实说明。
4. 回答要简洁、准确。"""


# 灵感分支专用的系统提示词：同样基于原文，但语气是"创作建议"
INSPIRE_SYSTEM_PROMPT = """你是「小说岛」的创作灵感助手，帮助小说作者拓展后续剧情。

规则：
1. 只能基于提供的「原文片段」做建议，不要编造原文没有的人物或设定。
2. 给出 2-3 个具体的剧情发展方向，每个方向说明"为什么符合现有设定"。
3. 建议要具体、可操作，不要空泛（不要只说"可以增加冲突"）。
4. 如果片段信息不足，说明"目前信息不足以给出好建议，建议先补充XX设定"。"""


class InspireNode:
    """灵感节点：创作建议 —— 复用检索结果，用不同 prompt 生成"""

    name = "inspire"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        results = state.get("retrieved_chunks", [])

        if not results:
            return {
                "agent_response": "当前知识库信息不足，无法给出具体灵感建议。建议先补充更多章节内容。",
                "sources": [],
                "current_step": self.name,
            }

        # 复用 build_rag_prompt 拼上下文（同一个函数，不同 system prompt）
        user_prompt = build_rag_prompt(query, results)
        answer = chat(INSPIRE_SYSTEM_PROMPT, user_prompt)

        sources = [
            {"chunk_id": r["chunk"].id, "score": round(r["score"], 4)}
            for r in results
        ]

        return {
            "agent_response": answer,
            "sources": sources,
            "current_step": self.name,
        }


# 质检（LLM-as-Judge）的系统提示词：让 LLM 扮演"编辑"，检查回答是否有原文依据
CRITIC_SYSTEM_PROMPT = """你是「小说岛」的质检编辑，负责检查AI的回答是否严格基于原文。

规则：
1. 检查回答中的每一条事实，是否都能在「原文片段」中找到依据。
2. 如果回答包含原文中没有的信息（编造、过度推断、不确定话术如"可能/也许/或许"），判为不通过。
3. 回答简洁、不冗长、不重复也算合格。
4. 只输出 JSON，格式：{"pass": true或false, "issues": ["具体问题1", "问题2"]}
   - pass: true 表示通过，issues 为空数组
   - pass: false 表示不通过，issues 列出所有问题"""


def _build_critic_prompt(query: str, response: str, results: list) -> str:
    """拼质检用的 prompt：原文片段 + AI回答 + 用户问题"""
    context_parts = []
    for r in results:
        c = r["chunk"]
        context_parts.append(f"【片段#{c.id + 1}】\n{c.text}")
    context = "\n\n---\n\n".join(context_parts)

    return f"""以下是检索到的原文片段：

{context}

---

用户问题：{query}

---

AI 的回答：
{response}

---

请检查AI回答是否严格基于原文。只输出JSON。"""


class HallucinationCriticNode:
    """防幻觉质检节点 — 里程碑4：LLM-as-Judge

    agent 生成回答后，这里让 LLM 当"编辑"，检查回答的每一条事实
    是否都能在原文片段里找到依据。返回 pass / issues。

    如果 pass: false（回答有幻觉），图里的条件边会把它打回 agent 重新生成。
    """

    name = "hallucination_critic"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        response = state.get("agent_response", "")
        results = state.get("retrieved_chunks", [])

        # 没检索到内容时的兜底回答，直接判通过（不是幻觉，是信息不足）
        if not results:
            return {
                "critic_pass": True,
                "critic_issues": [],
                "current_step": self.name,
            }

        # LLM-as-Judge：让 LLM 检查回答是否有原文依据
        judge_prompt = _build_critic_prompt(query, response, results)
        judge_output = chat(CRITIC_SYSTEM_PROMPT, judge_prompt, temperature=0.0, max_tokens=512)

        # 解析 LLM 输出的 JSON
        import json
        try:
            verdict = json.loads(judge_output)
            passed = bool(verdict.get("pass", False))
            issues = verdict.get("issues", [])
        except (json.JSONDecodeError, AttributeError):
            # 解析失败 = 质检不可靠，宁可放行（避免因质检故障卡死流程）
            passed, issues = True, ["质检输出解析失败，已放行"]

        return {
            "critic_pass": passed,
            "critic_issues": issues,
            "current_step": self.name,
        }


class AgentNode:
    """Agent节点 — 里程碑3+4：让LLM自己决定是否调用工具

    里程碑3：LLM 看到工具清单 → 自己决定调不调 → 代码执行 → 结果回填 → 最终回答。
    里程碑4：被质检打回重试时，递增 retry_count 防止死循环。

    为教学保留预检索结果（state['retrieved_chunks']）作为背景，
    但 LLM 仍会再自主决定一次是否调用 search_kb —— 让你亲眼看到 tool_call。
    """

    name = "agent"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        # 每次被调用都递增重试计数（包含质检打回的重新生成）
        retry = state.get("retry_count", 0) + 1

        # 如果是被质检打回的，带上质检意见重新生成（让它知道自己错在哪）
        critic_feedback = ""
        if retry > 1 and state.get("critic_issues"):
            critic_feedback = (
                "\n\n上一次回答被质检驳回，驳回原因如下，请修正后重新回答：\n"
                + "\n".join(f"- {i}" for i in state["critic_issues"])
            )

        # 里程碑6：读取短期记忆（对话历史），拼进 prompt
        # 如果历史超长，get_context 会自动做摘要压缩
        history = memory.get_context()
        # 里程碑6修复：messages 必须以 system 开头（OpenAI协议要求）
        context_messages = [{'role': 'system', 'content': AGENT_SYSTEM_PROMPT}] + list(history)
        # 把"新问题+质检反馈"作为最后一条 user 消息
        context_messages.append({"role": "user", "content": query + critic_feedback})

        # 调 chat_with_tools：给 LLM 工具清单 + 执行函数映射表
        # 注意：这里传入完整 messages（含历史），而不是单独的 user_prompt
        # 内部完成：LLM决策 → 执行工具 → 结果回填 → 最终回答
        answer = chat_with_tools(
            AGENT_SYSTEM_PROMPT,
            context_messages,  # 里程碑6：传完整消息列表（含对话历史）
            tools=AVAILABLE_TOOLS,
            tool_executors=TOOL_EXECUTORS,
            use_messages=True,  # 标记：第二个参数已是 messages 而非字符串
        )

        # 预检索结果仍作为来源展示（供调试看召回情况）
        results = state.get("retrieved_chunks", [])
        sources = [
            {"chunk_id": r["chunk"].id, "score": round(r["score"], 4)}
            for r in results
        ]

        return {
            "agent_response": answer,
            "sources": sources,
            "retry_count": retry,
            "current_step": self.name,
        }
