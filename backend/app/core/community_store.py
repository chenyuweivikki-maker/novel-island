"""
社区存储 — 作者互动社区（帖子/点赞/收藏/评论）

能力：
  create_post      — 发帖（可附带灵感来源 / 所属书）
  list_posts       — 帖子流（最新排序，可过滤类型）
  get_post         — 帖子详情（含点赞/收藏/评论数）
  toggle_like      — 点赞 / 取消点赞
  toggle_favorite  — 收藏 / 取消收藏
  add_comment      — 评论
  list_comments    — 帖子评论列表

存储：SQLite（复用 data/novels.db 文件，独立表，保持单库单文件）
"""
import sqlite3
import os
import time
from typing import List, Dict, Any, Optional


class CommunityStore:
    """SQLite 社区存储"""

    def __init__(self, db_path: str = "data/novels.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            post_type TEXT DEFAULT 'post',      -- post=分享 / inspiration=灵感分享 / idea=使用心得 / question=提问
            author TEXT DEFAULT '陈雨薇',
            novel_id INTEGER DEFAULT 0,          -- 关联作品（0=通用）
            inspiration_id INTEGER DEFAULT 0,    -- 来源灵感（灵感分享时）
            like_count INTEGER DEFAULT 0,
            favorite_count INTEGER DEFAULT 0,
            comment_count INTEGER DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS post_likes (
            post_id INTEGER NOT NULL,
            user TEXT DEFAULT '陈雨薇',
            created_at REAL NOT NULL,
            PRIMARY KEY (post_id, user)
        );
        CREATE TABLE IF NOT EXISTS post_favorites (
            post_id INTEGER NOT NULL,
            user TEXT DEFAULT '陈雨薇',
            created_at REAL NOT NULL,
            PRIMARY KEY (post_id, user)
        );
        CREATE TABLE IF NOT EXISTS post_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            author TEXT DEFAULT '陈雨薇',
            created_at REAL NOT NULL
        );
        """)
        conn.commit()
        conn.close()

    # ── 帖子 ──
    def create_post(self, title: str, content: str, post_type: str = "post",
                    author: str = "陈雨薇", novel_id: int = 0,
                    inspiration_id: int = 0) -> int:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO posts (title, content, post_type, author, novel_id, inspiration_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (title.strip(), content.strip(), post_type, author, novel_id, inspiration_id, time.time()),
        )
        conn.commit()
        pid = cur.lastrowid
        conn.close()
        return pid

    def list_posts(self, post_type: Optional[str] = None, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        sql = "SELECT * FROM posts WHERE 1=1"
        args: list = []
        if post_type:
            sql += " AND post_type = ?"
            args.append(post_type)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_post(self, post_id: int) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def delete_post(self, post_id: int) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
        conn.execute("DELETE FROM post_likes WHERE post_id = ?", (post_id,))
        conn.execute("DELETE FROM post_favorites WHERE post_id = ?", (post_id,))
        conn.execute("DELETE FROM post_comments WHERE post_id = ?", (post_id,))
        conn.commit()
        conn.close()

    # ── 点赞 / 收藏 ──
    def toggle_like(self, post_id: int, user: str = "陈雨薇") -> Dict[str, Any]:
        conn = self._get_conn()
        exists = conn.execute(
            "SELECT 1 FROM post_likes WHERE post_id = ? AND user = ?", (post_id, user)
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM post_likes WHERE post_id = ? AND user = ?", (post_id, user))
            conn.execute("UPDATE posts SET like_count = MAX(0, like_count - 1) WHERE id = ?", (post_id,))
            liked = False
        else:
            conn.execute(
                "INSERT INTO post_likes (post_id, user, created_at) VALUES (?, ?, ?)",
                (post_id, user, time.time()),
            )
            conn.execute("UPDATE posts SET like_count = like_count + 1 WHERE id = ?", (post_id,))
            liked = True
        conn.commit()
        row = conn.execute("SELECT like_count FROM posts WHERE id = ?", (post_id,)).fetchone()
        conn.close()
        return {"liked": liked, "like_count": row["like_count"] if row else 0}

    def toggle_favorite(self, post_id: int, user: str = "陈雨薇") -> Dict[str, Any]:
        conn = self._get_conn()
        exists = conn.execute(
            "SELECT 1 FROM post_favorites WHERE post_id = ? AND user = ?", (post_id, user)
        ).fetchone()
        if exists:
            conn.execute("DELETE FROM post_favorites WHERE post_id = ? AND user = ?", (post_id, user))
            conn.execute("UPDATE posts SET favorite_count = MAX(0, favorite_count - 1) WHERE id = ?", (post_id,))
            favorited = False
        else:
            conn.execute(
                "INSERT INTO post_favorites (post_id, user, created_at) VALUES (?, ?, ?)",
                (post_id, user, time.time()),
            )
            conn.execute("UPDATE posts SET favorite_count = favorite_count + 1 WHERE id = ?", (post_id,))
            favorited = True
        conn.commit()
        row = conn.execute("SELECT favorite_count FROM posts WHERE id = ?", (post_id,)).fetchone()
        conn.close()
        return {"favorited": favorited, "favorite_count": row["favorite_count"] if row else 0}

    # ── 评论 ──
    def add_comment(self, post_id: int, content: str, author: str = "陈雨薇") -> int:
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO post_comments (post_id, content, author, created_at) VALUES (?, ?, ?, ?)",
            (post_id, content.strip(), author, time.time()),
        )
        conn.execute("UPDATE posts SET comment_count = comment_count + 1 WHERE id = ?", (post_id,))
        conn.commit()
        cid = cur.lastrowid
        conn.close()
        return cid

    def list_comments(self, post_id: int) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM post_comments WHERE post_id = ? ORDER BY id ASC", (post_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def delete_comment(self, comment_id: int) -> None:
        conn = self._get_conn()
        row = conn.execute("SELECT post_id FROM post_comments WHERE id = ?", (comment_id,)).fetchone()
        if row:
            conn.execute("DELETE FROM post_comments WHERE id = ?", (comment_id,))
            conn.execute("UPDATE posts SET comment_count = MAX(0, comment_count - 1) WHERE id = ?", (row["post_id"],))
            conn.commit()
        conn.close()


# 全局单例（与 novel_store / inspiration_store 一致）
community_store = CommunityStore()
