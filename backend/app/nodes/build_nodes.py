"""
建库流程节点 — 里程碑5：建库状态机化 + 并行抽取

节点：
  BuildNode           — 清洗 + 分块
  EntityExtractNode   — LLM 抽取实体（人物/地点/物品）
  EventExtractNode    — LLM 抽取事件（情节/伏笔）
  BuildOutputNode     — 汇总两个并行分支的结果，构建索引

并行结构：
  build → [extract_entities, extract_events] → output
"""
import json
from typing import Any, Dict, List

from ..core.chunker import clean_text, chunk_text
from ..core.retriever import retriever
from ..core.llm_client import chat
from ..core.graph_store import get_graph_for
from ..models.state import NovelIslandState


# ===== 抽取用的系统提示词 =====

ENTITY_EXTRACT_PROMPT = """你是「小说岛」的文本分析专家。请阅读下面的小说片段，抽取所有人物实体及属性。

规则：
1. 只抽取原文明确出现的人物，不要编造。
2. 每个人物记录：名字、身份、以及以下属性（原文有才写，没有留空）：
   - 职业（做什么工作）
   - 性格（性格特质）
   - 外貌（身高/长相/穿着）
   - 家庭（家人/家庭背景）
   - 宠物（养的动物及名字）
   - 物品（随身/拥有的重要物品）
   - 事件（该人物经历的关键事件）
3. 输出严格的 JSON 数组，格式：
[{"name": "角色名", "identity": "身份", "attributes": {"职业": "...", "性格": "...", "外貌": "...", "家庭": "...", "宠物": "...", "物品": "...", "事件": "..."}}]
4. attributes 里只放原文明确提到的，没有的键可以省略。
5. 只输出 JSON，不要其他文字。"""

RELATION_EXTRACT_PROMPT = """你是「小说岛」的文本分析专家。请阅读下面的小说片段，抽取人物之间的关系。

规则：
1. 只抽取原文明确体现的人物关系，不要编造。
2. 关系类型用简单词：朋友、恋人、房东租客、同事、家人、前任、邻居等。
3. 输出严格的 JSON 数组，格式：
[{"source": "人物A", "relation": "关系", "target": "人物B", "weight": 1到10的整数}]
4. 只输出 JSON，不要其他文字。"""


EVENT_EXTRACT_PROMPT = """你是「小说岛」的文本分析专家。请阅读下面的小说片段，抽取关键事件。

规则：
1. 只抽取原文明确提到的关键情节/事件，不要编造。
2. 每个事件记录：简述、涉及的章节或阶段（如原文有）、后续影响（如原文可推断）。
3. 输出严格的 JSON 数组，格式：
[{"summary": "事件简述", "stage": "事件发生的阶段", "impact": "后续影响"}]
4. 只输出 JSON，不要其他文字。"""


def _extract_json_array(llm_output: str) -> List[dict]:
    """从 LLM 输出中提取 JSON 数组（容错：去掉可能的 markdown 代码块围栏）"""
    text = llm_output.strip()
    # 去掉可能的 ```json ... ``` 围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, AttributeError):
        return []


class BuildNode:
    """建库节点：清洗 + 分块，写入 processed_chunks"""

    name = "build"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        text = state.get("raw_input_files", [""])
        # 接口传入的是文本字符串（兼容：raw_input_files 传 [text] 或字符串）
        if isinstance(text, list):
            text = text[0] if text else ""
        text = text if isinstance(text, str) else ""

        cleaned = clean_text(text)
        chunks = chunk_text(cleaned)
        # 转成可序列化的 dict（状态机里存 plain dict）
        chunk_dicts = [
            {"id": c.id, "text": c.text, "char_count": c.char_count}
            for c in chunks
        ]

        return {
            "processed_chunks": chunk_dicts,
            "current_step": self.name,
        }


class EntityExtractNode:
    """实体抽取节点：LLM 从分块中抽人物实体（并行分支1）"""

    name = "extract_entities"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunks = state.get("processed_chunks", [])

        # 把分块拼成一段文本喂给 LLM（限制长度，避免超token）
        text_pool = "\n".join(c["text"] for c in chunks)[:6000]

        llm_output = chat(ENTITY_EXTRACT_PROMPT, text_pool, temperature=0.0, max_tokens=1024)
        entities = _extract_json_array(llm_output)

        # 注意：并行分支只写自己独占的字段，不写 current_step（会与另一分支冲突）
        return {
            "extracted_entities": entities,
        }


class EventExtractNode:
    """事件抽取节点：LLM 从分块中抽关键事件（并行分支2）"""

    name = "extract_events"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunks = state.get("processed_chunks", [])

        text_pool = "\n".join(c["text"] for c in chunks)[:6000]

        llm_output = chat(EVENT_EXTRACT_PROMPT, text_pool, temperature=0.0, max_tokens=1024)
        events = _extract_json_array(llm_output)

        # 注意：并行分支只写自己独占的字段，不写 current_step（会与另一分支冲突）
        return {
            "extracted_events": events,
        }


class RelationExtractNode:
    """关系抽取节点：LLM 从分块中抽人物关系（并行分支3）"""

    name = "extract_relations"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunks = state.get("processed_chunks", [])

        text_pool = "".join(c["text"] for c in chunks)[:6000]
        llm_output = chat(RELATION_EXTRACT_PROMPT, text_pool, temperature=0.0, max_tokens=1024)
        relations = _extract_json_array(llm_output)

        # 并行分支：只写自己独占的字段
        return {
            "extracted_relationships": relations,
        }


class BuildOutputNode:
    """汇总节点：构建检索索引，汇总实体+事件结果（汇合点）"""

    name = "build_output"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunk_dicts = state.get("processed_chunks", [])

        # 重建 Chunk 对象并建 TF-IDF 索引
        from ..core.chunker import Chunk
        chunks = [Chunk(id=c["id"], text=c["text"], char_count=c["char_count"]) for c in chunk_dicts]
        retriever.build_index(chunks)

        entities = state.get("extracted_entities", [])
        events = state.get("extracted_events", [])
        relations = state.get("extracted_relationships", [])

        # 里程碑11：按项目写图谱（novel_id 在 state 里，None 时回退默认图）
        g = get_graph_for(state.get("novel_id"))

        # 里程碑8：写入知识图谱（实体→节点，关系→边）
        for e in entities:
            name = e.get("name", "")
            if name:
                g.add_entity(name, {
                    "identity": e.get("identity", ""),
                    "traits": e.get("traits", []),
                })
                # 里程碑9：挂人设属性（职业/性格/外貌/家庭/宠物等）
                persona = e.get("attributes", {})
                if persona:
                    g.add_persona(name, persona)
        for r in relations:
            source = r.get("source", "")
            target = r.get("target", "")
            if source and target:
                g.add_relation(source, r.get("relation", ""), target, r.get("weight", 1))

        # 里程碑8：持久化图谱到文件
        g.save()

        return {
            "final_output": {
                "chunks": len(chunks),
                "total_chars": sum(c.char_count for c in chunks),
                "entities": entities,
                "events": events,
            },
            "current_step": self.name,
        }
