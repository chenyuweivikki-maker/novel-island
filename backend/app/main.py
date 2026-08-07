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
from .core.retriever import retriever
from .core.llm_client import chat, chat_stream, RAG_SYSTEM_PROMPT, build_rag_prompt
from .core.memory import memory
from .core.model_router import get_cost_summary, clear_cost_logs
from .core.graph_store import graph, graph_manager, get_graph_for
from .core.novel_store import novel_store
from .core.hybrid_retriever import hybrid_search
from .core.hybrid_retriever import precise_attribute_search
from .core.vector_store import vector_store, vector_store_manager
from .tools.consistency_tools import check_plot_consistency
from .graphs.qa_graph import qa_app
from .graphs.build_graph import build_app

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


# ===== 创作空间请求模型（里程碑10）=====
class NovelCreateRequest(BaseModel):
    title: str


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
    }


@app.get("/api/kb/status")
def kb_status():
    return {
        "ready": retriever.is_ready,
        "chunks": len(retriever.chunks),
    }


@app.post("/api/kb/ask")
def ask(req: AskRequest):
    """提问：检索 Top-K → LLM 生成回答"""
    if not retriever.is_ready:
        return {"error": "知识库未构建，请先调用 /api/kb/build"}

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
            if 0 <= chunk_id < len(retriever.chunks):
                results.append({"chunk": retriever.chunks[chunk_id], "score": hit["score"], "index": chunk_id})
        # 向量没结果就回退 TF-IDF
        if not results:
            results = retriever.search(req.query, req.top_k)
    else:
        results = retriever.search(req.query, req.top_k)

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
    memory.add_turn(req.query, result["agent_response"])

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
def retrieve(query: str, top_k: int = 5):
    """纯检索接口，不调用LLM"""
    if not retriever.is_ready:
        return {"error": "知识库未构建"}
    results = retriever.search(query, top_k)
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


# ===== 创作空间 API（里程碑10）=====

@app.post("/api/novel")
def create_novel(req: NovelCreateRequest):
    """创建作品"""
    novel_id = novel_store.create_novel(req.title)
    return {"novel_id": novel_id, "title": req.title}


@app.get("/api/novels")
def list_novels():
    """列出所有作品"""
    return {"novels": novel_store.list_novels()}


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

    # 1. 保存章节（有 chapter_id 则更新，无则新增）
    if req.chapter_id:
        # 更新：先删旧数据（向量/图谱），再加新内容
        vs.remove_by_chapter(req.chapter_id)
        g.remove_by_chapter(req.chapter_id)
        novel_store.update_chapter(req.chapter_id, req.content, req.title)
        chapter_id = req.chapter_id
    else:
        chapter_id = novel_store.add_chapter(req.novel_id, req.content, req.title)

    # 2. 增量更新知识库（build 状态机 + 向量），新内容打上章节标记
    try:
        result = build_app.invoke({"raw_input_files": [req.content], "novel_id": req.novel_id})
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
        "stats": {
            "chunks": len(result["processed_chunks"]) if updated else 0,
            "entities": result["final_output"]["entities"] if updated else [],
        },
    }


@app.get("/api/novel/{novel_id}/chapters")
def list_novel_chapters(novel_id: int):
    """列出作品章节"""
    return {"chapters": novel_store.list_chapters(novel_id)}


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
