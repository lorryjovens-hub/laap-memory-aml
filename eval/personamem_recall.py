"""PersonaMem-v2 retrieval pre-run for LAAP Memory.

Downloads a subset of the public PersonaMem-v2 benchmark, ingests each persona's
chat history through the Add contract, and measures gold-snippet retrieval
recall — the metric that isolates what a memory system is actually responsible
for, without depending on an answer model or a judge.

The gold evidence for each question is the benchmark's
``related_conversation_snippet``. A question counts as retrieved when the
word-level 5-gram overlap between the snippet and the returned evidence reaches
a threshold (default 0.5).

Usage::

    python eval/personamem_recall.py --personas 20 --limit 0
    python eval/personamem_recall.py --personas 20 --window 3 --neighbors 4

Data source: https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2
(public). Data is downloaded to a local cache directory and is not committed.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import re
import statistics
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from laap.aml.service import AMLMemoryService  # noqa: E402

HF = "https://huggingface.co/datasets/bowen-upenn/PersonaMem-v2/resolve/main/"
WS = re.compile(r"\s+")
WORD = re.compile(r"[a-z0-9]+")
NGRAM = 5


# ── helpers ─────────────────────────────────────────────────────────────

def fetch(url: str, dest: Path, retries: int = 3) -> bool:
    if dest.exists() and dest.stat().st_size > 0:
        return True
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "laap-memory-eval/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r, open(dest, "wb") as f:
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"    retry {i+1}: {type(e).__name__}: {e}")
            time.sleep(2 + i * 2)
    return False


def ngrams(text: str, n: int = NGRAM) -> set:
    w = WORD.findall(WS.sub(" ", (text or "").lower()).strip())
    if len(w) < n:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def parse_query(q: str) -> str:
    """Benchmark stores the query as a Python dict literal string."""
    s = (q or "").strip()
    try:
        d = ast.literal_eval(s)
        if isinstance(d, dict):
            return str(d.get("content", s))
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r"['\"]content['\"]\s*:\s*(['\"])(.*?)\1", s, re.DOTALL)
    return m.group(2) if m else s


def gold_text(snippet: str) -> str:
    try:
        arr = json.loads(snippet)
        if isinstance(arr, list):
            return " ".join(str(m.get("content", "")) for m in arr
                            if isinstance(m, dict))
    except Exception:  # noqa: BLE001
        pass
    return str(snippet or "")


# ── main ────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="PersonaMem-v2 retrieval recall")
    ap.add_argument("--cache", default="", help="data cache dir (default: <tmp>/laap-personamem)")
    ap.add_argument("--personas", type=int, default=20)
    ap.add_argument("--topk", type=int, default=100)
    ap.add_argument("--window", type=int, default=3, help="index chunk window")
    ap.add_argument("--neighbors", type=int, default=4, help="return expansion radius")
    ap.add_argument("--hit", type=float, default=0.5, help="5-gram overlap threshold")
    ap.add_argument("--limit", type=int, default=0, help="cap questions (0 = all)")
    args = ap.parse_args()

    cache = Path(args.cache) if args.cache else Path(os.environ.get("TEMP", "/tmp")) / "laap-personamem"
    cache.mkdir(parents=True, exist_ok=True)
    print(f"cache: {cache}")

    # 1) benchmark.csv
    csv_path = cache / "benchmark.csv"
    if not csv_path.exists():
        print("downloading benchmark.csv (~40 MB)...")
        if not fetch(HF + "benchmark/text/benchmark.csv", csv_path):
            print("download failed")
            return 1
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8", newline="")))
    print(f"questions: {len(rows)}")

    pids, seen = [], set()
    for r in rows:
        p = r.get("persona_id")
        if p and p not in seen:
            seen.add(p)
            pids.append(p)
    pids = pids[: args.personas]
    subset = [r for r in rows if r.get("persona_id") in set(pids)]
    if args.limit:
        subset = subset[: args.limit]
    print(f"personas: {len(pids)}   questions: {len(subset)}")

    # 2) persona histories
    links = {}
    for r in subset:
        pid, link = r.get("persona_id"), (r.get("chat_history_32k_link") or "").strip()
        if pid and link and pid not in links:
            links[pid] = link
    ok = 0
    for pid, link in links.items():
        if fetch(HF + link, cache / f"persona_{pid}.json"):
            ok += 1
    print(f"histories: {ok}/{len(links)}")

    # 3) ingest
    db = str(cache / f"recall_w{args.window}_n{args.neighbors}.sqlite3")
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(db + suf)
        except OSError:
            pass
    svc = AMLMemoryService(db, chunk_window=args.window,
                           return_neighbors=args.neighbors)
    t0 = time.time()
    n_msg = 0
    for pid in pids:
        f = cache / f"persona_{pid}.json"
        if not f.exists():
            continue
        d = json.load(open(f, encoding="utf-8"))
        msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                for m in d.get("chat_history", []) if m.get("role") != "system"]
        for i in range(0, len(msgs), 40):
            batch = msgs[i:i + 40]
            svc.add(f"ingest-{pid}-{i}", batch, f"persona_{pid}", f"sess-{i//40}")
            n_msg += len(batch)
    ing = time.time() - t0
    print(f"ingested {n_msg} messages -> {svc.stats()['chunks']} chunks in {ing:.1f}s")

    # 4) evaluate
    hits = tot = 0
    by_type = defaultdict(lambda: [0, 0])
    lat = []
    for r in subset:
        q = parse_query(r.get("user_query", ""))
        gold = gold_text(r.get("related_conversation_snippet", ""))
        if not q or not gold:
            continue
        t1 = time.time()
        data = svc.search(q, f"persona_{r['persona_id']}", top_k=args.topk)
        lat.append((time.time() - t1) * 1000)
        gg = ngrams(gold)
        if not gg:
            continue
        got = set()
        for c in data:
            got |= ngrams(c["content"])
        hit = int(len(gg & got) / len(gg) >= args.hit)
        tot += 1
        hits += hit
        by_type[str(r.get("pref_type", "?"))][0] += hit
        by_type[str(r.get("pref_type", "?"))][1] += 1

    print("\n" + "=" * 66)
    print(f"gold-snippet Recall@{args.topk}: {hits}/{tot} = {hits/max(tot,1)*100:.1f}%")
    print(f"median search latency: {statistics.median(lat):.0f} ms")
    print("-" * 66)
    for t, (h, n) in sorted(by_type.items(), key=lambda kv: -kv[1][1]):
        print(f"  {t:32s} {h:>5d}/{n:<5d} {h/max(n,1)*100:>6.1f}%")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    sys.exit(main())
