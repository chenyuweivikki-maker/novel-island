"""
节点定义 - LangGraph状态机的各个处理节点
每个节点接收State，处理逻辑，返回更新后的State
"""
from typing import Dict, Any
from app.models.state import NovelIslandState


class BaseNode:
    """节点基类"""
    name: str = "base"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        raise NotImplementedError


class InputParserNode(BaseNode):
    """入口节点：接收输入，清洗分块，区分init/update模式"""
    name = "input_parser"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        mode = state.get("mode", "init")
        if mode == "init":
            # 初次建库：解析所有上传文件
            raw_files = state.get("raw_input_files", [])
            chunks = self._parse_and_chunk(raw_files)
        else:
            # 增量更新：只处理新章节
            new_content = state.get("new_chapter_content", "")
            chunks = self._parse_and_chunk([new_content])

        return {"processed_chunks": chunks, "current_step": self.name}

    def _parse_and_chunk(self, files: list) -> list:
        """文本解析+分块（512 tokens + 15% overlap）"""
        # TODO: 接入LlamaIndex解析器
        # TODO: 实现固定分块策略
        return []


class EntityExtractionNode(BaseNode):
    """LLM实体抽取：从文本中抽取人物、地点、物品及关系"""
    name = "entity_extraction"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunks = state.get("processed_chunks", [])
        # TODO: 调用LLM，Prompt扮演"小说主编"，输出结构化JSON
        # entities = self._extract_with_llm(chunks)
        return {"extracted_entities": [], "current_step": self.name}


class EventExtractionNode(BaseNode):
    """LLM事件抽取：从文本中抽取关键事件、情节发展"""
    name = "event_extraction"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunks = state.get("processed_chunks", [])
        # TODO: 调用LLM，抽取事件+伏笔+预设
        return {"extracted_events": [], "current_step": self.name}


class GraphBuilderNode(BaseNode):
    """图谱构建：将实体和关系写入Neo4j，增量模式下先查库融合"""
    name = "graph_builder"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        entities = state.get("extracted_entities", [])
        relationships = state.get("extracted_relationships", [])
        # TODO: 写入Neo4j，去重、融合（解决别名问题）
        return {"character_graph": {}, "current_step": self.name}


class OutlineBuilderNode(BaseNode):
    """大纲构建：将事件整理为层级大纲与时间脉络"""
    name = "outline_builder"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        events = state.get("extracted_events", [])
        # TODO: 生成智能大纲（每章摘要+伏笔+预设）
        return {"detailed_outline": {}, "story_timeline": {}, "current_step": self.name}


class IntentRouterNode(BaseNode):
    """意图路由：识别用户问题类型，分流到对应Agent"""
    name = "intent_router"

    INTENT_MAP = {
        "fact_qa": "fact_qa",
        "logic_critique": "logic_critique",
        "inspiration": "inspiration",
        "companion": "companion",
    }

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        query = state.get("user_query", "")
        # TODO: 调用小模型做意图分类
        intent = self._classify_intent(query)
        return {"current_intent": intent, "current_step": self.name}

    def _classify_intent(self, query: str) -> str:
        # TODO: 接入LLM意图分类
        return "fact_qa"


class HallucinationCriticNode(BaseNode):
    """防幻觉质检：事实类答案必须基于原文，不允许臆测"""
    name = "hallucination_critic"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        response = state.get("agent_response", "")
        chunks = state.get("reranked_chunks", [])
        # TODO: 检查response中的事实是否都能在chunks中找到依据
        return {"current_step": self.name}


class CharacterCriticNode(BaseNode):
    """人设质检：创造性内容必须符合已设定的人物性格与世界观"""
    name = "character_critic"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        response = state.get("agent_response", "")
        # TODO: 检查response是否符合人设卡片中的性格设定
        return {"current_step": self.name}
