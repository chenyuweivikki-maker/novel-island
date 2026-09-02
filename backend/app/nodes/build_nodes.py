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
import re
from typing import Any, Dict, List

from ..core.chunker import clean_text, chunk_text
from ..core.retriever import get_retriever_for
from ..core.llm_client import chat
from ..core.graph_store import get_graph_for
from ..models.state import NovelIslandState


# ===== 抽取用的系统提示词 =====

ENTITY_EXTRACT_PROMPT = """你是「小说岛」的文本分析专家。请阅读下面的小说片段，抽取所有人物实体及属性。

规则：
1. 只抽取原文**逐字出现**的人物，不要编造。名字必须能在输入文本里找到字面。
2. 严禁由题材（都市/百合/悬疑等）、角色数量、剧情设定联想补充任何输入里**没有出现**的名字（如配角、情侣、其他角色、书名当人物），一律不要抽。
3. 每个人物记录：名字、身份、以及以下属性（原文有才写，没有留空）：
   - 职业（做什么工作）
   - 年龄（年龄/年龄段）
   - 外貌（身高/长相/穿着）
   - 性格（性格特质）
   - 家庭（家人/家庭背景/家境，如原生家庭、出身）
   - 经历（该人物过往经历/成长经历，尽量完整概括）
   - 创伤（核心创伤/伤痛/阴影）
   - 动机（最重要的动机/目标/渴望）
   - 物品（随身/拥有的重要物品）
   - 宠物（养的动物及名字）
   - 事件（该人物经历的关键事件）
4. 输出严格的 JSON 数组，格式：
[{"name": "角色名", "identity": "身份", "attributes": {"职业": "...", "年龄": "...", "外貌": "...", "性格": "...", "家庭": "...", "经历": "...", "创伤": "...", "动机": "...", "物品": "...", "宠物": "...", "事件": "..."}}]
5. attributes 里只放原文明确提到的，没有的键可以省略。
6. 只输出 JSON，不要其他文字。"""

RELATION_EXTRACT_PROMPT = """你是「小说岛」的文本分析专家。请阅读下面的小说片段，抽取人物之间的关系。

规则：
1. 只抽取原文**明确体现**的人物关系，不要编造。
2. 关系双方的名字必须都**逐字出现**在输入文本里；严禁为输入中未出现/脑补的人物建立关系，严禁由题材联想出情侣/家人等关系。
3. 关系类型用简单词：朋友、恋人、房东租客、同事、家人、前任、邻居等。
4. 输出严格的 JSON 数组，格式：
[{"source": "人物A", "relation": "关系", "target": "人物B", "weight": 1到10的整数}]
5. 只输出 JSON，不要其他文字。"""


EVENT_EXTRACT_PROMPT = """你是「小说岛」的文本分析专家。请阅读下面的小说片段，抽取关键事件。

规则：
1. 只抽取原文明确提到的关键情节/事件，不要编造。严禁脑补：不虚构原文没有的人物、情节、结局或冲突，不推测作者还没说的事。
2. 每个事件记录：简述、涉及的章节或阶段（如原文有）、后续影响（如原文可推断）。
3. 输出严格的 JSON 数组，格式：
[{"summary": "事件简述", "stage": "事件发生的阶段", "impact": "后续影响"}]
4. 只输出 JSON，不要其他文字。"""


# 里程碑18：章纲 prompt —— 结构化 JSON（P2-3 伏笔管理系统：summary + foreshadowing + setup）
CHAPTER_OUTLINE_PROMPT = """你是「小说岛」的编辑，负责给一章小说写结构化"章纲"。

输出严格 JSON，格式：
{
  "summary": "这一章发生了什么：主要事件、人物行动、转折点、情感变化（只写正文里真实出现的）",
  "foreshadowing": ["本章埋下的伏笔描述（正文里真实出现的线索；确实没有则为空数组）"],
  "setup": "本章为后续情节做的铺垫/预设（正文里真实写到的；没有则为空字符串）"
}

规则（必须严格遵守，绝不能编造）：
1. **只基于正文逐字已有的内容**总结，不推测、不评论、不补全、不剧透后续。
2. 正文里**没有出现**的人物、物品、情节、事件、台词、转折，一律**不得写进 summary/foreshadowing/setup**——绝不允许根据"望城/主角"这类零星词脑补出完整剧情（如神秘老者、铜钱、伏笔等）。
3. 伏笔必须能在正文里找到对应写法；正文没埋伏笔就返回空数组 []。
4. 若正文内容很少、不足以概括出一章，就只概括正文实有的部分，不要为了凑字数编造。
5. 只输出 JSON，不要任何其他文字。"""


