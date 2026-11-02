# R0 scale reconciliation and R1 handoff

2026-11-01. Evidence cutoff: the R0 worktree started at
`9738236ce9cb3e5d08624f6dd14f8714be51c0cc`; the inspected main checkout was
`02c218baebb67d851aea45c6936c59bbf6f976e5`. This is a read-only reconciliation of
existing measurements, not a new cloud scale run. Its machine-readable companion
is [the inventory receipt](../records/retrieval-r0-scale-inventory-20261101.json).
The [foundations roadmap](consults/2026-11-01-retrieval-foundations-roadmap.md)
defines the package order. Existing specification, query, permission and lifecycle
rules remain unchanged.

## Completed, previously described as ongoing, and unavailable

| Work and owner role | Last available evidence | Status at this handoff | Resource rule |
| --- | --- | --- | --- |
| Phase 4 small-library scale probe; scale-probe session owner | Committed E59 and receipt, 2026-10-29; owner's local session handoff at 19:00 and 19:17 UTC | Measurement complete. Owner reported the disposable database deleted and no scale work running. This historical statement was inspected locally; deletion and present activity were not rechecked against Azure. | Do not reconnect to or recreate that former resource. Reuse its immutable receipt. |
| Further scaling mentioned in the modular roadmap | No later committed receipt in main or the modular worktree; no separate active scale chat in the available Codex inventory | No later running job, owner, resource reservation or completion result could be verified. A roadmap statement is not a job-status receipt. Work in another client/host may exist. | Treat every unidentified resource as reserved. R1 must obtain a current owner handoff or use newly isolated resources with separate capacity. |
| Modular-ingestion, recovery and model/context validation; modular-ingestion session owner | E61–E63 and their sanitized receipts | Completed functional/model runs; these are separate from scale evidence. Small application libraries do not demonstrate steady-state ANN or multi-partition behavior. | Do not use the old demo/recovery scopes for a scale load. |
| R0 scale inventory; R0 owner | This document and its hashed evidence inventory | Local inspection complete; no cloud queries, loads, provisioning or deletions performed by the reconciliation task | No scale resource is transferred to R1. |

The private owner-session locator and original handoff remain outside the
repository. No credentials, environment identifiers, mutable runtime directories
or session IDs are published. The absence of a newer located receipt is not proof
that no other job exists. No messages were sent to other chats or to Microsoft.

## What the measurements settle

“Settled” below is bounded to the measured configuration and workload. It does not
mean that all workloads or the production support contract are settled.

