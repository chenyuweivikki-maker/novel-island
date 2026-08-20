"""知识库入库服务：素材解析入库（对话式建库）+ 建库进度引导。

从 main.py 拆分（分类管理）：main.py 只保留路由编排，本模块管「文本 → 知识库」的入库链路。
"""
from ..core.llm_client import chat  # noqa: F401（next_guide_question 未来可能用 LLM）
from ..core.vector_store import vector_store, vector_store_manager
from ..graphs.build_graph import build_app


def ingest_material(text: str, novel_id: int | None) -> str:
    """素材解析入库：跑建库状态机（清洗分块 → 并行抽取 → 图谱一致性 → 入库）→ 返回入库摘要

    设计：作者在对话里拖入/粘贴素材，Agent 自动解析并增量入库（对话式建库）。
    """
    result = build_app.invoke({
        "raw_input_files": [text],
        "novel_id": novel_id,
    })
    out = result["final_output"]
    chunks = result.get("processed_chunks", [])
    # 向量库增量追加（里程碑9/11：按项目）
    vs = vector_store_manager.get_store(novel_id) if novel_id is not None else vector_store
    vs.add_chunks(
        [c["text"] for c in chunks],
        [{"chunk_id": c["id"]} for c in chunks],
    )
    vs.save()

    n_ent = len(out.get("entities", []))
    n_rel = len(out.get("relationships", []))
    n_evt = len(out.get("events", []))
    summary = (
        f"收到，素材已解析入库：提取 {n_ent} 个人物、{n_rel} 条关系、{n_evt} 个事件，"
        f"新增 {len(chunks)} 个文本片段。"
    )
    conflicts = (result.get("consistency_report") or {}).get("conflicts", [])
    if conflicts:
        c = conflicts[0]
        summary += f"\n⚠️ 检测到 1 处图谱冲突：[{c['dimension']}] {c['conflict']}"
    summary += "\n\n" + next_guide_question(novel_id)
    return summary


def next_guide_question(novel_id: int | None) -> str:
    """按流程询问（题材→主角→配角→大纲→设定），用图谱已有内容推断当前进度，无需额外状态"""
    from ..core.graph_store import get_graph_for

    g = get_graph_for(novel_id)
    n_ents = len(g) if g else 0
    n_rels = len(g.all_relations()) if g and n_ents else 0
    n_evts = len(g.get_timeline()) if g and n_ents else 0

    if n_ents == 0:
        return ("知识库还是空的，我们从人设聊起吧——这本书的主角是谁？\n"
                "直接告诉我名字、性格、外貌都行；或者把素材（片段/设定）拖进对话框，我自动解析入库。")
    if n_rels == 0:
        return "主角已经有了。主要配角呢？他们和主角是什么关系？"
    if n_evts == 0:
        return "人物和关系都进库了，故事的大纲方向呢？想写一个什么故事？"
    return ("库在慢慢长大啦。可以继续补充设定，也可以直接去「写作编辑器」写第一章——"
            "保存后我会自动抽取人物、更新知识库。")
