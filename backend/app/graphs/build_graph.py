"""
建库流程状态机 — 里程碑5

结构（并行分支 + 汇合）：
  entry → build（清洗分块）
              ↓
      ┌───────┴────────┐
      ↓       ↓        ↓
extract_entities  extract_events  extract_relations  ← 并行分支
      ↓       ↓        ↓
      └───────┬────────┘
              ↓
         build_output（汇总）        ← 汇合点
              ↓
             END

里程碑15：新增 extract_chapter_summaries 第4路并行（情节大事年表）。

说明：
  - build 之后分叉到多个抽取节点（并行语义）
  - build_output 是汇合点，所有分支都完成后才执行
  - 各抽取节点互相独立，未来可升级为真正并发
"""
from langgraph.graph import StateGraph, END

from ..models.state import NovelIslandState
from ..nodes.build_nodes import (
    BuildNode,
    EntityExtractNode,
    EventExtractNode,
    RelationExtractNode,
    ChapterSummaryExtractNode,
    BuildOutputNode,
)
from ..nodes.graph_consistency import GraphConsistencyNode  # 路线图P1-6


def build_build_graph():
    """构建并返回编译后的建库图"""
    graph = StateGraph(NovelIslandState)

    # 1. 加节点
    graph.add_node("build", BuildNode())
    graph.add_node("extract_entities", EntityExtractNode())
    graph.add_node("extract_events", EventExtractNode())
    graph.add_node("extract_relations", RelationExtractNode())
    graph.add_node("extract_chapter_summaries", ChapterSummaryExtractNode())
    graph.add_node("graph_consistency", GraphConsistencyNode())  # P1-6：图谱一致性校验
    graph.add_node("build_output", BuildOutputNode())

    # 2. 连边：入口 → 清洗分块
    graph.set_entry_point("build")

    # 3. 并行分叉：build 后同时去四个抽取节点
    graph.add_edge("build", "extract_entities")
    graph.add_edge("build", "extract_events")
    graph.add_edge("build", "extract_relations")
    graph.add_edge("build", "extract_chapter_summaries")

    # 4. 汇合：四个抽取分支都完成后，先做图谱一致性校验（对照旧图谱），再入库
    graph.add_edge("extract_entities", "graph_consistency")
    graph.add_edge("extract_events", "graph_consistency")
    graph.add_edge("extract_relations", "graph_consistency")
    graph.add_edge("extract_chapter_summaries", "graph_consistency")
    graph.add_edge("graph_consistency", "build_output")

    # 5. 结束
    graph.add_edge("build_output", END)

    # 6. 编译
    return graph.compile()


build_app = build_build_graph()
