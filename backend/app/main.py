"""
FastAPI 入口 — 小说岛后端 API

接口：
  POST /api/kb/build    — 构建知识库（接收文本，分块+建索引）
  POST /api/kb/ask      — 提问（检索+LLM生成）
  GET  /api/kb/status   — 知识库状态
  POST /api/kb/extract  — 提取人设卡片（Phase 2 预留）
  GET  /api/health      — 健康检查
"""
import json
import os
import re
import time
from fastapi import FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core.config import settings
from .core.chunker import clean_text, chunk_text
from .core.retriever import get_retriever_for, retriever, retriever_manager
from .core.llm_client import chat, chat_stream, RAG_SYSTEM_PROMPT, build_rag_prompt
from .core.memory import memory_manager
from .core.model_router import get_cost_summary, clear_cost_logs
from .core.graph_store import graph, graph_manager, get_graph_for
from .core.novel_store import novel_store
from .core.inspiration_store import inspiration_store
from .core.community_store import community_store
from .core.semantic_cache import semantic_cache
from .core.procedural_memory import record_feedback, preference_summary
from .core.tracking import tracking, new_session_id
from .core.fallback_templates import fallback_inspiration
from .core.writing_report import generate_writing_report
from .core.doc_parser import parse_text_file
from .core.hybrid_retriever import hybrid_search
from .core.hybrid_retriever import precise_attribute_search
from .core.vector_store import vector_store, vector_store_manager
from .tools.consistency_tools import check_plot_consistency
from .graphs.qa_graph import qa_app
from .graphs.build_graph import build_app
from .nodes.build_nodes import CHAPTER_OUTLINE_PROMPT, parse_outline_json
from .nodes.qa_nodes import IntentRouterNode, CompanionNode
# 业务服务（分类管理：路由编排留在本文件，业务逻辑按域拆在 app/services/）
from .services.kb import ingest_material, next_guide_question
from .services.auto_book import (
    _AUTO_BOOKED, _auto_create_book, _home_session_booked, _auto_create_book_from_material,
    _try_create_book_from_title,
)
from .services.title_sync import _maybe_sync_book_title, _sync_project_chat_to_kb
from .services.chat import (
    _no_project_stream_or_dict, _llm_no_project_reply, _llm_no_project_inspiration,
    _llm_no_project_critic, _llm_no_project_booked_reply,
)

app = FastAPI(title="小说岛 API", version="0.1.0")


@app.middleware("http")
async def no_cache_html(request, call_next):
    """开发期：HTML + 静态资源（JS/CSS）禁用缓存。
    HTML 已 no-store；JS/CSS 若不处理，浏览器会吃旧版（etag 需重验证、无 cache-control
    时还有启发式缓存窗口），导致用户"开着新网站跑着旧代码"——会话列表不刷新等怪问题。
    """
    resp = await call_next(request)
    path = request.url.path
    if path.endswith((".html", ".js", ".css")) or path in ("/",) or path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp

# 启动时加载已持久化的图谱
if len(graph) == 0:
    graph.load()
# 里程碑9：启动时加载向量库
vector_store.load()


def restore_tfidf_retrievers() -> None:
    """启动时从持久化向量库重建各项目 TF-IDF 检索器

    修复产品级缺陷：TF-IDF 索引纯内存，后端重启后问答会退化成"空库引导"。
    向量库（data/vector_*.npz）持久化了全部文本块，用它们重建索引即可恢复。
    """
    import glob
    import re as _re
    from .core.chunker import Chunk

    def rebuild(store, r):
        if store.is_ready:
            chunks = [Chunk(id=i, text=t, char_count=len(t)) for i, t in enumerate(store.texts)]
            r.build_index(chunks)

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    # 全局库（data/vector_store.npz）
    rebuild(vector_store, retriever)
    # 各项目库（data/vector_{n}.npz）
    for path in glob.glob(os.path.join(data_dir, "vector_*.npz")):
        m = _re.match(r".*vector_(\d+)\.npz$", path)
        if not m:
            continue
        nid = int(m.group(1))
        store = vector_store_manager.get_store(nid)
        rebuild(store, retriever_manager.get_retriever(nid))
        print(f"[restore] 重建 TF-IDF 检索器 novel_id={nid} chunks={len(store.texts)}")


restore_tfidf_retrievers()

# CORS — 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 托管前端静态文件 — 单端口，无跨域问题
def _resolve_frontend_dir() -> str:
    """解析前端静态目录：环境变量 FRONTEND_DIR 优先，否则按常见布局探测。

    本地开发：backend/app/main.py → ../../frontend/public = 项目根/frontend/public ✅
    Docker 单容器：Dockerfile COPY frontend /app/frontend → /app/frontend/public ✅
    （旧写法 ../../frontend/public 在容器内会解析到 /frontend/public，首页 404）
    """
    candidates: list[str] = []
    env_dir = os.environ.get("FRONTEND_DIR", "").strip()
    if env_dir:
        candidates.append(env_dir)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates += [
        os.path.join(here, "..", "..", "frontend", "public"),  # 本地开发
        os.path.join("/app", "frontend", "public"),            # Docker 单容器（/app/app/main.py）
        os.path.join(here, "..", "frontend", "public"),        # 兜底
    ]
    for c in candidates:
        p = os.path.abspath(c)
        if os.path.isdir(p):
            return p
    # 全部探测失败时返回第一个候选（serve_index 会给出 404 提示）
    return os.path.abspath(candidates[0])


FRONTEND_DIR = _resolve_frontend_dir()
if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


# ===== 请求模型 =====
class BuildRequest(BaseModel):
    text: str
    chunk_size: int = 400
    overlap: int = 60
    mode: str = "init"  # init=全量建库, update=增量更新（里程碑9）
    novel_id: int | None = None  # 里程碑11：多项目隔离，None=默认知识库


class AskRequest(BaseModel):
    query: str
    top_k: int = 5
    stream: bool = False
    novel_id: int | None = None  # 里程碑11：按项目检索/问答
    material: str | None = None  # 对话式建库：随消息拖入/粘贴的素材文本（Agent 解析入库）
    session_id: str = "default"  # 前端稳定会话标识（localStorage 持久化；区分多组对话便于对比）
    model: str = ""              # Agent 设置：模型覆盖（空=按任务自动路由）
    complex_model: str = ""      # Agent 设置：高阶推理模型覆盖（逻辑批判等，空=默认 kimi）
    temperature: float | None = None  # Agent 设置：温度覆盖（None=默认）
    persona: str = ""            # Agent 设置：人设/语气附加说明（拼进 system prompt）


# ===== 创作空间请求模型（里程碑10）=====
class NovelCreateRequest(BaseModel):
    title: str
    expected_words: int = 0  # 里程碑18：预计总字数
    chapter_words: int = 0   # 里程碑18：每章字数
    genre: str = ""          # 题材（我的作品页分类联动：留空=未分类）


