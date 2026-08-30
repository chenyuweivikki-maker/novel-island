"""
短期记忆 — 里程碑6：多轮对话历史 + 摘要压缩（v2：SQLite 持久化）

三层记忆（PRD）：
  工作记忆 = LangGraph State（节点间传状态，任务结束即清）
  长期记忆 = 向量/检索知识库（建库后永久，已有 TF-IDF）
  短期记忆 = 本文件：对话历史列表，会话内有效

v2 变更：对话历史落 SQLite（data/chat_history.db），服务重启不丢。
  按 (scope, session_id) 分组——scope='home'（无项目闲聊）或 str(novel_id)（项目对话），
  session_id 是前端生成的稳定会话标识（localStorage 持久化，刷新不丢），
  从而支持「对比不同对话的效果」：每组对话独立保存、独立恢复。

核心逻辑：
  add_turn(user_msg, ai_msg)  — 存一轮对话（写内存 + 落库）
  ensure_session(title)       — 会话占位：无记录时先建一条 system 空行（列表立刻可见，主流 AI 产品「一开聊就进列表」）
  get_context()               — 返回历史，超长时自动摘要压缩
  restore()                   — 启动时从 SQLite 恢复最近 N 轮
  list_sessions()             — 列出所有会话组（供前端对比）
"""
import os
import sqlite3
import time
from typing import List, Dict

from ..core.llm_client import chat
from .tracking import tracking

# 超过这个轮数就触发摘要压缩（PRD：对话历史摘要控制token）
MAX_HISTORY_TURNS = 6
# 摘要时保留最近的轮数
KEEP_RECENT_TURNS = 2
# 从库恢复时最多恢复的轮数（内存上下文窗口）
RESTORE_TURNS = 12

# 摘要用的系统提示词
SUMMARY_SYSTEM_PROMPT = """你是「小说岛」的对话摘要助手。
请把下面的对话历史压缩成一段50字以内的摘要，保留：用户关注的话题、AI给出的关键信息。
只输出摘要，不要其他内容。"""

_HISTORY_DB = os.environ.get("CHAT_HISTORY_DB", "data/chat_history.db")


def _history_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_HISTORY_DB) or ".", exist_ok=True)
    conn = sqlite3.connect(_HISTORY_DB, timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,            -- 会话范围：'home' 或 str(novel_id)
            session_id TEXT NOT NULL,       -- 前端稳定会话标识（localStorage）
            role TEXT NOT NULL,             -- user | assistant | system
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_scope_session ON chat_history (scope, session_id, id)"
    )
    # 迁移：老库补 title 列（会话重命名 v2.1）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_history)").fetchall()]
    if "title" not in cols:
        conn.execute("ALTER TABLE chat_history ADD COLUMN title TEXT DEFAULT ''")
    # 迁移：synced 列（对话内容增量入库知识库的标记：1=已入库，0=待入库）
    if "synced" not in cols:
        conn.execute("ALTER TABLE chat_history ADD COLUMN synced INTEGER DEFAULT 0")
    # 迁移：archived/pinned 列（会话归档与置顶标记）
    if "archived" not in cols:
        conn.execute("ALTER TABLE chat_history ADD COLUMN archived INTEGER DEFAULT 0")
    if "pinned" not in cols:
        conn.execute("ALTER TABLE chat_history ADD COLUMN pinned INTEGER DEFAULT 0")
    conn.commit()
    return conn