def parse_outline_json(llm_output: str) -> tuple[str, list, str]:
    """解析章纲 JSON → (summary, foreshadowing列表, setup)。容错：解析失败回退纯文本"""
    import json as _json
    text = (llm_output or "").strip()
    # 剥离可能的 ```json 围栏
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            data = _json.loads(text[start:end + 1])
            summary = str(data.get("summary", "")).strip()
            fh = data.get("foreshadowing", [])
            if isinstance(fh, str):
                fh = [fh]
            fh = [str(x).strip() for x in fh if str(x).strip()]
            setup = str(data.get("setup", "")).strip()
            return summary or text, fh, setup
    except Exception:
        pass
    # 回退：整段文本当 summary
    return text, [], ""

# 里程碑15：整章情节摘要 prompt —— 情节大事年表的抽取源
CHAPTER_SUMMARY_EXTRACT_PROMPT = """你是「小说岛」的编辑，负责把小说片段浓缩成"情节大事年表"条目。

规则：
1. 把片段按时间顺序拆成 1-5 个情节单元（一个情节单元 = 一个完整的推进事件，如"初次相遇""矛盾爆发"）。
2. 每个情节单元用一句话概括（20-60字），按原文先后顺序排列。
3. 只概括原文明确发生的事，不要编造、不要推测、不要评论。严禁脑补：不虚构原文没有的人物、情节、结局或冲突。
4. 输出严格的 JSON 数组，格式：
[{"summary": "情节单元的一句话概括", "order": 0}, {"summary": "...", "order": 1}]
5. order 从 0 开始递增，必须与数组顺序一致。
6. 只输出 JSON，不要其他文字。"""


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


def _name_in_text(name: Any, text: str) -> bool:
    """硬校验：抽取出的实体名/角色名必须**逐字出现**在入库原文里，否则视为 LLM 幻觉，丢弃。

    这是防"脑补角色"的机制保障——LLM 即使违反 prompt 编造名字（如从"都市百合"联想出
    不存在的配角），只要它在原文里找不到字面，就进不了知识图谱。
    """
    if not name or not isinstance(name, str):
        return False
    n = name.replace(" ", "").strip()
    t = (text or "").replace(" ", "")
    return bool(n) and n in t


# 不该当"人物实体/图谱节点"的通用词（LLM 常把书名场景词误抽成实体）
_ENTITY_STOPWORDS = {"书", "新书", "小说", "作者", "主角", "女主", "男主", "配角",
                     "剧情", "题材", "灵感", "写作", "章节", "故事", "大纲", "人设",
                     "作品", "本文", "本文书", "一个", "这个", "那个"}


def _is_entity_name(name: Any, text: str) -> bool:
    """实体名合法性：必须逐字出现在原文 + 且不是"书/主角/剧情"这类通用词"""
    n = (name or "").replace(" ", "").strip()
    if not n or n in _ENTITY_STOPWORDS:
        return False
    return _name_in_text(n, text)


