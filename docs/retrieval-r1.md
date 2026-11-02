# R1 — direct database correctness and measured limits

R1 starts at R0 commit `a449dc01e5acde6391bfc1aa50ca07670d54097d` and uses
unchanged application code, container definitions, source extraction/chunking,
embedding model revision and query defaults. Its only comparison variable is the
query route. It makes no representation, physical-layout or production change.
The answering model is not involved; held-out tasks remain reserved for I1.

The [local audit](../records/retrieval-r1-audit-20261101.json) records issues found
before any new comparison. R0's version-2 scale grade applied the documented
repeat-spread rule only to RU; version 3 also applies it to retrieved rows.
The immutable historical files were copied privately and regraded without cloud
access. All ten probes retain complete runs, all-repeat recall and retrieved-row
spread passes. Only three of ten satisfy the strict RU-spread condition at each
later stage. The historical E59 receipt and its implemented tolerance remain
unchanged; this is a reanalysis, not a repeated scale run.

The canary previously allowed duplicate query ordinals, did not fully validate
exact-reference cardinality and retained only the successful retry's counters.
It now requires each expected query/reference, exactly ten unique results and
finite distances, and preserves visible failed attempts, retry waits, every
response's index report and partition-range telemetry. Successful-attempt fields
remain available for historical comparisons. Known charges are retained, but the
raw hook cannot prove that SDK or query-plan requests were all visible, so total
RU is unavailable rather than assumed complete.

An opt-in private canary capture binds exact source chunks, vectors, query text,
query vectors and embedding usage to the passing receipt by SHA-256. It refuses
an existing path or a path inside any Git checkout. It is never a repository
artifact. The capture is required to use verified `text-embedding-3-large` revision
`1`, 3,072 dimensions. The dedicated deployment must be independently checked
through fresh management receipts before and after embedding, using
`scripts.retrieval_controls.deployment_binding`.

## Independent reference and controls

The current SDK's installed non-aggregate merge helper concatenates partial
results. A local call with test-only distance replies reproduces that behavior,
including an out-of-order four-row result for `TOP 2`; exact module hashes are
in the audit receipt. This strengthens the code-based concern, but does not prove
a live library query reaches that path across multiple physical partitions.
A library's placement and live results are separate required evidence.

`scripts.retrieval_r1` reuses the canary's immutable 1,500 real passage vectors
and source structure under a fresh test-library identity. It prepares no new
representation and embeds nothing. A private capture and a passing receipt must
match each other and the actual target container's incarnation and full policy.
Code hashes and source/vector hashes are frozen before loading.

For each of twenty deterministic source-derived vector queries, the harness runs
exact searches against **every known full document partition key**, with library,
document and generation equality. It merges the results locally by distance,
then ID for reproducibility. The minimum of ten results per document, or the
entire smaller document, suffices for independent global top-ten truth. Missing,
duplicate, misordered, nonfinite or wrongly bound exact results fail the gate.
It never selects an embedding or raw text from Cosmos.

Three serial repeats compare normal prefix routing with library equality/no
explicit partition key. The hybrid diagnostic uses twenty different frozen
source-derived queries and the required no-partition-key route. Order reverses
on the second repeat. Every result must have the same aligned ID/library/document/
generation binding as its frozen input. Vector output must contain exactly ten
unique rows in distance order. Every distance also found in its independently
measured per-document reference is compared within **1e-6 absolute distance**.
Reported recall includes ordinary ID recall and a declared boundary-tie allowance;
rows tied at the global cutoff can differ without a false order/recall failure.
Every repeat's mean tie-aware recall must reach **0.95**. Hybrid is checked for
source isolation and distinct rankings, not exact-hybrid recall or semantic quality.

A per-document full-key count census before and after measurement must match
frozen counts and placement. A library is proven to span ranges only if every
census response identifies its range and the union contains multiple ranges.
Global multi-range ranking confirmation additionally requires query winners in
more than one verified range. Missing range diagnostics mean unverified fan-out,
never zero fan-out. A fresh multi-partition container alone proves neither gate.

Readiness is measured on the actual loaded scope. Correctness can still be
reported when a scope scans, but no steady-state performance conclusion follows.
A provisional 20% useful efficiency improvement remains predeclared; this bounded
routing diagnostic does not select an efficiency or physical-layout winner.

## Execute on a newly isolated reservation

The coordinator owns provisioning, deployment quota, scheduling and cleanup.
Cloud credentials and resource names never enter setup or repository config.
Do not use R0 libraries or unidentified scaling resources. Run the canary before
loading any experiment rows, with unchanged `cosmos/*.json` definitions, managed
identity/default credentials and a dedicated embedding reservation.

```sh
sh scripts/dev setup
sh scripts/dev check -q
# Load only explicitly supplied private isolated environment bindings.
.venv/bin/python -m scripts.canary --corpus "$R1_REAL_CORPUS" \
  --target-chunks 1500 --index-wait-minutes 20 \
  --records-dir "$R1_PRIVATE/canary" \
  --capture-inputs "$R1_PRIVATE/canary-inputs.json"
.venv/bin/python -m scripts.retrieval_r1 prepare \
  --state "$R1_PRIVATE/probe" --capture "$R1_PRIVATE/canary-inputs.json" \
  --canary "$R1_CANARY_RECEIPT"
.venv/bin/python -m scripts.retrieval_r1 load \
  --state "$R1_PRIVATE/probe" --canary "$R1_CANARY_RECEIPT"
.venv/bin/python -m scripts.retrieval_r1 measure \
  --state "$R1_PRIVATE/probe" --canary "$R1_CANARY_RECEIPT" --minutes 20
.venv/bin/python -m scripts.retrieval_r1 report \
  --state "$R1_PRIVATE/probe" --out "$R1_PRIVATE/result.json"
```

`prepare` requires a fresh private directory. Loading records a durable attempt
marker; an interrupted or ambiguous load refuses blind retry. Measurement keeps
every completed call in a private JSONL file and records interruption status.
A partial matrix cannot pass. Processes lock the private state; snapshots and code
must remain unchanged. Export only the allowlisted report after reviewing it.

Direct chunk probes cannot establish API authorization, stale-generation
publication, metadata propagation, replacement/deletion or worker retry safety.
Existing local application tests remain necessary, and live application lifecycle
checks belong to I1/production gates. No direct-query pass promotes application
behavior or waives those gates.
