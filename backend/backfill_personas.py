"""人设补全脚本（一次性数据修复）：从会话历史重抽 persona，合并进知识图谱。

背景：旧版 ENTITY_EXTRACT_PROMPT 抽取字段与前端人设卡片不对齐（缺 年龄/经历/创伤/动机），
且部分历史抽取质量差（只抽到"事件"）。本脚本对 novels 表里绑定了会话的书，
取会话全部用户消息按新 prompt 重抽人物属性，add_persona 键级合并（不丢已有、不重复建关系）。

用法：.venv/bin/python backfill_personas.py [novel_id]
不传 novel_id 则处理所有绑定了会话的书。
"""
import os
import sys
import json
import sqlite3

sys.path.insert(0, os.path.dirname(__file__))

from app.core.graph_store import get_graph_for
from app.core.llm_client import chat
from app.core.novel_store import novel_store
from app.nodes.build_nodes import ENTITY_EXTRACT_PROMPT, _extract_json_array


def refresh_personas(novel_id: int) -> dict:
    """对一本书：取绑定会话的历史 → LLM 重抽人物属性 → 合并进图谱"""
    conn = novel_store._get_conn()
    row = conn.execute("SELECT title, session_id FROM novels WHERE id = ?", (novel_id,)).fetchone()
    conn.close()
    if not row or not row["session_id"]:
        return {"novel_id": novel_id, "skipped": "未绑定会话，跳过"}

    # 取该会话全部用户消息（含已同步的，重抽不依赖同步标记）
    conn2 = sqlite3.connect(os.environ.get("CHAT_HISTORY_DB", "data/chat_history.db"))
    rows = conn2.execute(
        "SELECT content FROM chat_history WHERE scope='home' AND session_id=? AND role='user' ORDER BY id",
        (row["session_id"],),
    ).fetchall()
    conn2.close()
    all_text = "\n\n".join(r[0] for r in rows if (r[0] or "").strip())
    if len(all_text) < 10:
        return {"novel_id": novel_id, "skipped": "会话无内容，跳过"}

    # 分块喂给 LLM（每块 ~1500 字，避免超长）
    text = all_text
    g = get_graph_for(novel_id)
    updated = 0
    while len(text) > 0 and updated < 12:
        chunk = text[:1500]
        text = text[1500:]
        try:
            out = chat(ENTITY_EXTRACT_PROMPT, f"片段：\n{chunk}\n\n请抽取人物及属性。",
                       temperature=0.2, max_tokens=700, task="extract")
            entities = _extract_json_array(out)
        except Exception as e:
            print(f"[backfill] novel={novel_id} 抽取失败: {e}")
            continue
        for e in entities:
            name = (e.get("name") or "").strip()
            attrs = e.get("attributes") or {}
            if not name or not isinstance(attrs, dict) or not attrs:
                continue
            g.add_persona(name, attrs)
            updated += 1
    g.save()
    return {"novel_id": novel_id, "title": row["title"], "updated_personas": updated}


if __name__ == "__main__":
    target = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else None
    if target:
        print(json.dumps(refresh_personas(target), ensure_ascii=False))
    else:
        conn = novel_store._get_conn()
        books = conn.execute(
            "SELECT id, title, session_id FROM novels WHERE session_id != '' ORDER BY id"
        ).fetchall()
        conn.close()
        for b in books:
            print(json.dumps(refresh_personas(b["id"]), ensure_ascii=False))
    print("完成。")
