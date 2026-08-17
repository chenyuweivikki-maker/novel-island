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
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core.config import settings
from .core.chunker import clean_text, chunk_text
from .core.retriever import get_retriever_for
from .core.llm_client import chat, chat_stream, RAG_SYSTEM_PROMPT, build_rag_prompt
from .core.memory import memory_manager
from .core.model_router import get_cost_summary, clear_cost_logs
from .core.graph_store import graph, graph_manager, get_graph_for
from .core.novel_store import novel_store
from .core.hybrid_retriever import hybrid_search
from .core.hybrid_retriever import precise_attribute_search
from .core.vector_store import vector_store, vector_store_manager
from .tools.consistency_tools import check_plot_consistency
from .graphs.qa_graph import qa_app
from .graphs.build_graph import build_app
from .nodes.build_nodes import CHAPTER_OUTLINE_PROMPT
from .nodes.qa_nodes import IntentRouterNode, CompanionNode

app = FastAPI(title="小说岛 API", version="0.1.0")

# 启动时加载已持久化的图谱
if len(graph) == 0:
    graph.load()
# 里程碑9：启动时加载向量库
vector_store.load()

# CORS — 允许前端跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 托管前端静态文件 — 单端口，无跨域问题
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "public")
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


# ===== 创作空间请求模型（里程碑10）=====
class NovelCreateRequest(BaseModel):
    title: str
    expected_words: int = 0  # 里程碑18：预计总字数
    chapter_words: int = 0   # 里程碑18：每章字数


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


# ===== 对话式建库（P3）：素材解析入库 + 空库引导提问 =====
def ingest_material(text: str, novel_id: int | None) -> str:
    """素材解析入库：跑建库状态机（清洗分块 → 并行抽取 → 图谱一致性 → 入库）→ 返回入库摘要

    设计：作者在对话里拖入/粘贴素材，Agent 自动解析并增量入库（对话式建库）。
    """
    result = build_app.invoke({
        "raw_input_files": [text],
        "novel_id": novel_id,
    })
    out = result["final_output"]
    chunks = result.get("processed_chunks", [])
    # 向量库增量追加（里程碑9/11：按项目）
    vs = vector_store_manager.get_store(novel_id) if novel_id is not None else vector_store
    vs.add_chunks(
        [c["text"] for c in chunks],
        [{"chunk_id": c["id"]} for c in chunks],
    )
    vs.save()

    n_ent = len(out.get("entities", []))
    n_rel = len(out.get("relationships", []))
    n_evt = len(out.get("events", []))
    summary = (
        f"收到，素材已解析入库：提取 {n_ent} 个人物、{n_rel} 条关系、{n_evt} 个事件，"
        f"新增 {len(chunks)} 个文本片段。"
    )
    conflicts = (result.get("consistency_report") or {}).get("conflicts", [])
    if conflicts:
        c = conflicts[0]
        summary += f"\n⚠️ 检测到 1 处图谱冲突：[{c['dimension']}] {c['conflict']}"
    summary += "\n\n" + next_guide_question(novel_id)
    return summary


def next_guide_question(novel_id: int | None) -> str:
    """按流程询问（题材→主角→配角→大纲→设定），用图谱已有内容推断当前进度，无需额外状态"""
    g = get_graph_for(novel_id)
    n_ents = len(g) if g else 0
    n_rels = len(g.all_relations()) if g and n_ents else 0
    n_evts = len(g.get_timeline()) if g and n_ents else 0

    if n_ents == 0:
        return ("知识库还是空的，我们从人设聊起吧——这本书的主角是谁？\n"
                "直接告诉我名字、性格、外貌都行；或者把素材（片段/设定）拖进对话框，我自动解析入库。")
    if n_rels == 0:
        return "主角已经有了。主要配角呢？他们和主角是什么关系？"
    if n_evts == 0:
        return "人物和关系都进库了，故事的大纲方向呢？想写一个什么故事？"
    return ("库在慢慢长大啦。可以继续补充设定，也可以直接去「写作编辑器」写第一章——"
            "保存后我会自动抽取人物、生成章纲、更新知识库。随时问我设定、逻辑或灵感的问题。")


