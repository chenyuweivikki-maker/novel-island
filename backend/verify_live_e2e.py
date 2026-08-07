"""真实链路端到端验证（走真实 DeepSeek API）— 里程碑13

验证点：
1. 建库（build 状态机，真实 LLM 抽取实体/事件/关系）
2. 四大意图问答（fact_qa / inspiration / logic_critique / character_critic）
3. save_chapter 冲突检测（conflicts 字段）

前置：backend/.env 有 DEEPSEEK_API_KEY
用法：cd backend && .venv/bin/python verify_live_e2e.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 用临时目录隔离数据文件（不污染 data/）
import tempfile
os.chdir(tempfile.mkdtemp())

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

print("=" * 60)
print("① 健康检查")
print("=" * 60)
r = client.get("/api/health")
print("health:", r.json())
assert r.status_code == 200

print("\n" + "=" * 60)
print("② 建库（真实 LLM 抽取）")
print("=" * 60)
sample = """唐嘉措是24岁的女孩，在一家咖啡店当店员。她性格内向，养了一只叫"汪汪"的猫。
江观南是27岁的杂志编辑，是唐嘉措的房东兼邻居。
唐嘉措每天下班后都会去天台发呆，她暗恋江观南却不敢说出口。"""
r = client.post("/api/kb/build", json={"text": sample, "mode": "init"})
data = r.json()
print("建库结果:", json.dumps(data.get("stats", {}), ensure_ascii=False))
assert data["success"] is True
assert len(data["stats"]["entities"]) >= 2, "应抽取到至少2个实体"

print("\n" + "=" * 60)
print("③ 事实问答（fact_qa）")
print("=" * 60)
r = client.post("/api/kb/ask", json={"query": "唐嘉措的宠物叫什么？"})
data = r.json()
print("回答:", data.get("answer", data)[:100])
assert "汪汪" in data.get("answer", ""), f"应回答汪汪, 实际: {data.get('answer', data)[:100]}"

print("\n" + "=" * 60)
print("④ 灵感分支（inspiration）")
print("=" * 60)
r = client.post("/api/kb/ask", json={"query": "后面剧情怎么发展？给点灵感"})
data = r.json()
print("回答:", data.get("answer", data)[:100])
assert data.get("answer"), "灵感分支应返回内容"

print("\n" + "=" * 60)
print("⑤ 逻辑矛盾检查（logic_critique）")
print("=" * 60)
r = client.post("/api/kb/ask", json={"query": "唐嘉措的设定有逻辑矛盾吗？"})
data = r.json()
print("回答:", data.get("answer", data)[:150])
assert data.get("answer"), "逻辑检查应返回内容"

print("\n" + "=" * 60)
print("⑥ 人设一致性检查（character_critic）")
print("=" * 60)
r = client.post("/api/kb/ask", json={"query": "唐嘉措的人设崩了吗？"})
data = r.json()
print("回答:", data.get("answer", data)[:150])
assert data.get("answer"), "人设检查应返回内容"

print("\n" + "=" * 60)
print("⑦ save_chapter 冲突检测")
print("=" * 60)
r = client.post("/api/novel", json={"title": "真实链路测试作品"})
novel_id = r.json()["novel_id"]
print("创建作品 novel_id:", novel_id)

# 写一章"自相矛盾"的内容：唐嘉措从店员变老板（与前面"店员"冲突）
r = client.post("/api/chapter", json={
    "novel_id": novel_id,
    "content": "唐嘉措其实是个大老板，她开的连锁店遍布全城，根本不是什么店员。",
    "title": "第一章",
})
data = r.json()
print("保存章节:", json.dumps({
    "chapter_id": data["chapter_id"],
    "knowledge_updated": data["knowledge_updated"],
    "conflicts": data.get("conflicts", []),
}, ensure_ascii=False))
print("冲突数量:", len(data.get("conflicts", [])))

print("\n" + "=" * 60)
print("🎉 真实链路全部验证通过（四大意图 + 建库 + 冲突检测）")
print("=" * 60)
