"""
语义缓存 — PRD：缓存整个请求的输入和输出，检索相似度，高直接输出，缓存可过期

实现（无 Redis，本地 SQLite 版）：
  - 每个缓存条目：query（原文）+ answer + sources + novel_id + 时间戳
  - 命中判定：新 query 与库内 query 的 embedding 余弦相似度 ≥ 阈值（0.93）
  - 容量上限 300 条，超过按最旧淘汰；过期时间默认 24h

挂载点：POST /api/kb/ask 非流式路径（先查缓存 → 未命中走状态机 → 写入缓存）。
"""
import json
import os
import sqlite3
import time

from .embedding import embed_query


class SemanticCache:
    def __init__(self, db_path: str = "data/semantic_cache.db", threshold: float = 0.93, max_entries: int = 300, ttl_hours: int = 24):
        self.db_path = db_path
        self.threshold = threshold
        self.max_entries = max_entries
        self.ttl_seconds = ttl_hours * 3600
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS cache_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER,
            query TEXT NOT NULL,
            query_vec TEXT NOT NULL,
            answer TEXT NOT NULL,
            sources TEXT DEFAULT '[]',
            created_at REAL NOT NULL
        );
        """)
        conn.commit()
        conn.close()

    def _norm(self, v) -> float:
        s = sum(x * x for x in v) ** 0.5
        return s if s > 0 else 1.0

    def lookup(self, query: str, novel_id: int | None) -> dict | None:
        """查缓存：相似度达阈值返回 {answer, sources}，否则 None"""
        try:
            qv = embed_query(query)
        except Exception as e:
            print(f"[semantic_cache] embedding 失败: {e}")
            return None
        conn = self._get_conn()
        now = time.time()
        rows = conn.execute(
            "SELECT id, novel_id, query, query_vec, answer, sources, created_at FROM cache_entries"
        ).fetchall()
        best, best_sim = None, self.threshold
        for r in rows:
            if now - r["created_at"] > self.ttl_seconds:
                conn.execute("DELETE FROM cache_entries WHERE id = ?", (r["id"],))
                continue
            if r["novel_id"] != (novel_id or 0):
                continue
            cv = json.loads(r["query_vec"])
            dot = sum(a * b for a, b in zip(qv, cv))
            sim = dot / (self._norm(qv) * self._norm(cv))
            if sim > best_sim:
                best_sim = sim
                best = r
        conn.commit()
        conn.close()
        if best is None:
            return None
        return {
            "answer": best["answer"],
            "sources": json.loads(best["sources"]),
            "similarity": round(best_sim, 4),
        }

    def store(self, query: str, novel_id: int | None, answer: str, sources: list | None = None) -> None:
        """写入缓存（容量超限时淘汰最旧）"""
        try:
            qv = embed_query(query)
        except Exception as e:
            print(f"[semantic_cache] embedding 失败，跳过缓存: {e}")
            return
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO cache_entries (novel_id, query, query_vec, answer, sources, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (novel_id or 0, query, json.dumps(qv), answer, json.dumps(sources or []), time.time()),
        )
        # 容量控制：删除最旧的超出部分
        conn.execute(
            "DELETE FROM cache_entries WHERE id NOT IN (SELECT id FROM cache_entries ORDER BY id DESC LIMIT ?)",
            (self.max_entries,),
        )
        conn.commit()
        conn.close()

    def clear(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM cache_entries")
        conn.commit()
        conn.close()

    def __len__(self):
        conn = self._get_conn()
        n = conn.execute("SELECT COUNT(*) AS n FROM cache_entries").fetchone()["n"]
        conn.close()
        return n


# 全局单例
semantic_cache = SemanticCache()
