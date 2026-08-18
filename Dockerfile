# 小说岛 — 后端 + 前端单容器（FastAPI 托管静态页面）
FROM python:3.11-slim

WORKDIR /app

# 依赖（含文档解析与文件上传）
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# 应用代码
COPY backend/app /app/app
COPY frontend /app/frontend

# 数据目录（图谱/向量/SQLite，挂卷持久化）
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
