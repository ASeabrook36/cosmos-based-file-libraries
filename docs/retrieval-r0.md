# R0 — benchmark and scale handoff

R0 freezes an evaluation baseline; it selects no retrieval architecture, provider,
chunk size, index policy or evidence-format change. The implementation starts at
`9738236ce9cb3e5d08624f6dd14f8714be51c0cc` on `codex/retrieval-r0`.
Use the final committed R0 revision reported with this handoff as `R0_COMMIT` for
successor worktrees. Each measurement also records the exact code-file hashes,
so a later documentation/receipt commit cannot obscure what ran.

The [frozen pack](testing/retrieval-r0/README.md) contains 12 original public DOCX
files and 48 tasks (32 development, 16 held-out). Source bytes and gold text stay
private; committed hashes, public acquisition links, source locators and the
reconstruction command bind the labels to original source. This is a purposeful
public-policy pilot, including a long response compilation, not broad enterprise
accuracy evidence. Four earlier DOCX cases are development regressions now.
The four new held-out files and the sanction-classification task family remain
unused for candidate selection.

The [scale reconciliation](retrieval-r0-scale-handoff.md) identifies exactly which
foundation questions are settled in a measured scope and which remain open. It
also gives R1's commands and acceptance criteria. The last located scale-owner
handoff reports the E59 run complete and its disposable database deleted; this is
historical owner evidence, not a fresh cloud inventory. Unknown resources remain
reserved. R0 neither loads nor modifies any scaling resource.

## Completed baseline and remaining limits

[The baseline receipt](../records/retrieval-r0-baseline-20261101.json),
[source scorecard](../records/retrieval-r0-scorecard-20261101.json),
[live runtime/attempt receipt](../records/retrieval-r0-runtime-20261101.json) and
[local distributions](../records/retrieval-r0-local-baseline-20261101.json)
record the executed result. All 36 turns completed with source/code snapshots and
answering deployment settings unchanged. The full local suite passed **961 tests**
with 14 existing warnings; lint, formatting and private-reference scans passed.

| Complete strategy | Task completion | Factual support | Citation accuracy | All labeled source regions delivered, applicable task turns |
| --- | --- | --- | --- | --- |
| Current | 10/12 | 10/12 | 12/12 | 8/10 |
| Custom structured | 12/12 | 12/12 | 12/12 | 6/10 |
| Microsoft layout | 12/12 | 12/12 | 12/12 | 10/10 |

These are source-based assistant judgments over six tasks repeated twice, not
independent human accuracy estimates. Current's form answers failed in both
repeats: the delivered extraction omitted all 32 labeled blank cells, but the
answers asserted blankness without disclosing the lost grid. Custom preserved
usable table separators/blank positions; Microsoft delivered literal HTML table
rows/cells and empty cells. This is an observed case-specific capability, not
proof of broad table or visual parity. All six complete-section turns delivered
all 71 required original regions in order, including authenticated source headings.

Custom's missing labeled regions were auxiliary instructions/headings or claims
supported elsewhere in the delivered source; task completion and label coverage
therefore differ deliberately. Catalogue tasks have no body-region denominator
and passed exhaustive metadata enumeration. Citations are assessed at the supplied
source granularity: current often supplies section-start locations, not exact gold
paragraph coordinates. Preserve this limitation in P1.

Four custom and one Microsoft per-call read-budget refusals recovered. Microsoft
also recovered two `source_scope_unavailable` attempts. No task failed because of
the shared conversation allowance; maximum provider-reported input was **94,452**
tokens, not 200k. Fourteen model rate-limit retries incurred 178,106.2 ms of reported
wait. All occurred in Microsoft cells; agent tool choices and cache state also
varied, so no timing/cost winner is inferred. Its incomplete retrieval charges
remain a known subtotal with the total unavailable.

The actual five-file text-vector inventories were 112 current, 917 custom and 9
Microsoft, plus three image rows each for current/custom. Small scopes do not
establish DiskANN graph use. The post-replay census/full reads did not warm model
runs; all 15 full reads completed. All 19 logical ingestion-stage attempts have
finish receipts; provider connector usage is still unavailable. The inherited
canary was verified against the current container incarnation. No new container
or scale load was created. The temporary answering deployment was removed;
original deployment configurations were unchanged, and the three R0 libraries
remain frozen for handoff.

The runtime receipt separates the API recipe revision from the inherited worker
build and verifies its deployed image. Ingestion modules have no diff from the
R0 base; retrieval runs in this checkout's API. R0 changes measurement tools only.
The remaining 26 development tasks, all 16 held-out tasks, full long-document
cloud ingestion, actual-corpus ANN readiness, large-library/multi-partition/hybrid
correctness, concurrency and lifecycle-under-load remain **untested here**.
No representation, provider, layout, model-default or production decision is made.

