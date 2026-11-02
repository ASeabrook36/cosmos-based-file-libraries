# R2 — controlled passage granularity and navigation

This experiment starts from R0 `a449dc01e5acde6391bfc1aa50ca07670d54097d`.
It extends the existing structure lab, without changing the API, worker, immutable
profiles, Cosmos layout, permissions or publication lifecycle. The coordinator's
shared frozen-control validation is included as an explicit prerequisite.

## Audit and frozen comparison

The existing lab cannot be treated as the granular baseline: `rows_for` already
concatenates source blocks within a node and `prepare` always generates summaries.
Its document-ID recall and full-node expansion also cannot establish passage
relevance. R2 uses the same lab source/cache/embedding helpers with a controlled
passage-only branch before considering discovery vectors.

R0 gold uses zero-based top-level paragraph and visible table-cell coordinates.
The application DOCX adapter uses one-based XML paragraphs, including table cells.
R2 never compares these numbers directly. It calls the unchanged
`docx-heading-body-v1` adapter, verifies an independent original-DOCX locator
sidecar against every canonical block, and checks every labeled region's normalized
hash against original bytes. Tables preserve the adapter's canonical text and an
original grid/merged-cell sidecar. This does not add source content missing from
the original extraction or claim broader Office fidelity.

Before embeddings, freeze the eight development documents and all 30 development
tasks with body-source labels. Metadata-only tasks `r0-31`/`r0-32` are inapplicable
to passage ranking. No held-out document is extracted or evaluated. The two
candidates are:

- `granular-zero-overlap-v1`: one vector per nonempty original source block,
  splitting oversized blocks only as needed.
- `coalesced-500-zero-overlap-v1`: pack adjacent prose blocks within the same
  authored node to a 500-token target, including the same carried heading.
  Tables never merge with prose. Oversized blocks split identically in both.

Both retain exact Unicode spans, source tree, metadata, 3,072-dimensional
`text-embedding-3-large` revision 1, zero overlap, top 8, and deterministic full
source-block expansion. This is a normalized component experiment; the deployed
custom profile's 50-token overlap is not silently redefined. Heading prefixes use
the same 64-token bound in both candidates. Blank-only blocks remain readable in
the source artifact but do not create standalone heading-only vectors.

The first test ranks immutable actual embeddings by independent exact cosine
using the production float16 conversion, with stable row-ID tie breaking. It
isolates representation relevance. It does **not** measure Cosmos ANN behavior,
index readiness, retrieval RU or latency. All vectors and query embeddings are
frozen once; the private cache binds endpoint, deployment, actual model revision
and text digest. The shared control helper requires fresh management-plane
account/deployment receipts before and after the embedding window and verifies
endpoint, deployment, model, upgrade policy and SKU.

The predeclared selection gate is at least 20% fewer passage vectors and **zero
per-task loss** of delivered original gold region IDs: the baseline-delivered set
must be a subset of the candidate set. Equal counts with exchanged regions fail.
Averages cannot hide a task regression. This is a provisional engineering gate, not statistical equivalence.
Complete-section gold, table blank cells, exact facts and cross-file labels remain
separate task rows. Blank-cell coverage needs an expanded complete table block and
original grid; an empty substring cannot earn coverage. Whole-block expansion is
the same for both candidates and can amplify the evidence from long table hits.
Expanded source JSON estimates are reported per case. A selection requiring more
than 200k evidence-only tokens makes the gate inconclusive, not an index-quality
failure. That bound excludes answering prompt/tool/output reserves and cannot
prove a real answering conversation fits. Source delivery is scored separately
from answering quality; no answering model
is invoked by this direct retrieval comparison.

## Reproduction

Use a fresh private runtime directory for each named comparison. Cloud deployment,
credentials and capacity are coordinator-owned and are never loaded by automatic
setup. Announce the embedding window and run it exclusively against its dedicated
model deployment. Never use or modify R0 libraries or unidentified scale scopes.

```sh
sh scripts/dev setup
.venv/bin/python -m scripts.structure_lab r2-local \
  --source-dir "$R0_CORPUS" --work-dir "$R2_PRIVATE/pilot"
# Review r2-local.json controls and exact token forecast before cloud work.
.venv/bin/python -m scripts.structure_lab r2-embed \
  --work-dir "$R2_PRIVATE/pilot" \
  --deployment-receipt "$R2_PRIVATE/embedding-before.json"
# Capture a fresh management-plane account/deployment envelope after embeddings.
.venv/bin/python -m scripts.structure_lab r2-evaluate \
  --work-dir "$R2_PRIVATE/pilot" \
  --deployment-receipt "$R2_PRIVATE/embedding-after.json"
sh scripts/dev check
```

`AOAI_ENDPOINT` and `AOAI_EMBEDDING_DEPLOYMENT` are private bindings. Receipt
envelopes contain `capturedAt`, `account` and `deployment` from actual ARM reads.
The source freeze captures implementation hashes and refuses code drift. Every
finished embedding batch checkpoints transactionally to a private SQLite cache;
retries reuse it. A partial vector array is not an accepted frozen result. Attempt
receipts retain reported token/retry counts and waits, including interruption;
unknown failed-call tokens remain unavailable. Completed arrays are immutable and
hashed with their row/query index maps. No vector or raw source is committed.

