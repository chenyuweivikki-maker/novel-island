"""
章节存储 — 里程碑10：SQLite 持久化

作者创作空间的存储层。PRD 存储层的最小实现（本地 SQLite），
以后换 PostgreSQL 只改这里（存储层抽象）。

能力：
  create_novel(title)      — 创建作品
  add_chapter(novel_id, content) — 保存章节（返回章节id）
  list_chapters(novel_id)  — 章节列表
  get_chapter(id)          — 读取章节
"""
import sqlite3
import os
import time
from typing import List, Dict, Any, Optional


class NovelStore:
    """SQLite 章节存储"""

    def __init__(self, db_path: str = "data/novels.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """建表：novels（作品）+ chapters（章节）"""
        conn = self._get_conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS novels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chapters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            title TEXT DEFAULT '',
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (novel_id) REFERENCES novels(id)
        );
        """)
        conn.commit()
        conn.close()

    def create_novel(self, title: str) -> int:
        """创建作品，返回 novel_id"""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO novels (title, created_at) VALUES (?, ?)",
            (title, time.time()),
        )
        conn.commit()
        novel_id = cur.lastrowid
        conn.close()
        return novel_id

    def add_chapter(self, novel_id: int, content: str, title: str = "") -> int:
        """保存章节，返回 chapter_id"""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO chapters (novel_id, title, content, created_at) VALUES (?, ?, ?, ?)",
            (novel_id, title, content, time.time()),
        )
        conn.commit()
        chapter_id = cur.lastrowid
        conn.close()
        return chapter_id

    def update_chapter(self, chapter_id: int, content: str, title: str = ""):
        """更新章节正文（里程碑10：作者改旧章节）"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE chapters SET content = ?, title = ? WHERE id = ?",
            (content, title, chapter_id),
        )
        conn.commit()
        conn.close()

    def list_chapters(self, novel_id: int) -> List[Dict[str, Any]]:
        """列出某作品的所有章节（不含正文，只含元信息）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, title, created_at FROM chapters WHERE novel_id = ? ORDER BY id",
            (novel_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_chapter(self, chapter_id: int) -> Optional[Dict[str, Any]]:
        """读取章节全文"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM chapters WHERE id = ?", (chapter_id,),
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def list_novels(self) -> List[Dict[str, Any]]:
        """列出所有作品"""
        conn = self._get_conn()
        rows = conn.execute("SELECT id, title, created_at FROM novels ORDER BY id").fetchall()
        conn.close()
        return [dict(r) for r in rows]


# 全局单例
novel_store = NovelStore()
