"""
向量存储 — 里程碑9：向量检索 + 增量 + 持久化

用 BGE-M3 把文本块变成向量，按余弦相似度检索。
相比 TF-IDF（字符匹配），向量能匹配"语义相近"的内容。

支持：
  增量添加（add_chunks）—— 新章节只加新向量，不重建旧库
  语义检索（search）—— 按余弦相似度找最相关
  持久化（save/load）—— 存 numpy .npz 到磁盘，重启不丢
"""
import os
import numpy as np
from typing import List, Dict, Any

from .embedding import embed_texts, embed_query


class VectorStore:
    """向量库：文本块 + 向量 + 余弦检索"""

    def __init__(self, persist_path: str = "data/vector_store.npz"):
        self.persist_path = persist_path
        self.texts: List[str] = []        # 原始文本（按顺序）
        self.metadata: List[Dict] = []    # 每个文本块的元信息
        self.vectors: List[np.ndarray] = []  # 对应向量

    def add_chunks(self, texts: List[str], metadata: List[Dict] | None = None, chapter_id: int | None = None):
        """增量添加：新文本块向量化后追加（不重建旧库）

        这是增量更新的核心 —— 只处理新内容，旧向量原样保留。
        chapter_id 标记来源章节，用于按章删除（里程碑10）。
        """
        if not texts:
            return
        vectors = embed_texts(texts)  # 只对新文本向量化
        for i, text in enumerate(texts):
            meta = dict(metadata[i]) if metadata and i < len(metadata) else {}
            if chapter_id is not None:
                meta["chapter_id"] = chapter_id
            self.texts.append(text)
            self.metadata.append(meta)
            self.vectors.append(np.array(vectors[i]))

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """语义检索：查询向量 与 所有向量 算余弦相似度，取 Top-K"""
        if not self.vectors:
            return []
        q_vec = np.array(embed_query(query))

        # 余弦相似度 = 点积 / (模长乘积)
        scores = []
        for v in self.vectors:
            norm = np.linalg.norm(v) * np.linalg.norm(q_vec)
            sim = float(np.dot(v, q_vec) / norm) if norm > 0 else 0.0
            scores.append(sim)

        # 取 Top-K（按相似度降序）
        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for i in top_indices:
            if scores[i] > 0.1:  # 过滤低相关
                results.append({
                    "index": int(i),
                    "text": self.texts[i],
                    "score": round(scores[i], 4),
                    "metadata": self.metadata[i],
                })
        return results

    def remove_by_chapter(self, chapter_id: int):
        """删除某章节贡献的所有向量块（里程碑10：改章节时先删旧）"""
        keep_texts, keep_meta, keep_vecs = [], [], []
        for i, meta in enumerate(self.metadata):
            if meta.get("chapter_id") == chapter_id:
                continue  # 跳过该章的
            keep_texts.append(self.texts[i])
            keep_meta.append(meta)
            keep_vecs.append(self.vectors[i])
        self.texts, self.metadata, self.vectors = keep_texts, keep_meta, keep_vecs

    def save(self):
        """持久化到磁盘（numpy .npz）"""
        os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
        np.savez_compressed(
            self.persist_path,
            texts=np.array(self.texts, dtype=object),
            metadata=np.array(self.metadata, dtype=object),
            vectors=np.array([v for v in self.vectors]),
        )

    def load(self):
        """从磁盘加载"""
        if not os.path.exists(self.persist_path):
            return
        data = np.load(self.persist_path, allow_pickle=True)
        self.texts = list(data["texts"])
        self.metadata = list(data["metadata"])
        self.vectors = [np.array(v) for v in data["vectors"]]

    @property
    def is_ready(self) -> bool:
        return len(self.vectors) > 0

    def __len__(self):
        return len(self.texts)


# 全局单例（向量库，路径在 data/ 下）
vector_store = VectorStore(persist_path="data/vector_store.npz")