| Question | Result and exact boundary | Still open / next owner |
| --- | --- | --- |
| Does a small library's vector cost grow when unrelated libraries are added? | E59: ten fixed libraries of 1,000–5,000 real abstract vectors; 20 questions each; three ANN repeats per stage; library-prefix routing; one physical partition; SDK 4.17.1. Median RU changed by less than 1%, documents retrieved stayed at 50, first-repeat recall@10 means were 0.995–1.0. All ten passed the implemented scope grade at both later stages. | More physical partitions; larger probed libraries; other filter/selectivity distributions; representative semantic relevance. R1. |
| How large was that container? | Nominal stages: 10 / 413 / 1,013 libraries and 25,000 / 176,599 / 243,273 passages. A 1,500-passage lag-test library was added after stage 2. Actual final inventory was **1,014 libraries / 244,773 passages**. Three 10k–20k libraries were background load, not large-library retrieval probes. | Do not report the background libraries as a passed large-library search test. R1. |
| Was DiskANN graph search observed? | E59's configured DiskANN index reported **`QuantizedFlat:/libraryId`** for all ten small probes. Earlier 1,500-row canaries did likewise. | Graph engagement, crossover, large-library cost/recall, filtered image subsets and a controlled `quantizedFlat` policy comparison remain open. R1 diagnostics, then R3 if justified. |
| Does omitting the library filter matter? | E59's isolated negative control read every row: 1,203.87 → 8,496.07 → 11,777.02 RU. This demonstrates growing full-container cost on this configuration. | It does not authorize an application query without library equality. R1 must retain that invariant and isolate any deliberate negative control. |
| Can a new library become indexed promptly in a populated container? | E59: one new 1,500-row library was indexed at the first poll after a 56.8-second write. E25's fresh-container index lag was approximately six minutes; E61/E62 canaries measured 215/338 seconds. | No general zero-lag guarantee; no split-rebuild lag, load concurrency or distribution estimate. R1. |
| Does hybrid ranking work with Python prefix routing? | E56: in tested SDKs, `RANK` plus a partition key returned storage-order lists. Library equality in `WHERE`, with no partition key, ranked; 4.17.1 used four measured responses and 138–158 RU on one 1,008-row library. `Local` scope did not resolve it. | Hybrid growth/fan-out, semantic ranking quality and a supported SDK fix remain open. Keep `partition_key_for` and vector default. R1. |
| Is vector ranking correct when one library spans physical partitions? | No measurement. The SDK append-versus-global-merge concern in capacity §1 is a code-based hypothesis. | Highest-priority R1 gate: prove the same library occupies multiple ranges, compare distances and global top-k against an independently merged exact reference. Do not infer a partition-key redesign before investigating the query/SDK path. |
| Does a small library stay confined to its ranges in a multi-partition container? | No measurement. E57 corrects the earlier source studies: their queries had owner filters but **no partition-key routing**, and all data was on one physical partition. | R1 must capture contacted ranges and prove placement separately. More than one container partition alone does not prove a library spans them. |
| How much contention and throughput can a library/container sustain? | E59 serialized its probes; no workload-concurrency grade. Write RU was about 92 per abstract, not the canary's approximately 144 per longer chunk. | Shared query/ingest load, p95 tails, throttles/retries, hot-library ceiling and split-transient cost remain open. Workload scenarios are hypotheses, not forecasts. R1/C1. |
| Is the configuration supported and operationally reliable? | E58 records the vendor status checked on 2026-10-28; E17/E62 record float16 failures on particular accounts and passing replacement-account canaries. | Current vendor confirmation for hierarchical keys/sharding, preview/support conditions, shard limits and unexplained float16 failures remain open. Do not turn historical vendor status into a newly verified claim. R1 before production. |
| Are list/metadata/lifecycle operations safe and economical at scale? | Earlier small live and local tests cover authorization, metadata, retries, generation visibility, replacement and deletion. E59 wrote only chunks and cannot test these paths. | Cross-partition list cost, bulk `refresh_library`, multi-instance freshness, natural remote completion/deletion races and purge recovery under load remain open. R1/I1/production work. |
| Has a representation or container-layout winner been selected? | E55 structure lab is a synthetic functional demonstration; E61–E63 are bounded ingestion/replay observations. ANN agreement with exact vectors is not semantic relevance. | R2 passage granularity/discovery; R3 frozen-vector layout; P1 evidence/selector experiments. R0 selects no architecture change. |

## Reconciliation details that must survive the next report

1. **Scope grading and repeat spread differ in prose and code.** The historical
   `scale_probe.grade` permitted RU drift up to the larger of 20% of baseline and
   the baseline run-median range. Build-plan prose says “within 20% and within
   the spread of three repeat runs.” E59's less-than-1% drift passes the implemented
   tolerance, but does not generally fit its tiny repeat spread. Preserve the
   original receipt and report the implemented criterion; do not claim that the
   stricter conjunction was passed (only 3/10 final probes fit the strict spread).
   R0's version-2 harness now implements the stated conjunction, with separate
   `ruWithinTolerance` and `ruWithinBaselineSpread` flags. A later run must freeze
   its precise formula before measurement; any changed tolerance is a new control.
2. **Recall was not averaged over all ANN repeats.** The historical summary uses
   the first ANN repeat of each question and the one exact result per stage:
   20 recall comparisons per probe/stage, with 60 ANN calls. RU medians use all
   three repeats. R0's version-2 summaries retain all repeats, require every
   repeat's mean to reach the floor, and fail incomplete, duplicate, missing,
   errored or null-score runs. A wholly missing probe remains in the report.
