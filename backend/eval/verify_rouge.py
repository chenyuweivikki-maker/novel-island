"""
评测增强 — PRD 评测体系：ROUGE / BLEU 自动指标（零依赖自实现）

ROUGE-1/2：n-gram 召回率（关键信息有没有被提到）
BLEU-1：1-gram 精确匹配（生成与参考的词汇重合度）

用法：python eval/verify_rouge.py [reference] [hypothesis]
无参数时跑内置示例（生成 vs 参考答案）。
"""
import re
import sys
from collections import Counter


def _ngrams(text: str, n: int) -> list[str]:
    """字符级 n-gram（中文场景比词级更稳）"""
    cleaned = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", text)
    if len(cleaned) < n:
        return [cleaned] if cleaned else []
    return [cleaned[i:i + n] for i in range(len(cleaned) - n + 1)]


def rouge_n(reference: str, hypothesis: str, n: int = 1) -> float:
    """ROUGE-N：召回率 = 命中 n-gram 数 / 参考 n-gram 总数"""
    ref_ng = Counter(_ngrams(reference, n))
    hyp_ng = Counter(_ngrams(hypothesis, n))
    if not ref_ng:
        return 0.0
    hits = sum(min(ref_ng[g], hyp_ng.get(g, 0)) for g in ref_ng)
    return round(hits / sum(ref_ng.values()), 4)


def bleu_1(reference: str, hypothesis: str) -> float:
    """BLEU-1：1-gram 精确度（带 brevity penalty 简版）"""
    ref_ng = Counter(_ngrams(reference, 1))
    hyp_ng = Counter(_ngrams(hypothesis, 1))
    if not hyp_ng or not ref_ng:
        return 0.0
    clipped = sum(min(hyp_ng[g], ref_ng.get(g, 0)) for g in hyp_ng)
    precision = clipped / sum(hyp_ng.values())
    # 简版 brevity penalty：生成过短惩罚
    ref_len = sum(ref_ng.values())
    hyp_len = sum(hyp_ng.values())
    bp = 1.0 if hyp_len >= ref_len else (hyp_len / ref_len if ref_len else 0.0)
    return round(precision * bp, 4)


def evaluate(reference: str, hypothesis: str) -> dict:
    return {
        "rouge_1_recall": rouge_n(reference, hypothesis, 1),
        "rouge_2_recall": rouge_n(reference, hypothesis, 2),
        "bleu_1": bleu_1(reference, hypothesis),
    }


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        ref_text = open(sys.argv[1], encoding="utf-8").read() if sys.argv[1] != "-" else sys.stdin.read()
        hyp_text = open(sys.argv[2], encoding="utf-8").read() if sys.argv[2] != "-" else sys.stdin.read()
    else:
        # 内置示例：回答应包含参考的关键信息（人设/职业/性格）
        ref_text = "江观南是美妆博主，性格强硬自信，脾气大，喜欢自称老子，从小家里重男轻女。"
        hyp_text = "江观南做美妆直播，性格很强硬，自信，见人就骂，爱自称老子，原生家庭重男轻女。"
    result = evaluate(ref_text, hyp_text)
    print("参考：", ref_text[:60], "…")
    print("生成：", hyp_text[:60], "…")
    print(f"\nROUGE-1 召回率: {result['rouge_1_recall']:.4f}")
    print(f"ROUGE-2 召回率: {result['rouge_2_recall']:.4f}")
    print(f"BLEU-1:          {result['bleu_1']:.4f}")
    print("\n判定：ROUGE-1 ≥ 0.3 视为关键信息基本覆盖；BLEU-1 ≥ 0.5 视为词汇重合良好")
    ok = result["rouge_1_recall"] >= 0.3 and result["bleu_1"] >= 0.3
    print(("✅ 通过" if ok else "❌ 未达标"))
    sys.exit(0 if ok else 1)
