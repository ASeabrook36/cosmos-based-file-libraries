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
per-task loss** of delivered original gold regions. Averages cannot hide a task
regression. This is a provisional engineering gate, not statistical equivalence.
Complete-section gold, table blank cells, exact facts and cross-file labels remain
separate task rows. Blank-cell coverage needs an expanded complete table block and
original grid; an empty substring cannot earn coverage. Whole-block expansion is
the same for both candidates and can amplify the evidence from long table hits.
Source delivery is scored separately from answering quality; no answering model
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
