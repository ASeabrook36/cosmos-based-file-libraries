# Matched model and retrieval pilot

This is a new versioned, exploratory experiment on completed comparison commit
`8f7d436987724abac1efe633d071db66f8cbf4ea`. The earlier eight-document,
48-question pack has been consumed. Its reuse here supports paired diagnostics
and regression evidence, **not fresh confirmation**. The earlier run and all its
controls remain unchanged. Nothing in this driver deploys the application,
promotes a default, provisions infrastructure, or merges a branch.

## Prospective controls

[The new plan](testing/hybrid-model-pilot/plan.json) freezes four cells:
Sol/vector, Sol/hybrid, budget/vector, budget/hybrid. Sol is `gpt-6.1-sol`
`2026-09-29`; the economical model is `gpt-5.4-mini` `2026-03-17`. Both use actual
ARM-verified `GlobalStandard` deployments with capacity 200 and
`NoAutoUpgrade`. Capacity is recorded as a service allocation, not a guaranteed
throughput or latency. The coordinator verified offerings and quota before
selection. The four-call preparation probe verifies actual tools and Responses
continuity; a catalog capability alone is insufficient.

Each model gets a fresh reference on the same chosen 64,000 context allowance,
medium reasoning, 8,000 maximum output tokens, 12 tool rounds, at most 13 logical
model calls per turn, and three attempts per logical call. This context allowance
is an operating budget, **not either provider model's native limit**. The existing
agent estimates the remaining context and reserves output space; actual provider
input and output token counts are also checked against 64,000 and 8,000.
Automatic truncation and Responses storage remain disabled. There is no
comparison of this constrained budget model to the previous 200k-context Sol run.

All 48 questions run once in all four cells, for 192 turns. A four-period Williams
design rotates `[0,1,3,2]` over cells `[sol-vector,sol-hybrid,budget-vector,budget-hybrid]`
by question ordinal modulo four. Each cell appears in each position 12 times;
each ordered within-question predecessor pair occurs 12 times. Execution is
serial, with a coordinator-owned exclusive measurement window. This balances
order but does not remove all regional/service variation or support broad causal
claims from one observation per cell.

The client stays Python `azure-cosmos==4.17.1`; Responses uses `openai==3.37.0`.
Ingestion, embedding revision and stored rows, source questions, agent instructions,
compact evidence, tools and selectors remain fixed. Hybrid uses the existing
WHERE-only routing workaround. The representation is Microsoft's one canonical
section per document with improved selectors. It is **not the full custom document
hierarchy**. Table/list structure repairs belong to a subsequent experiment.

## Bounds and accounting

The scored schedule has strict ceilings of 2,496 logical calls and 7,488 physical
Responses create attempts. Its tighter operating guards are 32,000,000 input and
2,000,000 output units. Before each attempt the private meter reserves 64,000 input
and 8,000 output units; successful reported usage replaces the reservation.
Unmetered errors retain the full reservation. Reaching any ceiling stops the
whole schedule and preserves an incomplete run. There is no selective retry,
resume, or automatic increase of limits.

The separate synthetic tool probe makes exactly four logical calls, two per
model, with at most 12 physical attempts and guards of 768,000 input / 96,000
output units. It exercises the same optional nested JSON schema for both models,
a real returned tool call, local tool output and the next model response. Actual
reasoning and other output items must return unchanged in the following request.
It consumes no document question and does not prove every application-tool path.

These are request/token guard ceilings, **not an Azure invoice cap**. SDK retries
are disabled; the existing adapter only retries 429 responses, at most three
attempts. Unknown provider usage is never reported as zero. Credential, embedding
and index HTTP requests are outside the Responses-attempt counter. Provider token
meters, actual physical attempts, logical calls, retry waits, wall time, tool RU
and incomplete RU attribution remain separate. `elapsedMinusRecordedRateWaitMs`
is wall time less known rate-limit waits; it still includes provider queueing,
retry execution, local work and retrieval, so it is not pure inference time.

## Private preparation contract

