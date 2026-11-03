# Hybrid versus vector: answering confirmation

`scripts/hybrid_quality_replay.py` is a new experiment driver on foundation
`3980333ee8453af48c7133cab24522868701d1cc`. It reuses I1's independent-task
replay, source snapshots, actual provider-input capture and source-binding
checks without changing the consumed R0/I1 packs or weakening their guards.
It does not provision, ingest, replace, delete or tune anything.

The coordinator owns cloud resources, quota, credentials, permissions checks,
capture of actual API/deployment state, and the exclusive measurement window.
No answering starts until the complete direct-retrieval comparison and the
independent corpus review have been accepted. Direct-retrieval misses are
results, not a reason to block the answering comparison or change questions.

## Frozen controls

- One original-source library, Microsoft layout profile, original source bytes,
  metadata, generations, source/query embedding revision and all tool definitions.
- Two local API processes with identical non-mode settings and source code;
  `SEARCH_MODE=vector` or `hybrid` is the treatment. Both use compact evidence
  with selector handles. Loopback ports and process IDs necessarily differ.
- Fixed Sol `gpt-6.1-sol` revision `2026-09-29`, Responses transport, medium
  reasoning, 8,000 maximum output tokens, three provider attempts, `store=false`,
  truncation disabled, and 200,000 context allowance for each independent task.
  The implementing Codex model has no effect on those choices.
- 40–60 fresh, distinct confirmation questions, including supported questions and
  genuinely unavailable completed-contract values. The intended 48-question pack
  produces 192 answering turns. Gold is hash-bound in the plan; `run` never reads
  it. The answering adapter receives only each task's `case` and `question`.
- First repeat: each question vector then hybrid. Second repeat: each question
  hybrid then vector, using the same frozen question order. Each turn receives a
  new conversation and full context allowance. Run the complete schedule serially.
- Frozen direct pass: top 8, neighbors enabled, one pass, alternate arm first by
  case, 200,000 evidence-token ceiling, no filters. Validated hybrid routing has
  the SQL library equality predicate without partition-prefix routing; vector has
  the library prefix. Routing is separately reviewed before authorization.

The two repeats remain repeated observations of the same 48 questions. They do
not provide 192 independent questions. Purposeful corpus selection and overlapping
family tags limit generalization; no significance claim is inferred from counts.

## Local preparation and private artifacts

Run the repository setup in each worktree. It uses the repository dependency
lock and validates isolation without provisioning or putting credentials into
automatic setup:

```sh
sh scripts/dev setup
sh scripts/dev doctor
.venv/bin/ruff check scripts/hybrid_quality_api.py scripts/hybrid_quality_replay.py tests/test_hybrid_quality_replay.py
.venv/bin/pytest -q tests/test_hybrid_quality_replay.py
```

Before the first live turn, merge and commit the corpus, plan, direct driver and
this driver into the measured coordinator checkout. Its inventory includes all
application and measurement Python scripts. Keep that exact checkout available
for finalization and scoring: a later implementation change intentionally makes
the old run fail code verification. Do not edit scripts during measurement.

Keep all config, management/runtime receipts, full snapshots, original files,
answers, provider inputs, traces, review packets and ledger outside **every** Git
checkout. CLI output and scoring exports contain counters and hashes rather than
source bodies, answers or environment IDs. Review exports before committing.

The public plan must include the fields checked by `inputs`: schema
`fli.hybrid-quality.plan.v1`, `baseCommit`, ordered `arms=["vector","hybrid"]`,
`repeats=2`, `evidenceView="compact"`, `selectorMode="handles"`,
`sourceStrategy="microsoft-indexer-layout-v1"`, the exact `ANSWERING` and `DIRECT`
constants, ordered task `cases`, and byte SHA-256 values `manifestSha256`,
`tasksSha256`, `goldSha256`. Extra descriptive plan fields are allowed.

Private configuration has exactly `model`, `agent`, `libraryId`, `indexUrls`.
`model` has `model`, `version`, `deployment`; `agent` has `id`, `name`,
`instructions` with exact `retrieval_controls.AGENT_INSTRUCTIONS`.
`indexUrls` maps `vector` and `hybrid` to different local HTTP endpoints. Use
neutral library/agent names. Invocation requires `DEV_API_KEY` and `AOAI_ENDPOINT`
in the private environment; the endpoint is bound to both runtime and actual
answering deployment receipts. No credentials appear in arguments or committed
files.

