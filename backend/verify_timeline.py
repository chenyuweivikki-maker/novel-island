"""验证：里程碑15 情节大事年表（mock LLM）

验证点：
1. ChapterSummaryExtractNode：输出格式正确、写 state chapter_summaries
2. build_graph 四路并行跑通，BuildOutputNode 把摘要写入图谱年表
3. chapter_id 穿透：save_chapter 场景摘要带章节标记
4. get_timeline 按序返回；remove_by_chapter 删对应章摘要
5. /api/timeline 接口（FastAPI TestClient）
"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(tempfile.mkdtemp())

import app.core.llm_client as llm_client

# ---- mock LLM：按 prompt 类型返回不同结果 ----
_calls = []
def fake_chat(system_prompt, user_prompt, **kwargs):
    _calls.append(system_prompt[:30])
    if "情节大事年表" in system_prompt or "大事年表" in system_prompt:
        # 摘要抽取：返回3条有序摘要
        return json.dumps([
            {"summary": "林晚设计事故后辞职自我封闭", "order": 0},
            {"summary": "林晚收到沈屿的鼓励回复", "order": 1},
            {"summary": "林晚遇到新邻居许言", "order": 2},
        ], ensure_ascii=False)
    return "[]"  # 其他抽取（实体/事件/关系）返回空

llm_client.chat = fake_chat

# ---- 1. ChapterSummaryExtractNode 单独测 ----
from app.nodes.build_nodes import ChapterSummaryExtractNode
from app.core.chunker import Chunk

node = ChapterSummaryExtractNode()
result = node({"processed_chunks": [{"id": 0, "text": "测试文本", "char_count": 4}]})
summaries = result["chapter_summaries"]
assert len(summaries) == 3, f"应抽3条摘要, 实际{len(summaries)}"
assert summaries[0]["summary"] == "林晚设计事故后辞职自我封闭"
assert summaries[0]["order"] == 0
assert summaries[2]["order"] == 2
print("✅ ChapterSummaryExtractNode 输出:", [s["summary"][:12] + "..." for s in summaries])

# ---- 2. build_graph 完整跑通 + BuildOutputNode 写入年表 ----
from app.graphs.build_graph import build_app
from app.core.graph_store import get_graph_for

# 用项目99隔离测试数据
res = build_app.invoke({
    "raw_input_files": ["林晚是设计师，事故后辞职。沈屿安慰她。后来遇到许言。"],
    "novel_id": 99,
    "chapter_id": 5,
})
g99 = get_graph_for(99)
timeline = g99.get_timeline()
assert len(timeline) == 3, f"年表应有3条, 实际{len(timeline)}"
assert all(t["chapter_id"] == 5 for t in timeline), "摘要应带 chapter_id=5"
assert [t["seq"] for t in timeline] == [0, 1, 2], "seq 应按序"
print("✅ build_graph 四路并行 + 年表写入（chapter_id=5 打标）")

# ---- 3. 再写一章（chapter_id=6），年表追加且带新标记 ----
# mock 每章返回3条摘要，所以章5(3条) + 章6(3条) = 6条
res2 = build_app.invoke({
    "raw_input_files": ["第二章：林晚重新备考。"],
    "novel_id": 99,
    "chapter_id": 6,
})
timeline2 = get_graph_for(99).get_timeline()
assert len(timeline2) == 6, f"追加后应有6条(3+3), 实际{len(timeline2)}"
assert all(t["chapter_id"] == 6 for t in timeline2[3:]), "后3条应带 chapter_id=6"
assert [t["seq"] for t in timeline2] == [0, 1, 2, 3, 4, 5], "seq 继续递增"
print("✅ 增量追加：第二章摘要带 chapter_id=6，seq 递增")

# ---- 4. 更新章5：remove_by_chapter 删旧摘要再写入 ----
g99.remove_by_chapter(5)
timeline3 = g99.get_timeline()
assert len(timeline3) == 3 and all(t["chapter_id"] == 6 for t in timeline3), \
    f"删章5后应只剩章6的3条, 实际{timeline3}"
# 重新写入章5（模拟更新：先删后加）
res3 = build_app.invoke({
    "raw_input_files": ["第一章（修订）：林晚辞职。"],
    "novel_id": 99,
    "chapter_id": 5,
})
timeline4 = get_graph_for(99).get_timeline()
assert len(timeline4) == 6, f"重写章5后应有6条, 实际{len(timeline4)}"
assert timeline4[0]["chapter_id"] == 6 and timeline4[3]["chapter_id"] == 5, \
    f"章6摘要应在前面、章5新摘要追加在后面: {timeline4}"
print("✅ 按章更新：先删旧摘要再写新，年表正确")

# ---- 5. /api/timeline 接口 ----
from fastapi.testclient import TestClient
import app.main as main_module
# mock 掉建库状态机（避免真实 LLM），只测 timeline 接口
main_module.build_app.invoke = lambda state, **k: {
    "processed_chunks": [{"id": 0, "text": "x", "char_count": 1}],
    "final_output": {"entities": [], "events": []},
}
client = TestClient(main_module.app)
r = client.get("/api/timeline", params={"novel_id": 99})
data = r.json()
assert data["total"] == 6, data
assert data["timeline"][0]["chapter_id"] == 6
r2 = client.get("/api/timeline")
assert r2.json()["total"] == 0, "默认库应为空（隔离）"
print("✅ /api/timeline 接口：按项目返回、隔离正确")

# 清理项目99测试数据
from app.core.graph_store import graph_manager
graph_manager.remove_graph(99)

print("\n🎉 里程碑15 情节大事年表全部验证通过")
