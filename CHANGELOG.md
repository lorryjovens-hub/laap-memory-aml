# Changelog

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