Runtime receipt files map both arms to actual-process captures with schema
`fli.hybrid-quality.api-runtime.v1`. Each capture includes `capturedAt`, `indexUrl`,
`apiSourceSha256`, `settingsBinding`, `settingsBindingSha256`, `effectiveSettings`,
`authenticationMode="dev"`, `environment="local"`, `processId`,
`ownEnvironmentAndImportVerified=true`, `sourceRoot`, `pythonPrefix`, `gitRevision`
and identical `declaredCodeRevision`. `measurementCodeSha256` and `launcherSha256`
bind the loaded measurement helpers and wrapper. `storedRowsSha256` binds the
owned library's actual chunk metadata including content hashes and ETags.
Use `scripts.hybrid_quality_api` for capture inside each running API process;
copying a desired config is not a runtime check. The wrapper checks its own source
root and virtual environment, sets `FLI_CODE_REVISION` to actual HEAD and freezes
code/launcher hashes before starting. The authenticated runtime capture rejects
disk/code drift before re-reading stored rows. Non-loopback clients are rejected.
`settingsBinding` has exactly I1's `BINDING_KEYS`; all non-mode values must match.
Deployment receipts are timestamped actual account and deployment ARM GETs,
including endpoint, model version, upgrade policy, provisioning state and SKU.
Before and after captures must be fresh within 15 minutes when consumed. Separate
ARM envelopes must also establish the actual query embedding deployment as
`text-embedding-3-large` revision `1`, `NoAutoUpgrade`, with unchanged capacity.
That binding must equal the accepted direct query cache's deployment binding.

An accepted direct receipt must have `status="complete"`, `finishedAt`, matching
plan/corpus byte hashes, exactly one row per case/arm, original-region sets
`initialRegions` and `expandedRegions`, and complete `rawArtifactPaths` plus
`rawArtifactsSha256` bindings. A capture without source-based review is insufficient.
Initial evidence must be a subset of expanded evidence. No quality equality or
minimum recall threshold is required.
The direct receipt's common non-mode settings and API code hashes must match the
answering process. Its saved source-before/source-after snapshots must match each
other and the replay library/documents/tools; its chunk digest must match both
fresh API processes. Direct and adaptive searches therefore share a frozen
source generation and actual embedding provider identity.

The coordinator writes private authorization after reviewing controls, sources,
permissions and direct evidence. Schema is
`fli.hybrid-quality.execution-authorization.v1`; it requires `status="accepted"`,
fresh `reviewedAt`, byte `directReceiptSha256` and `planSha256`, canonical
`sourceSnapshotSha256`, `runtimeBindingsSha256`, `codeSha256`, and explicit true
`corpusIndependentlyReviewed`, `sourceLibraryBoundariesVerified`,
`hybridRanksMeaningfullyVerified`. Canonical hashing uses `fli.structure_lab.digest`;
runtime fingerprints come from `runtime_bindings`, code from `digest(hashes())`.
`outputDirectory` binds the exact new private run path. `executionLedger` is an
exclusive-create file outside that directory. Operator captures and authorization
are reviewed evidence, not cryptographic attestation of a remote service.

## Execute once, then recheck

The following paths are examples; the coordinator supplies the accepted receipts
and exclusive window. These commands perform live answering only at `run`.

```sh
export HQ_PRIVATE=/private/tmp/fli-hybrid-quality
# Start each process separately, only in the coordinator's announced window:
.venv/bin/python -m scripts.hybrid_quality_api \
  --environment "$HQ_PRIVATE/api-env.json" --config "$HQ_PRIVATE/replay-config.json" --arm vector
# The second process uses the same command with --arm hybrid.
# Authenticated GET /__hybrid_quality/runtime on each configured loopback API
# yields its actual capture; combine responses as {"vector": ..., "hybrid": ...}.
```

After collecting and accepting direct, corpus, source and runtime evidence:

```sh
.venv/bin/python -m scripts.hybrid_quality_replay run \
  --plan docs/testing/hybrid-quality/plan.json \
  --manifest docs/testing/hybrid-quality/manifest.json \
  --tasks docs/testing/hybrid-quality/tasks.json \
  --config "$HQ_PRIVATE/replay-config.json" \
  --deployment "$HQ_PRIVATE/deployment-before.json" \
  --embedding-deployment "$HQ_PRIVATE/embedding-before.json" \
  --runtimes "$HQ_PRIVATE/runtime-before.json" \
  --direct "$HQ_PRIVATE/direct-accepted.json" \
  --authorization "$HQ_PRIVATE/replay-authorization.json" \
  --output "$HQ_PRIVATE/replay"
```

