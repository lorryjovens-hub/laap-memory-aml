"""验证：window=1 vs window=3 的端到端差异（推翻/确认之前的消融结论）。"""
import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, r"D:\LAAP")
from laap.aml.service import AMLMemoryService

OUT = Path(os.environ["TEMP"]) / "aml_prerun"
WS = re.compile(r"\s+")
WORD = re.compile(r"[a-z0-9]+")


def ng(t, n=5):
    w = WORD.findall(WS.sub(" ", (t or "").lower()).strip())
    if len(w) < n:
        return {" ".join(w)} if w else set()
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def pq(q):
    try:
        d = ast.literal_eval((q or "").strip())
        if isinstance(d, dict):
            return str(d.get("content", q))
    except Exception:
        pass
    m = re.search(r"['\"]content['\"]\s*:\s*(['\"])(.*?)\1", q or "", re.DOTALL)
    return m.group(2) if m else (q or "")


def gt(s):
    try:
        a = json.loads(s)
        if isinstance(a, list):
            return " ".join(str(m.get("content", "")) for m in a if isinstance(m, dict))
    except Exception:
        pass
    return str(s or "")


rows = json.load(open(OUT / "benchmark_subset.json", encoding="utf-8"))
pids = []
for r in rows:
    if r["persona_id"] not in pids:
        pids.append(r["persona_id"])

print("=" * 72)
print(f"{'config':>16s} {'avg_ret':>8s} {'overall':>9s} {'non-sens':>9s}")
print("-" * 72)

for W, NB in ((1, 0), (1, 2), (1, 4), (2, 0), (3, 0), (3, 4)):
    db = str(OUT / f"vfy_w{W}_n{NB}.sqlite3")
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(db + suf)
        except OSError:
            pass
    svc = AMLMemoryService(db, chunk_window=W, return_neighbors=NB)
    for pid in pids:
        f = OUT / f"persona_{pid}.json"
        if not f.exists():
            continue
        d = json.load(open(f, encoding="utf-8"))
        msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))}
                for m in d.get("chat_history", []) if m.get("role") != "system"]
        for i in range(0, len(msgs), 40):
            svc.add(f"i-{pid}-{i}", msgs[i:i + 40], f"persona_{pid}", f"s-{i//40}")

    tot = hits = nst = nsh = 0
    rets = []
    for r in rows:
        q, g = pq(r.get("user_query")), gt(r.get("related_conversation_snippet"))
        if not q or not g:
            continue
        data = svc.search(q, f"persona_{r['persona_id']}", top_k=100)
        rets.append(len(data))
        gg = ng(g)
        if not gg:
            continue
        got = set()
        for c in data:
            got |= ng(c["content"])
        h = int(len(gg & got) / len(gg) >= 0.5)
        tot += 1
        hits += h
        if r.get("pref_type") != "sensitive_info":
            nst += 1
            nsh += h
    print(f"{f'w={W} neigh={NB}':>16s} {sum(rets)/max(1,len(rets)):>8.1f} "
          f"{hits/max(1,tot)*100:>8.1f}% {nsh/max(1,nst)*100:>8.1f}%")
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(db + suf)
        except OSError:
            pass
print("=" * 72)