Run `sh scripts/dev setup`, then `sh scripts/dev test` / `sh scripts/dev lint` as
needed in a new worktree. Keep all endpoint/account/library/deployment identifiers,
source content, gold, telemetry, credentials and provider payloads outside Git.
Create a mode-0700 private runtime directory and use `umask 077`. Cloud provisioning
and credentials are deliberately absent from automatic setup.

`config.json` has exactly these top-level keys:

```text
models: {sol: {model, version, deployment}, budget: {model, version, deployment}}
agent: {id, name, instructions: AGENT_INSTRUCTIONS}
libraryId: isolated reconstructed library
indexUrls: {vector: loopback URL, hybrid: different loopback URL}
```

Model identities must match the plan. The existing `hybrid_quality_api` launcher
uses a private projection `{model: config.models.sol, agent, libraryId, indexUrls}`;
its source, measurement hashes and actual API process settings must match the
experiment checkout. Both API processes read the same immutable isolated library
and stored rows. Preserve unrelated libraries and ongoing resources.

`deployments-before.json` is `{sol: envelope, budget: envelope}`. Each envelope
has `capturedAt`, `account` (actual ARM account GET), and `deployment` (actual ARM
deployment GET). `embedding-before.json` uses the same envelope shape for the
unchanged embedding deployment. `runtimes-before.json` contains actual launcher
captures `{vector: receipt, hybrid: receipt}` from those running API processes,
not recreated environment assertions. Fresh model/runtime captures expire after
15 minutes. Refresh read-only captures immediately before preflight and execution.

`source-before.json` has `{library, documents, tools, chunks}`. The first three,
after the standard URL/expiry scrubbing, must equal the evaluation snapshot's
sole library. Its full stored-row `chunks` digest must match both API receipts.
`preparation.json` requires:

```text
schema: fli.hybrid-model-pilot.preparation.v1
status: accepted
capturedAt: UTC ISO timestamp (within one hour)
manifestSha256: frozen manifest file SHA-256
sourceStrategy: microsoft-indexer-layout-v1
sourceSnapshotPath: private absolute source-before.json path
sourceSnapshotFileSha256: file-byte SHA-256
sourceSnapshotSha256: canonical JSON digest of source-before.json
embeddingDeploymentBinding: hybrid_quality_replay.embedding_binding(envelope, runtimes)
sourceOriginalsVerified: true
allIngestionsTerminalPublished: true
hybridWhereOnlyRanksMeaningfully: true
protectedScopesUnchanged: true
evidenceReferences: [{path: private absolute receipt path, sha256: file-byte SHA-256}, ...]
```

The coordinator must verify these assertions against source checksums, ingestion
terminal/published receipts, a bounded direct ranking check and before/after
protected-scope inventories. They are acceptance decisions, not automatically
inferred from an existing boolean. Hashes bind their supporting evidence.

## Executable sequence

The coordinator announces cloud steps and supplies isolated resources, captured
receipts and `AOAI_ENDPOINT` privately. No cloud/model calls happen in local tests.
Use this checkout's Python and a fresh private directory for every phase. Here
`$PILOT_RUNTIME` is an existing private directory outside the repository; the
config and captures described above live there. Capture paths may be adapted,
but controls and task selection may not.

```sh
umask 077
.venv/bin/python -m scripts.hybrid_model_pilot probe \
  --config "$PILOT_RUNTIME/config.json" \
  --deployments "$PILOT_RUNTIME/deployments-before.json" \
  --output "$PILOT_RUNTIME/tool-probe"

.venv/bin/python -m scripts.hybrid_model_pilot preflight \
  --plan docs/testing/hybrid-model-pilot/plan.json \
  --manifest docs/testing/hybrid-quality/manifest.json \
  --tasks docs/testing/hybrid-quality/tasks.json \
  --config "$PILOT_RUNTIME/config.json" \
  --deployments "$PILOT_RUNTIME/deployments-before.json" \
  --runtimes "$PILOT_RUNTIME/runtimes-before.json" \
  --embedding "$PILOT_RUNTIME/embedding-before.json" \
  --preparation "$PILOT_RUNTIME/preparation.json" \
  --probe "$PILOT_RUNTIME/tool-probe/receipt.json" \
  --output "$PILOT_RUNTIME/scored-run" \
  > "$PILOT_RUNTIME/authorization-template.json"
```

