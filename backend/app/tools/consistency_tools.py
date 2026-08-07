"""
情节一致性检查工具 — 里程碑12：check_plot_consistency

PRD 核心痛点之一"逻辑矛盾"的闭环：
作者写完新章节保存时，自动检测新章节与旧章节（知识库里已有内容）的矛盾。

原理：
  1. 用新章节文本检索知识库，找到最相关的旧片段（此时新章节还没入库，天然只检索旧内容）
  2. LLM 对照"新章节 + 旧片段"检查矛盾（人物设定、时间线、事件因果、伏笔）
  3. 返回冲突列表，作者能看到"哪里和前面写的不一致"

调用方：save_chapter（入库前调用）、也可作为 LLM 工具（作者主动问"我这段有没有bug"）
"""
import json
from typing import Dict, Any, List, Optional

from ..core.llm_client import chat
from ..core.retriever import retriever
from ..core.vector_store import vector_store, vector_store_manager
from ..core.graph_store import get_graph_for

CONSISTENCY_SYSTEM_PROMPT = """你是「小说岛」的情节一致性检查员，负责对照"新章节"和"旧章节片段"，找出情节矛盾。

检查维度：
1. 人物设定矛盾：同一人物的身份、性格、外貌、职业、宠物等前后不一致
2. 时间线矛盾：事件发生的先后、时间跨度对不上
3. 事件因果矛盾：后文事件与前面已确定的事件逻辑冲突
4. 伏笔/设定矛盾：新内容推翻/无视了前面明确的设定
5. 人物关系矛盾：两人关系（恋人/仇人/同事等）前后不一致

规则：
1. 只报"能明确指出的矛盾"，拿不准的不报（避免误报）
2. 每条冲突给出：冲突内容 + 依据（旧片段/新章节的具体说法）
3. 输出严格的 JSON 数组，格式：
[{"conflict": "冲突描述", "old": "旧章节的说法", "new": "新章节的说法", "severity": "high|medium|low"}]
4. 没有矛盾就输出空数组 []
5. 只输出 JSON，不要其他文字"""


def _build_consistency_prompt(new_content: str, old_chunks: list) -> str:
    """拼检查 prompt：旧片段 + 新章节"""
    context_parts = []
    for i, r in enumerate(old_chunks):
        c = r.get("chunk") or {}
        text = c.get("text") if isinstance(c, dict) else getattr(c, "text", str(c))
        context_parts.append(f"【旧片段#{i + 1}】(相似度:{r.get('score', 0):.2f})\n{text}")
    context = "\n\n---\n\n".join(context_parts) if context_parts else "（知识库暂无旧内容）"

    # 新章节截断，避免超 token
    new_trimmed = new_content[:4000]

    return f"""以下是知识库里已有的旧章节片段：

{context}

---

以下是作者新写的章节内容：

{new_trimmed}

---

请检查新章节与旧章节之间是否有情节矛盾。只输出JSON数组。"""


def check_plot_consistency(
    new_content: str,
    novel_id: int | None = None,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """检查新章节与旧章节的情节矛盾，返回冲突列表

    里程碑12：PRD"逻辑矛盾"痛点的闭环工具。
    在 save_chapter 入库前调用 —— 此时新章节还没进向量库，检索到的全是旧内容。

    返回：[{"conflict": "...", "old": "...", "new": "...", "severity": "high|medium|low"}, ...]
    """
    # 1. 检索旧片段（按项目取向量库；项目没有向量库时回退 TF-IDF）
    vs = vector_store_manager.get_store(novel_id) if novel_id is not None else vector_store
    if vs.is_ready:
        vector_hits = vs.search(new_content, top_k)
        old_chunks = []
        for hit in vector_hits:
            chunk_id = hit["metadata"].get("chunk_id", hit["index"])
            if 0 <= chunk_id < len(retriever.chunks):
                old_chunks.append({"chunk": retriever.chunks[chunk_id], "score": hit["score"]})
    else:
        old_chunks = retriever.search(new_content, top_k)

    if not old_chunks:
        return []

    # 2. LLM 对照检查
    prompt = _build_consistency_prompt(new_content, old_chunks)
    llm_output = chat(CONSISTENCY_SYSTEM_PROMPT, prompt, temperature=0.0, max_tokens=1024, task="logic")

    # 3. 解析 JSON
    try:
        text = llm_output.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        conflicts = json.loads(text)
        if not isinstance(conflicts, list):
            return []
        return conflicts
    except (json.JSONDecodeError, AttributeError):
        return []