## Frozen scenarios and experiment controls

These ranges are engineering scenarios, **not traffic forecasts**. Sizes refer to
published passage vectors unless another unit is stated. Discovery vectors, if
introduced by R2, must be counted separately rather than silently increasing the
scope. Changes to these assumptions require a new named experiment.

| Scenario | Library scope | Shared deployment | Update scenario | Query concurrency | Evidence boundary |
| --- | --- | --- | --- | --- | --- |
| Personal/small team | 10–100 files; 1k–5k vectors | 100–1,000 libraries | 1–10 changed files/library/day; bursts of 20 | 1–4 in a library, 10–50 total | E59 supports serialized vector cost at ten tested scopes on one physical partition; concurrency is untested. |
| Larger shared library | 1k–10k files; 50k–500k vectors | 100–1,000 libraries | 100–1,000 changed files/day in busiest library | 5–25 in that library, 25–100 total | Large-library ranking/graph behavior and update contention remain open. |
| Many libraries | 1k–10k libraries with the above skew | Several physical partitions expected; prove actual placement | Mixed steady updates plus a 1k-file import | 25–100 total | Beyond E59's actual 1,014 libraries/244,773 rows and all multi-partition claims remain open. |
| Large source read | Original 337k–346k-token transport compilation; selected regions vs full source | One source in the pilot, not a scale corpus | One replacement and retry scenario per candidate | Serial correctness first | Full source alone exceeds the fixed 200k conversation allowance. Keep this a context-limited full-read case; selected retrieval can be evaluated separately. |

The manifest freezes GPT-6.1 Sol `2026-09-29`, Responses, medium reasoning,
8,000 output tokens, three provider attempts, `store=false`, no automatic
truncation, and **200,000 conversation tokens for each independent task/member**.
This is a diagnostic control, not a product default or a pooled budget. The
implementation agent's model is unrelated. A refusal caused by inadequate
capacity is not an index-quality failure. Raise capacity for every member in a
new comparison or report that task separately.

Keep original bytes, initial metadata, prompts, tools, model/embedding revisions,
source generations/revisions, query defaults, actual tool arguments, overlap
policy and repeat order frozen. R0 uses current → custom → Microsoft, then the
reverse, serially. Before/after snapshots refuse changed metadata, profiles,
generations, tools or application code. Profile recipes retain their intentional
complete-strategy differences: current/custom 500-token targets and 50-token
sentence overlap; Microsoft 5,000-token windows with 200 **characters** overlap.
Do not interpret those as a controlled component comparison. R2 component work
holds extraction and overlap fixed; R3 holds the selected vectors fixed.

The initial cloud run uses five source files and six task IDs listed in
`manifest.baseline`, twice per profile: 36 turns. All eight development sources
receive local extraction/chunk measurement. No held-out answer, retrieval or
performance result is used in R0. Source inspection for writing gold is not a
held-out evaluation. The local source read distributions and cloud scorecard are
separate artifacts; neither substitutes for ANN recall or semantic relevance.

Provider tokens and reported RU/timing are measured counters. Source/evidence
JSON tokenization is estimated. Known subtotals remain visible when failed-call
usage, connector embedding tokens, layout pages, workflow actions, hosting
allocation or billed currency are unavailable. Provider cache state and actual
inference region are uncontrolled; serial scheduling reduces contention but does
not prove equal cache or compute. The small cloud libraries run in functional
smoke mode, without a new steady-state index-readiness claim. The existing
recovery container's passing real-text canary is inherited; no new container is
created by R0.

## Reproduce local artifacts

The [local environment](local-development.md) creates one `.venv` per worktree,
uses pinned dependencies, and exposes setup/test/lint actions. It never loads
credentials or provisions cloud services.

```sh
sh scripts/dev setup
sh scripts/dev check
# Set these to PRIVATE directories outside every Git checkout.
.venv/bin/python -m scripts.retrieval_r0_corpus verify \
  --source-dir "$R0_CORPUS" --gold-output "$R0_PRIVATE/gold.json"
.venv/bin/python -m scripts.retrieval_benchmark validate --corpus "$R0_CORPUS"
.venv/bin/python -m scripts.retrieval_benchmark local \
  --corpus "$R0_CORPUS" --output "$R0_PRIVATE/local-baseline"
```

Use `fetch` instead of `verify` only for explicit public downloads into a fresh
private corpus directory. Hash mismatches fail closed. Local output directories
must be fresh; interrupted runs are retained. `local-baseline/receipt.json`
contains only hashes/counts/distributions; its adjacent source/chunk/tree files
are private. The current extractor and existing source-structure adapter are
called directly; no alternative ingestion implementation was added.

## Reproduce the bounded cloud baseline

