"""
模型路由 + 成本记录 — 里程碑7（Phase 0 升级：三级模型真实落地 + 多 Provider）

三级模型路由（PRD 成本控制核心）：
  任务分级 → 选对应模型。三级：
    simple  → 海量低价值任务（清洗/分类/意图识别）   → 便宜模型
    main    → 常规任务（问答/摘要/陪伴）              → 主力模型
    complex → 复杂推理/高价值创作（逻辑纠错/深度分析） → 高阶模型

多 Provider 支持（PRD 结论：产品接国产模型）：
  - DeepSeek（deepseek-chat）为主力，已接入、成本最低
  - Moonshot Kimi / 腾讯混元为高阶备选（OpenAI 兼容协议）
  - 某 Provider 缺 API Key 时自动回退 DeepSeek（get_model_for_task 内判断）

成本记录：
  每次 LLM 调用记录 model / input_tokens / output_tokens / 估算成本，
  落 SQLite（data/cost.db）持久化，服务重启不丢（PRD埋点 api_call_llm）。
"""
import os
import sqlite3
import time
from typing import List, Dict, Any

from .config import settings


# ===== 模型路由表 =====
# 任务级别 → 模型名（Phase 0：填入真实模型；缺 key 时 get_model_for_task 自动回退）
MODEL_ROUTES = {
    "simple": "deepseek-chat",            # 意图分类、简单抽取、数据预处理
    "main": "deepseek-chat",              # 日常问答、摘要、情感陪伴
    "complex": "kimi-k2.6",               # 复杂创作、深度分析（Kimi K2.6，可在 .env 换混元）
}

# 任务 → 级别映射（哪些任务算simple/main/complex）
TASK_LEVELS = {
    "intent": "simple",
    "extract": "simple",
    "qa": "main",
    "summary": "main",
    "inspire": "main",
    "companion": "main",     # Phase 0：情感陪伴走主力模型
    "logic": "complex",      # 情节一致性检查：推理任务，走复杂级
    "creative": "complex",
}

# ===== Provider 注册表 =====
# 模型名 → Provider。llm_client 据此选客户端（每家 = base_url + key）
MODEL_PROVIDERS = {
    "deepseek-chat": "deepseek",
    "kimi-k2.6": "moonshot",
    "hunyuan-turbos-latest": "hunyuan",
}


def _provider_available(model: str) -> bool:
    """该模型所属 Provider 是否已配置 API Key（缺 key 就回退）"""
    provider = MODEL_PROVIDERS.get(model, "deepseek")
    if provider == "moonshot":
        return bool(settings.MOONSHOT_API_KEY)
    if provider == "hunyuan":
        return bool(settings.HUNYUAN_API_KEY)
    return bool(settings.DEEPSEEK_API_KEY)


# ===== 熔断状态（PRD：provider 级熔断，避免反复打无效 key） =====
# provider 连续失败 N 次 → 熔断 T 秒，期间 get_model_for_task 直接回退 DeepSeek
_CIRCUIT_BREAKER: Dict[str, Dict[str, Any]] = {}
BREAK_THRESHOLD = 3       # 连续失败次数
BREAK_COOLDOWN = 300      # 熔断冷却 5 分钟


def _provider_of(model: str) -> str:
    return MODEL_PROVIDERS.get(model, "deepseek")


def mark_provider_failure(model: str) -> None:
    """记录一次 provider 调用失败；达到阈值即熔断（llm_client 降级时调用）"""
    provider = _provider_of(model)
    if provider == "deepseek":
        return  # 主力模型不熔断（熔了主流程就没了）
    st = _CIRCUIT_BREAKER.get(provider, {"fails": 0, "open_until": 0})
    st["fails"] += 1
    if st["fails"] >= BREAK_THRESHOLD:
        st["open_until"] = time.time() + BREAK_COOLDOWN
        print(f"[circuit_breaker] {provider} 连续失败 {st['fails']} 次，熔断 {BREAK_COOLDOWN}s")
    _CIRCUIT_BREAKER[provider] = st


def provider_is_open(provider: str) -> bool:
    """provider 是否处于熔断中（未过冷却期）"""
    st = _CIRCUIT_BREAKER.get(provider)
    if not st:
        return False
    if time.time() >= st.get("open_until", 0):
        _CIRCUIT_BREAKER.pop(provider, None)
        return False
    return True


def get_model_for_task(task: str) -> str:
    """按任务返回模型名（路由核心）

    优雅回退：目标模型的 Provider 没配 key 或处于熔断 → 回退 DeepSeek 主力模型，
    保证任何情况下主流程都能跑（成本记录/降级链路上游）。
    """
    level = TASK_LEVELS.get(task, "main")
    model = MODEL_ROUTES[level]
    if not _provider_available(model) or provider_is_open(_provider_of(model)):
        return "deepseek-chat"
    return model


def get_level_for_task(task: str) -> str:
    """返回任务级别（供成本记录用）"""
    return TASK_LEVELS.get(task, "main")


