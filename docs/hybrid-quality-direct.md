# Direct hybrid versus vector source-quality measurement

This measurement changes no application behavior. It runs both frozen ranking modes against one completed, newly owned Microsoft-profile library. The unchanged API authorizes and produces compact evidence with selector handles. The only adapters replace query embedding calls with an immutable cache shared by both arms and observe the existing Cosmos calls. Run from the coordinator's reviewed integration commit after the fresh corpus, labels and controls have been frozen.

## Frozen behavior audit

`python -m scripts.hybrid_quality_direct audit` emits the exact public `queryAudit` plan object, including source hashes. At the retained foundation:

- Client lexical terms are lowercase ASCII alphanumeric runs, deduplicated in first-occurrence order and capped at 16. Hyphens split identifiers, non-ASCII characters split/drop terms, and late terms can be omitted. There is no client stemming, stopword removal, phrase preservation or identifier preservation. The service's English full-text policy is recorded; its internal tokenizer behavior is not independently measured here.
- RRF weights, `filterPriority` and vector search multipliers remain omitted and therefore use service defaults. No numeric RRF default is inferred.
- Vector queries have the library prefix partition key. Hybrid `ORDER BY RANK` queries omit the explicit partition key and retain the exact library equality, as required by the existing E27 finding. Query rendering keeps `TOP`, float16 conversion/subnormal flushing, and excludes embedding values from projections.
- Top 8 fetches 16 candidates, applies active-generation visibility, and retains at most 8. Adjacent passages are separately expanded. Hybrid queries with no lexical terms use the existing vector fallback; actual SQL determines the recorded effective mode.

These are comparison controls, not proposed query corrections. Changes to tokenization, weights, indexes, profiles or evidence presentation require a separate experiment.

## Execution contract

The public plan contains `arms: ["vector", "hybrid"]`, exact manifest/task/gold byte hashes, `queryAudit` from the command above, and:

```json
{"direct":{"top":8,"includeNeighbors":true,"repeats":1,"order":"alternate-arm-first-by-case","maxEvidenceTokens":200000,"filters":{}}}
```

There are 40–60 fresh confirmation questions; the selected corpus has 48. One paired direct pass is scheduled, alternating the first arm for each question. There is no top-20 diagnostic or automatic retry of a completed/partial schedule. SDK retries retain their existing policy. Readiness and meaningful hybrid-ranking probes use separate coordinator-frozen preparation queries; those probes and their complete outcomes are preserved independently.

A private config supplies `libraryId`, `agentId`, `sourceMap` from actual document IDs to public source IDs, and an `executionLedger` path outside the capture output. Capture requires an exclusively created ledger and new output directory. Local authenticated dev-mode ASGI is required; actual data-plane access uses the application's normal Entra/managed-identity credentials. Cloud steps are announced and scheduled centrally. No provisioning, ingestion, deletion or credential configuration is automatic.

The private coordinator preflight must bind `planSha256`, `settingsBindingSha256`,
`sourceSnapshotSha256`, `ownedLibraryId`, and explicitly accept
`preparationAccepted`, `incarnationAndPoliciesMatch`, `hybridRanksMeaningfully`.
It records `scaleResourcesAccessed=false`, `canaryReceiptPath` outside all Git
checkouts, exact receipt byte `canarySha256` and `targetFingerprint`. The canary
provenance must be one of these mutually exclusive cases:

- Existing verified incarnation: `newContainerCreated=false`,
  `inheritedCanaryPassed=true`, `freshCanaryPassed=false` (the last field may be omitted).
- Newly created isolated incarnation: `newContainerCreated=true`,
  `freshCanaryPassed=true`, `inheritedCanaryPassed=false`, with its own passed canary.

Both paths re-open the bound passing canary and check the live container policies
and endpoint/database/container RID fingerprint before confirmation searches.
An old receipt cannot authorize a recreated container. The raw receipt is copied
into `canary-private.json` and included in the capture artifact hashes. Recovery
does not relax the cache's actual embedding deployment/model/revision/SKU binding.

The preflight's `actualCorpusReadiness` object records observed indexed
document/chunk counts, index diagnostics or their unavailability, probe evidence
and unresolved limits. The runner requires the acceptance; the coordinator must
independently inspect the evidence behind it. A bare boolean is not evidence of
index readiness.

The source snapshot contains actual API `library`, sorted `documents`, agent-bound `tools`, and an owned-library `SELECT TOP 1000` chunk inventory containing only IDs, generation, chunk index, content hash, ETag, embedding model and metadata version. Empty/truncated inventories or missing hashes/ETags fail. Every original must have ready generation 1 with exact source bytes, metadata and Microsoft ingestion-job recipe. Before/after snapshots must be identical. Unchanged row ETags bind stored vectors without selecting embeddings. The snapshot hash covers the entire four-field object; compare its first three fields to replay's library snapshot and retain the chunk inventory alongside it.

The latest M1 continuation found that the deployed scheduled reconciler targets the default Blob container instead of its configured container. This runner does not repair or validate that service. The coordinator must use completed ingestion jobs and explicit cleanup verification for these owned resources. A successful read-only comparison makes no claim about remote scheduled orphan cleanup or complete lifecycle acceptance.

## Commands

Use the worktree's established local environment; setup installs dependencies but creates no cloud resources or credentials:

```sh
sh scripts/dev setup
sh scripts/dev lint
.venv/bin/pytest -q tests/test_hybrid_quality_direct.py
```

The following private paths are examples. `hybrid.json` is the reviewed frozen public plan; deployment receipts use the existing ARM capture format accepted by `scripts.retrieval_controls.deployment_binding`. Coordinator-selected isolated endpoints and credentials are loaded outside the repository. Redirect all live command output to private logs.

