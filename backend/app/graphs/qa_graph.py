"""
问答链路状态机 — 里程碑1+2+3+4

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
  事实问答分支升级为 AgentNode —— LLM 自己决定是否调 search_kb 工具

里程碑4结构（防幻觉质检 + 打回重来）：
  事实问答分支加 HallucinationCriticNode，质检不过打回 agent 重新生成（最多2次）
              agent（工具调用生成）
                    ↓
         hallucination_critic（LLM质检）
             ┌────┴────┐
        pass:false   pass:true
             ↓           ↓
          agent(重试)    END

说明：
  - 检索是共享的（两个分支都需要），意图路由决定"怎么回答"
  - 灵感分支不质检（创意不受事实约束，对应PRD"Inspiration走人设质检"）
  - 加分支/回边 = 加节点 + 加边，不动其他节点
"""
from langgraph.graph import StateGraph, END

from ..models.state import NovelIslandState
from ..nodes.qa_nodes import (
    RetrieveNode,
    IntentRouterNode,
    AgentNode,
    MultiHopInspirationNode,
    HallucinationCriticNode,
    LogicCritiqueNode,
    CharacterCriticNode,
    CompanionNode,
    route_by_intent,
)

# 质检最多打回重试次数（防止死循环）
MAX_CRITIC_RETRY = 2


def route_after_critic(state: NovelIslandState) -> str:
    """质检后的条件边：通过 → 结束，不通过且未超次数 → 打回 agent

    这是里程碑4的循环回边 —— 同一个 agent 节点可能被多次访问。
    """
    passed = state.get("critic_pass", False)
    retry = state.get("retry_count", 0)

    if passed or retry >= MAX_CRITIC_RETRY:
        # 质检通过，或重试次数用尽（防御死循环）→ 放行
        return "end"
    # 质检不通过，还有重试次数 → 打回 agent 重新生成
    return "agent"


def build_qa_graph():
    """构建并返回编译后的问答图"""
    graph = StateGraph(NovelIslandState)

    # 1. 加节点
    graph.add_node("retrieve", RetrieveNode())
    graph.add_node("intent_router", IntentRouterNode())
    graph.add_node("agent", AgentNode())
    graph.add_node("multi_hop_inspiration", MultiHopInspirationNode())  # Phase 0：多跳RAG灵感
    graph.add_node("hallucination_critic", HallucinationCriticNode())
    graph.add_node("logic_critique", LogicCritiqueNode())
    graph.add_node("character_critic", CharacterCriticNode())
    graph.add_node("companion", CompanionNode())  # Phase 0：情感陪伴

    # 2. 连边
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "intent_router")

    # 3. 条件边：intent_router 之后，根据路由函数返回值分流
    #    route_by_intent 返回 "fact_qa" → 走 agent（里程碑3升级为工具调用）
    #    route_by_intent 返回 "inspiration" → 走 multi_hop_inspiration（Phase 0 多跳RAG）
    #    route_by_intent 返回 "logic_critique"/"character_critic" → 走对应检查节点（里程碑13）
    #    route_by_intent 返回 "companion" → 走陪伴节点（Phase 0）
    graph.add_conditional_edges(
        "intent_router",
        route_by_intent,
        {
            "fact_qa": "agent",
            "inspiration": "multi_hop_inspiration",
            "logic_critique": "logic_critique",
            "character_critic": "character_critic",
            "companion": "companion",
        },
    )

    # 4. 事实问答分支：agent 生成 → 质检
    graph.add_edge("agent", "hallucination_critic")

    # 5. 质检后的条件边（循环回边）：
    #    pass: true → end
    #    pass: false 且有重试次数 → 打回 agent（循环）
    graph.add_conditional_edges(
        "hallucination_critic",
        route_after_critic,
        {
            "agent": "agent",
            "end": END,
        },
    )

    # 6. 灵感分支直接结束（不质检）
    graph.add_edge("multi_hop_inspiration", END)

    # 7. 里程碑13：逻辑/人设检查分支直接结束（质检类节点，输出即结论）
    graph.add_edge("logic_critique", END)
    graph.add_edge("character_critic", END)

    # 8. Phase 0：陪伴分支直接结束（PRD 后期可加 CharacterCritic 质检，保持人设）
    graph.add_edge("companion", END)

    # 9. 编译
    return graph.compile()


qa_app = build_qa_graph()