## Decision boundary and remaining gates

The navigation comparison may begin only after the passage result names the
retained candidate. Its passage vectors must remain byte-identical. Generated
summaries are discovery aids; source evidence still comes from the frozen original
blocks. Hold the query and read rules fixed, predeclare the additional level and
task set, and count all summary creation, refresh and omission work.

A retained lab candidate is not a production promotion. Live Cosmos canary and
actual-corpus readiness, ANN/source relevance, write/read RU, hosted source-read
cost, replacement/deletion/retry/metadata/permission integration, broad format
coverage and held-out four-cell confirmation remain separate gates. R3 must wait
for R1/R2 findings that justify a particular physical comparison.

## Bounded document-discovery follow-on

The follow-on names its retained passage representation from the completed first
comparison; it does not re-embed any passage or query. Its deliberately narrower
scope is the three development enforcement files (`d06`–`d08`) and five broad
source tasks (`r0-17`, `r0-22`, `r0-25`, `r0-27`, `r0-30`). This development selection
is declared before summary generation and is not evidence for broad vague-file
navigation, the long compilation, or held-out sources.

Both arms return at most eight candidates: eight passages versus seven passages
plus one document-summary candidate. The latter expands to the same original
canonical document blocks; generated summary text is never counted as evidence.
Document summaries use the existing `Summarizer.reduce` over every canonical
block, a 12k source input bound and 220-token summary bound. Fixed Sol Responses,
medium reasoning, 8k output and three provider attempts remain explicit. The three
source bodies fit this summary input bound; the reducer does not truncate them.
Both arms use the same frozen query vectors and float16 exact cosine reference.

Predeclare at least 20% reduction in missing source regions, no lost
baseline-delivered region IDs on any task, and no 200k evidence-only budget
excess. Report added source JSON/body tokens, summary creation and validation
retry usage, unchanged-source cache reuse and omission separately. Changed-source
refresh remains unmeasured; a document body change invalidates its summary.
A coverage gain at substantially higher read cost needs integration review.

```sh
.venv/bin/python -m scripts.retrieval_r2_navigation plan \
  --source-dir "$R2_PRIVATE/pilot" --work-dir "$R2_PRIVATE/navigation"
# Review navigation-plan.json, then obtain an exclusive coordinated cloud slot.
.venv/bin/python -m scripts.retrieval_r2_navigation freeze \
  --source-dir "$R2_PRIVATE/pilot" --work-dir "$R2_PRIVATE/navigation" \
  --model-receipt "$R2_PRIVATE/summary-before.json" \
  --embedding-receipt "$R2_PRIVATE/navigation-embedding-before.json"
.venv/bin/python -m scripts.retrieval_r2_navigation evaluate \
  --source-dir "$R2_PRIVATE/pilot" --work-dir "$R2_PRIVATE/navigation" \
  --model-receipt "$R2_PRIVATE/summary-after.json" \
  --embedding-receipt "$R2_PRIVATE/navigation-embedding-after.json"
```

The plan and both vector artifacts are hash-bound. Fresh management receipts are
required for both models before and after the measured window. Runtime directories
are locked against concurrent writers. Changed passage decisions, vectors, source
or navigation code invalidate the frozen comparison. No mutable-library
replacement, deletion or profile update happens in this lab.

## Passage result — retain the granular baseline

[The frozen local distribution](../records/retrieval-r2-local-20261101.json) and
[the exact source-relevance result](../records/retrieval-r2-passages-20261101.json)
record the first completed comparison. All eight canonical source trees matched
R0 exactly, and 256 distinct original development regions passed locator/hash
verification. Thirty body-evidence tasks produce 278 task-region incidences.

Coalescing reduced passage rows from **8,435 to 3,368 (60.1%)**. Complete gold-region
coverage increased from **14/30 to 19/30 tasks**, and delivered task-region incidences
from **149/278 to 179/278**. However it lost baseline-delivered region IDs on
**r0-01, r0-02, r0-09 and r0-10**. In r0-02 the total rose from three to five regions
while losing a previously delivered region, illustrating why count-only grading
would have hidden a regression. The predeclared strict gate therefore fails.
No follow-up tuning or held-out evaluation was used to rescue the candidate.

Source JSON estimates summed over these paired queries rose from 79,779 to 250,369
tokens; the maximum single expansion was 23,134, below the 200k evidence-only bound.
These are local source serialization estimates, not provider answering tokens or
live Blob/REST costs. Broader packing supplies more source per hit and may help
complete-section tasks while reducing exact-fact retrieval. The observed vector
count reduction does not establish proportional embedding-token, RU or cost gains.

The shared embedding cache made **589 provider requests**, reporting **642,547
input tokens**, no retries/throttles, and no retry wait. Elapsed embedding time was
402.84 seconds, with serial 16-item batches; it is not a strategy performance
comparison. Original model settings remained pinned across fresh management
receipts. Frozen vectors remain private, hashed, and available for the navigation
comparison and a later expressly justified experiment.
