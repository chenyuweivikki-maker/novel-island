"""
评测主脚本 — 里程碑16：评测体系落地（PRD 评测表实测）

流程：
  1. 建库（用观南嘉措真实样本，novel_id 隔离，不污染正式数据）
  2. 依次跑四个维度的测试用例，调 /api/kb/ask（真实四大意图路由）
  3. 规则脚本 + LLM裁判 打分
  4. 汇总报告：各维度通过率/平均分

用法：
  cd backend && .venv/bin/python eval/run_eval.py
  需要 backend/.env 配好 DEEPSEEK_API_KEY + SILICONFLOW_API_KEY（真实调用）
"""
import sys
import os
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient
from app.main import app
from eval.eval_cases import FACT_QA_CASES, CONSISTENCY_CASES, COHERENCE_CASES, SUMMARY_KEY_PLOTS
from eval.scorer import (
    check_hallucination_words,
    judge_fact_match,
    judge_persona_fit,
    judge_coherence_catch,
)

EVAL_NOVEL_ID = 900  # 评测专用项目号，与正式数据隔离

SAMPLE_TEXT = """江观南：27岁，微信没开朋友圈，头像是朵红莲，喜欢自称老子，会抽烟不多，没纹身。
性格很强硬，从来不觉得男女有别体现在能力上。有个全网过千万粉丝的账号，是美妆博主，后来去直播聊天。
大结局：去当化妆师了，到处跑。

唐嘉措：27岁，比江观南小几个月，开着宠物店，微信发朋友圈全是猫。性格不太好，很敏感很脆弱，骨子里很自我，占有欲很强。
头发短短的，没事做，在家里准备考研究生，靠江观南的房租和家里贴补生活。养了只猫，叫汪汪，以前被虐待过特别怕人。
唐嘉措是江观南的房东，江观南租房子的时候还不知道她是房东。
大结局：在全国开了好几家宠物店，还公益救助动物。

第一章：白唐唐是干中介的，江观南找她看房子。江观南脑子沉甸甸的，昨晚喝了太多酒。
两人骑电瓶车去别墅区看房，江观南很满意，当场决定租下来。

第二章：江观南问白唐唐房东是男是女，白唐唐说是女房东，二十三四岁，开宠物医院，性格有点闷但很善良。
江观南来望岛之前在北京，因为垃圾房东退租不肯退押金的事情报了警。她把录音威胁房东，逼房东又退了两个月房租，然后拉黑账号，删了所有联系方式，离开了北京。

江观南有个舅舅，在她被家暴时是旁观者，她对舅舅有严重的心理阴影，见到他会转身就跑，反应过激。"""


def build_eval_kb():
    """建评测专用知识库（隔离，不影响正式项目）"""
    client = TestClient(app)
    print(f"建库中（novel_id={EVAL_NOVEL_ID}）...")
    r = client.post("/api/kb/build", json={"text": SAMPLE_TEXT, "novel_id": EVAL_NOVEL_ID})
    data = r.json()
    assert data["success"], f"建库失败: {data}"
    print(f"  已建库：{data['stats']['chunks']} 块，"
          f"{len(data['stats']['entities'])} 实体，{len(data['stats']['events'])} 事件\n")
    return client


def ask(client, query: str) -> str:
    """调 /api/kb/ask，走真实四大意图路由，返回回答文本"""
    r = client.post("/api/kb/ask", json={"query": query, "novel_id": EVAL_NOVEL_ID, "top_k": 5})
    data = r.json()
    if "precise" in data and data.get("precise"):
        return data["precise"]["answer"]
    return data.get("answer", "")


def run_fact_accuracy(client) -> dict:
    """维度1：事实准确性"""
    print("=" * 60)
    print("① 事实准确性")
    print("=" * 60)
    results = []
    for case in FACT_QA_CASES:
        answer = ask(client, case["query"])
        rule = check_hallucination_words(answer)
        judge = judge_fact_match(case["query"], case["expected"], answer)
        passed = judge["pass"] and not rule["has_hallucination_words"]
        results.append({"query": case["query"], "answer": answer, "passed": passed,
                        "rule": rule, "judge": judge})
        mark = "✅" if passed else "❌"
        print(f"{mark} Q: {case['query']}")
        print(f"   A: {answer[:80]}")
        if not passed:
            print(f"   原因: rule={rule['matched_words']} judge={judge.get('reason')}")
    pass_rate = sum(r["passed"] for r in results) / len(results)
    print(f"\n通过率: {pass_rate:.0%} ({sum(r['passed'] for r in results)}/{len(results)})\n")
    return {"dimension": "事实准确性", "pass_rate": pass_rate, "results": results}


