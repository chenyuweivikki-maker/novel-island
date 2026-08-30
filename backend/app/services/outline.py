"""全文大纲（P9）相关：上下文构建 + 解析 + 生成 prompt。

从 main.py 拆分（架构瘦身）：大纲的"从项目知识拼上下文""LLM 输出解析""旧文本兼容解析"
都收进本模块，main.py 只保留生成/读写路由。
"""
import json

from ..core.graph_store import get_graph_for
from ..core.novel_store import novel_store
from ..core.llm_client import chat


OUTLINE_GEN_PROMPT = """你是「小说岛」的大纲助手。根据作者目前积累的创作资料，为这本书生成一个结构化的全文大纲。

创作资料：
{context}

只输出 JSON，包含 5 个字段（每字段 1-3 句话；资料不足时给合理的方向性描述，不要编造资料里没有的具体人名/事件）：
{{"logline":"一句话梗概（故事核）","theme":"主题立意","plot":"主线脉络·分卷结构","conflict":"核心冲突与转折","ending":"结局设定"}}

只输出 JSON。"""


def parse_outline_sections(raw: str) -> dict:
    """把存储的大纲解析成分块 dict。新数据为 JSON；旧纯文本兜底到 plot 分块，避免丢内容。"""
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {"plot": raw}  # 旧版单文本块 → 归入「主线」


def build_outline_context(novel_id: int) -> str:
    """从项目知识（人物/事件/章纲/背景）拼一段紧凑文本，供 LLM 生成大纲"""
    parts = []
    try:
        g = get_graph_for(novel_id)
        entities = g.all_entities()
        if entities:
            lines = ["【人物设定】"]
            for name in entities:
                node = g.get_entity(name)
                persona = (node or {}).get("persona", {})
                attrs = (node or {}).get("attributes", {})
                info = "；".join(f"{k}:{v}" for k, v in list(persona.items()) + list(attrs.items()) if v)
                lines.append(f"- {name}：{info[:200]}")
            parts.append("\n".join(lines))
        evts = g.get_timeline()
        if evts:
            parts.append("【事件时间线】\n" + "\n".join(f"- {e.get('summary', '')[:80]}" for e in evts[-12:]))
    except Exception as e:
        print(f"[outline] 图谱上下文失败: {e}")
    try:
        chs = novel_store.list_chapters(novel_id)
        ol = [c for c in chs if c.get("outline")]
        if ol:
            parts.append("【章纲】\n" + "\n".join(f"- {c.get('title', '')}：{(c.get('outline') or '')[:120]}" for c in ol[-8:]))
        bgs = novel_store.list_backgrounds(novel_id)
        if bgs:
            parts.append("【背景资料】\n" + "\n".join(f"- {b.get('title', '')}：{(b.get('content') or '')[:120]}" for b in bgs[:8]))
    except Exception as e:
        print(f"[outline] 章节/背景上下文失败: {e}")
    return "\n\n".join(parts).strip() or "（还没有积累创作资料）"


def parse_outline_json(text: str) -> dict:
    """解析 LLM 输出为 5 字段 dict，容错"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        d = json.loads(t)
        if isinstance(d, dict):
            keys = ["logline", "theme", "plot", "conflict", "ending"]
            return {k: (d.get(k) or "").strip() for k in keys}
    except Exception:
        pass
    return {}


# ===== 大纲一致性校验（P3-6 OutlineConsistencyNode）=====
OUTLINE_CONSISTENCY_PROMPT = """你是「小说岛」的大纲一致性检查员。核对"全文大纲"与"项目已积累的设定/事件"是否一致。

全文大纲：
{outline}

项目设定（人物/事件/关系/章纲）：
{context}

请检查：
1. 大纲里提到的人物/关键设定，是否在项目设定里有依据（有没有"凭空出现"的人或事）
2. 大纲的主线/冲突/结局，是否与已有事件、人物关系矛盾
3. 大纲方向是否与项目已积累的设定明显偏离

输出严格的 JSON 数组：
[{{"level": "high|medium|low", "issue": "问题描述", "suggestion": "建议"}}]
没有发现问题就输出 []。只输出 JSON，不要其他文字。"""


def outline_consistency_check(novel_id: int) -> dict:
    """校验全文大纲与项目记忆（人物/事件/关系）的一致性，返回问题列表。"""
    raw_outline = novel_store.get_novel_outline(novel_id)
    if not raw_outline:
        return {"checked": False, "issues": [], "note": "还没有大纲，先保存或生成大纲。"}
    outline = parse_outline_sections(raw_outline)
    outline_text = "；".join(f"{k}:{v}" for k, v in outline.items() if v) or raw_outline
    context = build_outline_context(novel_id) or "（项目还没有积累设定）"
    prompt = OUTLINE_CONSISTENCY_PROMPT.format(outline=outline_text[:1500], context=context[:2500])
    try:
        raw = chat(prompt, "请检查大纲一致性。", temperature=0.0, max_tokens=500, task="logic")
    except Exception as e:
        return {"checked": False, "issues": [], "note": f"一致性检查失败：{e}"}
    t = (raw or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.startswith("json"):
            t = t[4:]
    try:
        issues = json.loads(t)
        if isinstance(issues, list):
            return {"checked": True, "issues": issues, "note": ""}
    except Exception as e:
        print(f"[outline] 一致性解析失败: {e}")
    return {"checked": True, "issues": [], "note": "一致性检查未发现问题。"}
