"""
图谱一致性校验节点 — 路线图 P1-6

PRD 五维冲突检查：新增实体/关系/事件时，对照已有图谱找冲突：
  1. 生死冲突：角色不能死了又活
  2. 关系矛盾：关系应单向一致
  3. 时间线冲突：事件应发生在依赖事件之后
  4. 属性冲突：关键属性不能随意变更
  5. 人物关系冲突：新关系与已有关系相悖

实现：规则层 + LLM 层
  - 规则层（确定性、零成本）：
      同对人物（不分方向）已有不同关系类型 → 人物关系冲突
      同实体同属性键值互不包含 → 属性冲突
  - LLM 层（语义判断，task=logic 走高阶模型）：
      已有图谱摘要 + 新抽取数据 → 五维冲突检查

插入位置：建库状态机中 并行抽取汇合之后、build_output（图谱写入+保存）之前。
此时图谱还是"旧"的，正好对照新数据 —— 对应路线图"save 之前插入"的意图。

输出：state["consistency_report"] = {"conflicts": [...], "checked": bool}
不阻断写入 —— 报告由作者决定是否修改（PRD：作者决定）。
"""
import json
from typing import Any, Dict, List

from ..core.llm_client import chat
from ..core.graph_store import get_graph_for
from ..models.state import NovelIslandState


# ===== LLM 层：五维一致性检查 =====
GRAPH_CONSISTENCY_SYSTEM_PROMPT = """你是「小说岛」的图谱一致性检查员，负责对照"已有知识图谱"和"新抽取的知识数据"，找出冲突。

检查五维：
1. 生死冲突：角色不能死了又活（已有"死亡/去世"设定，新内容里又正常活动）
2. 关系矛盾：关系应单向一致（如 A 是 B 的房东，反过来 B 应是 A 的房客）
3. 时间线冲突：事件应发生在依赖事件之后（后发生的事件不能早于其前提）
4. 属性冲突：关键属性（身份/职业/外貌/年龄等）不能随意变更
5. 人物关系冲突：两人关系类型前后相悖（如 仇敌 ↔ 恋人）

规则：
1. 只报能明确指出的冲突，拿不准的不报（避免误报）
2. 每条给出：冲突内容 + 已有图谱的说法 + 新数据的说法
3. 输出严格的 JSON 数组，格式：
[{"dimension": "生死冲突|关系矛盾|时间线冲突|属性冲突|人物关系冲突", "conflict": "冲突描述", "old": "已有图谱的说法", "new": "新数据的说法", "severity": "high|medium|low"}]
4. 没有冲突输出空数组 []
5. 只输出 JSON，不要其他文字"""


def _build_graph_digest(g, max_entities: int = 40, max_relations: int = 80, max_events: int = 20) -> str:
    """把已有图谱压成文本摘要（实体+属性 / 关系 / 最近情节年表）"""
    lines: List[str] = []

    entities = g.all_entities()[:max_entities]
    if entities:
        lines.append("【已有实体】")
        for name in entities:
            node = g.get_entity(name)
            persona = node.get("persona", {}) if node else {}
            attrs = "、".join(f"{k}:{v}" for k, v in list(persona.items())[:6]) or "（无属性）"
            lines.append(f"- {name}（{attrs}）")

    relations = g.all_relations()
    if relations:
        lines.append("【已有关系】")
        for r in relations[:max_relations]:
            lines.append(f"- {r.get('source', '')} -[{r.get('relation', '')}]-> {r.get('target', '')}")

    events = g.get_timeline()[-max_events:]
    if events:
        lines.append("【已有情节年表（最近）】")
        for ev in events:
            lines.append(f"- {ev.get('summary', '')}")

    return "\n".join(lines) or "（图谱为空）"


def _build_new_data_text(entities: List[dict], relations: List[dict], events: List[dict]) -> str:
    """把新抽取的实体/关系/事件压成文本"""
    lines: List[str] = []

    if entities:
        lines.append("【新抽取的实体】")
        for e in entities:
            name = e.get("name", "")
            attrs = e.get("attributes") or {}
            attr_str = "、".join(f"{k}:{v}" for k, v in list(attrs.items())[:6]) if attrs else "（无）"
            lines.append(f"- {name}（{attr_str}）")

    if relations:
        lines.append("【新抽取的关系】")
        for r in relations:
            lines.append(f"- {r.get('source', '')} -[{r.get('relation', '')}]-> {r.get('target', '')}")

    if events:
        lines.append("【新抽取的事件】")
        for ev in events:
            lines.append(f"- {ev.get('summary', '')}")

    return "\n".join(lines) or "（无新数据）"