def run_consistency(client) -> dict:
    """维度2：内容一致性（人设贴合度，1-5分）"""
    print("=" * 60)
    print("② 内容一致性（人设贴合度）")
    print("=" * 60)
    results = []
    for case in CONSISTENCY_CASES:
        answer = ask(client, case["query"])
        rule = check_hallucination_words(answer)
        judge = judge_persona_fit(case["query"], case["expected_persona"], answer)
        results.append({"query": case["query"], "answer": answer, "score": judge.get("score", 1),
                        "rule": rule, "judge": judge})
        print(f"[{judge.get('score', '?')}/5] Q: {case['query'][:40]}...")
        print(f"   A: {answer[:100]}")
        print(f"   评语: {judge.get('reason')}")
    avg_score = sum(r["score"] for r in results) / len(results)
    print(f"\n平均分: {avg_score:.1f}/5\n")
    return {"dimension": "内容一致性", "avg_score": avg_score, "results": results}


def run_coherence(client) -> dict:
    """维度3：情节连贯性（能否识别矛盾）"""
    print("=" * 60)
    print("③ 情节连贯性（矛盾识别）")
    print("=" * 60)
    results = []
    for case in COHERENCE_CASES:
        answer = ask(client, case["query"])
        judge = judge_coherence_catch(case["query"], case["expected_verdict"], answer)
        passed = judge["pass"]
        results.append({"query": case["query"], "answer": answer, "passed": passed, "judge": judge})
        mark = "✅" if passed else "❌"
        print(f"{mark} Q: {case['query'][:50]}...")
        print(f"   A: {answer[:100]}")
        if not passed:
            print(f"   原因: {judge.get('reason')}")
    pass_rate = sum(r["passed"] for r in results) / len(results)
    print(f"\n通过率: {pass_rate:.0%} ({sum(r['passed'] for r in results)}/{len(results)})\n")
    return {"dimension": "情节连贯性", "pass_rate": pass_rate, "results": results}


def run_summary_coverage(client) -> dict:
    """维度4：大纲总结能力（年表是否覆盖关键情节）"""
    print("=" * 60)
    print("④ 大纲总结能力（情节大事年表覆盖度）")
    print("=" * 60)
    r = client.get("/api/timeline", params={"novel_id": EVAL_NOVEL_ID})
    data = r.json()
    timeline_text = " ".join(t["summary"] for t in data.get("timeline", []))
    print(f"年表共 {data.get('total', 0)} 条：")
    for t in data.get("timeline", []):
        print(f"  - {t['summary']}")

    covered = []
    for plot in SUMMARY_KEY_PLOTS:
        # 简单关键词覆盖检查（规则脚本，不需要LLM）
        hit = plot in timeline_text
        covered.append({"plot": plot, "covered": hit})
    coverage_rate = sum(c["covered"] for c in covered) / len(covered)
    print(f"\n关键情节覆盖率: {coverage_rate:.0%} ({sum(c['covered'] for c in covered)}/{len(covered)})")
    for c in covered:
        print(f"  {'✅' if c['covered'] else '❌'} {c['plot']}")
    print()
    return {"dimension": "大纲总结能力", "coverage_rate": coverage_rate,
            "timeline_total": data.get("total", 0), "covered": covered}


def main():
    client = build_eval_kb()

    reports = [
        run_fact_accuracy(client),
        run_consistency(client),
        run_coherence(client),
        run_summary_coverage(client),
    ]

    print("=" * 60)
    print("📊 评测总报告")
    print("=" * 60)
    for r in reports:
        if "pass_rate" in r:
            print(f"  {r['dimension']}: 通过率 {r['pass_rate']:.0%}")
        if "avg_score" in r:
            print(f"  {r['dimension']}: 平均分 {r['avg_score']:.1f}/5")
        if "coverage_rate" in r:
            print(f"  {r['dimension']}: 覆盖率 {r['coverage_rate']:.0%}")

    # 保存详细报告到 JSON（供后续对比迭代效果）
    out_path = os.path.join(os.path.dirname(__file__), "eval_report.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)
    print(f"\n详细报告已存: {out_path}")


if __name__ == "__main__":
    main()
