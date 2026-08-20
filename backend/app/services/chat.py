"""无项目对话服务：首页闲聊的 LLM 回复分支（建书引导 / 灵感 / 逻辑检查 / 建书告知）。

从 main.py 拆分（分类管理）：所有"还没选书时的小说猫对话"都在这里。
"""
import json

from fastapi.responses import StreamingResponse

from ..core.llm_client import chat, chat_stream
from ..core.memory import memory_manager
from ..core.fallback_templates import general_opening_fallback
from .title_sync import _maybe_sync_book_title

NO_PROJECT_SYSTEM_PROMPT = """你是「小说岛」的写作搭子「小说猫」，正在陪一位作者开启一本新书。

【任务】自然接住作者的话，向开书推进：
1. 没书名 → 问书名；有书名没题材 → 问题材；都定了 → 问主角/核心设定
2. 作者已给信息 → 先肯定/共鸣，再自然问下一个必要信息
3. 作者有明确建书意图但信息不足 → 逐项收集（书名/题材/主角），一次只问一个
4. 如果作者在倾诉情绪/卡文 → 先接住情绪（温柔共情），再轻轻引导
5. 语气：像朋友一样自然、简短（1-3 句话），不要机械，不要说"根据设定""作为AI"这类话
6. 不要编造作者没说的书名或题材，不要重复刚才已经说过的话

【动作描写规则】你是一只猫形写作搭子，可以偶尔在回复开头加一句动作描写增加生动感，但必须遵守：
- 动作要多样化、轮换使用，避免每次都是同一种（不要反复用"尾巴拍拍你""眼睛一亮"）
- 可轮换的动作示例：耳朵抖了抖 / 胡须微微颤动 / 用爪子轻轻拨了拨桌上的稿纸 / 打了个小小的哈欠 / 尾巴尖轻轻摆动 / 探过头来看了一眼屏幕 / 舔了舔爪子 / 眯起眼睛笑了笑 / 竖起耳朵认真听 / 在桌沿来回踱了两步 / 用脑袋蹭了蹭你的手 / 蜷成一团又直起身 / 尾巴绕了个圈 / 眨巴眨巴眼睛
- 不要每次都加动作：大约每 2-3 条回复才用一次，其余时候直接说话
- 动作要贴合对话气氛（安慰时温柔、聊到兴奋处活泼），一句话带过即可，不要喧宾夺主

之前的对话（可能有）：
{history}"""


def _llm_no_project_reply(query: str, novel_id: int | None = None, brief: bool = False, session_id: str = "default",
                            model: str = "", temperature: float | None = None, persona: str = "") -> str:
    """无项目对话统一走 LLM（带短期记忆，记住书名/题材等上下文）"""
    memory = memory_manager.get_memory(novel_id, session_id)
    history = memory.get_context()
    history_text = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in history[-6:]  # 最近几轮，避免超长
    ) or "（无）"
    system = NO_PROJECT_SYSTEM_PROMPT.format(history=history_text)
    if persona and persona.strip():
        system += f"\n\n作者要求你的人设/语气：{persona.strip()}"
    user_prompt = f"作者说：{query}\n\n请以小说猫的口吻回应。"
    try:
        reply = chat(
            system,
            user_prompt,
            temperature=temperature if temperature is not None else 0.8,
            max_tokens=300,
            task="companion",
            model=model or None,
        ).strip()
    except Exception as e:
        print(f"[ask] 无项目 LLM 引导失败: {e}")
        reply = general_opening_fallback(query)
    # 记忆该轮（无项目也记，让后续能接住书名/题材）
    memory.add_turn(query, reply)
    return reply


def _no_project_stream_or_dict(req, reply_fn, *args, **kwargs):
    """无项目/空库分支统一出口：req.stream=True 时返回 SSE 流式，否则返回 dict。

    让首页对话也具备打字机效果（和创作页一致）。reply_fn 是生成回复的函数，
    流式模式用 chat_stream 逐 token 输出；非流式保持原逻辑。
    """
    if not req.stream:
        answer = reply_fn(req.query, *args, session_id=req.session_id,
                          model=req.model, temperature=req.temperature, persona=req.persona, **kwargs)
        # 对话提书名 → 同步更新创作/我的作品的书名
        _maybe_sync_book_title(req.session_id, req.query)
        return {"answer": answer, "sources": []}

    # 流式：先取记忆上下文（与 reply_fn 一致），再 chat_stream 生成
    memory = memory_manager.get_memory(req.novel_id, req.session_id)
    history = memory.get_context()
    history_text = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in history[-6:]
    ) or "（无）"
    system_prompt = NO_PROJECT_SYSTEM_PROMPT.format(history=history_text)
    if req.persona and req.persona.strip():
        system_prompt += f"\n\n作者要求你的人设/语气：{req.persona.strip()}"
    user_prompt = f"作者说：{req.query}\n\n请以小说猫的口吻回应。"

    def generate():
        full = ""
        try:
            for token in chat_stream(system_prompt, user_prompt,
                                      temperature=req.temperature if req.temperature is not None else 0.8,
                                      max_tokens=300, task="companion", model=req.model or None):
                full += token
                yield "data: " + json.dumps({"type": "token", "data": token}, ensure_ascii=False) + "\n\n"
        except Exception as e:
            print(f"[ask] 无项目流式引导失败: {e}")
            fallback = general_opening_fallback(req.query)
            if not full:
                yield "data: " + json.dumps({"type": "token", "data": fallback}, ensure_ascii=False) + "\n\n"
                full = fallback
        # 记忆整轮（流式完成后统一写入）
        if full:
            memory.add_turn(req.query, full)
        # 对话提书名 → 同步更新创作/我的作品的书名（add_turn 之后，历史已含本轮）
        _maybe_sync_book_title(req.session_id, req.query)
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


