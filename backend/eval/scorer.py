"""
评测打分器 — 规则脚本 + LLM-as-judge 两档（复用现有 HallucinationCriticNode 的判分思路）

规则脚本：检测"可能/也许/或许/大概/应该/似乎/推测"等不确定性措辞
  —— PRD 要求事实类回答不能出现幻觉性话术，这类词一旦出现在
     确定性问答里，直接判不合格，不用等LLM裁判。

LLM裁判：复用现有 chat() 函数，让 LLM 扮演"评测员"按标准打分。
  三种裁判：
    judge_fact_match       事实准确性 —— 答案是否命中标准答案
    judge_persona_fit      内容一致性 —— 生成内容是否符合人设特质（1-5分）
    judge_coherence_catch  情节连贯性 —— 是否正确识别出了矛盾
"""
import json
import re
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.llm_client import chat

# 不确定性措辞（事实类回答出现即判定为疑似幻觉）
HALLUCINATION_WORDS = ["可能", "也许", "或许", "大概", "应该是", "似乎", "推测", "猜测", "估计"]


def check_hallucination_words(text: str) -> dict:
    """规则脚本：检测文本是否包含不确定性措辞"""
    hits = [w for w in HALLUCINATION_WORDS if w in text]
    return {"has_hallucination_words": len(hits) > 0, "matched_words": hits}


JUDGE_FACT_PROMPT = """你是评测员，判断AI的回答是否正确回答了问题。

问题：{query}
标准答案要点：{expected}
AI的回答：{answer}

规则：
1. 只要AI回答的核心内容和标准答案要点一致（不要求逐字匹配），就算通过。
2. 如果AI说"未找到相关内容"或答案明显错误/编造，判不通过。
3. 只输出JSON：{{"pass": true或false, "reason": "简短说明"}}"""


def judge_fact_match(query: str, expected: str, answer: str) -> dict:
    """LLM裁判：事实准确性 —— 答案是否命中标准答案"""
    prompt = JUDGE_FACT_PROMPT.format(query=query, expected=expected, answer=answer)
    output = chat("你是严格但公正的评测员，只输出JSON。", prompt, temperature=0.0, max_tokens=200, task="logic")
    return _parse_judge_json(output, default={"pass": False, "reason": "解析失败"})


JUDGE_PERSONA_PROMPT = """你是评测员，判断AI生成的内容是否符合指定的人物性格特质。

要求：{query}
应体现的特质：{persona_traits}
AI生成的内容：{answer}

规则：
1. 按1-5分打分：5=完全贴合人设，3=部分贴合，1=完全不符或未生成有效内容。
2. 只输出JSON：{{"score": 1到5的整数, "reason": "简短说明"}}"""


def judge_persona_fit(query: str, expected_persona: list, answer: str) -> dict:
    """LLM裁判：内容一致性 —— 生成内容是否贴合人设（1-5分）"""
    prompt = JUDGE_PERSONA_PROMPT.format(
        query=query, persona_traits="、".join(expected_persona), answer=answer,
    )
    output = chat("你是严格但公正的评测员，只输出JSON。", prompt, temperature=0.0, max_tokens=200, task="logic")
    return _parse_judge_json(output, default={"score": 1, "reason": "解析失败"})


JUDGE_COHERENCE_PROMPT = """你是评测员，判断AI是否正确识别出了情节矛盾。

问题：{query}
预期判断：{expected_verdict}
AI的回答：{answer}

规则：
1. 如果预期是"矛盾/不合理"，AI回答里应明确指出不合理/矛盾/不符合设定，才算通过。
2. 如果AI没有指出问题、含糊其辞、或说"合理"，判不通过。
3. 只输出JSON：{{"pass": true或false, "reason": "简短说明"}}"""


def judge_coherence_catch(query: str, expected_verdict: str, answer: str) -> dict:
    """LLM裁判：情节连贯性 —— 是否正确识别出矛盾"""
    prompt = JUDGE_COHERENCE_PROMPT.format(query=query, expected_verdict=expected_verdict, answer=answer)
    output = chat("你是严格但公正的评测员，只输出JSON。", prompt, temperature=0.0, max_tokens=200, task="logic")
    return _parse_judge_json(output, default={"pass": False, "reason": "解析失败"})


def _parse_json_object(text: str):
    """从LLM输出中提取JSON对象（容错：去掉markdown围栏）"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text)


def _parse_judge_json(output: str, default: dict) -> dict:
    try:
        return _parse_json_object(output)
    except (json.JSONDecodeError, AttributeError):
        return default
