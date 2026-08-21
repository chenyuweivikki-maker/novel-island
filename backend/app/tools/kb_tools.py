"""
知识库工具集 — 里程碑3：tool calling

这里定义 Agent 可以调用的工具。
每个工具 = 一个 JSON Schema 说明书（给 LLM 看）+ 一个执行函数（给代码跑）。

  search_kb  — 从知识库检索相关原文片段
"""
from typing import List, Dict, Any

from ..core.hybrid_retriever import hybrid_search


# ===== 工具定义（JSON Schema，发给 LLM 的"说明书"）=====
SEARCH_KB_TOOL = {
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": "搜索小说知识库，找到与问题最相关的原文片段。"
                       "当用户询问小说中的人物、情节、设定时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要检索的内容，通常是用户问题本身或关键词",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回的片段数量，默认 5",
                },
            },
            "required": ["query"],
        },
    },
}


# ===== 工具执行函数（LLM 决定调用后，代码跑这个）=====
def execute_search_kb(query: str, top_k: int = 5, novel_id: int | None = None) -> List[Dict[str, Any]]:
    """执行 search_kb：混合检索知识库（向量+TF-IDF），返回格式化后的片段列表

    注意：这个函数是"给代码跑"的，不是给 LLM 的。
    它把检索结果转成可读文本，方便塞回 LLM 上下文。
    里程碑17：novel_id 由工具上下文注入（不在 LLM 可见的工具参数里），
    确保 Agent 检索的是当前项目的内容。
    """
    results = hybrid_search(query, top_k, novel_id)

    # 把结果整理成 LLM 能看懂的格式
    return [
        {
            "chunk_id": r["chunk"].id,
            "score": round(r["score"], 4),
            "text": r["chunk"].text,
        }
        for r in results
    ]


# ===== PRD 五大工具补齐（路线图 P1-4）：润色 / 灵感 / 人设校验 / 逻辑检查 =====

POLISH_TOOL = {
    "type": "function",
    "function": {
        "name": "polish_writing_style",
        "description": "对一段用户文本进行润色，可调整语言风格或改善文笔。"
                       "当用户要求润色、改写、让文字更好时使用。返回润色后的文本。",
        "parameters": {
            "type": "object",
            "properties": {
                "original_text": {"type": "string", "description": "需要润色的原文"},
                "target_style": {"type": "string", "description": "目标风格，如'更有画面感''更简洁''保持原风格'，默认'保持作者原有风格，轻度润色'"},
            },
            "required": ["original_text"],
        },
    },
}

BRAINSTORM_TOOL = {
    "type": "function",
    "function": {
        "name": "brainstorm_plot_ideas",
        "description": "基于当前剧情和人物设定，生成多个后续情节发展建议。"
                       "当用户卡文、要灵感、问'接下来怎么写'时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "current_context": {"type": "string", "description": "当前剧情/卡点描述"},
                "direction": {"type": "string", "description": "希望的方向，如'感情线''事业线''悬疑线'，可空"},
            },
            "required": ["current_context"],
        },
    },
}

CHARACTER_CHECK_TOOL = {
    "type": "function",
    "function": {
        "name": "evaluate_character_consistency",
        "description": "判断一段人物对话或行为是否符合该角色的已知性格设定。"
                       "当用户担心'人设崩了''角色OOC'、检查角色行为是否一致时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "character_name": {"type": "string", "description": "要检查的角色名"},
                "dialogue_or_action": {"type": "string", "description": "该角色的一段对话或行为描述"},
            },
            "required": ["character_name", "dialogue_or_action"],
        },
    },
}

PLOT_CHECK_TOOL = {
    "type": "function",
    "function": {
        "name": "check_plot_consistency",
        "description": "检查新的情节构思与知识库中已有设定是否存在逻辑矛盾（时间线、人物关系等）。"
                       "当用户问'这段逻辑有问题吗''前后矛盾吗'时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "new_content": {"type": "string", "description": "新写的情节/构思"},
            },
            "required": ["new_content"],
        },
    },
}


def execute_polish_writing_style(original_text: str, target_style: str = "保持作者原有风格，轻度润色", novel_id: int | None = None) -> Dict[str, Any]:
    """执行润色（复用润色 prompt）"""
    from ..core.llm_client import chat
    if not original_text or not original_text.strip():
        return {"error": "没有可润色的文本"}
    text = original_text.strip()[:4000]
    user_prompt = f"润色下面的段落。风格要求：{target_style}。\n\n--- 原文 ---\n{text}"
    try:
        result = chat(
            "你是资深网文编辑，负责给作者的段落做润色。保留原文情节/人物/对话含义，不新增事实。只输出润色后的正文，不要解释。",
            user_prompt, temperature=0.4, max_tokens=2048, task="creative",
        )
        return {"polished": result.strip()}
    except Exception as e:
        return {"error": f"润色失败: {e}"}


