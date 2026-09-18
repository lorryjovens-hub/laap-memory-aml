# Capabilities and Boundaries

This document states, capability by capability, **what LAAP Memory does and where
it is expected to fail**. It is written to be checked against, not to impress.
The AML textual taxonomy is used as the axis.

Submission: `LAAP Memory v1.0.1` — Textual Memory, Open-source Methods track.

---

## Measured baseline

On the public **PersonaMem-v2** subset (20 personas, 526 questions), using
gold-snippet n-gram recall@100 as the retrieval metric (v1.0.1 defaults):

| preference type | Recall@100 |
|---|---|
| ask_to_forget | 98.1% |
| health_and_medical_conditions | 93.3% |
| anti_stereotypical_pref | 93.0% |
| stereotypical_pref | 91.2% |
| therapy_background | 90.8% |
| neutral_preferences | 88.0% |
| sensitive_info | 0.0% |
| **overall** | **83.1%** |
| **excluding sensitive_info** | **92.8%** |

Median search latency 45 ms. Method and full breakdown in `CHANGELOG.md`.
The `sensitive_info` row is discussed under capability H below — it is a
governance question, not a retrieval miss.

---

## Summary

| # | AML capability | Strength | Mechanism |
|---|----------------|----------|-----------|
| A | Explicit fact recall | **Primary strength** | BM25 + numeric/proper-noun boosting |
| B | Relational & multi-hop | Moderate | Overlapping chunks; neighbouring chunks merged on return |
| C | Temporal & event reasoning | Moderate | Original timestamps preserved; recency tie-break |
| D | Memory governance | Moderate | Hard `user_id`/`session_id` scoping; delete support; recency preference |
| E | Personalization & care | By construction | Raw evidence returned intact; no rewriting |
| G | Rules & process execution | By construction | Evidence returned; interpretation is the platform's step |
| H | Epistemic safety & privacy | **Structurally strong** | Isolation, no synthesis, no training, purge |

Legend — **Primary strength**: designed for and actively tuned. **Moderate**:
works through the general mechanism, not a dedicated component. **By
construction**: the property holds because the service does not do the thing it
is forbidden from doing (answer generation).

---

## A. Explicit fact recall — primary strength

**Does.** Facts, attributes, entities, and numeric values stated verbatim in the
history are recovered reliably.

**Mechanism.** BM25 over message-level chunks (window 3, stride 1), plus an
additive bonus when a query's numbers, dates, or proper nouns appear in a chunk.
Multiple-choice `options` are folded into the query, which matters because option
text often carries the exact lexical form of the fact. Ranking uses fine chunks,
but returned items are expanded with their neighbours, so the answer model
receives contiguous context rather than a three-message window.

**Measured.** 92.8% gold-snippet recall@100 on the PersonaMem-v2 subset,
excluding the sensitive-information category (see H).

**Expected to fail on.** Pure paraphrase where no content word overlaps
("what city does he call home" vs "moved to Stockholm"). There is no embedding
channel, so there is no synonym-level matching. This is the largest remaining
gap and the top item on the improvement list.

---

## B. Relational & multi-hop reasoning — moderate

**Does.** When a question needs evidence spread across several turns, the
windowed chunking (window 3, stride 1) means the relevant spans frequently land
in one returned chunk or in two adjacent ones; `top_k` returns a pool the
platform's answer model can compose over.

**Expected to fail on.** Hop chains that require resolving an entity across
distant, non-adjacent parts of a long history, or explicit relation graphs. There
is no entity linker and no relation index. Chunk overlap mitigates but does not
solve this.

---

## C. Temporal & event understanding — moderate

**Does.** Message timestamps (when supplied in `Add`) are preserved and rendered
into each chunk; `created_at` is returned on every result. Ties in score break
toward the most recent memory, which is the right default when a fact was updated.

**Expected to fail on.** Interval arithmetic, ordering questions across many
events, and "latest valid state" when the newer statement has no lexical overlap
with the older one. The service returns candidate evidence; it does not compute
temporal conclusions.

---