After the last turn, independently recapture both running API processes and the
actual answering deployment. Their capture times must follow run completion.
Finalization also re-reads both live source/tool snapshots, every turn's saved
snapshots, actual wire/source sequence, system context and tool definitions.

```sh
.venv/bin/python -m scripts.hybrid_quality_replay finalize \
  --deployment "$HQ_PRIVATE/deployment-after.json" \
  --embedding-deployment "$HQ_PRIVATE/embedding-after.json" \
  --runtimes "$HQ_PRIVATE/runtime-after.json" \
  --output "$HQ_PRIVATE/replay"
```

There is no resume or rerun operation. The ledger survives failures or interruption;
completed and partial answer/wire/usage receipts remain private. Do not delete or
replace it to obtain a nicer result. An incomplete run is reported as incomplete,
with the exact provider/control gate and unavailable counters. A run is only
finalized after its entire declared matrix and fresh post-run checks pass.

## Review source quality separately from costs

Random opaque review IDs are allocated after preparation and before the first
answer. Packets sorted by opaque ID omit explicit arms, repeats, rank scores,
timing/usage and telemetry. Source order and answer contents can still reveal
inferable differences; this is not a claim of fully blinded human evaluation.

```sh
.venv/bin/python -m scripts.hybrid_quality_replay review-packets \
  --source "$HQ_PRIVATE/replay" --gold "$HQ_PRIVATE/gold.json" \
  --output "$HQ_PRIVATE/answer-review-packets"
.venv/bin/python -m scripts.hybrid_quality_replay score \
  --source "$HQ_PRIVATE/replay" --gold "$HQ_PRIVATE/gold.json" \
  --reviews "$HQ_PRIVATE/answer-reviews.json" \
  --output "$HQ_PRIVATE/answer-scorecard.json"
```

Review every packet against original source regions and frozen task requirements.
Evaluate requested components and qualifications; the longer original paragraphs
in gold provide context and do not require verbatim recitation. Return one
`reviewTemplate` per opaque ID. Judge `taskCompletion`, `factualSupport`,
`citationAccuracy`, `appropriateAbstention`, `sourceRelationships` separately as
boolean or explicit unavailable/inapplicable null. Abstention is applicable to
the eight absent-value tasks and null on answerable tasks. Null is never a pass.
Source relationships include identity, order and table-cell association when the
request depends on them; correct term occurrence alone is not enough.

Manually record `deliveredRegionIds`, `supportedFactIds`, `fullReadRegionIds`,
`searchRegionIds`, `selectedReadRegionIds`, and `blankRegionIdsVerified`; IDs must
belong to the frozen task. Count a supported
fact only when the answer accurately expresses its requested content using
delivered original evidence. `fullReadRegionIds` is a subset of delivered evidence
and requires an actual complete-read body from the correct file. Search/selected
sets likewise require original bodies from the correct file in that channel.
Search includes actual search-tool replies and automatic document-scoped passage
recovery, which invokes search and remains identifiable in the saved traces.
Verify empty
regions positionally. Single-body normalized matches are explicitly diagnostics.

The coordinator independently reviews every failure and arm disagreement using
the opaque packets before accepting conclusions, retaining private adjudication
notes. Do not give reviewers the private arm map until judgments are recorded.
The scorecard reports paired question/repeat outcomes, overlapping families,
required source-region recall, required fact coverage and abstention separately.
It reports `fullReadRegionsBeyondDirectExpanded`,
`fullReadRegionsBeyondAgentSearch` and `fullReadOnlyRegions` separately. These
are set differences from the fixed direct query, actual adaptive searches, and
all actual search/selected reads, respectively. They do not establish causal
retrieval order; review the preserved mode/sequence when interpreting recovery.
A recovered answer does not erase an initial-search miss. Efficiency is secondary, with
provider/cache/reasoning/retrieval counters retained and unavailable values kept
unavailable. Routing, caching, backend placement and unpriced services limit
billed-cost conclusions. There is no automatic default promotion.

The existing M1 scheduled-discovery issue remains an open production lifecycle
gate. This isolated read-only quality comparison does not claim to resolve it or
to validate the complete production lifecycle.