class NovelRenameRequest(BaseModel):
    title: str


class NovelReorderRequest(BaseModel):
    ordered_ids: list[int]  # 拖拽后的新顺序（里程碑17）


class OutlineSaveRequest(BaseModel):
    content: str  # 大纲内容（里程碑18）


class BackgroundAddRequest(BaseModel):
    category: str  # 分类（如"世界观"/"人物原型"）
    title: str = ""
    content: str


class ChapterSaveRequest(BaseModel):
    novel_id: int
    content: str
    title: str = ""
    chapter_id: int | None = None  # 有则更新该章（先删旧），无则新增（里程碑10）
    # 里程碑11：novel_id 已存在，图谱/向量按 novel_id 隔离


# ===== 灵感库请求模型（UI 定稿 P12）=====
class InspirationAddRequest(BaseModel):
    novel_id: int
    content: str
    category: str = ""  # 空 → LLM 自动分类
    source: str = "text"


class InspirationCategoryRequest(BaseModel):
    insp_id: int
    category: str


class InspCategoryAddRequest(BaseModel):
    novel_id: int
    name: str


class InspCategoryRenameRequest(BaseModel):
    novel_id: int
    old_name: str
    new_name: str


class InspCategoryMoveRequest(BaseModel):
    novel_id: int
    name: str
    direction: str  # up | down


# ===== 社区请求模型（作者互动：帖子/点赞/收藏/评论）=====
class PostCreateRequest(BaseModel):
    title: str
    content: str
    post_type: str = "post"        # post=分享 / inspiration=灵感分享 / idea=使用心得 / question=提问
    novel_id: int = 0              # 关联作品（0=通用）
    inspiration_id: int = 0        # 来源灵感（灵感分享时）


class CommentCreateRequest(BaseModel):
    post_id: int
    content: str


# ===== 润色请求模型（P5 润色 Review 弹窗）=====
class PolishRequest(BaseModel):
    text: str
    style: str = "保持作者原有风格，轻度润色"  # 目标风格描述
    intensity: float = 0.5  # 0=保守 1=大胆


class MemoryFeedbackRequest(BaseModel):
    """程序性记忆：用户对 AI 建议的反馈（采纳/拒绝）"""
    novel_id: int | None = None
    session_id: str = ""
    suggestion_type: str = "generic"  # suggestion / inspiration / polish
    suggestion: str = ""
    feedback: str = "accept"          # accept / reject


# ===== 写作后分析请求模型（P3-1 DataAnalyst）=====
class AnalysisReportRequest(BaseModel):
    novel_id: int


# ===== 接口 =====

@app.get("/api/health")
def health():
    return {"status": "ok", "model": settings.DEEPSEEK_MODEL}


@app.post("/api/kb/build")
def build_kb(req: BuildRequest):
    """构建知识库：走状态机（清洗分块 → 并行实体/事件抽取 → 汇总建索引）

    里程碑9：支持增量更新 —— mode=update 时只向量化新分块追加，不重建旧库。
    里程碑11：按 novel_id 隔离向量库/图谱；None 时回退全局单例（老行为）。
    """
    # 把输入放进 State（背包），交给建库状态机跑（novel_id 穿透到图谱节点）
    result = build_app.invoke({
        "raw_input_files": [req.text],
        "novel_id": req.novel_id,
    })

    output = result["final_output"]
    chunks = [
        {"id": c["id"], "text": c["text"], "char_count": c["char_count"]}
        for c in result["processed_chunks"]
    ]

    # 里程碑11：按项目取向量库（None → 全局单例，保持老行为）
    vs = vector_store_manager.get_store(req.novel_id) if req.novel_id is not None else vector_store

    # 里程碑9：向量化 + 增量更新
    if req.mode == "update":
        # 增量：只把新分块向量化追加（vs.add_chunks 内部只处理新文本）
        vs.add_chunks(
            [c["text"] for c in chunks],
            [{"chunk_id": c["id"]} for c in chunks],
        )
    else:
        # 全量：清空重建
        vs.texts.clear()
        vs.vectors.clear()
        vs.metadata.clear()
        vs.add_chunks(
            [c["text"] for c in chunks],
            [{"chunk_id": c["id"]} for c in chunks],
        )
    vs.save()
    # 同步重建 TF-IDF（否则问答检索不到刚入库的内容）
    from .services.kb import _rebuild_tfidf
    _rebuild_tfidf(req.novel_id)

    return {
        "success": True,
        "stats": {
            "chunks": output["chunks"],
            "total_chars": output["total_chars"],
            "indexed": True,
            "vector_indexed": len(vs),
            "entities": output["entities"],
            "events": output["events"],
        },
        "chunks": chunks,
        # 路线图P1-6：图谱一致性校验报告（新建库时图谱为空，checked=False）
        "consistency": result.get("consistency_report", {"conflicts": [], "checked": False}),
    }


@app.get("/api/kb/status")
def kb_status(novel_id: int | None = None):
    """知识库状态（里程碑17：可按项目查）"""
    r = get_retriever_for(novel_id)
    return {
        "ready": r.is_ready,
        "chunks": len(r.chunks),
    }


# 注意：关键词要精确，不能宽泛到「写一本」「新书」——否则「想写一本都市文」会被误判成建书意图
NEW_BOOK_KEYWORDS = ["创建一本新书", "创建新书", "新建小说", "开新书", "开坑", "开始创作", "一本新书", "写一本新书"]
# 题材提示词：无项目时用户已给出题材信息 → 直接引导建书（而不是反复问）
GENRE_HINTS = ["都市", "奇幻", "悬疑", "古风", "科幻", "言情", "武侠", "末世", "穿越", "电竞", "校园", "职场", "修仙", "刑侦", "年代", "重生", "娱乐圈"]

# 创作型动词：命中即视为「创作/续写/描写」意图，不走精确属性检索短路
# （否则"写一段她在宴会上看到江观南的内心活动"会被属性检索截胡成设定文本）
CREATIVE_VERBS = [
    "写一段", "写一句", "写个", "写一个", "写写", "帮我写", "续写", "描写", "描述一下",
    "生成", "创作", "编一段", "编一个", "来一段", "发挥一下", "写个片段", "写个段落",
    "扩写", "改写", "润色", "假如", "如果", "想象", "写一写", "试写",
]


def _is_creative_query(query: str) -> bool:
    """创作型提问：要求 LLM 生成内容，而非查询既有设定"""
    return any(v in query for v in CREATIVE_VERBS)


def _is_new_book_intent(query: str) -> bool:
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


