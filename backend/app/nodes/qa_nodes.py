"""
问答链路节点 — 里程碑1：把顺序执行的问答流程改造成LangGraph状态机

两个节点：
  RetrieveNode  — 从知识库检索 Top-K 相关片段
  GenerateNode  — 基于检索结果调用 LLM 生成回答

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


class GenerateNode:
    """生成节点：拼 prompt → 调 LLM → 把回答和来源写回背包"""

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
