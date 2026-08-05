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

app = FastAPI(title="小说岛 API", version="0.1.0")

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
    """构建知识库：清洗 → 分块 → 建TF-IDF索引"""
    cleaned = clean_text(req.text)
    chunks = chunk_text(cleaned, req.chunk_size, req.overlap)
    retriever.build_index(chunks)

    total_chars = sum(c.char_count for c in chunks)
    return {
        "success": True,
        "stats": {
            "chunks": len(chunks),
            "total_chars": total_chars,
            "indexed": True,
        },
        "chunks": [
            {"id": c.id, "text": c.text, "char_count": c.char_count}
            for c in chunks
        ],
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

    # 1. 检索
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

    # 非流式
    answer = chat(RAG_SYSTEM_PROMPT, user_prompt)

    return {
        "answer": answer,
        "sources": [
            {"chunk_id": r["chunk"].id, "score": round(r["score"], 4)}
            for r in results
        ],
        "retrieval": [
            {
                "chunk_id": r["chunk"].id,
                "score": round(r["score"], 4),
                "text": r["chunk"].text,
                "preview": r["chunk"].text[:150],
            }
            for r in results
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