def _parse_conflicts(output: str) -> List[Dict[str, Any]]:
    """解析 LLM 冲突报告 JSON 数组（容错：去 markdown 围栏）"""
    text = output.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict) and d.get("conflict")][:20]
    except (json.JSONDecodeError, AttributeError):
        pass
    return []


# ===== 规则层：确定性检查 =====
def _rule_check_relations(new_relations: List[dict], g) -> List[Dict[str, Any]]:
    """同对人物（不分方向）已有不同关系类型 → 人物关系冲突"""
    conflicts: List[Dict[str, Any]] = []
    for r in new_relations:
        source, target, rel = r.get("source", ""), r.get("target", ""), r.get("relation", "")
        if not source or not target or not rel:
            continue
        pair = {source, target}
        for e in g.all_relations():
            if {e.get("source"), e.get("target")} == pair and e.get("relation") != rel:
                conflicts.append({
                    "dimension": "人物关系冲突",
                    "conflict": f"「{source}」与「{target}」的关系不一致：已有「{e.get('relation')}」，新内容写「{rel}」",
                    "old": f"{e.get('source')} -[{e.get('relation')}]-> {e.get('target')}",
                    "new": f"{source} -[{rel}]-> {target}",
                    "severity": "high",
                })
                break  # 同一对人物只报一条
    return conflicts


def _rule_check_attributes(new_entities: List[dict], g) -> List[Dict[str, Any]]:
    """同实体已有 persona 属性 vs 新属性：同键值互不包含 → 属性冲突"""
    conflicts: List[Dict[str, Any]] = []
    for e in new_entities:
        name = e.get("name", "")
        if not name:
            continue
        old_node = g.get_entity(name)
        if not old_node:
            continue
        old_persona = old_node.get("persona", {}) or {}
        for key, new_val in (e.get("attributes") or {}).items():
            old_val = old_persona.get(key)
            if not old_val or not new_val:
                continue
            old_s, new_s = str(old_val), str(new_val)
            if old_s == new_s:
                continue
            # 包含关系视为正常补充（如"美妆博主" vs "千万粉美妆博主"），不报
            if old_s in new_s or new_s in old_s:
                continue
            conflicts.append({
                "dimension": "属性冲突",
                "conflict": f"「{name}」的「{key}」前后不一致",
                "old": old_s,
                "new": new_s,
                "severity": "medium",
            })
    return conflicts


class GraphConsistencyNode:
    """图谱一致性校验节点 — 路线图 P1-6

    在建库状态机的"并行抽取汇合后、图谱入库前"运行：
      1. 规则层：人物关系冲突 + 属性冲突（确定性、零成本）
      2. LLM 层：五维语义检查（task=logic 走高阶模型）
    输出 state['consistency_report']，不阻断入库（报告由作者决定）。
    """

    name = "graph_consistency"

    def __call__(self, state: NovelIslandState) -> Dict[str, Any]:
        g = get_graph_for(state.get("novel_id"))
        entities = state.get("extracted_entities", [])
        relations = state.get("extracted_relationships", [])
        events = state.get("extracted_events", [])

        report = {"conflicts": [], "checked": False}

        # 首次建库（图谱为空）或没有新数据 → 无可对照，跳过检查（省成本）
        if len(g) == 0 or not (entities or relations):
            return {
                "consistency_report": report,
                "current_step": self.name,
            }

        # 1. 规则层（确定性、零成本）
        conflicts: List[Dict[str, Any]] = []
        conflicts += _rule_check_relations(relations, g)
        conflicts += _rule_check_attributes(entities, g)

        # 2. LLM 层（五维语义检查，失败不阻断，降级为一条提示）
        try:
            digest = _build_graph_digest(g)
            new_data = _build_new_data_text(entities, relations, events)
            prompt = (
                f"以下是已有的知识图谱：\n\n{digest}\n\n---\n\n"
                f"以下是新抽取的知识数据：\n\n{new_data}\n\n---\n\n"
                "请检查新数据与已有图谱之间的五维冲突。只输出JSON数组。"
            )
            llm_output = chat(
                GRAPH_CONSISTENCY_SYSTEM_PROMPT,
                prompt,
                temperature=0.0,
                max_tokens=1536,
                task="logic",
            )
            conflicts += _parse_conflicts(llm_output)
        except Exception as e:
            conflicts.append({
                "dimension": "检查失败",
                "conflict": f"图谱一致性检查未能完成：{e}",
                "old": "",
                "new": "",
                "severity": "low",
            })

        return {
            "consistency_report": {"conflicts": conflicts, "checked": True},
            "current_step": self.name,
        }
