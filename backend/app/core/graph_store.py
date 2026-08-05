"""
知识图谱存储 — 里程碑8

用内存的邻接表实现"实体+关系+查询"（PRD：人物关系图/图谱推理）。
以后换 Neo4j 时只改这里（存储层），不改查询逻辑。

节点 = 实体（人物/地点/物品）
边   = 关系（source -[relation]-> target，带权重）

查询能力：
  query_neighbors(entity)  — 找某实体的所有直接关系
  query_path(from, to)     — 找两实体间的路径（多跳推理）
"""
import json
import os
from typing import Dict, List, Optional, Any


class KnowledgeGraph:
    """内存知识图谱（邻接表实现）"""

    def __init__(self):
        self._nodes: Dict[str, Dict[str, Any]] = {}  # 实体名 → 属性
        self._edges: List[Dict[str, Any]] = []       # 关系列表

    def add_entity(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """添加节点（实体已存在则合并属性）"""
        if name not in self._nodes:
            self._nodes[name] = attributes or {}
        elif attributes:
            self._nodes[name].update(attributes)

    def add_relation(self, source: str, relation: str, target: str, weight: float = 1.0):
        """添加边（source -[relation]-> target）"""
        self.add_entity(source)
        self.add_entity(target)
        self._edges.append({
            "source": source,
            "target": target,
            "relation": relation,
            "weight": weight,
        })

    def query_neighbors(self, entity: str) -> List[Dict[str, Any]]:
        """查询某实体的所有直接关系（邻居）"""
        neighbors = []
        for e in self._edges:
            if e["source"] == entity:
                neighbors.append({"entity": e["target"], "relation": e["relation"], "weight": e["weight"], "direction": "out"})
            elif e["target"] == entity:
                neighbors.append({"entity": e["source"], "relation": e["relation"], "weight": e["weight"], "direction": "in"})
        return neighbors

    def query_path(self, start: str, end: str, max_depth: int = 3) -> Optional[List[str]]:
        """BFS 找两实体间的路径（多跳推理）"""
        if start == end:
            return [start]
        queue = [(start, [start])]
        visited = {start}
        for _ in range(max_depth):
            if not queue:
                return None
            node, path = queue.pop(0)
            for neighbor in self.query_neighbors(node):
                n = neighbor["entity"]
                if n not in visited:
                    visited.add(n)
                    new_path = path + [n]
                    if n == end:
                        return new_path
                    queue.append((n, new_path))
        return None

    def all_entities(self) -> List[str]:
        return list(self._nodes.keys())

    def all_relations(self) -> List[Dict[str, Any]]:
        return list(self._edges)

    def __len__(self):
        return len(self._nodes)

    def save(self, path: str = "data/graph.json"):
        """持久化图谱到 JSON 文件"""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"nodes": self._nodes, "edges": self._edges}, f, ensure_ascii=False, indent=2)

    def load(self, path: str = "data/graph.json"):
        """从 JSON 文件加载图谱"""
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._nodes = data.get("nodes", {})
            self._edges = data.get("edges", [])


# 全局单例
graph = KnowledgeGraph()