def _llm_empty_kb_reply(query: str, novel_id: int | None, session_id: str = "default") -> str:
    """空库对话走 LLM（图谱进度 + 记忆上下文）"""
    g = get_graph_for(novel_id)
    entities = ", ".join(g.all_entities()[:8]) if g else "（无）"
    rels = ", ".join(f"{r.get('source')}-{r.get('relation')}->{r.get('target')}" for r in (g.all_relations() if g else [])[:8]) or "（无）"
    evts = ", ".join(e.get("summary", "")[:20] for e in (g.get_timeline() if g else [])[-5:]) or "（无）"
    memory = memory_manager.get_memory(novel_id, session_id)
    history = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in memory.get_context()[-6:]
    ) or "（无）"
    prompt = EMPTY_KB_SYSTEM_PROMPT.format(entities=entities or "（无）", relations=rels, events=evts, history=history)
    try:
        reply = chat(prompt, f"作者说：{query}", temperature=0.8, max_tokens=300, task="companion").strip()
    except Exception as e:
        print(f"[ask] 空库 LLM 引导失败: {e}")
        reply = next_guide_question(novel_id)  # 降级：回到规则引导
    memory.add_turn(query, reply)
    return reply


def _empty_kb_stream_or_dict(req):
    """空库分支：req.stream=True 时 SSE 流式（打字机），否则 dict。"""
    if not req.stream:
        answer = _llm_empty_kb_reply(req.query, req.novel_id, req.session_id)
        # 创作页对话 → 增量入库当前项目（与首页路径一致）
        _sync_project_chat_to_kb(req.novel_id, req.session_id)
        return {"answer": answer, "sources": []}
    g = get_graph_for(req.novel_id)
    entities = ", ".join(g.all_entities()[:8]) if g else "（无）"
    rels = ", ".join(f"{r.get('source')}-{r.get('relation')}->{r.get('target')}" for r in (g.all_relations() if g else [])[:8]) or "（无）"
    evts = ", ".join(e.get("summary", "")[:20] for e in (g.get_timeline() if g else [])[-5:]) or "（无）"
    memory = memory_manager.get_memory(req.novel_id, req.session_id)
    history = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in memory.get_context()[-6:]
    ) or "（无）"
    system_prompt = EMPTY_KB_SYSTEM_PROMPT.format(entities=entities or "（无）", relations=rels, events=evts, history=history)
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
            memory.add_turn(req.query, full)
        # 创作页对话 → 增量入库当前项目（与首页路径一致）
        _sync_project_chat_to_kb(req.novel_id, req.session_id)
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/kb/ask")
def ask(req: AskRequest):
    """提问：检索 Top-K → LLM 生成回答（含对话式建库：素材解析入库 + 空库引导）"""
    # ===== 对话式建库：素材解析入库 =====
    if req.material and req.material.strip():
        if req.novel_id is None:
            # 无项目收到素材：自动建书（书名取素材前几字/未命名）并入库素材
            if not _home_session_booked(req.session_id):
                _auto_create_book_from_material(req.material, req.session_id)
            _maybe_sync_book_title(req.session_id, req.query)
            return {"answer": _llm_no_project_reply(f"我拖了一段素材进来：{req.material[:80]}…", session_id=req.session_id), "sources": []}
        answer = ingest_material(req.material, req.novel_id)
        return {"answer": answer, "sources": []}

    # ===== 建书意图 =====
    if _is_new_book_intent(req.query):
        if req.novel_id is not None:
            # 已在作品里：不打断当前创作，LLM 引导侧栏新建
            return _no_project_stream_or_dict(req, _llm_no_project_reply, req.novel_id, brief=True)
        # 无项目：本轮已给出明确书名 → 立即建书（只带书名+题材，后续对话增量补设定）；
        # 没有明确书名 → 照常接住引导，不提前建项目。
        _try_create_book_from_title(req.session_id, req.query)
        return _no_project_stream_or_dict(req, _llm_no_project_reply)

    # ===== 无项目（首页/默认对话）：意图路由 + 各分支 LLM（建书主路由）=====
    if req.novel_id is None:
        intent = IntentRouterNode()({"user_query": req.query, "model": req.model})["current_intent"]
        # 情感低落 → 纯陪伴优先（即使提到题材也不机械引导）
        if intent == "companion":
            comp = CompanionNode()({"user_query": req.query, "retrieved_chunks": []})
            _maybe_sync_book_title(req.session_id, req.query)
            return {"answer": comp["agent_response"], "sources": []}
        # 灵感意图（无库也能给方向性建议，不机械引导建书）
        if intent == "inspiration":
            return _no_project_stream_or_dict(req, _llm_no_project_inspiration)
        # 逻辑批判 / 人设检查：没有知识库可查，引导先建书（不假装能检查）
        if intent in ("logic_critique", "character_critic"):
            return _no_project_stream_or_dict(req, _llm_no_project_critic)
        # 对话式自动建库：作者已聊出角色但本会话还没建书 → 自动开书并入库
        if not _home_session_booked(req.session_id):
            auto_nid = _auto_create_book(req.session_id, current_query=req.query)
            if auto_nid:
                # 建书成功：本次回复告知用户，后续轮次按项目走
                return _no_project_stream_or_dict(req, _llm_no_project_booked_reply)
        # 建书意图 / 事实问答 / 其他 → LLM 建书引导，接住作者的话
        return _no_project_stream_or_dict(req, _llm_no_project_reply)

    # 里程碑17：按项目取 TF-IDF 检索器
    r = get_retriever_for(req.novel_id)

    # ===== 空库：不硬报错，LLM 引导建库（对话式建库）=====
    if not r.is_ready:
        # 情感低落 → 纯陪伴（CompanionNode 空库路径）
        intent = IntentRouterNode()({"user_query": req.query})["current_intent"]
        if intent == "companion":
            comp = CompanionNode()({"user_query": req.query, "retrieved_chunks": []})
            return {"answer": comp["agent_response"], "sources": []}
        # 大段输入视为素材 → 解析入库
        if len(req.query) > 60:
            answer = ingest_material(req.query, req.novel_id)
            return {"answer": answer, "sources": []}
        # 否则 → LLM 引导（带图谱进度上下文，自然推进建库；支持流式）
        return _empty_kb_stream_or_dict(req)

    # 1. 检索（里程碑9：先精确属性检索，再混合检索；里程碑11：按项目）
    #    创作型提问（写/描写/生成等）不走精确属性短路——"写一段X的内心活动"
    #    含实体+属性词但意图是创作，命中图谱会直接返回设定文本而非创作（评测暴露的 bug）
    if not _is_creative_query(req.query):
        precise = precise_attribute_search(req.query, req.novel_id)
        if precise and not req.stream:
            return {
                "answer": precise["answer"],
                "sources": [],
                "retrieval": [],
                "precise": precise,
            }

    # 里程碑11：按项目取向量库
    vs = vector_store_manager.get_store(req.novel_id) if req.novel_id is not None else vector_store

    # 混合检索（向量 + TF-IDF）
    if vs.is_ready:
        vector_hits = vs.search(req.query, req.top_k)
        # 把向量结果转成和 retriever 兼容的格式（用 metadata 里的 chunk_id）
        results = []
        for hit in vector_hits:
            chunk_id = hit["metadata"].get("chunk_id", hit["index"])
            # 从 TF-IDF 库里找对应 chunk（保持原有 Chunk 结构）
            if 0 <= chunk_id < len(r.chunks):
                results.append({"chunk": r.chunks[chunk_id], "score": hit["score"], "index": chunk_id})
        # 向量没结果就回退 TF-IDF
        if not results:
            results = r.search(req.query, req.top_k)
    else:
        results = r.search(req.query, req.top_k)

    if not results:
        # 检索无结果：LLM 说明未找到并引导（不硬编码一句空话）
        try:
            g = get_graph_for(req.novel_id)
            entities = ", ".join(g.all_entities()[:8]) if g and g.all_entities() else "（还没有）"
            answer = chat(
                f"你是「小说岛」写作搭子。作者问了：{req.query}\n\n"
                f"当前这本书的知识库里已有人物：{entities}\n"
                "知识库中检索不到与该问题相关的内容。请用 1-2 句话说明暂时没找到相关信息，"
                "并引导作者：要么补全相关设定（直接说给我听，我会入库），要么换个问法。语气自然。",
                "请回应作者。", temperature=0.8, max_tokens=200, task="qa",
            ).strip()
        except Exception as e:
            print(f"[ask] 无结果 LLM 回复失败: {e}")
            answer = "在知识库中暂时没找到相关内容。可以把相关设定直接告诉我，我帮你整理入库。"
        return {"answer": answer, "sources": [], "retrieval": []}

    # 2. 构建prompt
    user_prompt = build_rag_prompt(req.query, results)

    # 3. 流式 or 非流式
    if req.stream:
        # 流式模式下精确属性命中 → 也走 SSE（整段作为单个 token，前端打字机统一处理）
        if precise:
            def generate_precise():
                yield "data: " + json.dumps({"type": "retrieval", "data": []}) + "\n\n"
                yield "data: " + json.dumps({"type": "token", "data": precise["answer"]}, ensure_ascii=False) + "\n\n"
                yield "data: " + json.dumps({"type": "done"}) + "\n\n"
            return StreamingResponse(generate_precise(), media_type="text/event-stream")

        def generate():
            # 先发检索结果
            retrieval_data = [
                {"chunk_id": r["chunk"].id, "score": round(r["score"], 4),
                 "preview": r["chunk"].text[:120]}
                for r in results
            ]
            yield "data: " + json.dumps({"type": "retrieval", "data": retrieval_data}, ensure_ascii=False) + "\n\n"
            # 再流式输出回答（收集全文，完成后写记忆 + 增量入库，与首页路径一致）
            full = ""
            try:
                for token in chat_stream(RAG_SYSTEM_PROMPT, user_prompt):
                    full += token
                    yield "data: " + json.dumps({"type": "token", "data": token}, ensure_ascii=False) + "\n\n"
            except Exception as e:
                print(f"[ask] 项目流式生成失败: {e}")
            if full:
                memory_manager.get_memory(req.novel_id, req.session_id).add_turn(req.query, full)
            # 创作页对话 → 增量入库当前项目
            _sync_project_chat_to_kb(req.novel_id, req.session_id)
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # 非流式 — 先查语义缓存（PRD：相似问题直接复用答案，省 LLM 成本）
    try:
        hit = semantic_cache.lookup(req.query, req.novel_id)
    except Exception as e:
        hit = None
        print(f"[semantic_cache] 查询失败: {e}")
    if hit:
        tracking.record("cache_hit", query=req.query[:60], similarity=hit.get("similarity"))
        return {
            "answer": hit["answer"],
            "sources": hit["sources"],
            "retrieval": [],
            "cached": True,
            "similarity": hit.get("similarity"),
        }

    # 非流式 — 走状态机（里程碑1）
    # 把请求参数放进 State（背包），让状态机跑完整个流程（novel_id 穿透）
    try:
        result = qa_app.invoke({
            "user_query": req.query,
            "top_k": req.top_k,
            "novel_id": req.novel_id,
            "session_id": req.session_id,
            "model": req.model,
            "complex_model": req.complex_model,
        })
    except Exception as e:
        # 降级策略（PRD）：LLM 链路故障时不硬报错
        print(f"[ask] 状态机执行失败: {e}")
        intent = IntentRouterNode()({"user_query": req.query})["current_intent"]
        if intent == "inspiration":
            return {"answer": fallback_inspiration(req.query), "sources": [], "degraded": True}
        return {"answer": "知识库服务暂时不可用，回答可能不准确。请稍后再试，或换个问法。", "sources": [], "degraded": True}

    # P2-6 节点独立化：短期记忆写入已由状态机内的 MemoryUpdateNode 完成
    # （add_turn → 内存 + SQLite 落库 + 摘要评估），此处不再重复写
    # 创作页对话 → 增量入库当前项目（与首页路径一致）
    _sync_project_chat_to_kb(req.novel_id, req.session_id)

    # 写入语义缓存（只缓存有检索依据的回答，降低幻觉扩散）
    if result["retrieved_chunks"]:
        try:
            semantic_cache.store(req.query, req.novel_id, result["agent_response"], result["sources"])
        except Exception as e:
            print(f"[semantic_cache] 写入失败: {e}")

    return {
        "answer": result["agent_response"],
        "sources": result["sources"],
        "usage": latest_usage(),
        "tools_used": result.get("tool_log") or [],
        "retrieval": [
            {
                "chunk_id": r["chunk"].id,
                "score": round(r["score"], 4),
                "text": r["chunk"].text,
                "preview": r["chunk"].text[:150],
            }
            for r in result["retrieved_chunks"]
        ],
    }


