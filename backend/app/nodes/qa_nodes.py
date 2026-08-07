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
from ..core.graph_store import get_graph_for
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

    里程碑13：四大意图 —— fact_qa（事实问答）/ inspiration（灵感）
    / logic_critique（逻辑矛盾检查）/ character_critic（人设一致性检查）。
    判断顺序：character_critic → logic_critique → inspiration → fact_qa
    （越"具体"的意图越先判，避免关键词重叠误判）。
    """

    name = "intent_router"

    # 灵感类关键词：命中则走灵感分支，否则走事实问答
    INSPIRATION_KEYWORDS = [
        "卡文", "灵感", "剧情", "怎么发展", "后面", "写不下去",
        "设计", "方案", "反转", "建议", "下一个情节", "后续",
    ]

    # 逻辑矛盾检查关键词（里程碑13）：作者问"这段有没有矛盾/不合理"
    LOGIC_CRITIQUE_KEYWORDS = [
        "矛盾", "不合理", "逻辑", "对不上", "冲突", "bug", "硬伤",
        "漏洞", "时间线", "因果", "前后", "连不上",
    ]

    # 人设一致性检查关键词（里程碑13）：作者问"XX人设崩了吗/是不是OOC"
    CHARACTER_CRITIC_KEYWORDS = [
        "人设", "崩", "ooc", "OOC", "不符合", "性格变", "像不像",
        "身份", "设定一致", "跑偏", "变了",
    ]

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")

        # 规则判断（按优先级）：人设 → 逻辑 → 灵感 → 事实
        if any(kw in query for kw in self.CHARACTER_CRITIC_KEYWORDS):
            intent = "character_critic"
        elif any(kw in query for kw in self.LOGIC_CRITIQUE_KEYWORDS):
            intent = "logic_critique"
        elif any(kw in query for kw in self.INSPIRATION_KEYWORDS):
            intent = "inspiration"
        else:
            intent = "fact_qa"

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
        answer = chat(RAG_SYSTEM_PROMPT, user_prompt, task="qa")

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
        answer = chat(INSPIRE_SYSTEM_PROMPT, user_prompt, task="inspire")

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
            task='qa',
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


# ===== 里程碑13：四大功能节点补全 =====

# 逻辑矛盾检查的系统提示词：检查情节的时间线/因果/设定矛盾
LOGIC_CRITIQUE_SYSTEM_PROMPT = """你是「小说岛」的逻辑检查编辑，帮助作者检查小说情节中的逻辑矛盾。

规则：
1. 基于提供的「原文片段」检查逻辑问题，包括：时间线矛盾、事件因果矛盾、设定矛盾、人物行为不合逻辑。
2. 只报告能明确指出的问题，拿不准的不报（避免误报）。
3. 每个问题给出：问题描述 + 依据（片段里的具体说法）。
4. 如果片段信息不足，明确说明"目前信息不足，无法判断"。
5. 输出格式：先给结论，再逐条列出问题（有则列，无则说"未发现明显逻辑矛盾"）。"""


def _build_logic_critique_prompt(query: str, results: list) -> str:
    """拼逻辑检查 prompt：原文片段 + 用户问题"""
    context_parts = []
    for r in results:
        c = r["chunk"]
        context_parts.append(f"【片段#{c.id + 1}】\n{c.text}")
    context = "\n\n---\n\n".join(context_parts)
    return f"""以下是检索到的原文片段：

{context}

---

作者的问题：{query}

---

请检查这些片段中是否存在逻辑矛盾，并回答作者的问题。"""


class LogicCritiqueNode:
    """逻辑矛盾检查节点 — 里程碑13

    作者问"这段情节有没有矛盾/不合理"时，走这个分支。
    复用检索结果（state['retrieved_chunks']），LLM 检查时间线/因果/设定矛盾。
    """

    name = "logic_critique"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        results = state.get("retrieved_chunks", [])

        if not results:
            return {
                "agent_response": "当前知识库信息不足，无法检查逻辑矛盾。建议先补充更多章节内容。",
                "sources": [],
                "current_step": self.name,
            }

        # 拼 prompt → 调 LLM（复用 chat，task=logic 走复杂级路由）
        user_prompt = _build_logic_critique_prompt(query, results)
        answer = chat(LOGIC_CRITIQUE_SYSTEM_PROMPT, user_prompt, task="logic")

        sources = [
            {"chunk_id": r["chunk"].id, "score": round(r["score"], 4)}
            for r in results
        ]

        return {
            "agent_response": answer,
            "sources": sources,
            "current_step": self.name,
        }


# 人设一致性检查的系统提示词：结合图谱persona + 原文片段
CHARACTER_CRITIC_SYSTEM_PROMPT = """你是「小说岛」的人设一致性检查编辑，帮助作者检查人物设定是否崩塌（OOC）。

规则：
1. 基于「设定信息」（知识图谱中的人物属性）和「原文片段」检查。
2. 重点检查：人物性格、身份、职业、外貌、家庭、宠物、行为模式是否前后一致。
3. 只报告能明确指出的不一致，拿不准的不报（避免误报）。
4. 每个问题给出：不一致点 + 设定信息 vs 原文表现。
5. 输出格式：先给结论，再逐条列出问题（有则列，无则说"未发现人设崩塌"）。"""


def _build_character_critic_prompt(query: str, results: list, persona: dict, character: str) -> str:
    """拼人设检查 prompt：图谱persona + 原文片段 + 用户问题"""
    persona_lines = "\n".join(f"- {k}: {v}" for k, v in persona.items()) if persona else "（图谱中暂无该人物设定）"

    context_parts = []
    for r in results:
        c = r["chunk"]
        context_parts.append(f"【片段#{c.id + 1}】\n{c.text}")
    context = "\n\n---\n\n".join(context_parts) if context_parts else "（未检索到相关片段）"

    return f"""以下是知识图谱中「{character}」的设定信息：

{persona_lines}

---

以下是检索到的原文片段：

{context}

---

作者的问题：{query}

---

请检查「{character}」在原文中的表现是否与设定一致，并回答作者的问题。"""


class CharacterCriticNode:
    """人设一致性检查节点 — 里程碑13

    作者问"XX人设崩了吗/是不是OOC"时，走这个分支。
    查图谱 persona（novel_id 穿透）+ 检索原文片段 → LLM 对照检查。
    """

    name = "character_critic"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        results = state.get("retrieved_chunks", [])
        novel_id = state.get("novel_id")

        # 从问题里提取人物名（图谱里存在的实体）
        g = get_graph_for(novel_id)
        entities = g.all_entities()
        character = next((e for e in entities if e and e in query), None)
        persona = g.get_entity(character).get("persona", {}) if character else {}

        if not results:
            return {
                "agent_response": "当前知识库信息不足，无法检查人设一致性。建议先补充更多章节内容。",
                "sources": [],
                "current_step": self.name,
            }

        # 拼 prompt → 调 LLM（task=complex 走复杂级路由）
        user_prompt = _build_character_critic_prompt(query, results, persona, character or "该角色")
        answer = chat(CHARACTER_CRITIC_SYSTEM_PROMPT, user_prompt, task="creative")

        sources = [
            {"chunk_id": r["chunk"].id, "score": round(r["score"], 4)}
            for r in results
        ]

        return {
            "agent_response": answer,
            "sources": sources,
            "current_step": self.name,
        }
