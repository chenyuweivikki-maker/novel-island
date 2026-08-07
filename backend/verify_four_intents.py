"""验证：里程碑13 四大意图路由 + LogicCritique/CharacterCritic 节点（mock LLM）

验证点：
1. IntentRouterNode 正确分流：人设/逻辑/灵感/事实 四类问题
2. LogicCritiqueNode：返回逻辑矛盾分析（task=logic）
3. CharacterCriticNode：查图谱persona + 返回人设检查（task=creative）
4. qa_graph 完整跑通四大分支
"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(tempfile.mkdtemp())

import app.core.llm_client as llm_client

# ---- mock LLM ----
_calls = []
def fake_chat(system_prompt, user_prompt, **kwargs):
    _calls.append({"system": system_prompt[:30], "task": kwargs.get("task"), "user": user_prompt})
    return "【检查结果】未发现明显矛盾。"

llm_client.chat = fake_chat

from app.nodes.qa_nodes import IntentRouterNode
from app.nodes.qa_nodes import LogicCritiqueNode, CharacterCriticNode
from app.graphs.qa_graph import qa_app

# ---- 1. 意图路由 ----
router = IntentRouterNode()
cases = [
    ("唐嘉措的人设是不是崩了？", "character_critic"),
    ("这段剧情前后矛盾怎么办？", "logic_critique"),
    ("后面剧情怎么发展？", "inspiration"),
    ("唐嘉措的宠物叫什么？", "fact_qa"),
]
for query, expected in cases:
    result = router({"user_query": query})
    assert result["current_intent"] == expected, f"'{query}' 应为 {expected}, 实际 {result['current_intent']}"
    print(f"  '{query}' → {result['current_intent']} ✅")

# ---- 2. 准备检索数据 ----
from app.core.retriever import retriever
from app.core.chunker import Chunk
texts = [
    "唐嘉措是24岁的店员，性格内向。",
    "唐嘉措在咖啡店工作，喜欢猫。",
]
retriever.build_index([Chunk(id=i, text=t, char_count=len(t)) for i, t in enumerate(texts)])
results = retriever.search("唐嘉措", 2)

# ---- 3. LogicCritiqueNode ----
lc = LogicCritiqueNode()
r = lc({"user_query": "这段剧情矛盾吗？", "retrieved_chunks": results})
assert r["agent_response"], "LogicCritique 应返回回答"
assert _calls[-1]["task"] == "logic", f"LogicCritique task应为logic, 实际{_calls[-1]['task']}"
print(f"LogicCritiqueNode 返回: {r['agent_response'][:30]}... (task={_calls[-1]['task']}) ✅")

# ---- 4. CharacterCriticNode（查图谱persona） ----
from app.core.graph_store import get_graph_for
g = get_graph_for(None)  # 全局图
g.add_entity("唐嘉措", {"identity": "主角"})
g.add_persona("唐嘉措", {"职业": "店员", "性格": "内向"}, chapter_id=1)

cc = CharacterCriticNode()
r = cc({"user_query": "唐嘉措人设崩了吗？", "retrieved_chunks": results, "novel_id": None})
assert r["agent_response"], "CharacterCritic 应返回回答"
assert _calls[-1]["task"] == "creative", f"CharacterCritic task应为creative, 实际{_calls[-1]['task']}"
assert "唐嘉措" in _calls[-1]["user"], "persona 应拼进 prompt"
assert "设定" in _calls[-1]["user"] or "职业" in _calls[-1]["user"], "图谱persona没进prompt"
print(f"CharacterCriticNode 返回: {r['agent_response'][:30]}... (task={_calls[-1]['task']}) ✅")

# ---- 5. qa_graph 完整跑通四大分支（不质检，直接END） ----
# 5a. 逻辑分支
res = qa_app.invoke({"user_query": "这段剧情矛盾吗？", "top_k": 2})
assert res["agent_response"], res
print("graph 逻辑分支 ✅")

# 5b. 人设分支
res = qa_app.invoke({"user_query": "唐嘉措人设崩了吗？", "top_k": 2})
assert res["agent_response"], res
print("graph 人设分支 ✅")

# 5c. 灵感分支
res = qa_app.invoke({"user_query": "后面剧情怎么发展？", "top_k": 2})
assert res["agent_response"], res
print("graph 灵感分支 ✅")

# 5d. 事实问答分支（agent + 质检，mock 下质检也走 chat）
res = qa_app.invoke({"user_query": "唐嘉措的宠物叫什么？", "top_k": 2})
assert res["agent_response"], res
print("graph 事实问答分支 ✅")

print("\n🎉 里程碑13 四大意图 + 新节点全部验证通过")