@app.post("/api/kb/retrieve")
def retrieve(query: str, top_k: int = 5, novel_id: int | None = None):
    """纯检索接口，不调用LLM（里程碑17：可按项目）"""
    r = get_retriever_for(novel_id)
    if not r.is_ready:
        return {"error": "知识库未构建"}
    results = r.search(query, top_k)
    return {
        "results": [
            {
                "chunk_id": r["chunk"].id,
                "score": round(r["score"], 4),
                "text": r["chunk"].text,
            }
            for r in results
        ]
    }


@app.get('/api/graph')
def graph_data(novel_id: int | None = None):
    """图谱查询接口（里程碑8+9+11）：返回全部实体（含人设属性）和关系，可按项目"""
    g = get_graph_for(novel_id)
    entities = []
    for name in g.all_entities():
        node = g.get_entity(name)
        entities.append({
            "name": name,
            "persona": node.get("persona", {}) if node else {},
        })
    return {
        "entities": entities,
        "relations": g.all_relations(),
        "total_entities": len(g),
    }


@app.post('/api/graph/neighbors')
def graph_neighbors(entity: str, novel_id: int | None = None):
    """查询某实体的直接关系（邻居），可按项目"""
    g = get_graph_for(novel_id)
    return {"entity": entity, "neighbors": g.query_neighbors(entity)}


