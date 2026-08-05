"""
问答链路节点 — 里程碑1+2：LangGraph状态机

里程碑1：RetrieveNode（检索）+ GenerateNode（事实问答）
里程碑2：IntentRouterNode（意图路由）+ InspireNode（灵感分支）

每个节点：输入整个 State（背包）→ 返回要改写的字段（部分更新）
"""
from typing import Any, Dict

from ..core.retriever import retriever
from ..core.llm_client import chat, RAG_SYSTEM_PROMPT, build_rag_prompt
from ..models.state import NovelIslandState


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
