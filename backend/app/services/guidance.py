"""创作/建书意图判断 + 空库引导回复。

从 main.py 拆分（架构瘦身）：main.py 只保留路由编排，把
  - 创作型提问判断（写/创作/描写意图）
  - 建书意图判断（"开新书"关键词）
  - 空库 / 未建书时的 LLM 引导建库回复（含 SSE 流式）
都收进本模块，避免 main.py 越写越胖。
"""
import json

from fastapi.responses import StreamingResponse

from ..core.graph_store import get_graph_for
from ..core.memory import memory_manager
from ..core.llm_client import chat, chat_stream
from .kb import next_guide_question
from .title_sync import _sync_project_chat_to_kb


# ===== 建书意图关键词 =====
NEW_BOOK_KEYWORDS = ["创建一本新书", "创建新书", "新建小说", "开新书", "开坑", "开始创作", "一本新书", "写一本新书"]

# ===== 创作型动词：命中 = 「创作/续写/描写」意图，不走精确属性检索短路 =====
CREATIVE_VERBS = [
    "写一段", "写一句", "写个", "写一个", "写写", "帮我写", "续写", "描写", "描述一下",
    "生成", "创作", "编一段", "编一个", "来一段", "发挥一下", "写个片段", "写个段落",
    "扩写", "改写", "润色", "假如", "如果", "想象", "写一写", "试写",
]


def is_creative_query(query: str) -> bool:
    """创作型提问：要求 LLM 生成内容，而非查询既有设定"""
    return any(v in query for v in CREATIVE_VERBS)


def is_new_book_intent(query: str) -> bool:
    """作者说"开新书/创建新书"等 → 建书意图"""
    return any(kw in query for kw in NEW_BOOK_KEYWORDS)


# ===== 空库对话：LLM 引导建库（带图谱进度上下文，不硬编码）=====
EMPTY_KB_SYSTEM_PROMPT = """你是「小说岛」的写作搭子「小说猫」，正陪作者给一本书建知识库。

当前这本书的建库进度：
- 已有人物：{entities}
- 已有关系：{relations}
- 已有事件：{events}

你的任务：根据作者刚说的话，自然地推进建库流程：
1. 作者在补人设/设定 → 回应并接住，存入知识的意图，问下一个必要信息
2. 作者问问题 → 如果知识库还是空的，说明"这本书的库还在建，先把主角/关键设定告诉我，或把素材拖进来"
3. 按进度引导：主角（没有人）→ 配角与关系（有主角没配角）→ 大纲方向（有角色没事件）→ 可以开写了
4. 语气自然简短（1-3 句），像朋友聊天，不要列清单，不要机械

之前对话：
{history}"""


def _empty_kb_context(novel_id, session_id):
    """拼空库引导用的图谱进度 + 记忆上下文"""
    g = get_graph_for(novel_id)
    entities = ", ".join(g.all_entities()[:8]) if g else "（无）"
    rels = ", ".join(f"{r.get('source')}-{r.get('relation')}->{r.get('target')}"
                     for r in (g.all_relations() if g else [])[:8]) or "（无）"
    evts = ", ".join(e.get("summary", "")[:20] for e in (g.get_timeline() if g else [])[-5:]) or "（无）"
    memory = memory_manager.get_memory(novel_id, session_id)
    history = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in memory.get_context()[-6:]
    ) or "（无）"
    return entities or "（无）", rels, evts, history


def llm_empty_kb_reply(query: str, novel_id: int | None, session_id: str = "default") -> str:
    """空库对话走 LLM（图谱进度 + 记忆上下文）"""
    entities, rels, evts, history = _empty_kb_context(novel_id, session_id)
    prompt = EMPTY_KB_SYSTEM_PROMPT.format(entities=entities, relations=rels, events=evts, history=history)
    try:
        reply = chat(prompt, f"作者说：{query}", temperature=0.8, max_tokens=300, task="companion").strip()
    except Exception as e:
        print(f"[ask] 空库 LLM 引导失败: {e}")
        reply = next_guide_question(novel_id)  # 降级：回到规则引导
    memory_manager.get_memory(novel_id, session_id).add_turn(query, reply)
    return reply


def empty_kb_stream_or_dict(req):
    """空库分支：req.stream=True 时 SSE 流式（打字机），否则 dict。"""
    if not req.stream:
        answer = llm_empty_kb_reply(req.query, req.novel_id, req.session_id)
        # 创作页对话 → 增量入库当前项目（与首页路径一致）
        _sync_project_chat_to_kb(req.novel_id, req.session_id)
        return {"answer": answer, "sources": []}
    entities, rels, evts, history = _empty_kb_context(req.novel_id, req.session_id)
    system_prompt = EMPTY_KB_SYSTEM_PROMPT.format(entities=entities, relations=rels, events=evts, history=history)
    user_prompt = f"作者说：{req.query}"

    def generate():
        full = ""
        try:
            for token in chat_stream(system_prompt, user_prompt,
                                      temperature=req.temperature if req.temperature is not None else 0.8,
                                      max_tokens=300, task="companion", model=req.model or None):
                full += token
                yield "data: " + json.dumps({"type": "token", "data": token}, ensure_ascii=False) + "\n\n"
        except Exception as e:
            print(f"[ask] 空库流式引导失败: {e}")
            fallback = next_guide_question(req.novel_id)
            if not full:
                yield "data: " + json.dumps({"type": "token", "data": fallback}, ensure_ascii=False) + "\n\n"
                full = fallback
        if full:
            memory_manager.get_memory(req.novel_id, req.session_id).add_turn(req.query, full)
        # 创作页对话 → 增量入库当前项目（与首页路径一致）
        _sync_project_chat_to_kb(req.novel_id, req.session_id)
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