@app.post('/api/graph/path')
def graph_path(start: str, end: str, novel_id: int | None = None):
    """查询两实体间的路径（多跳推理），可按项目"""
    g = get_graph_for(novel_id)
    return {"path": g.query_path(start, end)}


@app.get('/api/timeline')
def timeline(novel_id: int | None = None):
    """情节大事年表接口（里程碑15）：只返回来自正文（章节）的事件——
    对话里聊的剧情不生成时间线，只有保存章节后才会记录（按入库顺序返回每章情节摘要）。"""
    g = get_graph_for(novel_id)
    all_events = g.get_timeline()
    # 只保留打上章节标记的事件（save_chapter 增量入库时携带 chapter_id；对话入库的不带）
    timeline_data = [e for e in all_events if e.get("chapter_id") is not None]
    return {
        "timeline": timeline_data,
        "total": len(timeline_data),
    }


# ===== 创作空间 API（里程碑10）=====

@app.post("/api/novel")
def create_novel(req: NovelCreateRequest):
    """创建作品（里程碑18：支持预计总字数/每章字数；题材可选，联动「我的作品」分类）"""
    novel_id = novel_store.create_novel(req.title, req.expected_words, req.chapter_words, req.genre)
    return {"novel_id": novel_id, "title": req.title}


@app.get("/api/novels")
def list_novels():
    """列出所有作品（按 sort_order 排序）"""
    return {"novels": novel_store.list_novels()}


@app.get("/api/novels/genres")
def list_novel_genres():
    """「我的作品」页题材分类按钮的数据源：所有作品已用题材（去重）；没有任何题材时返回空列表，前端只剩「全部」"""
    return {"genres": novel_store.list_genres()}


@app.post("/api/novel/{novel_id}/rename")
def rename_novel(novel_id: int, req: NovelRenameRequest):
    """重命名作品（里程碑17）"""
    novel_store.update_novel_title(novel_id, req.title)
    return {"novel_id": novel_id, "title": req.title}


class NovelGenreRequest(BaseModel):
    genre: str = ""  # 题材；空串=清除题材（回到未分类）


@app.post("/api/novel/{novel_id}/genre")
def update_novel_genre(novel_id: int, req: NovelGenreRequest):
    """修改作品题材（「我的作品」页分类联动）"""
    novel_store.update_novel_genre(novel_id, req.genre)
    return {"novel_id": novel_id, "genre": req.genre}


class OutlineSaveRequest(BaseModel):
    outline: str = ""  # 全文大纲（单文本块，自动保存）


@app.get("/api/novel/{novel_id}/outline")
def get_novel_outline(novel_id: int):
    """读取作品全文大纲（P9）"""
    return {"novel_id": novel_id, "outline": novel_store.get_novel_outline(novel_id)}


@app.post("/api/novel/{novel_id}/outline")
def save_novel_outline(novel_id: int, req: OutlineSaveRequest):
    """保存作品全文大纲（P9，单文本块自动保存）"""
    novel_store.update_novel_outline(novel_id, req.outline.strip())
    return {"success": True, "novel_id": novel_id}


@app.post("/api/novels/reorder")
def reorder_novels(req: NovelReorderRequest):
    """拖拽排序：按新顺序重新分配 sort_order（里程碑17）"""
    novel_store.reorder_novels(req.ordered_ids)
    return {"success": True}


@app.post("/api/chapter")
def save_chapter(req: ChapterSaveRequest):
    """保存章节 + 触发增量更新（写作→知识库闭环）

    作者写完新章节保存后：
      1. 冲突检测（里程碑12）：先查旧内容，新章节有没有和前面矛盾
      2. 章节持久化到 SQLite
      3. 触发建库状态机（mode=update），只处理新章节增量更新
      4. 知识库/图谱/向量库都更新，作者能查新章节内容
    """
    # 里程碑11：按项目取图谱/向量库（novel_id 已由请求携带）
    g = get_graph_for(req.novel_id)
    vs = vector_store_manager.get_store(req.novel_id)

    # 里程碑12：入库前做冲突检测（此时新章节还没进库，检到的全是旧内容）
    # 注意：这是"提示"不是"拦截"——作者可以无视冲突继续保存
    try:
        conflicts = check_plot_consistency(req.content, novel_id=req.novel_id)
    except Exception as e:
        conflicts = []
        print(f"冲突检测失败: {e}")

    # 里程碑18+P2-3：生成结构化章纲（summary + 伏笔 + 预设），与冲突检测并行，用户无感
    foreshadowing_list, setup = [], ""
    try:
        raw_outline = chat(CHAPTER_OUTLINE_PROMPT, req.content, temperature=0.0, max_tokens=800, task="creative")
        outline, foreshadowing_list, setup = parse_outline_json(raw_outline)
    except Exception as e:
        outline = ""
        print(f"章纲生成失败: {e}")
    import json as _json
    foreshadowing_json = _json.dumps(foreshadowing_list, ensure_ascii=False)

    # 1. 保存章节（有 chapter_id 则更新，无则新增）；更新时章纲也重新生成覆盖
    if req.chapter_id:
        # 更新：先删旧数据（向量/图谱），再加新内容
        vs.remove_by_chapter(req.chapter_id)
        g.remove_by_chapter(req.chapter_id)
        novel_store.update_chapter(req.chapter_id, req.content, req.title, outline, foreshadowing_json, setup)
        chapter_id = req.chapter_id
    else:
        chapter_id = novel_store.add_chapter(req.novel_id, req.content, req.title, outline, foreshadowing_json, setup)

    # P2-3：伏笔登记入看板（去重；仅新增章节登记，更新章节不重复累计）
    if not req.chapter_id and foreshadowing_list:
        novel_store.add_foreshadowings(req.novel_id, chapter_id, foreshadowing_list)

    # 2. 增量更新知识库（build 状态机 + 向量），新内容打上章节标记
    try:
        result = build_app.invoke({
            "raw_input_files": [req.content],
            "novel_id": req.novel_id,
            "chapter_id": chapter_id,  # 里程碑15：年表摘要打上章节标记
        })
        chunks = [
            {"id": c["id"], "text": c["text"], "char_count": c["char_count"]}
            for c in result["processed_chunks"]
        ]
        vs.add_chunks(
            [c["text"] for c in chunks],
            [{"chunk_id": c["id"]} for c in chunks],
            chapter_id=chapter_id,
        )
        g.save()
        vs.save()
        updated = True
    except Exception as e:
        updated = False
        print(f"增量更新失败: {e}")

        g.save()
        vs.save()
        updated = True
        # 埋点：知识库增量更新
        tracking.record("knowledge_graph_update", update_type="incremental",
                        entities_added=len(result["final_output"].get("entities", [])),
                        consistency_violations=len((result.get("consistency_report") or {}).get("conflicts", [])))
    except Exception as e:
        updated = False
        print(f"增量更新失败: {e}")

    return {
        "chapter_id": chapter_id,
        "knowledge_updated": updated,
        "conflicts": conflicts,  # 里程碑12：情节冲突检测结果（空数组=无冲突）
        # 路线图P1-6：图谱五维一致性校验（与旧图谱对照，报告由作者决定是否修改）
        "graph_conflicts": (result.get("consistency_report") or {}).get("conflicts", []) if updated else [],
        "stats": {
            "chunks": len(result["processed_chunks"]) if updated else 0,
            "entities": result["final_output"]["entities"] if updated else [],
        },
        "outline": outline,  # 里程碑18：章纲
        "foreshadowing": foreshadowing_list,  # P2-3：本章伏笔
        "setup": setup,  # P2-3：本章预设
    }