The coordinator reviews the private template, writes `authorization.json`, and
sets `status=accepted`, a fresh `reviewedAt`, and a new `executionLedger` path
**outside** the scored-run directory. Set the four flags to true only after
checking them: `withinExistingResourceAuthorization`, `exclusiveMeasurementWindow`,
`consumedPackAcknowledged`, `noRepresentationChanges`. Preserve every binding.
This is the coordinator's already-authorized experiment gate, not a new user
approval request. The execution ledger is created exclusively and never reused.

```sh
.venv/bin/python -m scripts.hybrid_model_pilot run \
  --plan docs/testing/hybrid-model-pilot/plan.json \
  --manifest docs/testing/hybrid-quality/manifest.json \
  --tasks docs/testing/hybrid-quality/tasks.json \
  --config "$PILOT_RUNTIME/config.json" \
  --deployments "$PILOT_RUNTIME/deployments-before.json" \
  --runtimes "$PILOT_RUNTIME/runtimes-before.json" \
  --embedding "$PILOT_RUNTIME/embedding-before.json" \
  --preparation "$PILOT_RUNTIME/preparation.json" \
  --probe "$PILOT_RUNTIME/tool-probe/receipt.json" \
  --authorization "$PILOT_RUNTIME/authorization.json" \
  --output "$PILOT_RUNTIME/scored-run"
```

A successful loop leaves `pending_recheck`, not a finalized result. Capture fresh
actual ARM and API receipts **after** completion, then:

```sh
.venv/bin/python -m scripts.hybrid_model_pilot finalize \
  --deployments "$PILOT_RUNTIME/deployments-after.json" \
  --runtimes "$PILOT_RUNTIME/runtimes-after.json" \
  --embedding "$PILOT_RUNTIME/embedding-after.json" \
  --output "$PILOT_RUNTIME/scored-run"

.venv/bin/python -m scripts.hybrid_model_pilot review-packets \
  --source "$PILOT_RUNTIME/scored-run" --gold "$PILOT_RUNTIME/gold.json" \
  --output "$PILOT_RUNTIME/blinded-review"

.venv/bin/python -m scripts.hybrid_model_pilot score \
  --source "$PILOT_RUNTIME/scored-run" --gold "$PILOT_RUNTIME/gold.json" \
  --reviews "$PILOT_RUNTIME/reviews.json" \
  --output "$PILOT_RUNTIME/scorecard-candidate.json"
```

Review all 192 packets against original source regions, facts, relationship/order
requirements and citation locators using the existing source-based rubric. Labels
are hidden and packets sorted by random opaque ID; answer style can still reveal
a model. Do not credit a qualifier or table relationship merely because matching
words appeared. Missing-value abstention is a separate stratum; unavailable or
inapplicable judgments are not passes. Review all paired discrepancies before
accepting conclusions. The known table/list loss must remain visible, not repaired
inside the client/model experiment.

Finalization reopens source, model, runtime, authorization, single-use ledger,
wire, physical provider payloads and token counts, then inventories every raw
artifact. Both the original preparation and probe evidence must remain available
at their private paths. Source text, opaque reasoning items and all private paths
stay outside Git. The score command produces an allowlisted, content-free
candidate with paired outcomes, model×retrieval interaction, citations, abstention,
source coverage, search activity and per-cell usage. Inspect it for privacy and
correct interpretation before copying it into a committed results directory.

## Acceptance and untested gates

Local tests exercise balance, controls, tool schema/reasoning continuity, retry
accounting, limits, source hashes, actual model capacity, replay/provider token
reconciliation and paired summary arithmetic with fake providers. They cannot
establish live availability, real source quality, actual 192-turn completeness,
service performance, or human source judgments. Those gates require the recorded
live probe, the whole unchanged schedule, post-run rechecks and complete blinded
review. A failed/limited run remains incomplete and must say exactly which gate
failed; it cannot be silently filled from an earlier library or selectively rerun.
