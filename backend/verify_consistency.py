"""验证：check_plot_consistency 冲突检测（mock LLM，不真实调用）"""
import sys, os, tempfile, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
os.chdir(tempfile.mkdtemp())

# ---- mock 掉 chat，返回预定义的 JSON ----
import app.core.llm_client as llm_client
_calls = []

def fake_chat(system_prompt, user_prompt, **kwargs):
    _calls.append({"system": system_prompt[:50], "user": user_prompt, "kwargs": kwargs})
    return json.dumps([
        {"conflict": "唐嘉措的职业前后不一致", "old": "店员", "new": "老板", "severity": "high"},
        {"conflict": "时间线矛盾", "old": "白天", "new": "晚上", "severity": "medium"},
    ], ensure_ascii=False)

llm_client.chat = fake_chat

from app.tools.consistency_tools import check_plot_consistency, _build_consistency_prompt

# ---- 造检索数据：往全局向量库塞旧内容 ----
from app.core.retriever import retriever
from app.core.chunker import Chunk
old_text = "唐嘉措是24岁的店员，在咖啡店工作。她住在江观南楼下。"
retriever.build_index([Chunk(id=0, text=old_text, char_count=len(old_text))])

# ---- 1. 直接测工具（不传 novel_id，用全局单例） ----
new_content = "唐嘉措现在是咖啡店老板了，她决定辞掉店员的工作。"
conflicts = check_plot_consistency(new_content)

assert len(_calls) == 1, f"应调用一次LLM，实际{len(_calls)}"
assert _calls[0]["kwargs"].get("task") == "logic", f"task应为logic, 实际{_calls[0]['kwargs'].get('task')}"
assert len(conflicts) == 2, f"应返回2条冲突, 实际{len(conflicts)}"
assert conflicts[0]["severity"] == "high"
print("✅ check_plot_consistency 返回冲突:", [c["conflict"] for c in conflicts])

# ---- 2. 测 prompt 拼接（旧片段应出现在 prompt 里） ----
prompt = _calls[0]["user"]
assert "唐嘉措是24岁的店员" in prompt, "旧片段没拼进prompt"
assert "咖啡店老板" in prompt, "新章节没拼进prompt"
print("✅ prompt 拼接正确（旧片段+新章节都在）")

# ---- 3. 空库时返回空列表 ----
retriever.chunks.clear()
conflicts = check_plot_consistency("新章节内容")
assert conflicts == [], f"空库应返回空列表, 实际{conflicts}"
print("✅ 空库返回空列表")

# ---- 4. LLM 返回非法 JSON 时容错 ----
llm_client.chat = lambda *a, **k: "不是JSON"
retriever.build_index([Chunk(id=0, text=old_text, char_count=len(old_text))])
conflicts = check_plot_consistency("新章节内容")
assert conflicts == [], f"非法JSON应返回空, 实际{conflicts}"
print("✅ 非法JSON容错")

print("\n🎉 冲突检测工具全部验证通过")
