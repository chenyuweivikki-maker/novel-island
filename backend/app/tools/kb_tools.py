"""
知识库工具集 — 里程碑3：tool calling

这里定义 Agent 可以调用的工具。
每个工具 = 一个 JSON Schema 说明书（给 LLM 看）+ 一个执行函数（给代码跑）。

  search_kb  — 从知识库检索相关原文片段
"""
from typing import List, Dict, Any

from ..core.retriever import retriever


# ===== 工具定义（JSON Schema，发给 LLM 的"说明书"）=====
SEARCH_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": "搜索小说知识库，找到与问题最相关的原文片段。"
                       "当用户询问小说中的人物、情节、设定时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要检索的内容，通常是用户问题本身或关键词",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回的片段数量，默认 5",
                },
            },
            "required": ["query"],
        },
    },
}

# 所有可用工具的清单（后续加工具就往这个列表加）
AVAILABLE_TOOLS = [SEARCH_KB_TOOL]


# ===== 工具执行函数（LLM 决定调用后，代码跑这个）=====
def execute_search_kb(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """执行 search_kb：检索知识库，返回格式化后的片段列表

    注意：这个函数是"给代码跑"的，不是给 LLM 的。
    它把检索结果转成可读文本，方便塞回 LLM 上下文。
    """
    results = retriever.search(query, top_k)

    # 把结果整理成 LLM 能看懂的格式
    return [
        {
            "chunk_id": r["chunk"].id,
            "score": round(r["score"], 4),
            "text": r["chunk"].text,
        }
        for r in results
    ]


# 工具名 → 执行函数的映射表（Agent 根据 LLM 的 tool_call 名字查表执行）
TOOL_EXECUTORS = {
    "search_kb": execute_search_kb,
}
