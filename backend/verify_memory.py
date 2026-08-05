"""
验证脚本 — 多轮对话记忆测试（里程碑6）
运行：./.venv/bin/python verify_memory.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.core.chunker import clean_text, chunk_text
from app.core.retriever import retriever
from app.core.memory import memory
from app.graphs.qa_graph import qa_app

sample_dir = os.path.join(os.path.dirname(__file__), "..", "data", "sample")
sample_file = None
for f in os.listdir(sample_dir):
    if f.startswith("观南嘉措"):
        sample_file = os.path.join(sample_dir, f)
        break
if not sample_file:
    sample_file = os.path.join(sample_dir, "novel_sample.txt")

with open(sample_file, encoding="utf-8") as f:
    retriever.build_index(chunk_text(clean_text(f.read())))

memory.clear()
print("=== 多轮对话记忆测试 ===\n")

r1 = qa_app.invoke({"user_query": "江观南是做什么工作的？", "top_k": 5})
memory.add_turn("江观南是做什么工作的？", r1["agent_response"])
print(f"第1轮 回答: {r1['agent_response'][:80]}...")
print(f"  质检通过: {r1.get('critic_pass')}")

r2 = qa_app.invoke({"user_query": "她的大结局是什么？", "top_k": 5})
memory.add_turn("她的大结局是什么？", r2["agent_response"])
print(f"第2轮 回答: {r2['agent_response'][:100]}...")
print()
print("=== 记忆内容（完整显示，不截断）===")
for m in memory._conversation:
    print(f"  [{m['role']}] {m['content']}")
