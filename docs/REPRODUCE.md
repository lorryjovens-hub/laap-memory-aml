# Reproduce

A clean-machine walkthrough. Nothing here depends on private resources.
Estimated time: **under 5 minutes**.

---

## 0. Prerequisites

- Python ≥ 3.11
- No GPU, no model download, no external API key

## 1. Clone and install

```bash
git clone https://github.com/<org>/laap-memory-aml.git
cd laap-memory-aml

python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate

pip install -r requirements.txt
pip install httpx               # only needed for the TestClient tests
```

## 2. Run the contract tests

Exercises the Add/Search contract plus the two AML red lines.

```bash
python tests/test_aml_api.py
```

Expected:

```
[OK] add contract (chunks=3)
[OK] search returns evidence (5 items, top=0.120614)
[OK] search returns raw evidence (no answer synthesis)
[OK] empty results -> []
[OK] sample isolation enforced (no cross-user leak)
[OK] signal matching (top hit = 'User: Project Alpha budget is 7311.\nUser: Pr')
[OK] top_k respected (5 <= 5)
[OK] HTTP end-to-end (/health, /add, /search)
[OK] auth enforced (401 without key, 200 with key)
[OK] purge_user (deleted 1 chunks, compliance support)

==================================================
10/10 passed
```

## 3. Start the service

```bash
export AML_MEMORY_KEY='reproduce-key'      # Windows: set AML_MEMORY_KEY=reproduce-key
python -m laap.aml.server --host 127.0.0.1 --port 8095 --db ./aml.sqlite3
```

In another terminal:

```bash
curl -s http://127.0.0.1:8095/health
```

Expected:

```json
{"ok":true,"service":"laap-aml-memory","version":"1.0.0","auth_required":true,"chunks":0,"users":0,"requests":{"add":0,"search":0,"errors":0},"ts":...}
```

## 4. Run the contract smoke test

```bash
python deploy/aml/smoke_test.py --base http://127.0.0.1:8095 --key reproduce-key
```

Expected:

```
==================================================================
AML 记忆服务 Smoke — http://127.0.0.1:8095
==================================================================
[PASS] health  (status=200 auth_required=True ...)
[PASS] add contract  (status=200 echoed=True)
[PASS] search contract  (status=200 items=3 ...)
[PASS] no answer synthesis (红线)  (raw_evidence=True synth_free=True)
[PASS] sample isolation (红线)  (no cross-user leak)
[PASS] empty -> []
[PASS] auth enforced  (no_key=401 with_key=200)
------------------------------------------------------------------
结果: 7 passed / 0 failed
SMOKE OK — 可以提交 AML 评测申请
```

## 5. Manual contract check (no tooling)

```bash
curl -s -X POST http://127.0.0.1:8095/add \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer reproduce-key' \
  -d '{"request_id":"r1","messages":[{"role":"user","content":"The launch code is 7788.","timestamp":1735689600000}],"user_id":"u1","session_id":"s1"}'
# {"success":true,"request_id":"r1","user_id":"u1","session_id":"s1"}

curl -s -X POST http://127.0.0.1:8095/search \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer reproduce-key' \
  -d '{"query":"launch code","user_id":"u1","top_k":5}'
# {"data":[{"id":"u1:s1:0:...","content":"User: The launch code is 7788.","score":...,"created_at":1735689600000}]}
```

Note the second response returns the **stored sentence**, not "7788" alone —
evidence, not an answer.

## 6. Verify isolation manually

```bash
# Add under a different user
curl -s -X POST http://127.0.0.1:8095/add -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer reproduce-key' \
  -d '{"request_id":"r2","messages":[{"role":"user","content":"Unrelated."}],"user_id":"u2","session_id":"s2"}'

# Search u2 for u1's fact -> must be empty
curl -s -X POST http://127.0.0.1:8095/search -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer reproduce-key' \
  -d '{"query":"launch code 7788","user_id":"u2","top_k":10}'
# {"data":[]}
```

## 7. Deletion

```bash
python deploy/aml/compliance_purge.py --list
python deploy/aml/compliance_purge.py --user u1
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ModuleNotFoundError: laap` | not run from repo root | `cd` to repo root, or `pip install -e .` |
| 401 on every call | `AML_MEMORY_KEY` set but not sent | add the `Authorization` header, or unset the env var |
| `search` always returns `[]` | `user_id` differs from the `Add` call | scopes are strict by design |
| `starlette.testclient` import error | `httpx` missing | `pip install httpx` |
| SQLite file locked (Windows) | an old server process is running | stop it; connections are closed per request |

---

## Public endpoint

The submitted deployment is reachable at `https://aml.laap.cn`
(Cloudflare Tunnel → local `127.0.0.1:8095`). The tunnel and authentication setup
is documented in `deploy/aml/README.md`; reproducing the tunnel is optional for
reviewing the memory system itself.
