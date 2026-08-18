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
    novel_id: Optional[int]  # 里程碑11：项目隔离标识，未声明会被LangGraph丢弃（并行节点汇合时尤其明显）
    chapter_id: Optional[int]  # 里程碑15：章节标记（save_chapter 穿透，用于年表按章更新）

    # 2. 原始文本数据
    raw_input_files: List[str]           # 初次：上传的文件列表
    new_chapter_content: Optional[str]    # 增量：新章节文本

    # 3. 中间处理结果
    processed_chunks: List[dict]          # 预处理后的文本块
    extracted_entities: List[dict]        # 抽取的实体（人物、地点、物品）
    extracted_relationships: List[dict]   # 抽取的实体间关系
    extracted_events: List[dict]          # 抽取的事件/情节（细粒度，伏笔追踪）
    chapter_summaries: List[dict]         # 里程碑15：整章情节摘要（大事年表）

    # 4. 结构化图谱数据（最终产物）
    character_graph: dict                 # 人物关系图数据
    detailed_outline: dict                # 详细大纲数据
    story_timeline: dict                  # 故事脉络图数据
    consistency_report: Optional[dict]    # 路线图P1-6：图谱一致性校验报告

    # 5. 问答交互相关
    user_query: str
    top_k: int = 5                        # 检索返回数量
    session_id: str = "default"           # 对话会话标识（记忆按会话分组持久化）
    current_intent: str                   # fact_qa | logic_critique | inspiration | companion
    retrieved_chunks: List[dict]          # 检索结果
    reranked_chunks: List[dict]           # 重排序后结果
    agent_response: str
    sources: List[dict]                   # 检索来源（chunk_id + score）
    tool_log: Optional[list]              # 工具调用日志（可见性：本次问答调了哪些工具）

    # 6. 质检相关（里程碑4）
    critic_pass: Optional[bool]           # 质检是否通过
    critic_issues: List[str]              # 质检发现的问题
    retry_count: int = 0                  # 打回重试次数

    # 7. 控制流与结果
    current_step: str
    error_message: Optional[str]
    final_output: Optional[str]