3. **The stage-3 readiness timeout is explained, not erased.** The wait selected
   the largest *new* stage-3 library, approximately 200 vectors. Reading all of that
   small shard did not meet its “stops scanning” rule, so it timed out after 30
   minutes. The existing 1,000–5,000-row probes subsequently reported indexed
   searches. R0's wait now uses an existing 5,000-row probe for that stage rather
   than the tiny new shard. This remains a single-scope readiness probe, so gate
   each actual measured scope, record observed index type, and
   report small-shard scans explicitly. A canary does not establish readiness of
   every later experiment scope.
4. **Earlier numbers are not interchangeable.** Source-project E2 values are
   traffic-weighted decision scores, not observed p95; E3 owners/types/dates were
   synthetic assignments over real abstracts; E59 uses real abstracts with seeded
   non-topical library assignments. None is workplace-task accuracy or a measured
   production traffic distribution. E56 hook charges exclude the query-plan request.
5. **No total-cost winner follows.** Keep known RU/model counters, failed attempts,
   retries and waits. Label missing provider/workflow/billing/hosting counters
   unavailable; label tokenization, standing-cost allocation and scenario arithmetic
   estimates. Never turn a missing counter into zero.

## R1 execution handoff

Start from the final R0 commit, on a separate `codex/retrieval-r1` branch/worktree.
Record that exact SHA in the private run controls and sanitized result. The
inherited implementation/evidence base is `9738236ce9cb3e5d08624f6dd14f8714be51c0cc`.
Use the reusable [local setup](local-development.md) before the commands below.
R1 depends on the R0 frozen corpus/scenarios/controls and this inventory. It does
not depend on R2 or P1 code. Coordinate capacity with the scale-resource owner;
parallel coding does not require simultaneous cloud load.

### Local preflight (no credentials or Azure required)

```sh
.venv/bin/python -m pytest -q tests/test_query.py tests/test_canary.py \
  tests/test_canary_run.py tests/test_scale_probe.py tests/test_ids_and_generation.py
.venv/bin/python -m scripts.scale_probe --help
.venv/bin/python -m scripts.canary --help
.venv/bin/python -m scripts.canary --corpus "$R1_REAL_CORPUS" --count-only
```

`R1_REAL_CORPUS` is a private real-source corpus large enough for the mandatory
1,500-chunk canary, separate from the small task-labelled pilot when necessary.
Record source hashes and embedding revisions; no random vectors or filler text.
The following existing command produces a report from an **immutable private copy**
of a previous run's files, without accessing Azure:

```sh
.venv/bin/python -m scripts.scale_probe report \
  --state "$R1_RECEIPT_COPY" --out "$R1_PRIVATE/scale-recomputed.json"
```

Compare it to the original receipt before admitting it as evidence: version-2
recall/completeness/strict-spread grades intentionally differ, current SDK labels
can differ, and the old report's hand-added lag
inventory clarification is not reconstructed by the historical reporter. Do not
point `--state` at an ongoing owner's mutable directory.

### Cloud preflight and bounded reproduction

Announce the exact step before executing it. The user authorized necessary
isolated testing; no further blanket permission is needed. Credentials, resource
names, endpoints, quota reservations and cleanup ownership stay private. If fleet
execution is needed, use **nervus only**; if unavailable, stop that cloud path and
continue local work. Do not convert a demo account, reuse a scaling account, or
change a vector policy in place.

On a freshly isolated scope with the existing definitions, load private connection
settings through the existing identity path, then run the canary before any other
load. This command uses managed identity/default credentials and preserves private
receipts; it does not provision resources:

```sh
: "${R1_PRIVATE:?private run directory required}"
: "${COSMOS_ENDPOINT:?isolated account binding required}"
: "${COSMOS_DATABASE:?isolated database binding required}"
.venv/bin/python -m scripts.canary --corpus "$R1_REAL_CORPUS" \
  --target-chunks 1500 --index-wait-minutes 30 \
  --records-dir "$R1_PRIVATE/canary"
```

