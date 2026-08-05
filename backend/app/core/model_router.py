"""
模型路由 + 成本记录 — 里程碑7

三级模型路由（PRD成本控制核心）：
  任务分级 → 选对应模型。当前只有一个 deepseek-chat，但架构支持扩展
  多个模型（只需改配置，不改代码）。

成本记录：
  每次 LLM 调用记录 model / input_tokens / output_tokens / 估算成本，
  为监控和优化提供数据基础（PRD埋点 api_call_llm）。
"""
import time
from typing import List, Dict, Any


# ===== 模型路由表 =====
# 任务级别 → 模型名。当前都是 deepseek-chat（只有一个key），
# 以后接多个模型时只改这里。
MODEL_ROUTES = {
    "simple": "deepseek-chat",   # 意图分类、简单抽取
    "main": "deepseek-chat",     # 日常问答、摘要
    "complex": "deepseek-chat",  # 复杂创作、深度分析
}

# 任务 → 级别映射（哪些任务算simple/main/complex）
TASK_LEVELS = {
    "intent": "simple",
    "extract": "simple",
    "qa": "main",
    "summary": "main",
    "inspire": "main",
    "creative": "complex",
}


def get_model_for_task(task: str) -> str:
    """按任务返回模型名（路由核心）"""
    level = TASK_LEVELS.get(task, "main")
    return MODEL_ROUTES[level]


def get_level_for_task(task: str) -> str:
    """返回任务级别（供成本记录用）"""
    return TASK_LEVELS.get(task, "main")


# ===== 成本记录 =====
# 粗略单价（每百万token，单位元）。deepseek-chat 约 2元/百万输入，8元/百万输出
PRICE_PER_MILLION = {
    "deepseek-chat": {"input": 2.0, "output": 8.0},
}

# 内存中的调用日志（PRD埋点 api_call_llm）
_llm_call_logs: List[Dict[str, Any]] = []


def record_llm_cost(model: str, task: str, input_tokens: int, output_tokens: int) -> float:
    """记录一次 LLM 调用，返回估算成本（元）"""
    price = PRICE_PER_MILLION.get(model, {"input": 2.0, "output": 8.0})
    cost = (input_tokens / 1_000_000) * price["input"] + (output_tokens / 1_000_000) * price["output"]

    _llm_call_logs.append({
        "timestamp": time.time(),
        "model": model,
        "task": task,
        "level": get_level_for_task(task),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost": round(cost, 6),
    })
    return cost


def get_cost_summary() -> Dict[str, Any]:
    """汇总成本：总调用次数、总token、总成本、按任务分布"""
    total_cost = sum(log["cost"] for log in _llm_call_logs)
    total_input = sum(log["input_tokens"] for log in _llm_call_logs)
    total_output = sum(log["output_tokens"] for log in _llm_call_logs)

    by_task: Dict[str, Dict[str, float]] = {}
    for log in _llm_call_logs:
        t = log["task"]
        if t not in by_task:
            by_task[t] = {"calls": 0, "cost": 0.0}
        by_task[t]["calls"] += 1
        by_task[t]["cost"] += log["cost"]

    return {
        "total_calls": len(_llm_call_logs),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost": round(total_cost, 4),
        "by_task": {k: {"calls": v["calls"], "cost": round(v["cost"], 4)} for k, v in by_task.items()},
    }


def clear_cost_logs():
    """清空成本日志（测试用）"""
    _llm_call_logs.clear()
