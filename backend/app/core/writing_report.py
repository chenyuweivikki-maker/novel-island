"""
写作数据面板 — PRD Tool 7: generate_writing_report

汇总本书的字数趋势、创作时段、高频词、人物出场统计等数据报告。
纯代码聚合（不调 LLM），结果可缓存。

来源：
  - 章节表（novels.db）：字数趋势、章节节奏
  - 图谱（graph）：人物清单，统计在章节正文中的出场次数
"""
import re
import sqlite3
import os
from collections import Counter
from typing import Dict, Any

from .novel_store import novel_store
from .graph_store import graph_manager

# 常见停用词（双字组合的过滤白名单之外的常见虚词）
STOPWORDS = {
    "一个", "这个", "那个", "什么", "自己", "没有", "就是", "知道", "时候",
    "他们", "我们", "你们", "现在", "已经", "还是", "不是", "这么", "那么",
    "因为", "所以", "但是", "如果", "可以", "起来", "出来", "下去", "过来",
    "这样", "那样", "怎么", "有点", "一下", "一样", "突然", "然后", "最后",
    "开始", "觉得", "说道", "看着", "听到", "看到", "走到", "回到", "来到",
    "好像", "似乎", "仿佛", "几乎", "真的", "也许", "难道", "居然", "竟然",
    "终于", "终于", "于是", "接着", "跟着", "想要", "打算", "决定", "可能",
}


def _get_conn(db_path: str = "data/novels.db") -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _bigram_freq(text: str) -> Counter:
    """双字词频（简单中文分词替代：统计高频双字组合）"""
    cleaned = re.sub(r"[^\u4e00-\u9fa5]", "", text)
    if len(cleaned) < 2:
        return Counter()
    return Counter(cleaned[i:i + 2] for i in range(len(cleaned) - 1))


def generate_writing_report(novel_id: int) -> Dict[str, Any]:
    """生成写作数据报告：字数趋势 / 章节节奏 / 高频词 / 人物出场 / 创作时段"""
    chapters = novel_store.list_chapters(novel_id)
    full_texts = []
    chapter_stats = []
    total_words = 0

    for i, c in enumerate(chapters):
        full = novel_store.get_chapter(c["id"])
        body = (full or {}).get("content", "")
        wc = len(re.sub(r"\s", "", body))
        total_words += wc
        full_texts.append(body)
        chapter_stats.append({
            "chapter_id": c["id"],
            "title": c.get("title", "") or f"第{c['id']}章",
            "words": wc,
            "order": i + 1,
        })

    all_text = "\n".join(full_texts)

    # 1. 高频词（双字，过滤停用词）
    freq = _bigram_freq(all_text)
    top_words = [
        {"word": w, "count": n}
        for w, n in freq.most_common(60)
        if w not in STOPWORDS and not w.isdigit()
    ][:20]

    # 2. 人物出场统计（图谱实体在正文出现次数）
    g = graph_manager.get(novel_id) if hasattr(graph_manager, "get") else None
    from .graph_store import get_graph_for
    g = get_graph_for(novel_id)
    char_mentions = []
    if g is not None:
        for name in g.all_entities():
            if len(name) < 2 or len(name) > 8:
                continue
            count = all_text.count(name)
            if count > 0:
                char_mentions.append({"name": name, "mentions": count})
        char_mentions.sort(key=lambda x: -x["mentions"])

    # 3. 创作时段（按保存时间的小时分布）
    hour_counts = Counter()
    for c in chapters:
        import time as _t
        local = _t.localtime(c["created_at"])
        hour_counts[local.tm_hour] += 1
    hour_dist = [{"hour": h, "count": hour_counts.get(h, 0)} for h in range(24)]

    # 4. 节奏：平均每章字数、最长/最短章节
    avg_words = round(total_words / len(chapter_stats)) if chapter_stats else 0
    longest = max(chapter_stats, key=lambda x: x["words"]) if chapter_stats else None
    shortest = min(chapter_stats, key=lambda x: x["words"]) if chapter_stats else None

    return {
        "novel_id": novel_id,
        "total_chapters": len(chapter_stats),
        "total_words": total_words,
        "avg_words_per_chapter": avg_words,
        "chapters": chapter_stats,
        "top_words": top_words,
        "character_mentions": char_mentions,
        "hour_distribution": hour_dist,
        "longest_chapter": longest,
        "shortest_chapter": shortest,
    }
