"""
NovelIslandState - 小说岛全局状态定义
LangGraph状态机的"背包"，所有Node读写同一个State
"""
from typing import TypedDict, List, Optional, Literal


class NovelIslandState(TypedDict):
    # 1. 输入与上下文
    user_id: str
    book_id: str
    mode: Literal["init", "update"]  # 初始化 或 增量更新

    # 2. 原始文本数据
    raw_input_files: List[str]           # 初次：上传的文件列表
    new_chapter_content: Optional[str]    # 增量：新章节文本

    # 3. 中间处理结果
    processed_chunks: List[dict]          # 预处理后的文本块
    extracted_entities: List[dict]        # 抽取的实体（人物、地点、物品）
    extracted_relationships: List[dict]   # 抽取的实体间关系
    extracted_events: List[dict]          # 抽取的事件/情节

    # 4. 结构化图谱数据（最终产物）
    character_graph: dict                 # 人物关系图数据
    detailed_outline: dict                # 详细大纲数据
    story_timeline: dict                  # 故事脉络图数据

    # 5. 问答交互相关
    user_query: str
    top_k: int = 5                        # 检索返回数量
    current_intent: str                   # fact_qa | logic_critique | inspiration | companion
    retrieved_chunks: List[dict]          # 检索结果
    reranked_chunks: List[dict]           # 重排序后结果
    agent_response: str
    sources: List[dict]                   # 检索来源（chunk_id + score）

    # 6. 控制流与结果
    current_step: str
    error_message: Optional[str]
    final_output: Optional[str]
