"""验证：set序列化修复 + GraphManager按项目隔离"""
import sys, tempfile, os
sys.path.insert(0, "backend")
# 用临时目录存数据，不污染仓库
os.chdir(tempfile.mkdtemp())

from app.core.graph_store import KnowledgeGraph, GraphManager

# 1. 基本图：加实体+persona（带chapter_id → 触发set）
g = KnowledgeGraph(persist_path="data/graph_1.json")
g.add_entity("唐嘉措", {"identity": "主角"})
g.add_persona("唐嘉措", {"职业": "店员", "宠物": "汪汪"}, chapter_id=1)
g.add_relation("唐嘉措", "喜欢", "江观南", weight=1.0, chapter_id=1)
g.save()  # 修复前这里会 TypeError: Object of type set is not JSON serializable

# 2. 重新加载：persona_chapters 应转回 set
g2 = KnowledgeGraph(persist_path="data/graph_1.json")
g2.load()
assert g2.query_attribute("唐嘉措", "职业") == "店员", "persona加载失败"
pc = g2._nodes["唐嘉措"].get("persona_chapters", {})
assert isinstance(pc.get("职业"), set), f"persona_chapters应为set, 实际{type(pc.get('职业'))}"
assert 1 in pc.get("职业", set())

# 3. remove_by_chapter 仍能正确删独占属性
g2.remove_by_chapter(1)
assert g2.query_attribute("唐嘉措", "职业") is None, "按章删除失败"
assert len(g2.all_relations()) == 0, "按章删除边失败"

# 4. GraphManager 多项目隔离
m = GraphManager()
ga = m.get_graph(1)
gb = m.get_graph(2)
ga.add_entity("甲")
gb.add_entity("乙")
assert "甲" in ga.all_entities() and "乙" not in ga.all_entities()
assert "乙" in gb.all_entities() and "甲" not in gb.all_entities()
assert ga.persist_path == "data/graph_1.json"
assert gb.persist_path == "data/graph_2.json"
m.remove_graph(1)
assert 1 not in m._graphs

print("✅ 全部通过：set序列化/加载、按章删除、多项目隔离")