The plan also freezes `preparationQueries`, each with `id`, `question` and public `expectedSourceIds`. `prepare` rejects confirmation questions, uses one shared embedding for each paired preparation query, and records every attempted query. Distinct hybrid rank lists and expected-source presence are bounded sanity checks. The prospective corpus gate requires more than 16 published chunks and at least six documents with multiple chunks; all eight original documents must satisfy the source snapshot checks. Index build progress and execution mode remain explicitly unknown unless the coordinator obtains separate evidence.

```sh
RUNTIME=/private/tmp/fli-hybrid-quality-runtime
.venv/bin/python -m scripts.hybrid_quality_direct audit
.venv/bin/python -m scripts.hybrid_quality_direct snapshot \
  --config "$RUNTIME/direct-config.json" --output "$RUNTIME/source-preparation.json"
.venv/bin/python -m scripts.hybrid_quality_direct prepare \
  --config "$RUNTIME/direct-config.json" --plan "$RUNTIME/hybrid.json" \
  --manifest docs/testing/hybrid-quality/manifest.json \
  --tasks docs/testing/hybrid-quality/tasks.json --output "$RUNTIME/preparation"
.venv/bin/python -m scripts.hybrid_quality_direct embed \
  --tasks docs/testing/hybrid-quality/tasks.json \
  --deployment "$RUNTIME/embedding-before.json" --output "$RUNTIME/query-cache"
# Capture a fresh ARM receipt after the embedding pass, then finalize:
.venv/bin/python -m scripts.hybrid_quality_direct finalize-embeddings \
  --source "$RUNTIME/query-cache" --deployment "$RUNTIME/embedding-after.json"
.venv/bin/python -m scripts.hybrid_quality_direct capture \
  --config "$RUNTIME/direct-config.json" --plan "$RUNTIME/hybrid.json" \
  --manifest docs/testing/hybrid-quality/manifest.json \
  --tasks docs/testing/hybrid-quality/tasks.json --cache-dir "$RUNTIME/query-cache" \
  --preflight "$RUNTIME/direct-preflight.json" --output "$RUNTIME/direct-capture"
.venv/bin/python -m scripts.hybrid_quality_direct score \
  --source "$RUNTIME/direct-capture" \
  --manifest docs/testing/hybrid-quality/manifest.json \
  --tasks docs/testing/hybrid-quality/tasks.json --gold "$RUNTIME/gold.json" \
  --reviews "$RUNTIME/direct-reviews.json" --output "$RUNTIME/direct-reviewed.json"
```

Embedding preparation makes exactly one input request per frozen question through the existing retrying embedder. It preserves returned vectors, float16 fingerprints, token/request/retry counters and before/after actual deployment bindings. Partial caches cannot be finalized or used. Search then has zero provider embedding calls, so paired search timing excludes preparation latency; preparation cost must be reported separately.

Every search preserves its actual raw response, compact wire response, SQL shape/parameter digest, visible SDK response headers, latency and failure status. Compact evidence must exactly match projecting the captured raw response with the frozen budget/handles contract. No budget refusal or foreign/invisible generation can be scored. Code, raw captures, cache, settings/config/preflight and before/after snapshots have byte hashes. Failed attempts remain private and cannot yield an accepted complete receipt.

## Review and scoring

Private reviews contain exactly one row per case/arm. Each row explicitly supplies:

- `initialRegions`, `expandedRegions`: delivered required original region IDs; expansion includes the initial hits and their neighbors.
- `initialFacts`, `expandedFacts`: required fact IDs whose requested meaning and necessary relationships are present in that evidence, with source attribution intact.
- `initialEvidenceReviewed: true`, `expandedEvidenceReviewed: true`, set only after original-source review. Table associations and split text require review; text matches alone never set these flags.
- `initialUsefulHitRanks`, `initialIrrelevantHitRanks`, `initialUnjudgedHitRanks`: disjoint arrays partitioning all returned initial ranks. Unlabeled evidence remains unjudged until reviewed, rather than becoming irrelevant by default.
- Optional `unanswerableHelpfulContext`: `helpful_absence_context`, `not_helpful`, `unjudged`, or null. Never place raw text here.

The public labels identify minimal required evidence. Expected fact prose can contain surrounding clause context; answer credit depends on the requested meaning, not verbatim wording. Initial and expanded required-region recall, required-fact recall and all-required-evidence rate are separate primary measures. Both arms' macro means over answerable questions are reported. Question-level paired gains/losses and overlapping family breakdowns expose exact-term versus concept effects.

`labeledEvidenceHitPrecision` is explicitly a normalized-substring diagnostic. It neither measures semantic precision nor calls unmatched chunks irrelevant. `initialUsefulHitPrecision` requires every initial hit to be reviewed; otherwise it is null with explicit unjudged counts. Unanswerable recall and precision are null. Helpful absence context is a separate judgment. A missing required region is not automatically an answer failure; later selected/full-source reads are measured by replay.

Scoring produces a **private** complete receipt with the frozen hashes, exact matrix, raw artifact paths/hashes and reviewed source coverage for replay authorization. Raw paths contain environment identifiers and must not be committed. The coordinator publishes an allowlisted result summary with artifact hashes, public case/source IDs and aggregate counts only. Raw files, questions' returned content, gold text, deployment receipts and credentials remain in private runtime storage.

Known API RU, visible chunk-response RU, observed partition contacts and visible 429/5xx/retry-after responses are retained. SDK callbacks do not guarantee visibility into every plan/component retry or partition contact: total RU/retries and completeness remain unavailable. These quality results are not an independent exact hybrid-rank or ANN-recall oracle. All live ranking, readiness, original-evidence review and replay acceptance remain untested until the coordinator executes those gates.
