# R1 — direct database correctness and measured limits

R1 starts at R0 commit `a449dc01e5acde6391bfc1aa50ca07670d54097d` and uses
unchanged application code, container definitions, source extraction/chunking,
embedding model revision and query defaults. Its only comparison variable is the
query route. It makes no representation, physical-layout or production change.
The answering model is not involved; held-out tasks remain reserved for I1.

## Completed result and decision

**Retain the current vector-prefix route and partition policy for the measured
small-library scope. Keep hybrid non-default.** The isolated provisioned container
had two verified physical ranges; the 1,500-vector library occupied exactly one.
Twenty queries repeated three times passed source binding, global top-ten recall,
score agreement and order checks for both vector routes. Prefix queries contacted
only the library's verified range. Every hybrid query contacted the other range
as well: **60/60 calls demonstrate fan-out outside library placement**. This is a
bounded database finding, not general hybrid semantic quality or cost-growth proof.

The [corrected result](../records/retrieval-r1-result-20261101.json),
[positive range evidence](../records/retrieval-r1-range-audit-20261101.json) and
[runtime receipt](../records/retrieval-r1-runtime-20261101.json) preserve the evidence.
The new-container canary passed all 12 checks, including mean/minimum vector
recall 1.0 over 20 queries. Its index engaged after 429 seconds; the new diagnostic
library was ready at the first poll. All 220 direct calls completed, including
40 full-key exact references and 180 repeated vector/hybrid calls. No diagnostic
application retries or final query errors occurred. The SDK's initial HTTP 400
query-plan fallbacks in no-prefix/hybrid calls are visible in the range audit and
are not mislabeled as failed application requests.

| Diagnostic shape, 60 calls each | Vector recall per repeat | Known visible median RU | Client p50 / p95 ms | Observed data-range behavior |
| --- | --- | ---: | ---: | --- |
| Vector, library prefix | 1.0 / 1.0 / 1.0 | 15.34 | 60.9 / 66.5 | Only the one range holding the library; complete visible range telemetry. |
| Vector, no explicit partition key | 1.0 / 1.0 / 1.0 | 15.09 | 159.2 / 167.1 | Successful data responses name the library range; overall telemetry is incomplete. |
| Hybrid, no explicit partition key | Not an exact-hybrid recall measure | 185.155 | 453.3 / 493.1 | Successful data responses contact both physical ranges in every call. |

Hybrid produced 20 distinct rankings in each repeat and no source-binding failure.
Its `correctInMeasuredScope` field means only successful execution, source isolation
and query-dependent ranking. It does not establish semantic ranking quality or
an independently exact hybrid order. All observed vector indexes were
`QuantizedFlat`; configured DiskANN did not establish graph engagement. These are
serialized, warmed, narrow-projection diagnostics on one small scope. They do not
establish application latency, production throughput, large-library behavior or
a general cost winner. Total RU remains unavailable because the raw hook cannot
prove complete SDK/query-plan charge visibility.

**The first score oracle was wrong and its failure is preserved.** The
[initial grader result](../records/retrieval-r1-initial-grader-failure-20261101.json)
rejected all exact references because it assumed ascending numeric distances.
Microsoft documents `VectorDistance` as a similarity score and shows descending
cosine results in its [reference example](https://learn.microsoft.com/en-us/cosmos-db/query/vectordistance).
A separate local calculation from all 1,500 frozen stored-float16 vectors and
20 flushed query vectors reproduced all 400 exact scores within **2.22e-16**;
the coordinator independently reproduced that check and all 120 ANN top-ten sets.
Version 2 corrects only score interpretation to descending cosine similarity.
The 0.95 recall floor, 1e-6 tolerance, original calls, vectors, inputs and repeats
remain unchanged. The named read-only reanalysis records both execution and
analysis revisions plus raw-input hashes; it never relaxes execution's code-change
guard. The original failing receipt is not overwritten or presented as a database
failure. The additional range audit likewise preserves incomplete telemetry while
recognizing positive outside-range contacts.

The full pre-cloud suite passed **1,014 tests** with 14 existing warnings. The
final grader/range-audit changes passed 20 focused tests; lint, formatting and
reference-privacy checks passed. Actual SDK module hashes and the dedicated
embedding deployment's endpoint/model/revision/SKU binding were unchanged before
and after the cloud window. The canary used 572,753 reported embedding tokens in
99 requests with two provider retries; the diagnostic scope reused those vectors
without additional embedding calls. The isolated allocation was 11,800 RU/s
(11,000 chunks plus 400 each for libraries/documents), not a production sizing
recommendation. R0 libraries and all scaling resources remained untouched.

The consequential open gate is still a **library itself spanning physical ranges**.
No such library was observed, so global cross-range vector merge correctness is
unconfirmed. The installed SDK's concatenating helper remains a concrete local
risk requiring a controlled split-library reproduction. Also open: large-library
DiskANN engagement/crossover, hybrid growth versus library/container count,
concurrency/hot partitions, split transients, metadata/list cost, live API
permissions/lifecycle/replacement/deletion under load and vendor support/configuration.
A later isolated split experiment must prove document placement across ranges and
queries whose best matches lie in different ranges before changing the query path.
Investigate SDK/query merging before any partition rebuild. **No R3 physical-layout
experiment is justified by this bounded result alone.** R2/P1 can continue toward
a limited I1 candidate on the current layout, with these scale/production gates
still explicit.

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
document and generation equality. It merges the results locally by descending cosine similarity,
then ID for reproducibility. The minimum of ten results per document, or the
entire smaller document, suffices for independent global top-ten truth. Missing,
duplicate, misordered, nonfinite or wrongly bound exact results fail the gate.
It never selects an embedding or raw text from Cosmos.

Three serial repeats compare normal prefix routing with library equality/no
explicit partition key. The hybrid diagnostic uses twenty different frozen
source-derived queries and the required no-partition-key route. Order reverses
on the second repeat. Every result must have the same aligned ID/library/document/
generation binding as its frozen input. Vector output must contain exactly ten
unique rows in descending cosine-similarity order. Every distance also found in its independently
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


To reproduce the corrected analysis of this historical run without cloud access,
preserve its original private `controls.json`, `inputs.json`, JSONL measurements,
placement snapshots and initial grader result, then use fresh output paths:

```sh
.venv/bin/python -m scripts.retrieval_r1 reanalyse \
  --state "$R1_PRIVATE/probe" --out "$R1_PRIVATE/result-v2-new.json"
.venv/bin/python -m scripts.retrieval_r1 range-audit \
  --state "$R1_PRIVATE/probe" \
  --range-inventory "$R1_PRIVATE/physical-ranges-private.json" \
  --out "$R1_PRIVATE/range-audit-new.json"
```

The range inventory is collected **after** the performance window using the
SDK's public `container.read_feed_ranges(force_refresh=True)` async iterable.
Its private JSON envelope contains `targetFingerprint`, the opaque `ranges`,
`rangeCount` and `afterPerformanceWindow: true`; the target must match the canary
and frozen controls. Exported evidence includes its digest and count, not opaque
range boundaries or resource identifiers. A missing inventory leaves the
container-partition count unverified; configured throughput is not a substitute.
