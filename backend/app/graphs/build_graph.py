"""
建库流程状态机 — 里程碑5

结构（并行分支 + 汇合）：
  entry → build（清洗分块）
              ↓
      ┌───────┴───────┐
      ↓               ↓
extract_entities  extract_events   ← 并行分支
      ↓               ↓
      └───────┬───────┘
              ↓
         build_output（汇总）        ← 汇合点
              ↓
             END

说明：
  - build 之后分叉到两个抽取节点（并行语义）
  - build_output 是汇合点，两个分支都完成后才执行
  - 实体抽取和事件抽取互相独立，未来可升级为真正并发
"""
from langgraph.graph import StateGraph, END

from ..models.state import NovelIslandState
from ..nodes.build_nodes import (
    BuildNode,
    EntityExtractNode,
    EventExtractNode,
    RelationExtractNode,
    BuildOutputNode,
)


def build_build_graph():
    """构建并返回编译后的建库图"""
    graph = StateGraph(NovelIslandState)

    # 1. 加节点
    graph.add_node("build", BuildNode())
    graph.add_node("extract_entities", EntityExtractNode())
    graph.add_node("extract_events", EventExtractNode())
    graph.add_node("extract_relations", RelationExtractNode())
    graph.add_node("build_output", BuildOutputNode())

    # 2. 连边：入口 → 清洗分块
    graph.set_entry_point("build")

    # 3. 并行分叉：build 后同时去两个抽取节点
    graph.add_edge("build", "extract_entities")
    graph.add_edge("build", "extract_events")
    graph.add_edge("build", "extract_relations")

    # 4. 汇合：三个抽取分支都完成后，才到 build_output
    graph.add_edge("extract_entities", "build_output")
    graph.add_edge("extract_events", "build_output")
    graph.add_edge("extract_relations", "build_output")

    # 5. 结束
    graph.add_edge("build_output", END)

    # 6. 编译
    return graph.compile()


build_app = build_build_graph()