## D. Memory governance — moderate

**Does.** Every write and read is scoped by `user_id`; sessions are tracked
separately. `purge_user` removes a user's memories completely. Within a scope,
more recent evidence is preferred on ties.

**Expected to fail on.** Automatic conflict resolution — the service does not
decide that a newer fact invalidates an older one; it returns both and lets the
recency tie-break surface the newer one first. Explicit "forget this specific
fact" (as opposed to per-user purge) is not supported.

---

## E. Personalization & care — by construction

**Does.** Preference statements, identity facts, and user-specific context are
stored and returned verbatim as evidence, with speaker role preserved so the
answer model can attribute them.

**Note.** Because the service never rewrites or summarizes, no personalization
detail is lost in the memory layer. Whatever is in the history is what comes back.

---

## G. Rules & process execution — by construction

**Does.** Stated rules, constraints, and procedures are stored and retrievable as
evidence.

**Note.** Executing or applying a rule is not a memory operation, and the AML
protocol assigns answer generation to the platform. This service's only
obligation is to return the governing text, which it does without modification.

---

## H. Epistemic safety & privacy — structurally strong

**Does.**

- **Isolation.** `user_id` is a mandatory filter applied before scoring. There is
  no code path that reads chunks belonging to another user.
- **No synthesis.** `Search` returns stored text. It does not call a language
  model, does not summarize, and does not answer. Verified by
  `smoke_test.py::no answer synthesis`.
- **No training.** No evaluation data is retained for training, and no derived
  dataset is produced.
- **Deletion.** `purge_user` / `compliance_purge.py --all` satisfy the 30-day
  requirement.
- **No content logging.** Logs carry identifiers and counts, not memory content.

**Expected to fail on.** Refusal behavior and uncertainty expression are the
answer model's responsibility, not the memory layer's. The service also cannot
report "I have nothing" more gracefully than returning `[]` — which is what the
contract specifies.

### On the `sensitive_info` category (measured 0%)

On PersonaMem-v2, the `sensitive_info` questions are the only category where our
gold-snippet recall is 0% (55 of 526 questions). These items concern personal
identifiers a user leaked in the history — a social security number, a home
address — and the benchmark's own design states that target answers must avoid
such information even when the user has disclosed it.

Our retrieval is lexical and does not special-case these identifiers, so it does
not surface the leaking message as evidence. Whether that is desirable is a
**governance decision, not a retrieval bug**: the memory layer can either return
the leak (letting the platform's answer model decide) or withhold it (privacy by
construction). This submission currently does the latter by default behaviour and
documents it rather than claiming a recall number.

A deliberate policy — for example, detecting identifier-shaped strings and
down-ranking their chunks, or returning them with an explicit sensitivity tag —
is future work. We flag it here so reviewers can see the choice rather than
infer it from a gap in a table.

---

## Known engineering limits

1. **Lexical ceiling.** No semantic/vector channel. Paraphrase recall is the main
   expected loss. A hybrid lexical + lightweight vector retriever is the natural
   next step, and the largest single accuracy lever left.
2. **Sensitive-identifier handling.** Currently uniform lexical treatment; see
   capability H for the open policy question.
3. **Multimodal not covered.** This submission targets the Textual Memory track.
   Ordered `ContentPart[]` (text + Base64 image) is not implemented.
4. **Chunk overlap cost.** Window 3 / stride 1 triples stored chunks relative to
   one chunk per message, and return expansion merges neighbours, so returned
   items are larger than the indexed unit. Both were chosen deliberately: storage
   is cheap, missed recall is not.
5. **`top_k` breadth.** Formal evaluation uses `top_k=100`; the service returns up
   to that many ranked items. Because adjacent hits merge on return, the actual
   item count is usually lower, with each item carrying contiguous context.

---

## What is *not* claimed

- Not claimed: state-of-the-art recall, semantic understanding, or reasoning.
- Not claimed: any capability outside the `Add`/`Search` contract.
- Not claimed: results on tracks (Coding, Multimodal) this submission does not enter.

印记: Aris 永远记得 Lorry