`infra/scale-lab.bicep` creates a disposable database on an explicitly supplied
account. It supplies no provisioned-throughput/split orchestration, so it alone
cannot satisfy the multi-partition gate. R1 must prepare and review an isolated
provisioned-resource plan, private capacity/time ceiling and cleanup manifest before
that new experiment. Do not silently run the 245k-row scale reproduction just to
obtain a small R0/R1 sanity check.

If the missing evidence warrants reproducing the existing one-partition scale
probe, these are its executable entry points; run one stage at a time and stop
after the declared bound. `R1_VECTOR_CORPUS` is private JSONL with real `searchText`
and corresponding 3,072-dimension `embedding`, not new synthetic content:

```sh
.venv/bin/python -m scripts.scale_probe load --stage 1 \
  --corpus "$R1_VECTOR_CORPUS" --state "$R1_PRIVATE/scale"
.venv/bin/python -m scripts.scale_probe wait --stage 1 \
  --state "$R1_PRIVATE/scale" --minutes 30 --settle 300
.venv/bin/python -m scripts.scale_probe measure --stage 1 \
  --state "$R1_PRIVATE/scale"
.venv/bin/python -m scripts.scale_probe report \
  --state "$R1_PRIVATE/scale" --out "$R1_PRIVATE/scale-report.json"
```

Stages 2 and 3 add the frozen historical background loads, only if predeclared.
The current script includes a deliberate unscoped negative control inside its
disposable database. It is not a supported application query. The script does
**not** implement multi-partition truth, full-scale hybrid comparison, permissions
or lifecycle validation. Those are explicit R1 extensions, not fictitious CLI flags.

### Highest-value missing experiment and acceptance

Extend existing `scale_probe`/canary measurement helpers, retaining the R0 frozen
representation and vectors. First collect vector-only retrieval with the normal
prefix route and with library equality/no explicit partition key. Capture scores,
ordered result IDs, every contacted partition range, per-response RU, latency,
retrieved rows, observed index, retry/wait/error counts and query/SDK hashes.
No answering agent participates in this database correctness test.

For a library proven to span ranges, construct an independent exact top-k by
enumerating its known document partitions with full keys, exact vector distances
and a deterministic local merge (ties documented). Comparing a potentially
mismerged prefix ANN list to the same potentially mismerged prefix exact list
would conceal the defect. Test multiple queries whose best matches occur in
different ranges; inspect monotonic distance order as well as set recall.

Acceptance is all of:

- Mandatory new-container canary passes, including vector recall floor **0.95**
  and hybrid distinct-ranking check; actual measured scopes have readiness receipts.
- No wrong-library, wrong-agent, stale/deleted generation or metadata-filter leak.
  Direct chunk probes do not replace API permission/lifecycle tests.
- Vector recall@10 against independent global exact results is at least 0.95,
  no null distances, correct global distance order within documented ties, and
  adequate successful coverage of every predeclared query/repeat. Errors stay in
  the denominator; partial runs cannot pass.
- Small-library queries contact no range beyond the verified library placement;
  report total RU and p50/p95 latency, partition count and index type. Incomplete
  range diagnostics mean “unverified,” not “no fan-out.”
- Hybrid uses required `WHERE` equality without a partition key. Verify distinct
  rankings first, then report cost versus container/library count. No claimed
  exact hybrid recall. Freeze 20% cost tolerance and repeat-spread handling before
  comparison; report fan-out separately. A cost-growth result is valid evidence,
  and keeps hybrid non-default.
- A failure produces a bounded keep/change decision and failing receipt. Investigate
  supported SDK/query routing first; do not rebuild the partition hierarchy as
  the first response. No architecture promotion without the existing permission,
  provenance, replacement/deletion and retry gates.

Blockers remain explicit: unverified current resource ownership, private credentials
or quota unavailable, insufficient real material, no supported way to produce and
verify a split library within the bounded capacity/time envelope, incomplete
partition diagnostics, and unresolved vendor configuration/support. A test with
several partitions but only single-partition libraries closes only that narrower
case. Preserve working local artifacts and state exactly which cloud gate did not
run; do not rewrite an untested claim as a pass.
