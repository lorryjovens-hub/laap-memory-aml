"""冻结检索回放世界：候选池 + 每候选对金标的重叠。

为什么这样设计
--------------
Dream-RSI 的核心是"把已完成的探索冻结成可回放的模拟器"。对检索而言：

  - **冻结**：对每个查询，用宽松配置取 top-N 候选，并**预先算好每个候选
    与金标证据的 5-gram 重叠**。
  - **可回放**：任何只做「重排 / 融合 / 选择」的策略，都能在这份冻结数据上
    零成本重算指标（因为命中判定 = 所选候选中是否有人重叠 ≥ 阈值，可分解）。
  - **不可回放**：改分块窗口 / 扩展强度这类**改变候选池本身**的参数，需要重新
    索引——那属于慢循环。

产出
----
pool.json:
{
  "meta": {dataset, permissive_config, n_queries, pool_size},
  "queries": [
    {"qid", "user_id", "query", "candidates": [
       {"cid", "ov", "feat": {...}}
    ]}
  ]
}

feat 是策略可用的**特征**（不含原文，保证离线可复算）：
  bm25      基础 BM25 分
  sig       命中查询强信号（数字/专名）的个数
  rank      基础 BM25 排名（1-based）
  tok       块 token 数
  ts        时间戳
  sess      会话 id（用于多样性去重）
  exp       扩展查询后的 BM25 分（与 bm25 之差反映改写类信号）
  cons      是否命中"约束/规则"语句（B 路线用）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\LAAP")
from laap.aml.service import AMLMemoryService, tokenize, extract_signals

OUT = Path(os.environ.get("AML_PRERUN", Path(os.environ["TEMP"]) / "aml_prerun"))
WS = re.compile(r"\s+")
WORD = re.compile(r"[a-z0-9]+")
N = 5

#: 宽松池配置：尽可能把金标捞进池子（大窗口 + 强扩展 + 大 N）
POOL_CFG = dict(chunk_window=1, return_neighbors=0, query_expansion=True, top_n=400)

#: 约束/规则句型（B 路线）
CONS_RE = re.compile(
    r"\b(must|should|never|always|required?|ensure|make sure|do not|don't|"
    r"only|avoid|prefer|rule|policy|guideline|format|step \d|in order to|"
    r"before you|after you|remember to|be sure)\b", re.I)


def ngrams(t, n=N):
    w = WORD.findall(WS.sub(" ", (t or "").lower()).strip())
    if len(w) < n:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i+n]) for i in range(len(w)-n+1)}


def pq(q):
    try:
        d = ast.literal_eval((q or "").strip())
        if isinstance(d, dict):
            return str(d.get("content", q))
    except Exception:
        pass
    m = re.search(r"['\"]content['\"]\s*:\s*([\'\"])(.*?)\1", q or "", re.DOTALL)
    return m.group(2) if m else (q or "")


def gt(s):
    try:
        a = json.loads(s)
        if isinstance(a, list):
            return " ".join(str(m.get("content", "")) for m in a if isinstance(m, dict))
    except Exception:
        pass
    return str(s or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--personas", type=int, default=20)
    ap.add_argument("--topn", type=int, default=POOL_CFG["top_n"])
    ap.add_argument("--out", default=str(OUT / "pool.json"))
    ap.add_argument("--db", default=str(OUT / "prerun.sqlite3"))
    args = ap.parse_args()

    rows = json.load(open(OUT / "benchmark_subset.json", encoding="utf-8"))
    pids, seen = [], set()
    for r in rows:
        p = r["persona_id"]
        if p not in seen:
            seen.add(p)
            pids.append(p)
    pids = pids[: args.personas]
    rows = [r for r in rows if r["persona_id"] in set(pids)]
    print(f"personas={len(pids)}  questions={len(rows)}")

    svc = AMLMemoryService(args.db)   # 已有库（由 eval 脚本建过）
    if svc.stats()["chunks"] == 0:
        print("库为空，请先跑 eval_personamem.py --reset 建库")
        return 1
    print(f"库: {svc.stats()}")

    queries = []
    t0 = time.time()
    pool_has_gold = 0
    for i, r in enumerate(rows, 1):
        q = pq(r.get("user_query", ""))
        gold = gt(r.get("related_conversation_snippet", ""))
        if not q or not gold:
            continue
        gg = ngrams(gold)
        if not gg:
            continue
        uid = f"persona_{r['persona_id']}"
        data = svc.search(q, uid, top_k=args.topn)
        cands = []
        best_ov = 0.0
        for rank, c in enumerate(data, 1):
            ov = len(gg & ngrams(c["content"])) / len(gg)
            best_ov = max(best_ov, ov)
            toks = len(tokenize(c["content"]))
            cands.append({
                "cid": c["id"],
                "ov": round(ov, 4),
                "feat": {
                    "bm25": round(float(c.get("score") or 0.0), 6),
                    "rank": rank,
                    "tok": toks,
                    "ts": int(c.get("created_at") or 0),
                    "sess": str(c["id"]).rsplit(":", 2)[-2] if ":" in str(c["id"]) else "",
                    "sig": len(extract_signals(q) & extract_signals(c["content"])),
                    "cons": 1 if CONS_RE.search(c["content"] or "") else 0,
                },
            })
        if best_ov >= 0.5:
            pool_has_gold += 1
        queries.append({
            "qid": f"q{i}", "user_id": uid, "query": q,
            "pref_type": r.get("pref_type", ""),
            "gold_in_pool": best_ov >= 0.5,
            "candidates": cands,
        })
        if i % 50 == 0:
            print(f"  {i}/{len(rows)}  pool-has-gold={pool_has_gold}/{i} "
                  f"({time.time()-t0:.0f}s)")

    meta = {
        "dataset": "personamem-v2",
        "pool_config": {k: v for k, v in POOL_CFG.items()},
        "topn": args.topn,
        "n_queries": len(queries),
        "pool_has_gold": pool_has_gold,
        "pool_recall_at_n": round(pool_has_gold / max(1, len(queries)), 4),
        "avg_candidates": round(sum(len(q["candidates"]) for q in queries)
                                / max(1, len(queries)), 1),
        "built_at": time.time(),
    }
    Path(args.out).write_text(json.dumps({"meta": meta, "queries": queries},
                                         ensure_ascii=False), encoding="utf-8")
    print("\n" + "=" * 62)
    print(f"候选池上界 Recall@{args.topn}: {pool_has_gold}/{len(queries)} = "
          f"{pool_has_gold/max(1,len(queries))*100:.1f}%")
    print(f"平均候选数: {meta['avg_candidates']}")
    print(f"已写入: {args.out} ({Path(args.out).stat().st_size/1024/1024:.1f} MB)")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
