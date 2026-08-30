"""写作后分析报告（P3-1）相关：LLM 输出 JSON 的容错解析。

从 main.py 拆分（架构瘦身）：把 DataAnalyst 报告结果的解析收进本模块，
main.py 的 analysis_report 路由只负责聚合素材 + 调用 + 返回。
"""
import json


def parse_report_json(text: str) -> dict:
    """容错解析 LLM 输出的 JSON（剥离 ```json 包裹 / 截取首尾括号）"""
    t = text.strip()
    t = t.replace("```json", "").replace("```", "").strip()
    start, end = t.find("{"), t.rfind("}")
    if start >= 0 and end > start:
        t = t[start:end + 1]
    try:
        data = json.loads(t)
        if isinstance(data, dict):
            return data
    except Exception as e:
        print(f"[data_analyst] JSON 解析失败，回退文本: {e}")
    return {"summary": t[:300]}
