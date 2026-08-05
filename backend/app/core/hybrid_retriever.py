"""
混合检索 — 里程碑9：向量 + TF-IDF 双路召回

PRD 设计：双路检索（向量RAG + 图谱/关键词）。
这里实现：向量语义召回 + TF-IDF 字面召回 → 合并去重 → 按最高分排序。

为什么混合：
  - TF-IDF 擅长字面匹配（人名、设定名精确命中）
  - 向量擅长语义匹配（换说法、同义表达）
  两路各召回 Top-K，合并后每块取最高分，既覆盖语义也覆盖字面。
"""
from typing import List, Dict, Any, Optional

from .retriever import retriever
from .vector_store import vector_store
from .graph_store import graph

# 属性关键词表：用户问"职业/工作/做什么"→ 查职业属性（里程碑9）
ATTRIBUTE_KEYWORDS = {
    "职业": ["职业", "工作", "做什么", "干什么", "主业", "上班"],
    "性格": ["性格", "个性", "脾气", "怎么样的人", "什么性格"],
    "外貌": ["外貌", "长相", "长什么样", "身高", "样子", "外观"],
    "家庭": ["家庭", "家人", "父母", "亲戚", "舅舅", "家里"],
    "宠物": ["宠物", "养了", "猫", "狗", "动物", "汪汪"],
    "物品": ["物品", "东西", "密码", "头像", "纹身", "装备"],
    "事件": ["事件", "经历", "发生过", "事故", "遭遇"],
}


def extract_entities_from_query(query: str) -> List[str]:
    """从用户问题里提取实体名（图谱里存在的）"""
    entities = graph.all_entities()
    found = []
    for e in entities:
        if e and e in query:
            found.append(e)
    return found


def extract_attribute_from_query(query: str) -> Optional[str]:
    """从用户问题里识别要查的属性类别"""
    for attr, keywords in ATTRIBUTE_KEYWORDS.items():
        if any(kw in query for kw in keywords):
            return attr
    return None


def precise_attribute_search(query: str) -> Optional[Dict[str, Any]]:
    """精确属性检索：实体 + 属性 → 查图谱，命中直接返回答案

    里程碑9：这是"精准命中"的核心层。
    命中格式：{"answer": "汪！...", "entity": "唐嘉措", "attribute": "宠物", "value": "..."}
    """
    # 1. 提取实体（图谱里存在的人名）
    entities = extract_entities_from_query(query)
    if not entities:
        return None

    # 2. 识别要查的属性类别
    attr = extract_attribute_from_query(query)
    if not attr:
        return None

    # 3. 精确查图谱属性
    for entity in entities:
        value = graph.query_attribute(entity, attr)
        if value:
            return {
                "answer": f"根据设定，{entity}的{attr}是：{value}",
                "entity": entity,
                "attribute": attr,
                "value": value,
            }
    return None


def hybrid_search(query: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """混合检索：向量召回 + TF-IDF 召回 → 合并去重 → 排序

    返回: [{"chunk": Chunk, "score": float, "index": int}, ...]
    """
    merged: Dict[int, Dict[str, Any]] = {}  # chunk index → 结果（去重）

    # 1. 向量召回（语义）
    if vector_store.is_ready:
        vector_hits = vector_store.search(query, top_k)
        for hit in vector_hits:
            chunk_id = hit["metadata"].get("chunk_id", hit["index"])
            if 0 <= chunk_id < len(retriever.chunks):
                # 保留最高分（可能两路都命中）
                merged[chunk_id] = {
                    "chunk": retriever.chunks[chunk_id],
                    "score": max(merged[chunk_id]["score"], hit["score"]) if chunk_id in merged else hit["score"],
                    "index": chunk_id,
                }

    # 2. TF-IDF 召回（字面）
    tfidf_hits = retriever.search(query, top_k)
    for hit in tfidf_hits:
        idx = hit["index"]
        if idx in merged:
            # 已命中，取最高分
            merged[idx]["score"] = max(merged[idx]["score"], hit["score"])
        else:
            merged[idx] = {"chunk": hit["chunk"], "score": hit["score"], "index": idx}

    # 3. 按分数降序，取 Top-K
    results = sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:top_k]
    return results
