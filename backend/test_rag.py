"""测试脚本 — 验证完整RAG链路"""
import json
import urllib.request

BASE = "http://localhost:8000"

# 1. 读取示例文本
with open("/Users/vesper/WorkBuddy/2026-07-30-20-06-43/novel-island/data/sample/novel_sample.txt", "r") as f:
    text = f.read()

# 2. 构建知识库
print("=== 构建知识库 ===")
resp = urllib.request.urlopen(
    urllib.request.Request(
        f"{BASE}/api/kb/build",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
)
data = json.loads(resp.read())
print(f"chunks: {data['stats']['chunks']}, chars: {data['stats']['total_chars']}")

# 3. 提问
print("\n=== 提问：林晚是做什么工作的？ ===")
resp = urllib.request.urlopen(
    urllib.request.Request(
        f"{BASE}/api/kb/ask",
        data=json.dumps({"query": "林晚是做什么工作的？", "top_k": 5}).encode(),
        headers={"Content-Type": "application/json"},
    )
)
data = json.loads(resp.read())
print(f"回答:\n{data['answer']}")
print(f"\n检索来源: {data['sources']}")