@app.get("/api/novel/{novel_id}/chapters")
def list_novel_chapters(novel_id: int):
    """列出作品章节（含章纲 outline）"""
    return {"chapters": novel_store.list_chapters(novel_id)}


@app.get("/api/novel/{novel_id}/chapter_outlines")
def list_chapter_outlines(novel_id: int):
    """章纲列表（里程碑18：每章300字总结，按时间序）"""
    chapters = novel_store.list_chapters(novel_id)
    outlines = [
        {"chapter_id": c["id"], "title": c.get("title", ""), "outline": c.get("outline", "")}
        for c in chapters if c.get("outline")
    ]
    return {"outlines": outlines, "total": len(outlines)}


@app.get("/api/novel/{novel_id}/outline")
def get_outline(novel_id: int):
    """读取大纲（里程碑18：作者自写单文本块）"""
    return {"content": novel_store.get_novel_outline(novel_id)}


@app.post("/api/novel/{novel_id}/outline")
def save_outline(novel_id: int, req: OutlineSaveRequest):
    """保存大纲（里程碑18）"""
    novel_store.update_novel_outline(novel_id, req.content)
    return {"success": True}


@app.get("/api/novel/{novel_id}/backgrounds")
def list_backgrounds(novel_id: int):
    """背景资料列表（里程碑18：按分类分组返回）"""
    bgs = novel_store.list_backgrounds(novel_id)
    grouped: dict[str, list] = {}
    for bg in bgs:
        grouped.setdefault(bg["category"], []).append(bg)
    return {"backgrounds": grouped, "total": len(bgs)}


@app.post("/api/novel/{novel_id}/backgrounds")
def add_background(novel_id: int, req: BackgroundAddRequest):
    """添加背景资料（里程碑18）"""
    bg_id = novel_store.add_background(novel_id, req.category, req.title, req.content)
    return {"id": bg_id}


@app.delete("/api/background/{bg_id}")
def delete_background(bg_id: int):
    """删除背景资料（里程碑18）"""
    novel_store.delete_background(bg_id)
    return {"success": True}


@app.get("/api/chapter/{chapter_id}")
def get_chapter(chapter_id: int):
    """读取章节全文"""
    chapter = novel_store.get_chapter(chapter_id)
    if not chapter:
        return {"error": "章节不存在"}
    return chapter


@app.get('/api/cost')
def cost_summary():
    """成本查询接口（里程碑7）"""
    return get_cost_summary()


@app.post('/api/cost/clear')
def cost_clear():
    """清空成本日志（测试用）"""
    clear_cost_logs()
    return {'success': True}


# ===== 灵感库 API（UI 定稿 P12：作者上传 → AI 自动分类 → 分类浏览）=====

@app.post("/api/inspirations")
def add_inspiration(req: InspirationAddRequest):
    """上传灵感；category 为空 → LLM 自动分类"""
    try:
        insp_id = inspiration_store.add_inspiration(req.novel_id, req.content, req.category, req.source)
        item = next((x for x in inspiration_store.list_inspirations(req.novel_id) if x["id"] == insp_id), None)
        return {"id": insp_id, "category": item["category"] if item else "", "auto": not req.category.strip()}
    except ValueError as e:
        return {"error": str(e)}


@app.get("/api/inspirations")
def list_inspirations(novel_id: int | None = None, category: str | None = None):
    """灵感列表（可按分类过滤）"""
    items = inspiration_store.list_inspirations(novel_id, category)
    return {"inspirations": items, "total": len(items)}


@app.patch("/api/inspiration/{insp_id}/category")
def change_inspiration_category(insp_id: int, req: InspirationCategoryRequest):
    """手动改分类"""
    inspiration_store.set_category(insp_id, req.category)
    return {"success": True}


@app.delete("/api/inspiration/{insp_id}")
def delete_inspiration(insp_id: int):
    inspiration_store.delete_inspiration(insp_id)
    return {"success": True}


@app.get("/api/inspiration/categories")
def list_inspiration_categories(novel_id: int):
    """分类列表（带数量角标）"""
    cats = inspiration_store.list_categories(novel_id)
    counts = inspiration_store.count_by_category(novel_id)
    return {
        "categories": [{"name": c["name"], "count": counts.get(c["name"], 0)} for c in cats],
    }


@app.post("/api/inspiration/category")
def add_inspiration_category(req: InspCategoryAddRequest):
    """添加分类"""
    try:
        cid = inspiration_store.add_category(req.novel_id, req.name)
        return {"id": cid}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/api/inspiration/category/rename")
def rename_inspiration_category(req: InspCategoryRenameRequest):
    """分类改名（条目同步）"""
    try:
        inspiration_store.rename_category(req.novel_id, req.old_name, req.new_name)
        return {"success": True}
    except ValueError as e:
        return {"error": str(e)}


@app.post("/api/inspiration/category/move")
def move_inspiration_category(req: InspCategoryMoveRequest):
    """分类上移/下移"""
    inspiration_store.move_category(req.novel_id, req.name, req.direction)
    return {"success": True}


@app.delete("/api/inspiration/category")
def delete_inspiration_category(novel_id: int, name: str):
    """删除分类（条目归入「其他」）"""
    inspiration_store.delete_category(novel_id, name)
    return {"success": True}


@app.get("/api/inspirations/export")
def export_inspirations(novel_id: int | None = None):
    """导出全部灵感为纯文本"""
    return {"text": inspiration_store.export_text(novel_id)}


# ===== 润色 API（P5 润色 Review 弹窗：AI 修改 → 对比 → 采纳/放弃）=====

POLISH_SYSTEM = (
    "你是资深网文编辑，负责给作者的段落做润色。要求：\n"
    "1. 保留原文的情节、人物、对话含义，不新增事实、不改设定；\n"
    "2. 只输出润色后的正文，不要解释、不要标题、不要引用原文；\n"
    "3. 如果原文已经很好了，可以只做轻微调整。"
)


