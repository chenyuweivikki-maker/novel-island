"""程序性记忆（PRD 四层记忆之四）：记录用户对 AI 建议的采纳/拒绝，用于优化推荐策略。

PRD 原文：记录用户对各类 AI 建议（如灵感方向、情节修改）的采纳与反馈数据，
用于优化后续的推荐策略。

实现：
  record_feedback()     — 记录一次反馈（accept / reject）
  preference_summary()  — 聚合最近反馈为「作者偏好摘要」（零成本规则聚合，不调 LLM），
                          供灵感/剧情推荐生成时注入 prompt：避开被拒绝的方向，优先被采纳的方向。
"""
import os
import sqlite3
import time
from collections import Counter
from typing import List, Dict

_PM_DB = os.environ.get("PROCEDURAL_MEMORY_DB", "data/procedural.db")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_PM_DB) or ".", exist_ok=True)
    conn = sqlite3.connect(_PM_DB, timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS procedural_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            novel_id INTEGER NOT NULL DEFAULT 0,   -- 0=通用（首页闲聊），>0=项目内
            session_id TEXT NOT NULL DEFAULT '',
            suggestion_type TEXT NOT NULL,          -- suggestion / inspiration / polish / generic
            suggestion TEXT NOT NULL,               -- 建议内容摘要（方向关键词）
            feedback TEXT NOT NULL,                 -- accept / reject
            created_at REAL NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pm_novel ON procedural_memory (novel_id, id)")
    return conn


def record_feedback(novel_id: int | None, session_id: str, suggestion_type: str,
                    suggestion: str, feedback: str) -> None:
    """记录一次用户对建议的反馈（accept=采纳 / reject=拒绝）"""
    if feedback not in ("accept", "reject"):
        return
    suggestion = (suggestion or "").strip()[:120]
    if not suggestion:
        return
    try:
        conn = _conn()
        conn.execute(
            "INSERT INTO procedural_memory (novel_id, session_id, suggestion_type, suggestion, feedback, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (novel_id or 0, session_id or "", suggestion_type or "generic", suggestion, feedback, time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[procedural_memory] 记录失败: {e}")


def _rows(novel_id: int | None, limit: int) -> List[tuple]:
    conn = _conn()
    try:
        if novel_id:
            rows = conn.execute(
                "SELECT suggestion_type, suggestion, feedback FROM procedural_memory "
                "WHERE novel_id = ? ORDER BY id DESC LIMIT ?", (novel_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT suggestion_type, suggestion, feedback FROM procedural_memory "
                "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return rows
    finally:
        conn.close()


def preference_summary(novel_id: int | None = None, limit: int = 40) -> str:
    """聚合最近反馈为作者偏好摘要（注入 LLM 推荐 prompt 用）：
    返回形如「采纳：inspiration·甜宠走向×2；拒绝：inspiration·虐心结局」；无反馈返回 ''。
    novel_id 为空时取全局偏好（首页闲聊场景）。
    """
    try:
        rows = _rows(novel_id, limit)
    except Exception as e:
        print(f"[procedural_memory] 读取失败: {e}")
        return ""
    if not rows:
        return ""
    accept: Counter = Counter()
    reject: Counter = Counter()
    for t, s, fb in rows:
        key = f"{t}·{s}"
        if fb == "accept":
            accept[key] += 1
        elif fb == "reject":
            reject[key] += 1
    parts = [f"采纳:{k}×{n}" for k, n in accept.most_common(5)]
    parts += [f"拒绝:{k}×{n}" for k, n in reject.most_common(5)]
    return "；".join(parts) if parts else ""
