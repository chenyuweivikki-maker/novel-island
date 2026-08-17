"""验证：Phase 0 — Companion / MultiHopInspiration / GraphConsistency 三节点 + 图接线（mock LLM）

验证点：
1. IntentRouter 情感陪伴路由（卡文/被骂/崩溃 → companion，优先级最高）
2. CompanionNode：空库纯陪伴 / 有素材结合作品共情（task=companion）
3. MultiHopInspirationNode：Hop1 → 线索提取 → Hop2 二次检索 → 合并来源生成
4. GraphConsistencyNode：规则层（关系/属性冲突）+ LLM 层五维 + 空图谱跳过 + 容错
5. build_graph / qa_graph 接线
"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(tempfile.mkdtemp())

import app.core.llm_client as llm_client

# ---- mock LLM（按 task 分派）----
_calls = []
def fake_chat(system_prompt, user_prompt, **kwargs):
    _calls.append({"task": kwargs.get("task"), "user": user_prompt[:80]})
    task = kwargs.get("task")
    if task == "extract":
        return "江观南\n宠物医院\n19960405门锁密码"
    if task == "companion":
        return "抱抱你，慢慢来，我一直在。"
    if task == "inspire":
        return "① 悬疑线：用门锁密码做信息差…\n② 事业线：宠物医院×美妆直播…\n③ 家庭线：原生家庭旧伤…"
    if task == "logic":
        return '[{"dimension": "时间线冲突", "conflict": "后发生的事件早于其前提", "old": "先发生A", "new": "先发生B", "severity": "high"}]'
    return "【检查结果】未发现明显矛盾。"

llm_client.chat = fake_chat

from app.nodes.qa_nodes import IntentRouterNode, CompanionNode, MultiHopInspirationNode
from app.nodes.graph_consistency import GraphConsistencyNode
from app.core.graph_store import KnowledgeGraph
import app.nodes.qa_nodes as qa_nodes_mod
import app.nodes.graph_consistency as gc_mod
from langgraph.graph import StateGraph

class SimpleChunk:
    def __init__(self, id_, text):
        self.id, self.text = id_, text

# ========== 1. IntentRouter companion 路由 ==========
print("【1】IntentRouter 情感陪伴路由")
router = IntentRouterNode()
cases = [
    ("写不下去了，好累", "companion"),
    ("今天被读者骂了，心态崩了", "companion"),
    ("好烦，想放弃了", "companion"),
    ("卡文了，后面剧情怎么发展？", "inspiration"),   # 卡文→灵感（陪伴词不含卡文）
    ("江观南人设是不是崩了？", "character_critic"),
    ("这段剧情逻辑有没有矛盾？", "logic_critique"),
    ("江观南的宠物叫什么？", "fact_qa"),
]
ok = True
for q, want in cases:
    got = router({"user_query": q})["current_intent"]
    mark = "✅" if got == want else "❌"
    if got != want:
        ok = False
    print(f"  '{q}' → {got}（期望 {want}）{mark}")

# ========== 2. CompanionNode ==========
print("【2】CompanionNode 情感陪伴")
companion = CompanionNode()
# 2a. 空库（无检索结果）→ 纯陪伴
out = companion({"user_query": "写不下去了", "retrieved_chunks": []})
task = _calls[-1]["task"]
assert out["agent_response"] == "抱抱你，慢慢来，我一直在。", out["agent_response"]
assert task == "companion", task
print(f"  空库 → task=companion，回复含鼓励语 ✅")
# 2b. 有素材 → 结合作品共情（sources 透传）
chunks = [{"chunk": SimpleChunk(0, "江观南把伞往她那边倾了倾"), "score": 0.9}]
out2 = companion({"user_query": "好累", "retrieved_chunks": chunks})
assert len(out2["sources"]) == 1 and out2["sources"][0]["chunk_id"] == 0
print(f"  有素材 → 回复={out2['agent_response']!r}，sources 透传 ✅")

# ========== 3. MultiHopInspirationNode ==========
print("【3】MultiHopInspirationNode 多跳灵感")
class FakeRetriever:
    def __init__(self):
        self.queries = []
    def search(self, query, top_k=5):
        self.queries.append(query)
        return [{"chunk": SimpleChunk(100 + len(self.queries), f"前文伏笔：{query}"), "score": 0.7}]

fake_r = FakeRetriever()
qa_nodes_mod.get_retriever_for = lambda novel_id: fake_r

mh = MultiHopInspirationNode()
# 3a. 无检索结果 → 兜底
out_a = mh({"user_query": "后面怎么发展", "novel_id": 1, "retrieved_chunks": []})
assert "无法给出" in out_a["agent_response"] and out_a["sources"] == []
print(f"  无检索结果 → 兜底：{out_a['agent_response'][:20]}… ✅")
# 3b. 有结果 → 两跳
hop1 = [{"chunk": SimpleChunk(1, "江观南盯着墙上的画，门锁密码是19960405"), "score": 0.9}]
out_b = mh({"user_query": "卡文了，密室这条线怎么收", "novel_id": 1, "retrieved_chunks": hop1, "top_k": 3})
tasks = [c["task"] for c in _calls]
assert "extract" in tasks and "inspire" in tasks, tasks
assert len(fake_r.queries) >= 1, "Hop2 未触发二次检索"
assert len(out_b["sources"]) >= 2, f"来源应合并两轮检索：{out_b['sources']}"
print(f"  Hop1={len(hop1)}条 → 线索{len(fake_r.queries)}条 → Hop2 +{len(out_b['sources'])-1}条 → 生成3方向（mock）✅")

# ========== 4. GraphConsistencyNode ==========
print("【4】GraphConsistencyNode 图谱一致性")
gc = GraphConsistencyNode()
# 4a. 空图谱 → 跳过检查
g_empty = KnowledgeGraph()
gc_mod.get_graph_for = lambda nid: g_empty
rep = gc({"novel_id": 1, "extracted_entities": [{"name": "江观南", "attributes": {"身份": "主理人"}}],
          "extracted_relationships": [], "extracted_events": []})
assert rep["consistency_report"]["checked"] is False
print("  空图谱 → checked=False（跳过，省成本）✅")
# 4b. 规则层：人物关系冲突
g_rel = KnowledgeGraph()
g_rel.add_entity("江观南"); g_rel.add_entity("唐嘉措")
g_rel.add_relation("江观南", "恋人", "唐嘉措")
gc_mod.get_graph_for = lambda nid: g_rel
rep = gc({"novel_id": 1, "extracted_entities": [],
          "extracted_relationships": [{"source": "唐嘉措", "relation": "仇敌", "target": "江观南"}],
          "extracted_events": []})
conflicts = rep["consistency_report"]["conflicts"]
assert any(c["dimension"] == "人物关系冲突" for c in conflicts), conflicts
print(f"  规则层·关系冲突 → {conflicts[0]['conflict'][:44]}… ✅")
# 4c. 规则层：属性冲突（persona 走 add_persona，与生产 build_nodes 一致）
g_attr = KnowledgeGraph()
g_attr.add_entity("江观南")
g_attr.add_persona("江观南", {"身份": "宠物医院主理人"})
gc_mod.get_graph_for = lambda nid: g_attr
rep = gc({"novel_id": 2, "extracted_entities": [{"name": "江观南", "attributes": {"身份": "刑警"}}],
          "extracted_relationships": [], "extracted_events": []})
conflicts = rep["consistency_report"]["conflicts"]
assert any(c["dimension"] == "属性冲突" for c in conflicts), conflicts
print(f"  规则层·属性冲突 → {conflicts[0]['conflict'][:40]}… ✅")
# 4d. LLM 层：五维 JSON 解析
rep = gc({"novel_id": 2, "extracted_entities": [{"name": "江观南", "attributes": {"身份": "刑警"}}],
          "extracted_relationships": [{"source": "江观南", "relation": "恋人", "target": "唐嘉措"}],
          "extracted_events": [{"summary": "江观南死了"}]})
conflicts = rep["consistency_report"]["conflicts"]
assert rep["consistency_report"]["checked"] is True
assert any(c["dimension"] == "时间线冲突" for c in conflicts), conflicts
print(f"  LLM 层五维 → 命中「时间线冲突」{sum(1 for c in conflicts if c['dimension']=='时间线冲突')} 条 ✅")
# 4e. LLM 非 JSON 容错
llm_client.chat = lambda *a, **k: "抱歉我不明白"
rep = gc({"novel_id": 2, "extracted_entities": [{"name": "江观南", "attributes": {"身份": "刑警"}}],
          "extracted_relationships": [], "extracted_events": []})
assert rep["consistency_report"]["checked"] is True
print("  LLM 非 JSON 输出 → 容错（checked=True，不阻断）✅")

# ========== 5. 图接线 ==========
print("【5】图接线")
added = []
orig_add = StateGraph.add_node
def spy(self, name, *a, **k):
    added.append(name)
    return orig_add(self, name, *a, **k)
StateGraph.add_node = spy
try:
    from app.graphs.build_graph import build_build_graph
    from app.graphs.qa_graph import build_qa_graph
    build_build_graph()
    build_qa_graph()
finally:
    StateGraph.add_node = orig_add

assert "graph_consistency" in added, added
assert "companion" in added and "multi_hop_inspiration" in added, added
print(f"  build_graph 含 graph_consistency（抽取汇合后入库前）✅")
print(f"  qa_graph 含 companion + multi_hop_inspiration ✅")

print()
print("🎉 Phase 0 三节点（Companion / MultiHopInspiration / GraphConsistency）全部验证通过" if ok else "❌ 存在失败项")
sys.exit(0 if ok else 1)