@app.post("/api/polish")
def polish_text(req: PolishRequest):
    """润色文本（无状态：原文→改写，前端负责 Review 对比）"""
    if not req.text.strip():
        return {"error": "没有可润色的文本"}
    text = req.text.strip()
    if len(text) > 4000:
        text = text[:4000]  # 保护成本：超长只润色前 4000 字
    intensity_desc = (
        "非常保守，尽量少改动"
        if req.intensity < 0.34
        else ("大胆改写，让文笔明显提升" if req.intensity > 0.66 else "适度润色")
    )
    user_prompt = (
        f"润色下面的段落。风格要求：{req.style}。力度：{intensity_desc}。\n\n"
        f"--- 原文 ---\n{text}"
    )
    try:
        result = chat(POLISH_SYSTEM, user_prompt, temperature=0.4, max_tokens=2048, task="creative")
        return {"polished": result.strip()}
    except Exception as e:
        return {"error": f"润色失败: {e}"}


@app.post("/api/memory/feedback")
def memory_feedback(req: MemoryFeedbackRequest):
    """程序性记忆（PRD 四层记忆之四）：记录用户对 AI 建议的采纳/拒绝，
    后续灵感/剧情推荐会参考（避开被拒绝的方向，优先被采纳的方向）。"""
    record_feedback(req.novel_id, req.session_id, req.suggestion_type, req.suggestion, req.feedback)
    return {"success": True}


@app.get("/api/memory/preferences")
def memory_preferences(novel_id: int | None = None):
    """程序性记忆偏好摘要（供前端调试/展示；推荐注入在服务端自动完成）"""
    return {"preferences": preference_summary(novel_id)}


# ===== 写作后智能分析 API（P3-1 DataAnalyst Agent：历史诊断 + 市场机会 + 策略建议）=====

DATA_ANALYST_SYSTEM = (
    "你是资深的网文编辑兼数据分析师。基于作者的作品资料，输出一份「创作策略报告」。\n"
    "报告必须输出严格 JSON，结构如下：\n"
    "{\n"
    "  \"summary\": \"一段总评（150字内）\",\n"
    "  \"strengths\": [\"优势1\", \"优势2\", \"优势3\"],\n"
    "  \"weaknesses\": [\"短板1\", \"短板2\"],\n"
    "  \"market_opportunities\": [\"1-3个有潜力的细分题材方向及理由\"],\n"
    "  \"strategy\": [\"具体可执行的写作策略建议\"],\n"
    "  \"opening_hook\": \"基于其风格设计的开篇钩子示例（100字内）\"\n"
    "}\n"
    "不要输出 JSON 以外的任何内容。"
)


def _parse_report_json(text: str) -> dict:
    """容错解析 LLM 输出的 JSON（剥离 ```json 包裹 / 截取首尾括号）"""
    t = text.strip()
    t = t.replace("```json", "").replace("```", "").strip()
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[data_analyst] JSON 解析失败，回退文本: {e}")
    return {"summary": t[:300]}


@app.post("/api/analysis/report")
def analysis_report(req: AnalysisReportRequest):
    """生成创作策略报告：聚合章节/大纲/图谱 → LLM 分析 → 结构化报告"""
    chapters = novel_store.list_chapters(req.novel_id)
    outline = novel_store.get_novel_outline(req.novel_id)
    g = get_graph_for(req.novel_id)

    # 1. 聚合素材（控制 token：最多 6 章 × 1200 字 + 图谱摘要）
    chapter_parts = []
    for c in chapters[-6:]:
        full = novel_store.get_chapter(c["id"])
        body = (full or {}).get("content", "")
        chapter_parts.append(f"第{c['id']}章《{c.get('title', '')}》\n{body[:1200]}")
    chapter_text = "\n\n".join(chapter_parts) or "（还没有章节）"

    rel_lines = []
    for r in g.all_relations()[:60]:
        rel_lines.append(f"{r['source']} ——{r['relation']}→ {r['target']}")
    graph_text = "\n".join(rel_lines) or "（图谱为空）"

    title_map = {n["id"]: n["title"] for n in novel_store.list_novels()}
    user_prompt = (
        f"作者的作品《{title_map.get(req.novel_id, '未命名')}》资料如下：\n\n"
        f"--- 大纲 ---\n{outline[:1500] or '（暂无）'}\n\n"
        f"--- 最近章节节选 ---\n{chapter_text}\n\n"
        f"--- 人物关系 ---\n{graph_text}\n\n"
        "请按系统要求输出创作策略报告 JSON。"
    )
    try:
        raw = chat(DATA_ANALYST_SYSTEM, user_prompt, temperature=0.5, max_tokens=2500, task="complex")
    except Exception as e:
        # 复杂级失败 → 回退主力模型再试一次
        try:
            raw = chat(DATA_ANALYST_SYSTEM, user_prompt, temperature=0.5, max_tokens=2500, task="summary")
        except Exception as e2:
            return {"error": f"报告生成失败: {e2}"}
    report = _parse_report_json(raw)
    report["_novel_id"] = req.novel_id
    report["_chapters"] = len(chapters)
    report["_words"] = sum(
        len((novel_store.get_chapter(c["id"]) or {}).get("content", "")) for c in chapters
    )
    return {"report": report}


# ===== 写作数据面板 API（Tool 7: generate_writing_report）=====

@app.get("/api/novel/{novel_id}/stats")
def novel_stats(novel_id: int):
    """写作数据面板：字数趋势/章节节奏/高频词/人物出场/创作时段"""
    try:
        return generate_writing_report(novel_id)
    except Exception as e:
        return {"error": f"数据面板生成失败: {e}"}


# ===== 伏笔看板 API（P2-3：伏笔管理 / 未填坑提醒）=====

@app.get("/api/novel/{novel_id}/foreshadowings")
def list_foreshadowings(novel_id: int, status: str = ""):
    """伏笔看板：全部伏笔 + 统计（status: pending/resolved/空=全部）"""
    items = novel_store.list_foreshadowings(novel_id, status)
    for it in items:
        it["created_at"] = it["created_at"]
    return {
        "foreshadowings": items,
        "stats": novel_store.foreshadowing_stats(novel_id),
        "total": len(items),
    }


@app.patch("/api/foreshadowing/{fh_id}")
def resolve_foreshadowing(fh_id: int):
    """标记伏笔已解决（填坑）"""
    novel_store.resolve_foreshadowing(fh_id)
    tracking.record("foreshadowing_resolved", fh_id=fh_id)
    return {"success": True}


# ===== 埋点 API（PRD 5.6）=====

class TrackRequest(BaseModel):
    event: str
    props: dict = {}
    session_id: str = ""


@app.post("/api/track")
def track_event(req: TrackRequest):
    """前端/任意端埋点上报"""
    tracking.record(req.event, session_id=req.session_id, **req.props)
    return {"success": True}


