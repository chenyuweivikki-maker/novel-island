"""
埋点 SDK — PRD 5.6 数据与埋点（15+ 事件）

本地 SQLite 版：record(event, **props) 落库，POST /api/track 供前端上报，
GET /api/tracking/stats 提供按事件计数与最近事件。

PRD 事件清单（后端埋点 + 前端上报）：
  user_register / create_book / upload_content / click_generate_outline / click_regenerate
  accept_suggestion / reject_suggestion / click_feedback_button / book_complete
  api_call_llm / tool_usage / memory_operation / knowledge_graph_update
  critic_node_intercept / session_start / session_end
  cost_attribution / cache_hit / model_fallback
"""
import json
import os
import sqlite3
import time
import uuid
from typing import Dict, Any


class Tracking:
    def __init__(self, db_path: str = "data/tracking.db"):
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
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            props TEXT DEFAULT '{}',
            created_at REAL NOT NULL,
            session_id TEXT DEFAULT ''
        );
        """)
        conn.commit()
        conn.close()

    def record(self, event: str, session_id: str = "", **props: Any) -> None:
        """记录一个事件（失败不抛错，埋点不能影响主流程）"""
        try:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO events (event, props, created_at, session_id) VALUES (?, ?, ?, ?)",
                (event, json.dumps(props, ensure_ascii=False, default=str), time.time(), session_id or ""),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[tracking] 埋点失败 {event}: {e}")

    def stats(self) -> Dict[str, Any]:
        """按事件计数的统计"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT event, COUNT(*) AS n FROM events GROUP BY event ORDER BY n DESC"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM events").fetchone()["n"]
        recent = conn.execute(
            "SELECT id, event, props, created_at FROM events ORDER BY id DESC LIMIT 20"
        ).fetchall()
        conn.close()
        return {
            "total": total,
            "by_event": {r["event"]: r["n"] for r in rows},
            "recent": [
                {"id": r["id"], "event": r["event"], "props": json.loads(r["props"]), "created_at": r["created_at"]}
                for r in recent
            ],
        }

    def clear(self):
        conn = self._get_conn()
        conn.execute("DELETE FROM events")
        conn.commit()
        conn.close()


# 全局单例
tracking = Tracking()


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
