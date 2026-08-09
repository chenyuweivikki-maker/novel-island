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
        """建表：novels（作品）+ chapters（章节）+ backgrounds（背景资料，里程碑18）"""
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
        CREATE TABLE IF NOT EXISTS backgrounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            category TEXT NOT NULL,
            title TEXT DEFAULT '',
            content TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY (novel_id) REFERENCES novels(id)
        );
        """)
        conn.commit()
        # 里程碑17：给老库补 sort_order 列（拖拽排序），新库建表时不会带这列，用迁移方式加
        cols = [row[1] for row in conn.execute("PRAGMA table_info(novels)").fetchall()]
        if "sort_order" not in cols:
            conn.execute("ALTER TABLE novels ADD COLUMN sort_order INTEGER DEFAULT 0")
            # 按现有创建顺序回填 sort_order，保证老数据也有稳定排序
            rows = conn.execute("SELECT id FROM novels ORDER BY id").fetchall()
            for i, row in enumerate(rows):
                conn.execute("UPDATE novels SET sort_order = ? WHERE id = ?", (i, row["id"]))
            conn.commit()
        # 里程碑18：novels 补 outline/expected_words/chapter_words，chapters 补 outline
        cols = [row[1] for row in conn.execute("PRAGMA table_info(novels)").fetchall()]
        if "outline" not in cols:
            conn.execute("ALTER TABLE novels ADD COLUMN outline TEXT DEFAULT ''")
        if "expected_words" not in cols:
            conn.execute("ALTER TABLE novels ADD COLUMN expected_words INTEGER DEFAULT 0")
        if "chapter_words" not in cols:
            conn.execute("ALTER TABLE novels ADD COLUMN chapter_words INTEGER DEFAULT 0")
        ch_cols = [row[1] for row in conn.execute("PRAGMA table_info(chapters)").fetchall()]
        if "outline" not in ch_cols:
            conn.execute("ALTER TABLE chapters ADD COLUMN outline TEXT DEFAULT ''")
        conn.commit()
        conn.close()

    def create_novel(self, title: str, expected_words: int = 0, chapter_words: int = 0) -> int:
        """创建作品，返回 novel_id（sort_order 排在当前最后，里程碑18支持字数设置）"""
        conn = self._get_conn()
        max_order = conn.execute("SELECT MAX(sort_order) AS m FROM novels").fetchone()["m"]
        next_order = (max_order or 0) + 1
        cur = conn.execute(
            "INSERT INTO novels (title, created_at, sort_order, expected_words, chapter_words) VALUES (?, ?, ?, ?, ?)",
            (title, time.time(), next_order, expected_words, chapter_words),
        )
        conn.commit()
        novel_id = cur.lastrowid
        conn.close()
        return novel_id

    def update_novel_title(self, novel_id: int, title: str):
        """重命名作品（里程碑17）"""
        conn = self._get_conn()
        conn.execute("UPDATE novels SET title = ? WHERE id = ?", (title, novel_id))
        conn.commit()
        conn.close()

    def reorder_novels(self, ordered_ids: List[int]):
        """按给定的 id 顺序重新分配 sort_order（里程碑17：拖拽排序落库）"""
        conn = self._get_conn()
        for i, novel_id in enumerate(ordered_ids):
            conn.execute("UPDATE novels SET sort_order = ? WHERE id = ?", (i, novel_id))
        conn.commit()
        conn.close()

    def add_chapter(self, novel_id: int, content: str, title: str = "", outline: str = "") -> int:
        """保存章节，返回 chapter_id（里程碑18：可带章纲 outline）"""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO chapters (novel_id, title, content, created_at, outline) VALUES (?, ?, ?, ?, ?)",
            (novel_id, title, content, time.time(), outline),
        )
        conn.commit()
        chapter_id = cur.lastrowid
        conn.close()
        return chapter_id

    def update_chapter(self, chapter_id: int, content: str, title: str = "", outline: str = ""):
        """更新章节正文（里程碑10：作者改旧章节；里程碑18：可更新章纲）"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE chapters SET content = ?, title = ?, outline = ? WHERE id = ?",
            (content, title, outline, chapter_id),
        )
        conn.commit()
        conn.close()

    def list_chapters(self, novel_id: int) -> List[Dict[str, Any]]:
        """列出某作品的所有章节（不含正文，含章纲 outline，里程碑18）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, title, created_at, outline FROM chapters WHERE novel_id = ? ORDER BY id",
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

    def update_novel_outline(self, novel_id: int, content: str):
        """保存大纲（里程碑18：作者自写单文本块）"""
        conn = self._get_conn()
        conn.execute("UPDATE novels SET outline = ? WHERE id = ?", (content, novel_id))
        conn.commit()
        conn.close()

    def get_novel_outline(self, novel_id: int) -> str:
        """读取大纲（里程碑18）"""
        conn = self._get_conn()
        row = conn.execute("SELECT outline FROM novels WHERE id = ?", (novel_id,)).fetchone()
        conn.close()
        return row["outline"] if row else ""

    def add_background(self, novel_id: int, category: str, title: str, content: str) -> int:
        """添加背景资料（里程碑18：作者自己分类）"""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO backgrounds (novel_id, category, title, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (novel_id, category, title, content, time.time()),
        )
        conn.commit()
        bg_id = cur.lastrowid
        conn.close()
        return bg_id

    def list_backgrounds(self, novel_id: int) -> List[Dict[str, Any]]:
        """列出某作品的所有背景资料（里程碑18）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, category, title, content, created_at FROM backgrounds WHERE novel_id = ? ORDER BY id",
            (novel_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_background(self, bg_id: int):
        """删除背景资料（里程碑18）"""
        conn = self._get_conn()
        conn.execute("DELETE FROM backgrounds WHERE id = ?", (bg_id,))
        conn.commit()
        conn.close()

    def list_novels(self) -> List[Dict[str, Any]]:
        """列出所有作品（按 sort_order 排序，里程碑17/18）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, title, created_at, sort_order, outline, expected_words, chapter_words FROM novels ORDER BY sort_order, id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# 全局单例
novel_store = NovelStore()
