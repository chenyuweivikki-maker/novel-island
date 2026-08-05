"""
问答链路状态机 — 里程碑1+2+3

里程碑1结构：
  entry → RetrieveNode → GenerateNode → END

里程碑2结构（意图路由）：
  entry → RetrieveNode → IntentRouterNode
                              │
                    ┌─────────┴─────────┐
                    ↓                   ↓
              GenerateNode         InspireNode
              (事实问答)           (灵感建议)
                    ↓                   ↓
                   END                 END

里程碑3结构（tool calling）：
  事实问答分支的 GenerateNode 升级为 AgentNode —— LLM 自己决定是否调 search_kb 工具
  entry → RetrieveNode → IntentRouterNode
                              │
                    ┌─────────┴─────────┐
                    ↓                   ↓
               AgentNode          InspireNode
              (工具调用)           (灵感建议)
                    ↓                   ↓
                   END                 END

说明：
  - 检索是共享的（两个分支都需要），意图路由决定"怎么回答"
  - 加分支 = 加节点 + 加条件边，不动其他节点
"""
from langgraph.graph import StateGraph, END

from ..models.state import NovelIslandState
from ..nodes.qa_nodes import (
    RetrieveNode,
    IntentRouterNode,
    AgentNode,
    InspireNode,
    route_by_intent,
)


def build_qa_graph():
    """构建并返回编译后的问答图"""
    graph = StateGraph(NovelIslandState)

    # 1. 加节点
    graph.add_node("retrieve", RetrieveNode())
    graph.add_node("intent_router", IntentRouterNode())
    graph.add_node("agent", AgentNode())
    graph.add_node("inspire", InspireNode())

    # 2. 连边
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "intent_router")

    # 3. 条件边：intent_router 之后，根据路由函数返回值分流
    #    route_by_intent 返回 "fact_qa" → 走 agent（里程碑3升级为工具调用）
    #    route_by_intent 返回 "inspiration" → 走 inspire
    graph.add_conditional_edges(
        "intent_router",
        route_by_intent,
        {
            "fact_qa": "agent",
            "inspiration": "inspire",
        },
    )

    # 4. 两个分支都汇合到结束
    graph.add_edge("agent", END)
    graph.add_edge("inspire", END)

    # 5. 编译
    return graph.compile()


qa_app = build_qa_graph()
