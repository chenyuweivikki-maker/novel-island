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
import copy
import json
import os
from typing import Dict, List, Optional, Any


class KnowledgeGraph:
    """内存知识图谱（邻接表实现）"""

    def __init__(self, persist_path: str = "data/graph.json"):
        self._nodes: Dict[str, Dict[str, Any]] = {}  # 实体名 → 属性
        self._edges: List[Dict[str, Any]] = []       # 关系列表
        self._events: List[Dict[str, Any]] = []      # 里程碑15：情节大事年表
        self._event_seq: int = 0                     # 年表全局递增序号（保证入库顺序）
        self.persist_path = persist_path

    def add_entity(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """添加节点（实体已存在则合并属性）

        里程碑8：attributes 存关系数据（identity/traits等）
        里程碑9：persona 存人设属性（职业/性格/外貌/宠物等键值）
        """
        if name not in self._nodes:
            self._nodes[name] = {
                "attributes": attributes or {},
                "persona": {},
            }
        elif attributes:
            self._nodes[name].setdefault("attributes", {}).update(attributes)

    def add_persona(self, entity: str, persona: Dict[str, Any], chapter_id: int | None = None):
        """给实体挂人设属性（职业/性格/外貌/宠物等键值） — 里程碑9
        chapter_id 标记来源章节，用于按章删除（里程碑10）。
        """
        node = self._nodes.setdefault(entity, {"attributes": {}, "persona": {}, "persona_chapters": {}})
        node.setdefault("persona", {})
        node.setdefault("persona_chapters", {})
        for key, val in persona.items():
            node["persona"][key] = val
            if chapter_id is not None:
                # 记录每个属性来自哪些章节（用于按章回滚）
                node["persona_chapters"].setdefault(key, set())
                node["persona_chapters"][key].add(chapter_id)

    def remove_by_chapter(self, chapter_id: int):
        """删除某章节贡献的图谱数据（里程碑10）
        1. 删该章的关系边
        2. 删该章贡献的人设属性（属性只被这一章提过才删）
        3. 删该章的年表摘要（里程碑15）
        """
        # 1. 删边
        self._edges = [e for e in self._edges if e.get("chapter_id") != chapter_id]

        # 2. 删该章独占的人设属性
        for entity, node in self._nodes.items():
            p_chapters = node.get("persona_chapters", {})
            to_remove = []
            for key, chapters in p_chapters.items():
                if chapter_id in chapters:
                    chapters.discard(chapter_id)
                    if not chapters:  # 没有其他章节引用该属性 → 删除
                        to_remove.append(key)
            for key in to_remove:
                node.get("persona", {}).pop(key, None)
                p_chapters.pop(key, None)

        # 3. 删该章的年表摘要（里程碑15：改章节时先删旧摘要）
        self._events = [e for e in self._events if e.get("chapter_id") != chapter_id]

    def query_attribute(self, entity: str, attr: str) -> Optional[Any]:
        """精确查询实体的某个属性值（如 唐嘉措 → 宠物）"""
        node = self._nodes.get(entity)
        if not node:
            return None
        persona = node.get("persona", {})
        # 属性名精确匹配；也尝试子串匹配（用户说"猫名"能查到"宠物"下的值）
        for key, val in persona.items():
            if attr in key or key in attr:
                return val
        return None

    def query_entity_by_attribute(self, attr: str) -> List[str]:
        """查所有拥有某属性的实体（如"谁有宠物"）"""
        results = []
        for name, node in self._nodes.items():
            for key in node.get("persona", {}):
                if attr in key or key in attr:
                    results.append(name)
                    break
        return results

    def add_relation(self, source: str, relation: str, target: str, weight: float = 1.0, chapter_id: int | None = None):
        """添加边（source -[relation]-> target），可带章节标记（里程碑10）"""
        self.add_entity(source)
        self.add_entity(target)
        edge = {
            "source": source,
            "target": target,
            "relation": relation,
            "weight": weight,
        }
        if chapter_id is not None:
            edge["chapter_id"] = chapter_id
        self._edges.append(edge)

    def add_chapter_summary(self, summary: str, chapter_id: int | None = None) -> int:
        """追加一条年表摘要，返回其序号（里程碑15：情节大事年表）

        seq 全局递增，天然按入库顺序排列（无需外部排序）。
        chapter_id 标记来源章节，用于按章更新/删除（save_chapter 改旧章节）。
        """
        self._events.append({
            "seq": self._event_seq,
            "summary": summary,
            "chapter_id": chapter_id,
        })
        self._event_seq += 1
        return self._event_seq - 1

    def get_timeline(self) -> List[Dict[str, Any]]:
        """按入库顺序返回情节大事年表（seq 升序）"""
        return sorted(self._events, key=lambda e: e.get("seq", 0))

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

    def get_entity(self, name: str) -> Optional[Dict[str, Any]]:
        """返回实体的完整信息（含 persona）"""
        return self._nodes.get(name)

    def all_relations(self) -> List[Dict[str, Any]]:
        return list(self._edges)

    def __len__(self):
        return len(self._nodes)

    def save(self, path: str | None = None):
        """持久化图谱到 JSON 文件（默认存到本实例的 persist_path）

        注意：persona_chapters 里是 set，JSON 存不了，存前转 list、加载后转回 set。
        """
        path = path or self.persist_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 深拷贝一份再序列化，避免污染内存里的数据
        nodes = copy.deepcopy(self._nodes)
        for node in nodes.values():
            pc = node.get("persona_chapters", {})
            for key, chapters in pc.items():
                node["persona_chapters"][key] = list(chapters)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({
                "nodes": nodes,
                "edges": self._edges,
                "events": self._events,      # 里程碑15：情节大事年表
                "event_seq": self._event_seq,
            }, f, ensure_ascii=False, indent=2)

    def load(self, path: str | None = None):
        """从 JSON 文件加载图谱（默认读本实例的 persist_path）"""
        path = path or self.persist_path
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self._nodes = data.get("nodes", {})
            self._edges = data.get("edges", [])
            self._events = data.get("events", [])          # 里程碑15（老数据无此字段→空）
            self._event_seq = data.get("event_seq", len(self._events))
            # 老数据里的 persona_chapters 可能是 list，转回 set（remove_by_chapter 用 discard）
            for node in self._nodes.values():
                pc = node.get("persona_chapters", {})
                for key, chapters in pc.items():
                    if isinstance(chapters, list):
                        node["persona_chapters"][key] = set(chapters)


class GraphManager:
    """按项目(novel_id)管理多个知识图谱 — 里程碑11：多租户隔离

    每个项目一份独立图谱，数据互不污染。
    路径：data/graph_{novel_id}.json
    """

    def __init__(self):
        self._graphs: Dict[int, KnowledgeGraph] = {}

    def get_graph(self, novel_id: int) -> KnowledgeGraph:
        """获取某项目的图谱（不存在则创建并加载）"""
        if novel_id not in self._graphs:
            g = KnowledgeGraph(persist_path=f"data/graph_{novel_id}.json")
            g.load()
            self._graphs[novel_id] = g
        return self._graphs[novel_id]

    def remove_graph(self, novel_id: int):
        """删除某项目图谱（内存）"""
        self._graphs.pop(novel_id, None)


# 全局单例
graph = KnowledgeGraph()
graph_manager = GraphManager()


def get_graph_for(novel_id: int | None) -> KnowledgeGraph:
    """取某项目(或默认)的知识图谱 — 里程碑11

    novel_id 为 None 时回退到全局单例 graph（兼容老接口/未分项目场景）。
    """
    if novel_id is None:
        return graph
    return graph_manager.get_graph(novel_id)
