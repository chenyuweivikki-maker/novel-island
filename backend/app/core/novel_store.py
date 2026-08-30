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

    def __init__(self, db_path: str = ""):
        # 支持 NOVELS_DB 环境变量覆盖（与 memory.py 的 CHAT_HISTORY_DB 一致，测试可隔离库）
        self.db_path = db_path or os.environ.get("NOVELS_DB", "data/novels.db")
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
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
        CREATE TABLE IF NOT EXISTS foreshadowings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at REAL NOT NULL,
            FOREIGN KEY (novel_id) REFERENCES novels(id)
        );
        CREATE TABLE IF NOT EXISTS chapter_backups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL,
            chapter_id INTEGER NOT NULL,
            kind TEXT DEFAULT 'save',
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
        # P2-3：章纲结构化（伏笔/预设列）
        if "foreshadowing" not in ch_cols:
            conn.execute("ALTER TABLE chapters ADD COLUMN foreshadowing TEXT DEFAULT '[]'")
        if "setup" not in ch_cols:
            conn.execute("ALTER TABLE chapters ADD COLUMN setup TEXT DEFAULT ''")
        # 题材列（我的作品页分类联动）：老库迁移补列，新库建表时也不会带，用同款迁移方式加
        cols = [row[1] for row in conn.execute("PRAGMA table_info(novels)").fetchall()]
        if "genre" not in cols:
            conn.execute("ALTER TABLE novels ADD COLUMN genre TEXT DEFAULT ''")
        # 会话绑定（首页对话自动建的书 ↔ session_id，供对话提到书名时同步更新书名）：
        # session_id — 该作品由哪个首页会话创建/绑定；title_auto — 书名是否自动兜底（1=可被对话总结覆盖，0=已明确命名）
        cols = [row[1] for row in conn.execute("PRAGMA table_info(novels)").fetchall()]
        if "session_id" not in cols:
            conn.execute("ALTER TABLE novels ADD COLUMN session_id TEXT DEFAULT ''")
        if "title_auto" not in cols:
            conn.execute("ALTER TABLE novels ADD COLUMN title_auto INTEGER DEFAULT 0")
        conn.commit()
        conn.close()

    def create_novel(self, title: str, expected_words: int = 0, chapter_words: int = 0, genre: str = "",
                     session_id: str = "", title_auto: int = 0) -> int:
        """创建作品，返回 novel_id（sort_order 排在当前最后，里程碑18支持字数设置，题材可选）
        session_id：首页对话自动建书时绑定会话（对话提书名可同步更新）；title_auto：书名是否自动兜底。
        """
        conn = self._get_conn()
        max_order = conn.execute("SELECT MAX(sort_order) AS m FROM novels").fetchone()["m"]
        next_order = (max_order or 0) + 1
        cur = conn.execute(
            "INSERT INTO novels (title, created_at, sort_order, expected_words, chapter_words, genre, session_id, title_auto) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (title, time.time(), next_order, expected_words, chapter_words, genre, session_id, title_auto),
        )
        conn.commit()
        novel_id = cur.lastrowid
        conn.close()
        return novel_id

    def update_novel_title(self, novel_id: int, title: str, title_auto: int | None = None):
        """重命名作品（里程碑17）。title_auto 传入时同步更新书名状态（0=明确命名，1=自动兜底可覆盖）"""
        conn = self._get_conn()
        if title_auto is None:
            conn.execute("UPDATE novels SET title = ? WHERE id = ?", (title, novel_id))
        else:
            conn.execute("UPDATE novels SET title = ?, title_auto = ? WHERE id = ?", (title, title_auto, novel_id))
        conn.commit()
        conn.close()

    def get_novel_by_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """按首页会话 id 找它自动建的书（对话提书名 → 同步更新创作/我的作品）"""
        if not session_id:
            return None
        conn = self._get_conn()
        row = conn.execute("SELECT * FROM novels WHERE session_id = ? ORDER BY id DESC LIMIT 1", (session_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def set_session_binding(self, novel_id: int, session_id: str):
        """把作品绑定到首页会话（老会话回填用）"""
        conn = self._get_conn()
        conn.execute("UPDATE novels SET session_id = ? WHERE id = ?", (session_id, novel_id))
        conn.commit()
        conn.close()

    def get_most_recent_unbound(self) -> Optional[Dict[str, Any]]:
        """最近一本未绑定会话的书（老会话回填：无映射时尽量续接，避免重复建书）"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM novels WHERE session_id = '' OR session_id IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def update_novel_genre(self, novel_id: int, genre: str):
        """修改作品题材（「我的作品」页分类联动；传空串=清除题材，回到未分类）"""
        conn = self._get_conn()
        conn.execute("UPDATE novels SET genre = ? WHERE id = ?", (genre, novel_id))
        conn.commit()
        conn.close()

    def reorder_novels(self, ordered_ids: List[int]):
        """按给定的 id 顺序重新分配 sort_order（里程碑17：拖拽排序落库）"""
        conn = self._get_conn()
        for i, novel_id in enumerate(ordered_ids):
            conn.execute("UPDATE novels SET sort_order = ? WHERE id = ?", (i, novel_id))
        conn.commit()
        conn.close()

    def delete_novel(self, novel_id: int) -> None:
        """删除作品及其关联数据（章节/背景/伏笔），并把该作品的灵感移回默认收件库（novel_id=0）。
        知识库文件（graph/vector）与内存缓存的清理由调用方（DELETE /api/novel/{id}）负责。"""
        conn = self._get_conn()
        try:
            conn.execute("DELETE FROM foreshadowings WHERE novel_id = ?", (novel_id,))
            conn.execute("DELETE FROM chapters WHERE novel_id = ?", (novel_id,))
            conn.execute("DELETE FROM backgrounds WHERE novel_id = ?", (novel_id,))
            conn.execute("UPDATE inspirations SET novel_id = 0 WHERE novel_id = ?", (novel_id,))
            conn.execute("DELETE FROM novels WHERE id = ?", (novel_id,))
            conn.commit()
        finally:
            conn.close()

    def add_chapter(self, novel_id: int, content: str, title: str = "", outline: str = "",
                    foreshadowing: str = "[]", setup: str = "") -> int:
        """保存章节，返回 chapter_id（里程碑18：可带章纲 outline；P2-3：伏笔/预设）"""
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO chapters (novel_id, title, content, created_at, outline, foreshadowing, setup) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (novel_id, title, content, time.time(), outline, foreshadowing, setup),
        )
        conn.commit()
        chapter_id = cur.lastrowid
        conn.close()
        return chapter_id

    def update_chapter(self, chapter_id: int, content: str, title: str = "", outline: str = "",
                       foreshadowing: str = "[]", setup: str = ""):
        """更新章节正文（里程碑10：作者改旧章节；里程碑18：可更新章纲；P2-3：伏笔/预设）"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE chapters SET content = ?, title = ?, outline = ?, foreshadowing = ?, setup = ? WHERE id = ?",
            (content, title, outline, foreshadowing, setup, chapter_id),
        )
        conn.commit()
        conn.close()

    # ===== 写操作备份/回滚（P2-7）=====
    def save_chapter_backup(self, novel_id: int, chapter_id: int, kind: str, content: str) -> None:
        """写操作前自动把旧章节内容备份进 chapter_backups（回滚用）"""
        conn = self._get_conn()
        conn.execute(
            "INSERT INTO chapter_backups (novel_id, chapter_id, kind, content, created_at) VALUES (?,?,?,?,?)",
            (novel_id, chapter_id, kind, content, time.time()),
        )
        conn.commit()
        conn.close()

    def list_chapter_backups(self, novel_id: int) -> List[Dict[str, Any]]:
        """列出某作品的所有章节备份（不含全文，含长度/时间）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, chapter_id, kind, length(content) AS chars, created_at "
            "FROM chapter_backups WHERE novel_id = ? ORDER BY id DESC LIMIT 50",
            (novel_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_chapter_backup(self, backup_id: int) -> Optional[Dict[str, Any]]:
        """读取某条备份全文（回滚用）"""
        conn = self._get_conn()
        r = conn.execute("SELECT * FROM chapter_backups WHERE id = ?", (backup_id,)).fetchone()
        conn.close()
        return dict(r) if r else None

    def list_chapters(self, novel_id: int) -> List[Dict[str, Any]]:
        """列出某作品的所有章节（不含正文，含章纲/伏笔/预设，里程碑18/P2-3）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, title, created_at, outline, foreshadowing, setup FROM chapters WHERE novel_id = ? ORDER BY id",
            (novel_id,),
        ).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            try:
                import json as _json
                d["foreshadowing_list"] = _json.loads(d.get("foreshadowing") or "[]")
            except Exception:
                d["foreshadowing_list"] = []
            result.append(d)
        return result

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
        """列出所有作品（按 sort_order 排序，里程碑17/18，含题材）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, title, created_at, sort_order, outline, expected_words, chapter_words, genre FROM novels ORDER BY sort_order, id"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def list_genres(self) -> List[str]:
        """所有作品用到的题材（去重、非空、按首次出现顺序）——「我的作品」页分类按钮的数据源"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT genre FROM novels WHERE genre IS NOT NULL AND genre != '' GROUP BY genre ORDER BY MIN(sort_order)"
        ).fetchall()
        conn.close()
        return [r["genre"] for r in rows]

    # ===== 伏笔管理（P2-3：伏笔看板 / 未填坑提醒） =====
    def add_foreshadowings(self, novel_id: int, chapter_id: int, texts: List[str]) -> int:
        """批量登记伏笔（同文本去重：同一本书同一伏笔只记一次）"""
        added = 0
        if not texts:
            return 0
        conn = self._get_conn()
        existing = {
            r["text"] for r in conn.execute(
                "SELECT text FROM foreshadowings WHERE novel_id = ?", (novel_id,)
            ).fetchall()
        }
        for t in texts:
            t = t.strip()
            if not t or t in existing:
                continue
            conn.execute(
                "INSERT INTO foreshadowings (novel_id, chapter_id, text, status, created_at) VALUES (?, ?, ?, 'pending', ?)",
                (novel_id, chapter_id, t, time.time()),
            )
            existing.add(t)
            added += 1
        conn.commit()
        conn.close()
        return added

    def list_foreshadowings(self, novel_id: int, status: str = "") -> List[Dict[str, Any]]:
        """伏笔列表（status: pending/resolved/空=全部）"""
        conn = self._get_conn()
        sql = "SELECT f.*, c.title AS chapter_title FROM foreshadowings f LEFT JOIN chapters c ON c.id = f.chapter_id WHERE f.novel_id = ?"
        args: list = [novel_id]
        if status:
            sql += " AND f.status = ?"
            args.append(status)
        sql += " ORDER BY f.id"
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def resolve_foreshadowing(self, fh_id: int) -> None:
        """标记伏笔已解决（填坑）"""
        conn = self._get_conn()
        conn.execute("UPDATE foreshadowings SET status = 'resolved' WHERE id = ?", (fh_id,))
        conn.commit()
        conn.close()

    def foreshadowing_stats(self, novel_id: int) -> Dict[str, int]:
        """伏笔统计：总数/未解决/已解决"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM foreshadowings WHERE novel_id = ? GROUP BY status",
            (novel_id,),
        ).fetchall()
        conn.close()
        stats = {"total": 0, "pending": 0, "resolved": 0}
        for r in rows:
            stats[r["status"]] = r["n"]
            stats["total"] += r["n"]
        return stats


# 全局单例
novel_store = NovelStore()
