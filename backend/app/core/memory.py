"""
短期记忆 — 里程碑6：多轮对话历史 + 摘要压缩

三层记忆（PRD）：
  工作记忆 = LangGraph State（节点间传状态，任务结束即清）
  长期记忆 = 向量/检索知识库（建库后永久，已有 TF-IDF）
  短期记忆 = 本文件：对话历史列表，会话内有效

核心逻辑：
  add_turn(user_msg, ai_msg)  — 存一轮对话
  get_context()               — 返回历史，超长时自动摘要压缩
"""
import json
from typing import List, Dict

from ..core.llm_client import chat

# 超过这个轮数就触发摘要压缩（PRD：对话历史摘要控制token）
MAX_HISTORY_TURNS = 6
# 摘要时保留最近的轮数
KEEP_RECENT_TURNS = 2

# 摘要用的系统提示词
SUMMARY_SYSTEM_PROMPT = """你是「小说岛」的对话摘要助手。
请把下面的对话历史压缩成一段50字以内的摘要，保留：用户关注的话题、AI给出的关键信息。
只输出摘要，不要其他内容。"""


class ConversationMemory:
    """短期记忆：对话历史存储 + 摘要压缩"""

    def __init__(self):
        self._conversation: List[Dict[str, str]] = []

    def add_turn(self, user_msg: str, ai_msg: str):
        """记录一轮对话（用户提问 + AI回答）"""
        self._conversation.append({"role": "user", "content": user_msg})
        self._conversation.append({"role": "assistant", "content": ai_msg})

    def get_context(self) -> List[Dict[str, str]]:
        """返回对话历史（供拼进 LLM messages）

        超过 MAX_HISTORY_TURNS 轮时：
          1. 把最早的对话交给 LLM 压缩成摘要
          2. 保留最近几轮 + 摘要
        这是 PRD"对话历史摘要"成本控制的落地。
        """
        if len(self._conversation) <= MAX_HISTORY_TURNS * 2:
            return list(self._conversation)

        # 超长：压缩早期对话
        early = self._conversation[:-KEEP_RECENT_TURNS * 2]  # 早期轮次
        recent = self._conversation[-KEEP_RECENT_TURNS * 2:]  # 最近轮次

        # 把早期对话格式化成文本，交给 LLM 压缩
        early_text = "\n".join(
            f"{'用户' if m['role'] == 'user' else 'AI'}: {m['content']}"
            for m in early
        )
        summary = chat(SUMMARY_SYSTEM_PROMPT, early_text, temperature=0.0, max_tokens=128)

        # 返回：摘要（作为 system 消息）+ 最近轮次
        return [
            {"role": "system", "content": f"【早期对话摘要】{summary}"},
            *recent,
        ]

    def clear(self):
        """清空历史（新会话）"""
        self._conversation = []

    def __len__(self):
        return len(self._conversation)


# 全局单例（兼容老调用，未按项目区分场景）
memory = ConversationMemory()


class MemoryManager:
    """按项目(novel_id)管理多个对话记忆 — 里程碑17：多租户对话隔离

    每个项目一份独立的短期记忆，切换项目时历史不串。
    None 表示"默认对话"（不选项目），也有自己独立的一份。
    """

    def __init__(self):
        self._memories: Dict[int | None, ConversationMemory] = {}

    def get_memory(self, novel_id: int | None) -> ConversationMemory:
        """获取某项目(或默认)的对话记忆（不存在则创建）"""
        if novel_id not in self._memories:
            self._memories[novel_id] = ConversationMemory()
        return self._memories[novel_id]


memory_manager = MemoryManager()