def execute_brainstorm_plot_ideas(current_context: str, direction: str = "", novel_id: int | None = None) -> Dict[str, Any]:
    """执行灵感拓展（单跳，直接基于上下文生成；多跳由 MultiHopInspirationNode 负责）"""
    from ..core.llm_client import chat
    from ..core.hybrid_retriever import hybrid_search
    try:
        results = hybrid_search(current_context, 4, novel_id)
        ctx = "\n".join(f"· {x['chunk'].text[:200]}" for x in results)
        prompt = (
            f"基于以下小说已有内容，为作者提供 3 个不同的后续情节方向"
            + (f"（侧重：{direction}）" if direction else "")
            + "。每个方向 2-4 句话，给出具体情节构思而非泛泛建议。\n\n"
            f"--- 已有内容 ---\n{ctx or '（暂无检索结果）'}\n\n--- 当前卡点 ---\n{current_context}"
        )
        system = "你是资深网文作者的创作搭子，擅长给出具体、可落地、不套路的情节方向。分点输出。"
        # 程序性记忆注入：作者近期的采纳/拒绝偏好（避开被拒方向，优先被采纳方向）
        try:
            from ..core.procedural_memory import preference_summary
            pref = preference_summary(novel_id)
            if pref:
                system += f"\n\n【作者偏好参考】{pref}\n（推荐时避开作者拒绝过的方向，优先作者采纳过的方向）"
        except Exception as e:
            print(f"[memory] 偏好注入失败: {e}")
        answer = chat(
            system,
            prompt, temperature=0.8, max_tokens=1200, task="inspire",
        )
        return {"ideas": answer.strip()}
    except Exception as e:
        return {"error": f"灵感生成失败: {e}"}


def execute_evaluate_character_consistency(character_name: str, dialogue_or_action: str, novel_id: int | None = None) -> Dict[str, Any]:
    """执行人设一致性校验（查图谱人设 + LLM 评判）"""
    from ..core.llm_client import chat
    from ..core.graph_store import get_graph_for
    try:
        g = get_graph_for(novel_id)
        node = g.get_entity(character_name) if g else None
        persona = (node or {}).get("persona", {})
        persona_text = "\n".join(f"{k}：{v}" for k, v in persona.items()) or "（知识库中暂无该角色的设定）"
        prompt = (
            f"角色设定：\n{persona_text}\n\n"
            f"待检查的内容（{character_name}的对话/行为）：\n{dialogue_or_action}\n\n"
            "请判断这段内容是否符合角色设定：1) 给出结论（符合/部分符合/不符合）；"
            "2) 说明依据；3) 如有偏离，给出如何修正。如果知识库没有该角色设定，说明无法判断。"
        )
        answer = chat(
            "你是小说人设质检编辑，严格对照角色设定评判 OOC 问题，语气平和、给可执行的修改建议。",
            prompt, temperature=0.3, max_tokens=800, task="logic",
        )
        return {"result": answer.strip()}
    except Exception as e:
        return {"error": f"人设校验失败: {e}"}


def execute_check_plot_consistency(new_content: str, novel_id: int | None = None) -> Dict[str, Any]:
    """执行情节一致性检查（复用 consistency_tools）"""
    from .consistency_tools import check_plot_consistency as _check
    try:
        conflicts = _check(new_content, novel_id=novel_id)
        if not conflicts:
            return {"result": "未发现明显逻辑矛盾。", "conflicts": []}
        return {
            "result": "发现以下逻辑矛盾：\n" + "\n".join(f"- {c['conflict']}" for c in conflicts),
            "conflicts": conflicts,
        }
    except Exception as e:
        return {"error": f"一致性检查失败: {e}"}


# 工具名 → 执行函数的映射表（Agent 根据 LLM 的 tool_call 名字查表执行）
TOOL_EXECUTORS = {
    "search_kb": execute_search_kb,
    "polish_writing_style": execute_polish_writing_style,
    "brainstorm_plot_ideas": execute_brainstorm_plot_ideas,
    "evaluate_character_consistency": execute_evaluate_character_consistency,
    "check_plot_consistency": execute_check_plot_consistency,
}

# 所有可用工具的清单（给 LLM 的说明书集合，定义必须在本文件所有工具之后）
AVAILABLE_TOOLS = [
    SEARCH_KB_TOOL,
    POLISH_TOOL,
    BRAINSTORM_TOOL,
    CHARACTER_CHECK_TOOL,
    PLOT_CHECK_TOOL,
]
