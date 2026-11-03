# Fresh health before hybrid-quality ingestion

`scripts/hybrid_quality_smoke.py` supplies the fresh data-plane prerequisite for
an existing, previously canary-validated container. It does not replace the full
`scripts/canary.py` requirement for a new account or container. This small check
makes no index-engagement, retrieval-quality, latency or scale claim.

The coordinator announces and runs it in a quiet preparation window. All inputs,
raw headers, source bytes, embeddings, SQL parameters, returned evidence and
ownership ledgers stay outside every Git checkout. The command checks the actual
three container policies and the chunks-container incarnation against the passing
canary. The separate canary corpus contributes twelve real chunks and two distinct
source sentences; the helper rejects any original-byte hash in the frozen
confirmation manifest. No confirmation questions or gold are read.

Before writes, a new UUID library and its exact library/document/chunk inventory
are saved durably. Every key must initially return 404. Each create is preceded by
a durable attempt record; no upsert or automatic create retry occurs. The helper
then verifies all created payloads by point read, the twelve-row scoped inventory,
and two top-three vector and two top-three hybrid queries. It uses the existing
float16 conversion, lexical extraction and SQL builder. Vector uses the library
partition prefix; hybrid omits the partition option while keeping the same single
library equality. All returned rows must belong to the smoke inventory, and the
two hybrid queries must produce different ordered lists.

The smoke has no queue, worker or Blob writes. After the direct writes are
quiescent, cleanup prechecks every exact row before deleting any. A changed row,
unknown read failure or unattempted existing row stops deletion. Deletes require
the observed ETag and proceed from chunks to documents to the library. Exact
point-read 404s and an empty scoped TOP 1 query are required immediately and again
after thirty seconds. Unknown rows are never broadly deleted; errors are never
interpreted as an empty inventory. A failed or ambiguous run keeps its ledger and
must be reviewed before using the explicit `cleanup` action.

The coordinator captures the actual embedding deployment before and after the
smoke. It must remain `text-embedding-3-large`, revision `1`, `NoAutoUpgrade`, with
unchanged SKU/capacity and 3,072 dimensions. Before receipt freshness is fifteen
minutes. `run` returns `pending_model_recheck` only after every live check and
cleanup passed. `finalize` requires a fresh matching capture after completion,
then writes `health.json` with the evidence and source-file hashes. It never
rewrites a completed receipt. The ingestion restart gate consumes this finalized
receipt together with the mounted-workflow binding and partial-ingestion cleanup
plan; smoke success alone does not authorize corpus ingestion.

```sh
.venv/bin/python -m scripts.hybrid_quality_smoke run \
  --state-dir "$HQ_PRIVATE/smoke" \
  --env "$HQ_PRIVATE/api-env.json" \
  --corpus "$CANARY_CORPUS_PRIVATE" \
  --canary "$CANARY_RECEIPT_PRIVATE" \
  --protected-scope "$HQ_PRIVATE/protected-scopes.json" \
  --embedding-before "$HQ_PRIVATE/smoke-embedding-before.json"

# Coordinator captures a new actual ARM embedding receipt after run completion.
.venv/bin/python -m scripts.hybrid_quality_smoke finalize \
  --state-dir "$HQ_PRIVATE/smoke" \
  --env "$HQ_PRIVATE/api-env.json" \
  --embedding-after "$HQ_PRIVATE/smoke-embedding-after.json"
```

A new `run` is refused when an execution or ownership ledger already exists.
`cleanup` uses the same state, environment and protected inventory; it verifies
actual policies and incarnation again, and cannot turn a failed smoke into a
passing health receipt. SDK connection/throttle retry settings are explicitly
zero and checked after client creation. Individual data operations have a
45-second deadline, embedding preparation 180 seconds and the command 900 seconds.
The embedding client retains up to three attempts and its usage counters. A
deadline interruption may leave owned rows; preserve the private ledger and
inspect before any cleanup or further run.

Local verification does not contact Azure:

```sh
.venv/bin/pytest -q tests/test_hybrid_quality_smoke.py
.venv/bin/ruff check scripts/hybrid_quality_smoke.py tests/test_hybrid_quality_smoke.py
.venv/bin/ruff format --check scripts/hybrid_quality_smoke.py tests/test_hybrid_quality_smoke.py
```