@app.get("/api/tracking/stats")
def tracking_stats():
    """埋点统计（按事件计数 + 最近事件）"""
    return tracking.stats()


@app.post("/api/tracking/clear")
def tracking_clear():
    tracking.clear()
    return {"success": True}


# ===== 多模态文档解析 API（P2-5：word / pdf / 文本上传）=====

@app.post("/api/material/parse")
async def parse_material(file: UploadFile):
    """解析上传的文档（.txt/.md/.docx/.pdf）→ 提取文本，供前端走素材入库流程"""
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        return {"error": "文件超过 20MB 限制"}
    try:
        text = parse_text_file(content, file.filename or "")
    except ValueError as e:
        return {"error": str(e)}
    tracking.record("upload_content", content_type=file.filename.split(".")[-1] if file.filename else "text",
                    file_size=len(content))
    return {"text": text[:100000], "filename": file.filename, "chars": len(text)}


# ===== 社区 API（作者互动：帖子 / 点赞 / 收藏 / 评论）=====
@app.post("/api/community/post")
def community_create_post(req: PostCreateRequest):
    """发帖（分享 / 灵感分享 / 使用心得 / 提问）"""
    if not req.title.strip() or not req.content.strip():
        return {"error": "标题和内容不能为空"}
    pid = community_store.create_post(
        title=req.title, content=req.content, post_type=req.post_type,
        novel_id=req.novel_id, inspiration_id=req.inspiration_id,
    )
    tracking.record("community_post", post_type=req.post_type, post_id=pid)
    return {"success": True, "post_id": pid}


@app.get("/api/community/posts")
def community_list_posts(post_type: str | None = None, limit: int = 50, offset: int = 0):
    """帖子流（最新在前；可按类型过滤）"""
    posts = community_store.list_posts(post_type, min(limit, 100), max(offset, 0))
    # 补充关联信息：书名 / 灵感内容
    novel_titles = {n["id"]: n["title"] for n in novel_store.list_novels()}
    all_insp = inspiration_store.list_inspirations(novel_id=None, category=None)
    insp_texts = {it["id"]: it.get("content", "") for it in all_insp}
    for p in posts:
        p["novel_title"] = novel_titles.get(p["novel_id"], "")
        p["inspiration_content"] = insp_texts.get(p["inspiration_id"], "")
    return {"posts": posts, "total": len(posts)}


@app.get("/api/community/post/{post_id}")
def community_get_post(post_id: int):
    """帖子详情 + 评论"""
    post = community_store.get_post(post_id)
    if not post:
        return {"error": "帖子不存在"}
    comments = community_store.list_comments(post_id)
    return {"post": post, "comments": comments}


@app.delete("/api/community/post/{post_id}")
def community_delete_post(post_id: int):
    community_store.delete_post(post_id)
    return {"success": True}


@app.post("/api/community/post/{post_id}/like")
def community_toggle_like(post_id: int):
    return community_store.toggle_like(post_id)


@app.post("/api/community/post/{post_id}/favorite")
def community_toggle_favorite(post_id: int):
    return community_store.toggle_favorite(post_id)


@app.post("/api/community/comment")
def community_add_comment(req: CommentCreateRequest):
    if not req.content.strip():
        return {"error": "评论内容不能为空"}
    cid = community_store.add_comment(req.post_id, req.content)
    return {"success": True, "comment_id": cid}


@app.delete("/api/community/comment/{comment_id}")
def community_delete_comment(comment_id: int):
    community_store.delete_comment(comment_id)
    return {"success": True}


def latest_usage() -> dict:
    '最近一次 LLM 调用的 token 用量（上下文窗口指示用）'
    try:
        import sqlite3 as _sql
        conn = _sql.connect(os.environ.get("COST_DB_PATH", "data/cost.db"), timeout=5)
        row = conn.execute(
            "SELECT model, input_tokens, output_tokens FROM llm_calls ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return {"model": row[0], "input_tokens": row[1], "output_tokens": row[2],
                    "total": row[1] + row[2]}
    except Exception:
        pass
    return {"model": "", "input_tokens": 0, "output_tokens": 0, "total": 0}


# ===== 会话历史 API（对话持久化 + 对比不同对话）=====
@app.get("/api/chat/sessions")
def chat_sessions():
    """列出所有对话组（scope + session_id + 消息数 + 最后消息），供前端对比不同对话"""
    return {"sessions": memory_manager.list_sessions()}


@app.get("/api/chat/history")
def chat_history(scope: str = "home", session_id: str = "default", limit: int = 100):
    """读取某个对话组的完整历史（前端查看/对比用）"""
    return {"history": memory_manager.get_session_history(scope, session_id, limit)}


@app.delete("/api/chat/session")
def chat_session_clear(scope: str = "home", session_id: str = "default"):
    """清空某个对话组的历史（新开一组对比）"""
    memory_manager.get_memory(None if scope == "home" else int(scope), session_id).clear()
    return {"success": True}


class ChatSessionRenameRequest(BaseModel):
    scope: str = "home"
    session_id: str
    title: str


class ChatSessionEnsureRequest(BaseModel):
    scope: str = "home"
    session_id: str
    title: str = ""


@app.post("/api/chat/session/ensure")
def chat_session_ensure(req: ChatSessionEnsureRequest):
    """会话占位（主流 AI 产品「一开聊就进列表」）：会话无记录时先建一条 system 空行，
    让会话立刻出现在左侧列表；已有记录则幂等跳过。前端在新建对话 / 发送首条消息时调用。
    """
    memory_manager.get_memory(None if req.scope == "home" else int(req.scope), req.session_id).ensure_session(req.title)
    return {"success": True}


@app.post("/api/chat/session/rename")
def chat_session_rename(req: ChatSessionRenameRequest):
    """重命名会话组"""
    memory_manager.rename_session(req.scope, req.session_id, req.title.strip()[:20])
    return {"success": True}


class ChatSessionFlagRequest(BaseModel):
    """会话管理标记：归档 / 置顶（仿 DSH 对话管理）"""
    scope: str = "home"
    session_id: str
    archived: bool = False
    pinned: bool = False


@app.post("/api/chat/session/flag")
def chat_session_flag(req: ChatSessionFlagRequest):
    """设置会话标记：归档（软归档：消息保留在库中，列表移入归档分组，可随时恢复）/ 置顶。
    归档保存位置：仍在 chat_history 表内（archived=1 标记），不删除任何数据。
    """
    memory_manager.set_session_flag(req.scope, req.session_id, "archived", req.archived)
    memory_manager.set_session_flag(req.scope, req.session_id, "pinned", req.pinned)
    return {"success": True}


# ===== 启动 =====
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


@app.get("/", response_class=HTMLResponse)
def serve_index():
    """托管前端首页"""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path, headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h1>frontend not found</h1>", status_code=404)
