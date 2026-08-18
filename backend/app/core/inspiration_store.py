"""
灵感库存储 — UI 定稿 P12

作者主动上传灵感 → AI 自动分类（LLM）→ 分类浏览（人设/剧情/金句/世界观/其他）。

能力：
  add_inspiration(novel_id, content, category) — 上传灵感（category 空则由 LLM 自动分类）
  list_inspirations(novel_id, category)        — 列表（可按分类过滤）
  set_category(id, category)                   — 手动改分类
  delete_inspiration(id)                       — 删除
  分类管理：add_category / rename_category / move_category / delete_category / list_categories
  export_text(novel_id)                        — 导出全文
"""
import sqlite3
import os
import time
import re
from typing import List, Dict, Any, Optional

from .llm_client import chat

DEFAULT_CATEGORIES = ["人设", "剧情", "金句", "世界观", "其他"]

CLASSIFY_SYSTEM = (
    "你是小说创作灵感库的分类助手。把一条创作灵感归类到最合适的分类："
    "人设（人物性格/外貌/身份/关系设想）、剧情（情节/冲突/转折/伏笔/脑洞）、"
    "金句（适合直接放进小说的句子/对白/描写）、世界观（设定/规则/背景/物品/地点）、"
    "其他（上述都不合适的）。"
    "只输出一个分类名（两个字），不要解释，不要标点。"
)