class ConversationMemory:
    """短期记忆：对话历史存储 + 摘要压缩 + SQLite 持久化

    一个 ConversationMemory = 一个 (scope, session_id) 对话组。
    """

    def __init__(self, scope: str = "home", session_id: str = "default"):
        self.scope = scope
        self.session_id = session_id
        self._conversation: List[Dict[str, str]] = []

    def add_turn(self, user_msg: str, ai_msg: str):
        """记录一轮对话（用户提问 + AI回答）：写内存 + 落库"""
        self._conversation.append({"role": "user", "content": user_msg})
        self._conversation.append({"role": "assistant", "content": ai_msg})
        try:
            conn = _history_conn()
            now = time.time()
            # 首次写入真实消息时自动生成默认标题（取首条用户消息前12字）
            # 用 role='user' 判断而非总数：system 占位行（ensure_session）不算真实首条
            title = ""
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM chat_history WHERE scope = ? AND session_id = ? AND role = 'user'",
                (self.scope, self.session_id),
            ).fetchone()
            if row and row[0] == 0:
                title = (user_msg or "新对话")[:12]
            conn.execute(
                "INSERT INTO chat_history (scope, session_id, role, content, created_at, title) VALUES (?, ?, ?, ?, ?, ?)",
                (self.scope, self.session_id, "user", user_msg, now, title),
            )
            conn.execute(
                "INSERT INTO chat_history (scope, session_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (self.scope, self.session_id, "assistant", ai_msg, now),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[memory] 历史落库失败: {e}")

    def ensure_session(self, title: str = ""):
        """会话占位（主流 AI 产品模式）：(scope, session_id) 尚无记录时，
        先插入一条 role='system' 的空行，让会话立刻出现在会话列表（置顶、可点击），
        后续 add_turn 写入真实消息后占位行自然被覆盖/忽略。幂等，可反复调用。
        """
        try:
            conn = _history_conn()
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM chat_history WHERE scope = ? AND session_id = ?",
                (self.scope, self.session_id),
            ).fetchone()
            if row and row[0] == 0:
                conn.execute(
                    "INSERT INTO chat_history (scope, session_id, role, content, created_at, title) VALUES (?, ?, ?, ?, ?, ?)",
                    (self.scope, self.session_id, "system", "", time.time(), title or "新对话"),
                )
                conn.commit()
            conn.close()
        except Exception as e:
            print(f"[memory] 会话占位失败: {e}")

    def restore(self):
        """从 SQLite 恢复最近轮次（服务重启后上下文不丢）"""
        try:
            conn = _history_conn()
            rows = conn.execute(
                "SELECT role, content FROM chat_history WHERE scope = ? AND session_id = ? "
                "AND role != 'system' ORDER BY id DESC LIMIT ?",
                (self.scope, self.session_id, RESTORE_TURNS * 2),
            ).fetchall()
            conn.close()
            # 库里是倒序取的，翻正
            self._conversation = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
        except Exception as e:
            print(f"[memory] 历史恢复失败: {e}")

    def get_context(self) -> List[Dict[str, str]]:
        """返回对话历史（供拼进 LLM messages）

        超过 MAX_HISTORY_TURNS 轮时：
          1. 把最早的对话交给 LLM 压缩成摘要
          2. 保留最近几轮 + 摘要
        这是 PRD"对话历史摘要"成本控制的落地。
        """
        if len(self._conversation) <= MAX_HISTORY_TURNS * 2:
            return list(self._conversation)

        # 超长：压缩早期对话
        early = self._conversation[:-KEEP_RECENT_TURNS * 2]  # 早期轮次
        recent = self._conversation[-KEEP_RECENT_TURNS * 2:]  # 最近轮次

        # 把早期对话格式化成文本，交给 LLM 压缩
        early_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content']}"
            for m in early
        )
        summary = chat(SUMMARY_SYSTEM_PROMPT, early_text, temperature=0.0, max_tokens=128)
        tracking.record("memory_operation", op="compress", session_id=getattr(self, "session_id", ""))  # PRD 埋点：记忆摘要压缩

        # 返回：摘要（作为 system 消息）+ 最近轮次
        return [
            {"role": "system", "content": f"【早期对话摘要】{summary}"},
            *recent,
        ]

    def clear(self):
        """清空历史（新会话）：内存 + 库"""
        self._conversation = []
        try:
            conn = _history_conn()
            conn.execute(
                "DELETE FROM chat_history WHERE scope = ? AND session_id = ?",
                (self.scope, self.session_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[memory] 历史清空失败: {e}")

    def __len__(self):
        return len(self._conversation)


# 全局单例（兼容老调用，未按项目区分场景）
memory = ConversationMemory()


class MemoryManager:
    """按 (scope, session_id) 管理多个对话记忆 — v2：持久化 + 多会话对比

    scope: 'home'（无项目闲聊）或 str(novel_id)（项目对话）
    session_id: 前端稳定会话标识（localStorage），同一 scope 下可开多个会话组
    """

    def __init__(self):
        self._memories: Dict[str, ConversationMemory] = {}

    def _key(self, scope: str, session_id: str) -> str:
        return f"{scope}|{session_id}"

    def get_memory(self, novel_id: int | None, session_id: str = "default") -> ConversationMemory:
        """获取某对话组的记忆（不存在则创建并恢复历史）"""
        scope = "home" if novel_id is None else str(novel_id)
        key = self._key(scope, session_id)
        if key not in self._memories:
            m = ConversationMemory(scope, session_id)
            m.restore()  # 首次访问时从库恢复
            self._memories[key] = m
        return self._memories[key]

    def list_sessions(self) -> List[Dict[str, object]]:
        """列出所有对话组（供前端对比不同对话）：
        每个会话组返回：scope / session_id / 消息数 / 最后消息时间 / 标题 / 最后消息预览 / archived / pinned
        system 占位行（ensure_session 的空会话）不计入消息数，也不作为最后消息/标题。
        """
        try:
            conn = _history_conn()
            rows = conn.execute(
                "SELECT scope, session_id, "
                "SUM(CASE WHEN role != 'system' THEN 1 ELSE 0 END) AS n, "
                "MAX(created_at) AS last_at, "
                "COALESCE("
                "  (SELECT h2.title FROM chat_history h2 "
                "   WHERE h2.scope = h.scope AND h2.session_id = h.session_id "
                "     AND h2.role != 'system' AND h2.title != '' "
                "   ORDER BY h2.id DESC LIMIT 1), "
                "  (SELECT h2.title FROM chat_history h2 "
                "   WHERE h2.scope = h.scope AND h2.session_id = h.session_id "
                "     AND h2.role = 'system' ORDER BY h2.id DESC LIMIT 1) "
                ") AS title, "
                "(SELECT content FROM chat_history h2 WHERE h2.scope = h.scope "
                "   AND h2.session_id = h.session_id AND h2.role != 'system' "
                " ORDER BY h2.id DESC LIMIT 1) AS last_msg, "
                "MAX(archived) AS archived, MAX(pinned) AS pinned "
                "FROM chat_history h GROUP BY scope, session_id ORDER BY last_at DESC"
            ).fetchall()
            conn.close()
            return [
                {
                    "scope": r[0],
                    "session_id": r[1],
                    "messages": r[2],
                    "last_at": r[3],
                    "title": r[4] or "",
                    "last_msg": (r[5] or "")[:80],
                    "archived": bool(r[6]),
                    "pinned": bool(r[7]),
                }
                for r in rows
            ]
        except Exception as e:
            print(f"[memory] 会话列表失败: {e}")
            return []

    def set_session_flag(self, scope: str, session_id: str, flag: str, value: bool) -> None:
        """设置会话级标记：archived（归档）/ pinned（置顶）——写该会话所有行"""
        if flag not in ("archived", "pinned"):
            return
        try:
            conn = _history_conn()
            conn.execute(
                f"UPDATE chat_history SET {flag} = ? WHERE scope = ? AND session_id = ?",
                (1 if value else 0, scope, session_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[memory] 会话{flag}失败: {e}")

    def rename_session(self, scope: str, session_id: str, title: str) -> None:
        """重命名会话组（把新标题写进该会话所有行的 title 字段）"""
        try:
            conn = _history_conn()
            conn.execute(
                "UPDATE chat_history SET title = ? WHERE scope = ? AND session_id = ?",
                (title, scope, session_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[memory] 会话重命名失败: {e}")

    def get_session_history(self, scope: str, session_id: str, limit: int = 100) -> List[Dict[str, str]]:
        """读取某个对话组的完整历史（供前端查看/对比）——跳过 system 占位行"""
        try:
            conn = _history_conn()
            rows = conn.execute(
                "SELECT role, content, created_at FROM chat_history "
                "WHERE scope = ? AND session_id = ? AND role != 'system' ORDER BY id DESC LIMIT ?",
                (scope, session_id, limit),
            ).fetchall()
            conn.close()
            return [
                {"role": r[0], "content": r[1], "created_at": r[2]}
                for r in reversed(rows)
            ]
        except Exception as e:
            print(f"[memory] 会话历史读取失败: {e}")
            return []

    def get_unsynced_user_messages(self, session_id: str, scope: str = "home") -> List[Dict[str, object]]:
        """某会话里尚未入库知识库的用户消息（对话增量入库：人设/关系/事件自动填充）。
        scope='home'（首页闲聊）或 str(novel_id)（创作页项目对话）。"""
        try:
            conn = _history_conn()
            rows = conn.execute(
                "SELECT id, content FROM chat_history "
                "WHERE scope = ? AND session_id = ? AND role = 'user' AND synced = 0 "
                "ORDER BY id ASC",
                (scope, session_id),
            ).fetchall()
            conn.close()
            return [{"id": r[0], "content": r[1]} for r in rows]
        except Exception as e:
            print(f"[memory] 未同步消息读取失败: {e}")
            return []

    def mark_messages_synced(self, session_id: str, ids: List[int], scope: str = "home") -> None:
        """把消息标记为已入库知识库（防重复抽取）"""
        if not ids:
            return
        try:
            conn = _history_conn()
            marks = ",".join("?" * len(ids))
            conn.execute(
                f"UPDATE chat_history SET synced = 1 WHERE scope = ? AND session_id = ? AND id IN ({marks})",
                [scope, session_id, *ids],
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[memory] 同步标记失败: {e}")

    def mark_all_synced(self, session_id: str, scope: str = "home") -> None:
        """把该会话所有消息标记为已入库（自动建书时历史已整体入库，防止下一轮重复抽取）"""
        try:
            conn = _history_conn()
            conn.execute(
                "UPDATE chat_history SET synced = 1 WHERE scope = ? AND session_id = ? AND role = 'user'",
                (scope, session_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[memory] 全部同步标记失败: {e}")


memory_manager = MemoryManager()
