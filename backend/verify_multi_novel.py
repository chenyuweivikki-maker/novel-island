"""验证：multi-novel 隔离 + 老行为兼容（不调真实LLM，只测存储/穿透层）"""
import sys, tempfile, os
sys.path.insert(0, "backend")
os.chdir(tempfile.mkdtemp())

from app.core.graph_store import KnowledgeGraph, GraphManager, get_graph_for

# 1. get_graph_for：None 回退全局单例
assert get_graph_for(None) is not None
from app.core.graph_store import graph as _global_graph
assert get_graph_for(None) is _global_graph, "None应回退全局单例"

# 2. 两个项目图谱互不污染
m = GraphManager()
g1 = get_graph_for(1)
g2 = get_graph_for(2)
g1.add_entity("项目1角色")
g2.add_entity("项目2角色")
assert "项目1角色" in get_graph_for(1).all_entities()
assert "项目1角色" not in get_graph_for(2).all_entities()

# 3. persona + chapter 标记，多项目保存/加载各自独立
g1.add_persona("项目1角色", {"职业": "店员"}, chapter_id=5)
g1.save()
g2.save()
# 重新取（模拟重启后）
m.remove_graph(1)
m.remove_graph(2)
g1r = get_graph_for(1)
assert g1r.query_attribute("项目1角色", "职业") == "店员", "项目1 persona 加载失败"
assert get_graph_for(2).query_attribute("项目1角色", "职业") is None, "项目2不应有项目1数据"

# 4. 按章删除在 multi-novel 下正确
g1r.remove_by_chapter(5)
assert g1r.query_attribute("项目1角色", "职业") is None, "按章删除失败"

# 5. 持久化路径隔离
assert g1r.persist_path == "data/graph_1.json"
assert get_graph_for(2).persist_path == "data/graph_2.json"

print("✅ 全部通过：get_graph_for回退、多项目隔离、persona按项目加载、按章删除、路径隔离")
