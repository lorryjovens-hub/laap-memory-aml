"""在冻结候选池上量化排序头寸：Recall@k 曲线 + 金标排名分布。

关键问题：金标在池里（99.6%），但 BM25 排它排到第几？
"""
import argparse
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

POOL = Path(os.environ.get("AML_PRERUN", Path(os.environ["TEMP"]) / "aml_prerun")) / "pool.json"


def load():
    d = json.load(open(POOL, encoding="utf-8"))
    return d["meta"], d["queries"]


def ev(queries, rank_key):
    """按给定 score 函数重排，算 Recall@k 与金标排名分布。"""
    ks = (1, 3, 5, 10, 20, 50, 100, 400)
    hits = {k: 0 for k in ks}
    ranks = []
    n = 0
    ns_hits = {k: 0 for k in ks}
    ns_n = 0
    for q in queries:
        cands = q["candidates"]
        if not cands:
            n += 1
            continue
        ordered = sorted(cands, key=rank_key)
        first = None
        for i, c in enumerate(ordered, 1):
            if c["ov"] >= 0.5:
                first = i
                break
        n += 1
        if first:
            ranks.append(first)
        for k in ks:
            if first and first <= k:
                hits[k] += 1
        if q.get("pref_type") != "sensitive_info":
            ns_n += 1
            for k in ks:
                if first and first <= k:
                    ns_hits[k] += 1
    return n, hits, ranks, ns_n, ns_hits


def show(label, n, hits, ranks, ns_n, ns_hits):
    ks = sorted(hits)
    line = " ".join(f"{hits[k]/max(1,n)*100:>5.1f}" for k in ks)
    med = f"{statistics.median(ranks):.0f}" if ranks else "n/a"
    ns100 = ns_hits.get(100, 0) / max(1, ns_n) * 100
    print(f"{label:>20s} {line}   {med:>6s} {ns100:>10.1f}%   {len(ranks)}/{n}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=0, help="抽样题数(0=全部)")
    args = ap.parse_args()
    meta, queries = load()
    if args.sample:
        queries = queries[: args.sample]
    print(f"数据集: {meta['dataset']}  题数: {len(queries)}  "
          f"池上界: {meta['pool_recall_at_n']*100:.1f}%")
    print("=" * 100)
    ks = (1, 3, 5, 10, 20, 50, 100, 400)
    print(f"{'策略':>20s} " + " ".join(f"R@{k:<4d}" for k in ks)
          + "   中位rank  非敏感R@100  金标在池")
    print("-" * 100)

    # 基线：BM25 原始顺序
    show("bm25(原始)", *ev(queries, lambda c: -c["feat"]["bm25"]))
    # 强信号优先，再 BM25
    show("sig优先+bm25", *ev(queries, lambda c: (-c["feat"]["sig"], -c["feat"]["bm25"])))
    # 约束命中优先
    show("cons优先+bm25", *ev(queries, lambda c: (-c["feat"]["cons"], -c["feat"]["bm25"])))
    # 长度惩罚（长块可能稀释）
    show("bm25/len", *ev(queries, lambda c: -(c["feat"]["bm25"] / max(1, c["feat"]["tok"]) ** 0.5)))
    # 新近度
    show("bm25+ts", *ev(queries, lambda c: -(c["feat"]["bm25"] + 1e-4 * c["feat"]["ts"] / 1e10)))
    print("=" * 100)
    # 金标排名分布
    _, _, ranks, _, _ = ev(queries, lambda c: -c["feat"]["bm25"])
    if ranks:
        buckets = Counter()
        for r in ranks:
            if r <= 5: buckets["1-5"] += 1
            elif r <= 10: buckets["6-10"] += 1
            elif r <= 20: buckets["11-20"] += 1
            elif r <= 50: buckets["21-50"] += 1
            elif r <= 100: buckets["51-100"] += 1
            else: buckets["100+"] += 1
        print("BM25 下金标首次命中排名分布:")
        for b in ("1-5", "6-10", "11-20", "21-50", "51-100", "100+"):
            print(f"  {b:>7s}: {buckets.get(b,0):>4d}")


if __name__ == "__main__":
    main()