# ===== 成本记录 =====
# 粗略单价（每百万token，单位元）。deepseek-chat 约 2元/百万输入，8元/百万输出
# Kimi K2 / 混元为估算价（以各家官方定价为准，仅用于成本监控看板）
PRICE_PER_MILLION = {
    "deepseek-chat": {"input": 2.0, "output": 8.0},
    "kimi-k2.6": {"input": 4.0, "output": 16.0},
    "hunyuan-turbos-latest": {"input": 1.0, "output": 4.0},
}

# ===== 成本记录（SQLite 持久化，重启不丢） =====
_COST_DB_PATH = os.environ.get("COST_DB_PATH", "data/cost.db")
# 内存镜像（向后兼容：外部可能读 _llm_call_logs；真实数据在 SQLite）
_llm_call_logs: List[Dict[str, Any]] = []

# 模型降级日志（PRD埋点 model_fallback）：高阶模型故障 → 回退 DeepSeek
_fallback_logs: List[Dict[str, Any]] = []


def _cost_conn() -> sqlite3.Connection:
    """获取成本库连接（自动建表）"""
    os.makedirs(os.path.dirname(_COST_DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(_COST_DB_PATH, timeout=5)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS llm_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            model TEXT NOT NULL,
            task TEXT NOT NULL,
            level TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cost REAL NOT NULL
        )"""
    )
    return conn


def _load_calls() -> List[Dict[str, Any]]:
    """从 SQLite 读全部调用记录（含历史，重启后仍可汇总）"""
    try:
        conn = _cost_conn()
        rows = conn.execute(
            "SELECT timestamp, model, task, level, input_tokens, output_tokens, cost "
            "FROM llm_calls ORDER BY id"
        ).fetchall()
        conn.close()
        return [
            {"timestamp": r[0], "model": r[1], "task": r[2], "level": r[3],
             "input_tokens": r[4], "output_tokens": r[5], "cost": r[6]}
            for r in rows
        ]
    except Exception as e:
        print(f"[model_router] 成本读取失败: {e}")
        return list(_llm_call_logs)


def record_llm_cost(model: str, task: str, input_tokens: int, output_tokens: int) -> float:
    """记录一次 LLM 调用，返回估算成本（元）。落 SQLite 持久化。"""
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
    # SQLite 持久化（失败不阻塞主流程）
    try:
        conn = _cost_conn()
        conn.execute(
            "INSERT INTO llm_calls (timestamp, model, task, level, input_tokens, output_tokens, cost) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (time.time(), model, task, get_level_for_task(task), input_tokens, output_tokens, round(cost, 6)),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[model_router] 成本落库失败: {e}")
    # PRD 埋点：api_call_llm + cost_attribution
    try:
        from .tracking import tracking
        tracking.record("api_call_llm", model_name=model, task=task, input_tokens=input_tokens,
                        output_tokens=output_tokens, total_cost=round(cost, 6))
        tracking.record("cost_attribution", cost_type="llm", cost_units="tokens",
                        input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost=round(cost, 6))
    except Exception:
        pass
    return cost


def record_model_fallback(original_model: str, fallback_model: str, reason: str = "provider_error"):
    """记录一次模型降级（PRD埋点 model_fallback）：高阶模型故障自动回退主力模型"""
    _fallback_logs.append({
        "timestamp": time.time(),
        "original_model": original_model,
        "fallback_model": fallback_model,
        "reason": reason,
    })
    try:
        from .tracking import tracking
        tracking.record("model_fallback", original_model=original_model, fallback_model=fallback_model, reason=reason)
    except Exception:
        pass


def get_cost_summary() -> Dict[str, Any]:
    """汇总成本：总调用次数、总token、总成本、按任务分布、降级次数（SQLite 聚合）"""
    calls = _load_calls()
    total_cost = sum(c["cost"] for c in calls)
    total_input = sum(c["input_tokens"] for c in calls)
    total_output = sum(c["output_tokens"] for c in calls)

    by_task: Dict[str, Dict[str, float]] = {}
    for c in calls:
        t = c["task"]
        if t not in by_task:
            by_task[t] = {"calls": 0, "cost": 0.0}
        by_task[t]["calls"] += 1
        by_task[t]["cost"] += c["cost"]

    return {
        "total_calls": len(calls),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cost": round(total_cost, 4),
        "model_fallbacks": len(_fallback_logs),  # PRD埋点 model_fallback
        "by_task": {k: {"calls": v["calls"], "cost": round(v["cost"], 4)} for k, v in by_task.items()},
    }


def clear_cost_logs():
    """清空成本日志（测试用）：SQLite + 内存"""
    try:
        conn = _cost_conn()
        conn.execute("DELETE FROM llm_calls")
        conn.commit()
        conn.close()
    except Exception:
        pass
    _llm_call_logs.clear()
    _fallback_logs.clear()
