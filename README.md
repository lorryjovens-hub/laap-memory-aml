# LAAP Memory

**An Add/Search memory service submitted to the [Agent Memory Leaderboard (AML)](https://agentmemoryleaderboard.ai/), Cycle 2 — Textual Memory, Open-source Methods track.**

LAAP Memory exposes exactly two operations — `Add` and `Search` — and nothing else.
It never generates answers: it returns **memory evidence**, and the platform performs
answer generation and scoring under its own contract.

- Live endpoint: `https://aml.laap.cn`
- System name / version: `LAAP Memory` / `v1.0.1`
- Team: LAAP Team · LAAP LAB

---

## What this is

Per the AML protocol, a participant supplies a memory system; the platform fixes the
answer model, the judge, and the aggregation rules so that score differences are
attributable to memory quality. This repository is the memory system only.

| Operation | Responsibility |
|-----------|----------------|
| `Add` | Store and durably index incoming memories (messages, ordered, with optional timestamps) |
| `Search` | Return ranked memory evidence for a query under a given `user_id` scope |

**Compliance posture**

- `Search` returns raw retrieved evidence. It does **not** synthesize, summarize, or answer.
- All reads and writes are partitioned by `user_id`. Cross-user retrieval is structurally impossible.
- No LLM is called anywhere in this repository. No fine-tuning, no derived training data.
- A `purge_user` operation supports the 30-day deletion requirement.

---

## Quick start

```bash
git clone <this-repo> && cd laap-memory-aml
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

export AML_MEMORY_KEY='<any-strong-random-string>'   # optional; omit to run unauthenticated
python -m laap.aml.server --host 127.0.0.1 --port 8095 --db ./aml.sqlite3
```

Health check:

```bash
curl -s http://127.0.0.1:8095/health
# {"ok":true,"service":"laap-aml-memory","version":"1.0.0",...}
```

Contract smoke test (7 checks, including the two AML red lines):

```bash
python deploy/aml/smoke_test.py --base http://127.0.0.1:8095 --key "$AML_MEMORY_KEY"
# expected: 7 passed / 0 failed
```

See [`docs/REPRODUCE.md`](docs/REPRODUCE.md) for the full reviewer walkthrough.

---

## API contract

### `POST /add`

```json
{
  "request_id": "abc-123",
  "messages": [
    {"role": "user", "content": "I moved to Stockholm.", "timestamp": 1735689600000}
  ],
  "user_id": "u1",
  "session_id": "s1"
}
```

Response:

```json
{"success": true, "request_id": "abc-123", "user_id": "u1", "session_id": "s1"}
```

`timestamp` is optional (Unix milliseconds). Messages are stored and processed in
source order. `request_id` is echoed verbatim so retries are idempotent from the
platform's perspective.

### `POST /search`

```json
{"query": "Where did the user move to?", "options": ["..."], "user_id": "u1", "top_k": 100}
```

Response:

```json
{
  "data": [
    {"id": "u1:s1:3:9f2c1a", "content": "User: I moved to Stockholm.", "score": 4.21, "created_at": 1735689600000}
  ]
}
```

- Results are in retrieval rank order; `score` is higher-is-more-relevant.
- `data` is always present. With no matches it is `[]`.

### Other endpoints

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /health` | none | Liveness, auth mode, counters |
| `GET /stats` | required | Chunk/user counts (operations) |

---

## How retrieval works

The design is deliberately simple and fully offline — no embedding service, no
external network calls, so results are reproducible and cheap.

1. **Windowed chunking (ranking granularity).** Consecutive messages are
   combined into overlapping memory chunks (window 3, stride 1). Each chunk keeps
   speaker role and, when provided, a human-readable timestamp. Overlap preserves
   context for multi-hop questions while keeping facts dense.
2. **BM25 lexical ranking.** Standard Robertson–Zaragoza BM25 (`k1=1.5`, `b=0.75`)
   over a light tokenizer that handles English words and per-character CJK.
   Document frequencies are computed per `user_id`.
3. **Strong-signal boosting.** Numbers, dates, and proper nouns extracted from the
   query (and from multiple-choice `options`) receive an additive bonus when they
   appear in a chunk. This is the main lever for explicit fact recall.
4. **Recency preference.** Ties break toward the more recent memory, which is the
   default policy for update/conflict situations.
5. **Rank fine, return wide.** Ranking uses the message-level chunks, but each
   returned item is expanded to include its neighbouring chunks within the same
   session, and adjacent top-ranked items merge. The answer model therefore
   receives contiguous context rather than a three-message window, while the
   ranking order is unchanged. Measured effect on PersonaMem-v2: gold-snippet
   recall@100 rises from 80.6% to 92.8% (excluding the sensitive-information
   category) with no change in search latency.
6. **Scope isolation.** Every query filters `WHERE user_id = ?` before scoring.

Design notes and known limits are documented in [`docs/CAPABILITIES.md`](docs/CAPABILITIES.md).

---

## Repository layout

```
laap-memory-aml/
├── laap/aml/
│   ├── service.py          # storage + BM25 retrieval (stdlib only)
│   └── server.py           # Starlette HTTP surface
├── tests/test_aml_api.py   # contract tests (10 checks)
├── deploy/aml/             # deployment + operations
│   ├── README.md           # deployment guide (incl. Cloudflare Tunnel)
│   ├── smoke_test.py       # contract smoke test
│   └── compliance_purge.py # 30-day deletion
└── docs/
    ├── CAPABILITIES.md     # capability-by-capability scope and limits
    └── REPRODUCE.md        # reviewer walkthrough
```

---

## Deployment

The reference deployment is a local process behind a Cloudflare Tunnel, so no
inbound port is opened and TLS is terminated by Cloudflare:

```
platform ──HTTPS──► aml.laap.cn ──Cloudflare──► cloudflared ──► 127.0.0.1:8095
```

`deploy/aml/README.md` documents the full procedure, including tunnel setup,
authentication, and troubleshooting.

---

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