# ===== 属性(personal)原文证据校验：防 LLM 给真实角色脑补"原文没提过"的人设 =====
def _attr_has_evidence(value: Any, text: str) -> bool:
    """判断某属性值是否有原文线索：值本身是原文子串，或其 2+ 字中文词能命中原文。

    只留"原文明确提到/能对上"的属性，避免 LLM 从几个词发散出整套虚假人设。
    宁缺毋滥：校验不过就丢弃（作者随时可自己在人设卡里补充）。
    """
    v = (str(value or "") or "").strip()
    if not v:
        return False
    t = (text or "").replace(" ", "")
    if v.replace(" ", "") in t:
        return True
    # 保守取 3+ 字连续中文词做子串命中（2 字词太泛，如"独立/温柔"可能误判为有据）
    for seg in re.findall(r"[\u4e00-\u9fa5]{3,}", v):
        if seg in t:
            return True
    return False


def _sanitize_attrs(attrs: Any, text: str) -> dict:
    """逐键过滤实体的 attributes，只保留有原文证据的属性"""
    if not isinstance(attrs, dict):
        return {}
    return {k: v for k, v in attrs.items() if _attr_has_evidence(v, text)}


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
        # 硬过滤 1：名字必须在原文逐字出现 + 非通用词，杜绝 LLM 从题材/语境脑补不存在的角色
        entities = [e for e in entities if isinstance(e, dict) and _is_entity_name(e.get("name", ""), text_pool)]
        # 硬过滤 2：属性(personal)只保留原文有线索的，防 LLM 给真实角色脑补"原文没提过"的人设
        for e in entities:
            if isinstance(e.get("attributes"), dict):
                e["attributes"] = _sanitize_attrs(e.get("attributes"), text_pool)

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
        # 硬过滤：关系双方名字都必须逐字出现在原文 + 非通用词，杜绝为脑补角色建边
        relations = [
            r for r in relations
            if isinstance(r, dict)
            and _is_entity_name(r.get("source", ""), text_pool)
            and _is_entity_name(r.get("target", ""), text_pool)
        ]

        # 并行分支：只写自己独占的字段
        return {
            "extracted_relationships": relations,
        }


class ChapterSummaryExtractNode:
    """整章情节摘要抽取节点 — 里程碑15：情节大事年表（并行分支4）

    把输入文本浓缩成 1-5 条按顺序的情节摘要，供 BuildOutputNode 写入年表。
    与 EventExtractNode（细粒度伏笔）两档并存：
      - events  → 细粒度事件（伏笔追踪）
      - summaries → 整章摘要（大事年表）
    """

    name = "extract_chapter_summaries"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunks = state.get("processed_chunks", [])

        text_pool = "\n".join(c["text"] for c in chunks)[:6000]
        llm_output = chat(CHAPTER_SUMMARY_EXTRACT_PROMPT, text_pool, temperature=0.0, max_tokens=1024)
        summaries = _extract_json_array(llm_output)

        # 只保留合法的 summary 字段，保证写入年表的数据干净
        cleaned = [
            {"summary": s.get("summary", ""), "order": s.get("order", i)}
            for i, s in enumerate(summaries)
            if isinstance(s, dict) and s.get("summary")
        ]

        # 并行分支：只写自己独占的字段
        return {
            "chapter_summaries": cleaned,
        }


class BuildOutputNode:
    """汇总节点：构建检索索引，汇总实体+事件结果（汇合点）"""

    name = "build_output"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        chunk_dicts = state.get("processed_chunks", [])

        # 重建 Chunk 对象并建 TF-IDF 索引（里程碑17：按项目建，不再全局共享覆盖）
        from ..core.chunker import Chunk
        chunks = [Chunk(id=c["id"], text=c["text"], char_count=c["char_count"]) for c in chunk_dicts]
        get_retriever_for(state.get("novel_id")).build_index(chunks)

        entities = state.get("extracted_entities", [])
        events = state.get("extracted_events", [])
        relations = state.get("extracted_relationships", [])
        summaries = state.get("chapter_summaries", [])  # 里程碑15
        chapter_id = state.get("chapter_id")  # 里程碑15：save_chapter 穿透的章节标记

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

        # 里程碑15：整章摘要写入情节大事年表（带章节标记，用于按章更新）
        for s in summaries:
            g.add_chapter_summary(s.get("summary", ""), chapter_id=chapter_id)

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