@app.post("/api/kb/ask")
def ask(req: AskRequest):
    """提问：检索 Top-K → LLM 生成回答（含对话式建库：素材解析入库 + 空库引导）"""
    # ===== 对话式建库：素材解析入库（拖入/粘贴的文本，随时可入）=====
    if req.material and req.material.strip():
        answer = ingest_material(req.material, req.novel_id)
        return {"answer": answer, "sources": []}

    # 里程碑17：按项目取 TF-IDF 检索器（不选项目时用全局单例）
    r = get_retriever_for(req.novel_id)

    # ===== 空库：不硬报错，进入建库引导（对话式建库）=====
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
        # 否则 → 引导提问（按图谱已有内容推断下一步）
        return {"answer": next_guide_question(req.novel_id), "sources": []}

    # 1. 检索（里程碑9：先精确属性检索，再混合检索；里程碑11：按项目）
    precise = precise_attribute_search(req.query, req.novel_id)
    if precise:
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
        return {
            "answer": "在当前知识库中未找到与问题相关的内容。",
            "sources": [],
            "retrieval": [],
        }

    # 2. 构建prompt
    user_prompt = build_rag_prompt(req.query, results)

    # 3. 流式 or 非流式
    if req.stream:
        def generate():
            # 先发检索结果
            retrieval_data = [
                {"chunk_id": r["chunk"].id, "score": round(r["score"], 4),
                 "preview": r["chunk"].text[:120]}
                for r in results
            ]
            yield "data: " + json.dumps({"type": "retrieval", "data": retrieval_data}, ensure_ascii=False) + "\n\n"
            # 再流式输出回答
            for token in chat_stream(RAG_SYSTEM_PROMPT, user_prompt):
                yield "data: " + json.dumps({"type": "token", "data": token}, ensure_ascii=False) + "\n\n"
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # 非流式 — 走状态机（里程碑1）
    # 把请求参数放进 State（背包），让状态机跑完整个流程（novel_id 穿透）
    result = qa_app.invoke({
        "user_query": req.query,
        "top_k": req.top_k,
        "novel_id": req.novel_id,
    })

    # 里程碑6：把这一轮对话存进短期记忆（下次提问能"记得"）
    # 里程碑17：按 novel_id 存（切换项目历史不串）
    memory_manager.get_memory(req.novel_id).add_turn(req.query, result["agent_response"])

    return {
        "answer": result["agent_response"],
        "sources": result["sources"],
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
    """情节大事年表接口（里程碑15）：按入库顺序返回每章情节摘要，可按项目"""
    g = get_graph_for(novel_id)
    timeline_data = g.get_timeline()
    return {
        "timeline": timeline_data,
        "total": len(timeline_data),
    }


# ===== 创作空间 API（里程碑10）=====

@app.post("/api/novel")
def create_novel(req: NovelCreateRequest):
    """创建作品（里程碑18：支持预计总字数/每章字数）"""
    novel_id = novel_store.create_novel(req.title, req.expected_words, req.chapter_words)
    return {"novel_id": novel_id, "title": req.title}


@app.get("/api/novels")
def list_novels():
    """列出所有作品（按 sort_order 排序）"""
    return {"novels": novel_store.list_novels()}


@app.post("/api/novel/{novel_id}/rename")
def rename_novel(novel_id: int, req: NovelRenameRequest):
    """重命名作品（里程碑17）"""
    novel_store.update_novel_title(novel_id, req.title)
    return {"novel_id": novel_id, "title": req.title}


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

    # 里程碑18：生成章纲（300字章节总结），与冲突检测并行，用户无感
    try:
        outline = chat(CHAPTER_OUTLINE_PROMPT, req.content, temperature=0.0, max_tokens=600, task="creative")
        outline = outline.strip()
    except Exception as e:
        outline = ""
        print(f"章纲生成失败: {e}")

    # 1. 保存章节（有 chapter_id 则更新，无则新增）；更新时章纲也重新生成覆盖
    if req.chapter_id:
        # 更新：先删旧数据（向量/图谱），再加新内容
        vs.remove_by_chapter(req.chapter_id)
        g.remove_by_chapter(req.chapter_id)
        novel_store.update_chapter(req.chapter_id, req.content, req.title, outline)
        chapter_id = req.chapter_id
    else:
        chapter_id = novel_store.add_chapter(req.novel_id, req.content, req.title, outline)

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


# ===== 启动 =====
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)


@app.get("/", response_class=HTMLResponse)
def serve_index():
    """托管前端首页"""
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return HTMLResponse("<h1>frontend not found</h1>", status_code=404)
