"""
TF-IDF 检索器 — sklearn 字符级 n-gram + cosine 相似度

使用字符级 2-3 gram 分词，天然适配中文，无需 jieba。
后续可替换为 Qdrant + 真实 embedding 模型，接口保持不变。
"""
import re
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from .chunker import Chunk


def preprocess(text: str) -> str:
    """预处理：去除标点和特殊符号，保留中英文字符和数字"""
    return re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9\s]", " ", text)


class TFIDFRetriever:
    def __init__(self):
        self.chunks: list[Chunk] = []
        self.vectorizer: TfidfVectorizer | None = None
        self.matrix = None  # tf-idf 矩阵

    def build_index(self, chunks: list[Chunk]):
        """构建 TF-IDF 索引（字符级 n-gram）"""
        self.chunks = chunks
        docs = [preprocess(c.text) for c in chunks]
        self.vectorizer = TfidfVectorizer(
            analyzer="char_wb",      # 字符级 n-gram，词边界模式
            ngram_range=(2, 3),      # 2-3 字符组合，适配中文
            max_features=20000,
            min_df=1,
            max_df=1.0,              # 不限制，兼容少量文档
        )
        self.matrix = self.vectorizer.fit_transform(docs)

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """
        检索 Top-K 最相关的文本块。
        返回: [{"chunk": Chunk, "score": float}, ...]
        """
        if not self.vectorizer or self.matrix is None or len(self.chunks) == 0:
            return []

        q_text = preprocess(query)
        q_vec = self.vectorizer.transform([q_text])
        scores = cosine_similarity(q_vec, self.matrix).flatten()

        # 取 Top-K
        top_indices = np.argsort(scores)[::-1][:top_k]
        results = []
        for i in top_indices:
            if scores[i] > 0.001:  # 过滤极低分
                results.append({
                    "chunk": self.chunks[i],
                    "score": float(scores[i]),
                    "index": int(i),
                })
        return results

    @property
    def is_ready(self) -> bool:
        return self.vectorizer is not None and len(self.chunks) > 0


# 全局单例
retriever = TFIDFRetriever()


class RetrieverManager:
    """按项目(novel_id)管理多个 TF-IDF 检索器 — 里程碑17：多租户隔离

    每个项目一份独立索引，避免建库时互相覆盖（此前建库到项目A再建库到
    项目B会覆盖全局共享索引，导致混合检索的TF-IDF一路召回错项目内容）。
    """

    def __init__(self):
        self._retrievers: dict[int, TFIDFRetriever] = {}

    def get_retriever(self, novel_id: int) -> TFIDFRetriever:
        if novel_id not in self._retrievers:
            self._retrievers[novel_id] = TFIDFRetriever()
        return self._retrievers[novel_id]


retriever_manager = RetrieverManager()


def get_retriever_for(novel_id: int | None) -> TFIDFRetriever:
    """取某项目(或默认)的检索器。novel_id 为 None 时回退全局单例。"""
    if novel_id is None:
        return retriever
    return retriever_manager.get_retriever(novel_id)