Announce cloud steps first. Use an isolated modular-validation service set with
a passing canary, a working shared Microsoft workflow, a managed-identity worker
and reconciliation job. Do not substitute a scale account, container, queue or
model deployment. Cloud setup is deliberately manual/explicit, separate from
`sh scripts/dev setup`; existing [modular hosting](modular-ingestion.md) and
[canary](structure-lab.md) runbooks apply. Authentication and all environment
bindings stay private. If access is unavailable, finish the local baseline and
list ingestion, actual-corpus readiness and real-model replay as unrun.

Start the API with **this checkout's** `.venv`, bind localhost, set `SEARCH_MODE=vector`,
and retain a private snapshot of its settings and source hashes. Copying another
worktree's editable environment can silently run the wrong implementation;
`sh scripts/dev doctor` checks this. Existing application source is unchanged. After loading the private isolated settings,
run `HOST=127.0.0.1 SEARCH_MODE=vector .venv/bin/fli-api` in its own process.

```sh
.venv/bin/python -m scripts.retrieval_benchmark ingest \
  --corpus "$R0_CORPUS" --output "$R0_PRIVATE/ingestion"
```

This creates one current library and an idempotent custom/Microsoft pair, loading
only the five baseline development sources with identical original bytes and
metadata. The private directory is locked against concurrent runners. Pair
retries retain source/experiment IDs. Ambiguous non-idempotent current-library or
upload requests stop for receipt reconciliation rather than creating duplicates.
Do not delete an interrupted directory or reset an ambiguity marker to retry
blindly. Save attempt counters, failed requests and resumed state.

Create a private `replay-config.json` with exactly `model`, `agent` and `libraries`:
`model` has deployment name, `model: gpt-6.1-sol`, `version: 2026-09-29`;
`libraries` maps `current`, `custom`, `microsoft` to the three newly created IDs;
`agent` has a fixed `id`, `name` and instructions. Use the exact frozen agent
instructions retained in the R0 controls for reproduction, without source facts:

> Use the attached library to answer the question. Read original source evidence,
> distinguish source statements from inference, preserve stated conditions and
> limitations, and cite the correct source file and location. Do not infer an
> answer from a generated description. If the requested evidence is unavailable,
> say precisely what could not be verified.

Resolve the answering deployment through the management plane before and after
replay: the model/version must match, provisioning must succeed, and automatic
model upgrade must be disabled. Preserve both receipts and compare model/SKU
settings. Do not reuse a deployment whose model is inferred only from its name.

```sh
.venv/bin/python -m scripts.retrieval_benchmark replay \
  --config "$R0_PRIVATE/replay-config.json" \
  --deployment-receipt "$R0_PRIVATE/model-before-replay.json" \
  --output "$R0_PRIVATE/replay" --repeats 2 \
  --cases r0-01 r0-02 r0-03 r0-11 r0-17 r0-32
.venv/bin/python -m scripts.score_retrieval_benchmark \
  --run "$R0_PRIVATE/replay" --gold "$R0_PRIVATE/gold.json" \
  --reviews "$R0_PRIVATE/quality-reviews.json" \
  --output "$R0_PRIVATE/sanitized-scorecard.json"
```

The scorer exports an allowlist of counters, task IDs, source-region matches and
explicit review judgments. The review file is private; use the four separate
quality fields described in the pack and retain reasons against original and
delivered evidence. Exact single-body text matches can undercount split paragraphs
or separately represented headings and cannot prove table relationships. Empty
cells never earn automatic coverage. A complete-section pass requires ordered,
complete original section evidence, even if a partial answer sounds correct.
Only reviewed sanitized receipts belong under `records/`.

Capture final metadata/generations, actual chunk distributions, source and
serialized-response sizes, canary lineage and deployment settings. Remove the
temporary answering deployment after the bounded test and verify original
settings unchanged. Preserve the isolated R0 library snapshots for handoff, with
private resource ownership/cleanup notes. They must not become shared mutable
experiment resources: successors copy immutable artifacts or create new scopes.

## R2 executable handoff — vector granularity and navigation

Dependency: final R0 commit, frozen pack, source gold, local snapshots and baseline
scorecard. R2 can develop independently of R1; cloud performance must wait for
capacity coordination and a passing canary/readiness gate. No partition, physical
index, dimensions, precision or query-routing change belongs in R2.

```sh
git worktree add -b codex/retrieval-r2 "$R2_WORKTREE" "$R0_COMMIT"
cd "$R2_WORKTREE"
sh scripts/dev setup
.venv/bin/python -m scripts.retrieval_r0_corpus verify --source-dir "$R0_CORPUS"
.venv/bin/python -m scripts.retrieval_benchmark local \
  --corpus "$R0_CORPUS" --output "$R2_PRIVATE/baseline-local"
.venv/bin/python -m pytest -q tests/test_extraction_and_chunking.py tests/test_structure_lab.py \
  tests/test_modular_ingestion.py tests/test_retrieval_benchmark.py
.venv/bin/python -m scripts.structure_lab --help
```