NO_PROJECT_INSPIRATION_PROMPT = """你是「小说岛」的写作搭子「小说猫」。作者还没建书，但向你要灵感或剧情建议。

任务：给方向性灵感（不开书也能聊）：
1. 接住作者的具体需求（题材/角色/卡点），给 2-3 个具体的灵感方向
2. 每个方向一句话说清"怎么用"，不写完整大纲
3. 结尾轻轻提一句：正式建书后可以把这些灵感存进灵感库，或点「＋ 新建项目」开书
4. 语气自然简短（3-5 句），像朋友聊创作

之前对话：
{history}"""

NO_PROJECT_CRITIC_PROMPT = """你是「小说岛」的写作搭子「小说猫」。作者想让你检查逻辑/人设，但还没建书、没有知识库可查。

任务：
1. 诚实说明：还没建书，我没有这本书的设定库可以核对
2. 但可以基于作者刚说/之前聊到的内容，给一个轻量的初步判断（1-2 句）
3. 引导：把已定的设定/章节内容告诉我（或点「＋ 新建项目」开书后拖进来），我就能认真检查
4. 语气自然，不机械

之前对话：
{history}"""


def _llm_no_project_inspiration(query: str, novel_id: int | None = None, brief: bool = False,
                                session_id: str = "default", model: str = "", temperature: float | None = None,
                                persona: str = "") -> str:
    """无项目灵感：LLM 给方向性灵感（不进状态机，不需要知识库）"""
    memory = memory_manager.get_memory(novel_id, session_id)
    history = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in memory.get_context()[-6:]
    ) or "（无）"
    system = NO_PROJECT_INSPIRATION_PROMPT.format(history=history)
    if persona and persona.strip():
        system += f"\n\n作者要求你的人设/语气：{persona.strip()}"
    try:
        reply = chat(
            system,
            f"作者说：{query}",
            temperature=0.9,
            max_tokens=400,
            task="inspire",
            model=model or None,
        ).strip()
    except Exception as e:
        print(f"[ask] 无项目灵感失败: {e}")
        reply = "这个方向很有的写！正式建书后（点「＋ 新建项目」）我可以结合你的人物和设定给你更贴的灵感。"
    memory.add_turn(query, reply)
    return reply


def _llm_no_project_critic(query: str, novel_id: int | None = None, brief: bool = False,
                           session_id: str = "default", model: str = "", temperature: float | None = None,
                           persona: str = "") -> str:
    """无项目逻辑/人设检查：诚实说明无库可查 + 轻量判断 + 引导建书"""
    memory = memory_manager.get_memory(novel_id, session_id)
    history = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in memory.get_context()[-6:]
    ) or "（无）"
    system = NO_PROJECT_CRITIC_PROMPT.format(history=history)
    if persona and persona.strip():
        system += f"\n\n作者要求你的人设/语气：{persona.strip()}"
    try:
        reply = chat(
            system,
            f"作者说：{query}",
            temperature=0.5,
            max_tokens=300,
            task="logic",
            model=model or None,
        ).strip()
    except Exception as e:
        print(f"[ask] 无项目检查失败: {e}")
        reply = "这本书还没建库，我暂时没法认真核对逻辑/人设。点「＋ 新建项目」开书后把设定和章节放进来，我就能查了。"
    memory.add_turn(query, reply)
    return reply


def _llm_no_project_booked_reply(query: str, novel_id: int | None = None, brief: bool = False,
                                 session_id: str = "default", model: str = "", temperature: float | None = None,
                                 persona: str = "") -> str:
    """自动建书成功后的告知回复：告知已开书入库，引导继续补设定或去创作页"""
    memory = memory_manager.get_memory(None, session_id)
    history = "\n".join(
        f"{'作者' if m['role'] == 'user' else '小说猫'}: {m['content']}"
        for m in memory.get_context()[-6:]
    ) or "（无）"
    system = NO_PROJECT_SYSTEM_PROMPT.format(history=history)
    system += ("\n\n【重要】就在刚才，你已经自动为作者建好了书，并把聊过的设定（角色/题材）入库了！"
               "这条回复要做两件事：① 用一两句话告诉作者书已建好、设定已入库 ② 继续自然地问下一个设定问题"
               "（比如：配角是谁？故事想从哪里开始？），保持建书对话的连贯。不要重复问书名/题材（已经有了）。")
    if persona and persona.strip():
        system += f"\n\n作者要求你的人设/语气：{persona.strip()}"
    try:
        reply = chat(system, f"作者说：{query}", temperature=0.8, max_tokens=300,
                     task="companion", model=model or None).strip()
    except Exception as e:
        print(f"[ask] 建书告知失败: {e}")
        reply = "书已经自动建好了，聊过的设定也入库了！我们可以继续补人设、配角，或者你点「创作」进去看看建好的书。"
    memory.add_turn(query, reply)
    return reply