class InspirationStore:
    """SQLite 灵感库存储"""

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
        CREATE TABLE IF NOT EXISTS inspirations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT '其他',
            source TEXT DEFAULT 'text',
            created_at REAL NOT NULL,
            FOREIGN KEY (novel_id) REFERENCES novels(id)
        );
        CREATE TABLE IF NOT EXISTS inspiration_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL DEFAULT 0,
            name TEXT NOT NULL,
            sort_order INTEGER DEFAULT 0
        );
        """)
        conn.commit()
        # 老库补列（source 字段）
        cols = [row[1] for row in conn.execute("PRAGMA table_info(inspirations)").fetchall()]
        if "source" not in cols:
            conn.execute("ALTER TABLE inspirations ADD COLUMN source TEXT DEFAULT 'text'")
            conn.commit()
        conn.close()

    # ===== 自动分类（LLM）=====
    def classify(self, content: str) -> str:
        """LLM 自动分类，失败/超时回退「其他」"""
        try:
            text = content[:500]
            out = chat(CLASSIFY_SYSTEM, text, temperature=0.0, max_tokens=16, task="extract")
            out = (out or "").strip().strip("。，、")
            if out in DEFAULT_CATEGORIES:
                return out
            for c in DEFAULT_CATEGORIES:  # 容错：输出里含分类名即命中
                if c in out:
                    return c
            return "其他"
        except Exception as e:
            print(f"[inspiration] 自动分类失败: {e}")
            return "其他"

    # ===== 灵感条目 =====
    def add_inspiration(self, novel_id: int, content: str, category: str = "", source: str = "text") -> int:
        """上传灵感。category 为空 → LLM 自动分类。返回 id"""
        if not content.strip():
            raise ValueError("内容不能为空")
        cat = category.strip() or self.classify(content)
        conn = self._get_conn()
        cur = conn.execute(
            "INSERT INTO inspirations (novel_id, content, category, source, created_at) VALUES (?, ?, ?, ?, ?)",
            (novel_id, content.strip(), cat, source, time.time()),
        )
        conn.commit()
        rid = cur.lastrowid
        conn.close()
        return rid

    def list_inspirations(self, novel_id: Optional[int] = None, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """列表（可按分类过滤；novel_id 为空则全部）"""
        conn = self._get_conn()
        sql = "SELECT * FROM inspirations WHERE 1=1"
        args: list = []
        if novel_id is not None:
            sql += " AND novel_id = ?"
            args.append(novel_id)
        if category and category != "全部":
            sql += " AND category = ?"
            args.append(category)
        sql += " ORDER BY id DESC"
        rows = conn.execute(sql, args).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def set_category(self, insp_id: int, category: str) -> None:
        """手动改分类"""
        conn = self._get_conn()
        conn.execute("UPDATE inspirations SET category = ? WHERE id = ?", (category, insp_id))
        conn.commit()
        conn.close()

    def delete_inspiration(self, insp_id: int) -> None:
        conn = self._get_conn()
        conn.execute("DELETE FROM inspirations WHERE id = ?", (insp_id,))
        conn.commit()
        conn.close()

    def count_by_category(self, novel_id: Optional[int] = None) -> Dict[str, int]:
        """各分类数量（用于侧栏角标）"""
        conn = self._get_conn()
        if novel_id is not None:
            rows = conn.execute(
                "SELECT category, COUNT(*) AS n FROM inspirations WHERE novel_id = ? GROUP BY category",
                (novel_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT category, COUNT(*) AS n FROM inspirations GROUP BY category"
            ).fetchall()
        conn.close()
        counts = {c: 0 for c in DEFAULT_CATEGORIES}
        for r in rows:
            counts[r["category"]] = r["n"]
        return counts

    def export_text(self, novel_id: Optional[int] = None) -> str:
        """导出全部灵感为纯文本（按分类分组）"""
        items = self.list_inspirations(novel_id)
        groups: Dict[str, List[str]] = {}
        for it in items:
            groups.setdefault(it["category"], []).append(it["content"])
        lines = []
        for cat in DEFAULT_CATEGORIES + [c for c in groups if c not in DEFAULT_CATEGORIES]:
            if cat in groups and groups[cat]:
                lines.append(f"【{cat}】")
                lines.extend(f"- {t}" for t in groups[cat])
                lines.append("")
        return "\n".join(lines) or "（灵感库还是空的）"

    # ===== 分类管理 =====
    def ensure_default_categories(self, novel_id: int) -> None:
        """首次进入某本书时补齐默认分类框架"""
        conn = self._get_conn()
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM inspiration_categories WHERE novel_id = ?", (novel_id,)
        ).fetchone()["n"]
        if n == 0:
            for i, name in enumerate(DEFAULT_CATEGORIES):
                conn.execute(
                    "INSERT INTO inspiration_categories (novel_id, name, sort_order) VALUES (?, ?, ?)",
                    (novel_id, name, i),
                )
            conn.commit()
        conn.close()

    def list_categories(self, novel_id: int) -> List[Dict[str, Any]]:
        self.ensure_default_categories(novel_id)
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM inspiration_categories WHERE novel_id = ? ORDER BY sort_order, id",
            (novel_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_category(self, novel_id: int, name: str) -> int:
        name = name.strip()
        if not name:
            raise ValueError("分类名不能为空")
        conn = self._get_conn()
        max_order = conn.execute(
            "SELECT MAX(sort_order) AS m FROM inspiration_categories WHERE novel_id = ?",
            (novel_id,),
        ).fetchone()["m"]
        cur = conn.execute(
            "INSERT INTO inspiration_categories (novel_id, name, sort_order) VALUES (?, ?, ?)",
            (novel_id, name, (max_order or 0) + 1),
        )
        conn.commit()
        cid = cur.lastrowid
        conn.close()
        return cid

    def rename_category(self, novel_id: int, old_name: str, new_name: str) -> None:
        """分类改名：分类表 + 灵感条目同步更新"""
        new_name = new_name.strip()
        if not new_name:
            raise ValueError("分类名不能为空")
        conn = self._get_conn()
        conn.execute(
            "UPDATE inspiration_categories SET name = ? WHERE novel_id = ? AND name = ?",
            (new_name, novel_id, old_name),
        )
        conn.execute(
            "UPDATE inspirations SET category = ? WHERE novel_id = ? AND category = ?",
            (new_name, novel_id, old_name),
        )
        conn.commit()
        conn.close()

    def move_category(self, novel_id: int, name: str, direction: str) -> None:
        """分类上移/下移（与相邻分类交换 sort_order）"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT id, name, sort_order FROM inspiration_categories WHERE novel_id = ? ORDER BY sort_order, id",
            (novel_id,),
        ).fetchall()
        idx = next((i for i, r in enumerate(rows) if r["name"] == name), None)
        if idx is None:
            conn.close()
            return
        target = idx - 1 if direction == "up" else idx + 1
        if target < 0 or target >= len(rows):
            conn.close()
            return
        a, b = rows[idx], rows[target]
        conn.execute("UPDATE inspiration_categories SET sort_order = ? WHERE id = ?", (b["sort_order"], a["id"]))
        conn.execute("UPDATE inspiration_categories SET sort_order = ? WHERE id = ?", (a["sort_order"], b["id"]))
        conn.commit()
        conn.close()

    def delete_category(self, novel_id: int, name: str) -> None:
        """删除分类：分类条目归入「其他」"""
        conn = self._get_conn()
        conn.execute(
            "UPDATE inspirations SET category = '其他' WHERE novel_id = ? AND category = ?",
            (novel_id, name),
        )
        conn.execute(
            "DELETE FROM inspiration_categories WHERE novel_id = ? AND name = ?",
            (novel_id, name),
        )
        conn.commit()
        conn.close()


# 全局单例（与 novel_store 共用同一个 db 文件）
inspiration_store = InspirationStore()
