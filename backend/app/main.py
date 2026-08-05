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
from .core.graph_store import graph
from .core.hybrid_retriever import precise_attribute_search
from .core.vector_store import vector_store
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


class AskRequest(BaseModel):
    query: str
    top_k: int = 5
    stream: bool = False


# ===== 接口 =====

@app.get("/api/health")
def health():
    return {"status": "ok", "model": settings.DEEPSEEK_MODEL}


@app.post("/api/kb/build")
def build_kb(req: BuildRequest):
    """构建知识库：走状态机（清洗分块 → 并行实体/事件抽取 → 汇总建索引）

    里程碑9：支持增量更新 —— mode=update 时只向量化新分块追加，不重建旧库。
    """
    # 把输入放进 State（背包），交给建库状态机跑
    result = build_app.invoke({
        "raw_input_files": [req.text],
    })

    output = result["final_output"]
    chunks = [
        {"id": c["id"], "text": c["text"], "char_count": c["char_count"]}
        for c in result["processed_chunks"]
    ]

    # 里程碑9：向量化 + 增量更新
    if req.mode == "update":
        # 增量：只把新分块向量化追加（vector_store 内部只处理新文本）
        vector_store.add_chunks(
            [c["text"] for c in chunks],
            [{"chunk_id": c["id"]} for c in chunks],
        )
    else:
        # 全量：清空重建
        vector_store.texts.clear()
        vector_store.vectors.clear()
        vector_store.metadata.clear()
        vector_store.add_chunks(
            [c["text"] for c in chunks],
            [{"chunk_id": c["id"]} for c in chunks],
        )
    vector_store.save()

    return {
        "success": True,
        "stats": {
            "chunks": output["chunks"],
            "total_chars": output["total_chars"],
            "indexed": True,
            "vector_indexed": len(vector_store),
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

    # 1. 检索（里程碑9：先精确属性检索，再混合检索）
    precise = precise_attribute_search(req.query)
    if precise:
        return {
            "answer": precise["answer"],
            "sources": [],
            "retrieval": [],
            "precise": precise,
        }

    # 混合检索（向量 + TF-IDF）
    if vector_store.is_ready:
        vector_hits = vector_store.search(req.query, req.top_k)
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
    # 把请求参数放进 State（背包），让状态机跑完整个流程
    result = qa_app.invoke({
        "user_query": req.query,
        "top_k": req.top_k,
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
def graph_data():
    """图谱查询接口（里程碑8+9）：返回全部实体（含人设属性）和关系"""
    entities = []
    for name in graph.all_entities():
        node = graph.get_entity(name)
        entities.append({
            "name": name,
            "persona": node.get("persona", {}) if node else {},
        })
    return {
        "entities": entities,
        "relations": graph.all_relations(),
        "total_entities": len(graph),
    }


@app.post('/api/graph/neighbors')
def graph_neighbors(entity: str):
    """查询某实体的直接关系（邻居）"""
    return {"entity": entity, "neighbors": graph.query_neighbors(entity)}


@app.post('/api/graph/path')
def graph_path(start: str, end: str):
    """查询两实体间的路径（多跳推理）"""
    return {"path": graph.query_path(start, end)}


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
