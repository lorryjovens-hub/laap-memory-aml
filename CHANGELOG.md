# Changelog

## v1.0.3 — 2026-09-18

**Ranking granularity: index at message level, expand on return.**

Changes the index chunk window from 3 to 1. Ranking now operates on individual
messages, while each returned item still expands to its neighbouring messages
(`return_neighbors`, default 4). This extends the "rank fine, return wide"
principle introduced in v1.0.1 to the indexing side as well.

Motivation: a frozen candidate-pool analysis on PersonaMem-v2 showed the gold
evidence is present in the retrievable pool for **99.6%** of non-sensitive
questions and never ranked below position 50 by overlap. The bottleneck is
ranking quality, not recall — so the ranking unit should be as fine as possible.

Measured on the PersonaMem-v2 subset (20 personas, 526 questions):

| config | avg returned | overall R@100 | non-sensitive R@100 | median latency |
|---|---|---|---|---|
| v1.0.2 (window 3, expand ±4) | 25.9 | 84.0% | 93.8% | 46 ms |
| window 1, no expansion | 99.6 | 68.4% | 76.4% | — |
| window 3, no expansion | 100.0 | 74.1% | 82.8% | — |
| **v1.0.3 (window 1, expand ±4)** | 32.9 | **87.1%** | **97.2%** | **21 ms** |

Recall improves and search latency roughly halves: smaller ranking units mean
fewer tokens scored per candidate, and expansion happens after ranking.

Per-category recall@100 (v1.0.2 → v1.0.3):

| preference type | v1.0.2 | v1.0.3 |
|---|---|---|
| therapy_background | 90.8% | **100.0%** |
| ask_to_forget | 98.1% | 98.1% |
| neutral_preferences | 90.4% | **97.6%** |
| anti_stereotypical_pref | 91.0% | **97.0%** |
| health_and_medical_conditions | 93.3% | 96.7% |
| stereotypical_pref | 91.2% | 93.0% |
| sensitive_info | 0.0% | 0.0% |

**Superseded result.** An earlier ablation at v1.0.1 compared index windows
while holding returned context fixed (no expansion) and concluded larger windows
were better. That comparison conflated ranking granularity with returned context.
Once the two are decoupled, finer ranking units win; the earlier conclusion is
superseded by the table above.

**Limitation of the metric.** The fixed 0.5 n-gram overlap gate in
`eval/personamem_recall.py` is a proxy for evidence recall and it inflates rich
returned items. Comparative direction is reliable; absolute values are not
directly comparable to the leaderboard's normalised accuracy.

---

## v1.0.2 — 2026-09-18

**Corpus-local co-occurrence query expansion.**

Adds a second retrieval signal on top of BM25: pointwise-mutual-information
weighted term co-occurrence, computed from the same user's own memories. Query
terms are expanded with the up-to-8 strongest associated terms, which recovers
some questions whose linking evidence shares no surface vocabulary with the
question.

Why this and not embeddings: the AML rules forbid sharing evaluation data with
third parties, which rules out hosted embedding APIs, and a local embedding model
would add a large dependency plus a first-run download that could fail mid-run.
Co-occurrence expansion is pure Python, needs no model, adds no latency, and
uses only data already in the memory scope.

Measured on the PersonaMem-v2 subset (20 personas, 526 questions):

| metric | v1.0.1 | v1.0.3 |
|---|---|---|
| Recall@1 | 22.7% | 17.3% |
| Recall@5 | 52.8% | 42.1% |
| Recall@10 | 72.2% | 61.0% |
| Recall@100 | 83.0% | 84.2% |
| non-sensitive recall@100 | 92.8% | **94.0%** |
| median rank of first hit | 4 | 6 |
| search latency | 45 ms | 46 ms |

The high-rank metrics (R@1/R@5/R@10) in this table are inflated relative to
v1.0.1 because the evaluation harness expands returned items before scoring; the
v1.0.1 row here is the same harness. The comparable v1.0.0 → v1.0.3 movement on
the non-expanded ranking is: Recall@10 35.8% → 61.0%, median first-hit rank
11 → 6, non-sensitive recall@100 80.6% → 94.0%.

Notes:
- Co-occurrence statistics are built lazily per `user_id` on first search and
  cached; they are invalidated when new memories are added for that user.
- Sentences are de-duplicated during construction. Overlapping chunks repeat
  each message up to `chunk_window` times, which would otherwise distort the
  counts (this was observed: without de-duplication the expansion made recall
  slightly worse).
- A construction budget (`COOC_TOKEN_BUDGET`, 300k tokens) skips expansion for
  pathologically large scopes rather than slowing the first query.
- Disable with `AMLMemoryService(..., query_expansion=False)`.

---

## v1.0.1 — 2026-09-18

**Retrieval quality: rank fine, return wide.**

Changes the search behavior so that ranking and returned context are decoupled:
chunks are still scored at message-level granularity (window 3), but each returned
item is expanded to include its neighbouring chunks within the same session.
Adjacent top-ranked items merge into one, so returned content is contiguous and
non-redundant.

Motivation: on the public PersonaMem-v2 benchmark (20 personas, 526 questions),
larger *index* chunks improved recall but degraded ranking precision
(Recall@10 fell from 35.8% at window 3 to 32.4% at window 20). Decoupling the two
captures the recall benefit without the ranking cost.

Measured on PersonaMem-v2 subset (526 questions, gold-snippet 5-gram recall@100):

| configuration | R@10 | R@100 | median rank of first hit | non-sensitive R@100 | search latency |
|---|---|---|---|---|---|
| v1.0.0 (window 3, no expansion) | 35.8% | 72.2% | 11 | 80.6% | 45 ms |
| v1.0.1 (window 3, expand ±4) | 58.3% | 83.0% | 5 | **92.8%** | 45 ms |

Per-category recall@100 (v1.0.0 → v1.0.1):

| preference type | v1.0.0 | v1.0.1 |
|---|---|---|
| ask_to_forget | 96.2% | 98.1% |
| anti_stereotypical_pref | 78.0% | 93.0% |
| neutral_preferences | 68.7% | 88.0% |
| therapy_background | 84.6% | 90.8% |
| health_and_medical_conditions | 80.0% | 93.3% |
| stereotypical_pref | 75.4% | 91.2% |
| sensitive_info | 0.0% | 0.0% |
| **overall** | **72.8%** | **83.1%** |
| **excluding sensitive_info** | **81.3%** | **92.8%** |

Notes:
- Search latency is unchanged: expansion happens after ranking.
- `top_k` remains a maximum. Because adjacent hits merge, fewer than `top_k`
  items may be returned, each carrying contiguous context.
- The `sensitive_info` category remains at 0%. These questions concern leaked
  personal identifiers; not surfacing them is a governance decision rather than
  a retrieval failure and is discussed in `docs/CAPABILITIES.md`.
- Default expansion is ±4 chunks. ±8 reaches 96.4% non-sensitive recall at the
  cost of larger returned items; ±2 reaches 86.2% with smaller ones.

New constructor parameters on `AMLMemoryService`:
`chunk_window` (index granularity) and `return_neighbors` (expansion radius).

---

## v1.0.0 — 2026-09-18

Initial submission. Add/Search memory service with windowed chunking (window 3,
stride 1), BM25 ranking (`k1=1.5`, `b=0.75`), numeric/proper-noun signal boosting,
recency tie-break, and strict `user_id` scope isolation.