Implement the controlled coalescing candidate in the existing structure lab's
freeze path, consuming the frozen DOCX source tree, rather than reparsing or
inventing a second ingestion application. The current lab CLI's manifest adapter
is Markdown-oriented; accepting the frozen DOCX tree and source-region labels is
an explicit **R2 implementation dependency**, not an already runnable DOCX cloud
experiment. Keep the tree/source extents fixed. Compare granular passages with
section-aware coalescing near 500 tokens, preserving headings, small blocks,
source order, tables and locators, with a declared identical overlap policy.
Freeze vectors once per representation, cache by model/revision/text digest,
record actual lengths/write counts/embedding tokens and every failed attempt.

Only after selecting a passage representation, hold its vectors unchanged and
add a section/document discovery level. Generated summaries are navigation aids;
answers still read original source. Count summary creation/refresh/omission costs.
Use the existing `structure_lab freeze`, `load`, `evaluate`, `read` commands once
the DOCX/label adapter is implemented; each load requires bound canary receipts.
The existing lab `evaluate` grade is vector agreement, not the source-relevance
score. Add label-based direct retrieval scoring before drawing a semantic claim.

Acceptance: paired development counts for source relevance and selective tasks,
actual chunk/overlap distributions, ingestion/read/write costs, and no observed
source-quality regression. Predeclare the manifest's provisional 20% efficiency
gate; it is not statistical equivalence. If adding discovery gives no useful
gain, retain passages alone. Freeze finalists and their vectors for R3/I1;
confirm on held-out only in I1. Permissions, provenance, metadata, retry and
replacement/deletion failures block promotion. The long taxi full read is a
separate context-limited case; R2 may still test selective retrieval on it.

Blockers: no full-corpus cloud baseline/embeddings or broad format parity yet;
large-library/index correctness remains with R1; provider usage gaps remain
unavailable; the DOCX bridge/source-relevance evaluator must be implemented.
Do not switch to a weaker answering model to fit a quota or compare only the
members that fit. Retaining the existing representation is a valid decision.

## P1 executable handoff — evidence format and selectors

Dependency: final R0 commit and private saved tool replies plus source snapshots;
no R1/R2 result is required for local equivalence work. Use a separate worktree
and runtime directory, with the unchanged model/context/metadata controls.

```sh
git worktree add -b codex/retrieval-p1 "$P1_WORKTREE" "$R0_COMMIT"
cd "$P1_WORKTREE"
sh scripts/dev setup
.venv/bin/python -m scripts.measure_compact_responses > "$P1_PRIVATE/existing-format-fixtures.json"
.venv/bin/python -m pytest -q tests/test_compact_rest.py tests/test_reading.py \
  tests/test_agent_capabilities.py tests/test_retrieval_benchmark.py
```

The measurement above is existing invented-fixture raw/compact behavior, not the
new P1 candidate and not cloud model usage. Extend it to transform **saved R0
responses** into an opt-in dictionary/reference representation that factors
repeated metadata/heading/citation fields. Compare deterministic reconstruction
of exact source text, order, source coordinates, table relationships when present,
coverage, document/section scope and generation binding. Preserve raw/current
compact output. Do not manufacture table relationships already lost in extraction.
Record serialized-token estimates separately from actual provider input counters.

Implement usable source handles/capabilities and recovery as a separate candidate,
covering invalid document selectors, `source_scope_unavailable`, unsupported scopes
and stale generations. Preserve personal binding, all-or-nothing selected reads
and explicit completeness. Use sanitized categories for failures, not raw invalid
selectors in repository logs. First compare format-only and selector-only changes
separately with the same saved replies/source snapshots; then paired agent replay
through `scripts.evaluate_agent_retrieval.replay` and the fixed Responses factory.
R0's three-role runner is for the baseline strategy matrix, not a ready-made
format/selector factorial driver: extend its scheduling/controls explicitly in P1.

Acceptance: deterministic evidence-equivalence tests pass; no permission,
provenance, coverage, lifecycle or stale-source regressions; matched 200k replay
reports source delivery, completion, factual support and citation accuracy
separately, including recovered/remaining selector errors and failed-attempt cost.
Predeclare 20% target token/latency improvement and zero observed source-quality
regression. Held-out remains for I1, including current/new foundation ×
current/compact evidence. No storage/index change or provider rewrite is required.

Blockers: R0 has no P1 format or selector implementation; the replay baseline may
show failures that P1 must reproduce first. Signed attachments/raw source captures
are private. Full-document source fidelity cannot be repaired by JSON compaction.
R2/R3 architectural changes must not be mixed into a P1-only comparison.
