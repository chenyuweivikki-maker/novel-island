"""
问答链路状态机 — 里程碑1

结构：
  entry → RetrieveNode → GenerateNode → END

说明：
  - 整个流程只有 检索 → 生成 两步，但已经具备状态机的形态
  - 后续里程碑在 RetrieveNode 和 GenerateNode 之间插入 质检节点 时，
    只需要加一个节点 + 加一条边，不需要改其他任何代码
"""
from langgraph.graph import StateGraph, END

from ..models.state import NovelIslandState
from ..nodes.qa_nodes import RetrieveNode, GenerateNode


def build_qa_graph():
    """构建并返回编译后的问答图"""
    graph = StateGraph(NovelIslandState)

    # 1. 加节点
    graph.add_node("retrieve", RetrieveNode())
    graph.add_node("generate", GenerateNode())

    # 2. 连边：入口 → 检索 → 生成 → 结束
    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "generate")
    graph.add_edge("generate", END)

    # 3. 编译
    return graph.compile()


qa_app = build_qa_graph()
