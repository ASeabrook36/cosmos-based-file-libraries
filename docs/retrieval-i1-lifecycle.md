# I1 live selector lifecycle gate

This harness precedes held-out replay. It uses the retained Microsoft layout
profile, existing canary-validated isolated Azure services, and three newly
created library scopes. It changes no application or worker code, policies,
permissions, deployment settings, queue configuration or existing libraries.

Two localhost HTTP API instances each create their own production Azure client
stack, including separate Cosmos session caches. The existing warm worker and
its authorized identity perform real Microsoft workflow ingestion and purge.
Local caller authentication uses `AUTH_MODE=dev`; production Entra platform
authentication is unavailable and is not tested by this experiment.

The inputs must hash-match frozen development originals d01 and d02. The first
shared scope receives both files; d01 is subsequently replaced by d02 under the
same upload filename. The other scopes are an empty shared library for foreign
selectors and an empty personal library for permission checks. Held-out sources
and evaluation libraries are never used here.

## Predeclared checks

The thirteen names must exactly match the independently frozen I1 plan:

- Missing/wrong caller keys fail on all five retrieval endpoints; wrong/missing
  agents fail on the personal scope. Its kind and bound agent are immutable.
- Copied outline read selectors and document-scoped search selectors return the
  pinned document/generation/revision. Foreign library, foreign document handle,
  body-supplied library, stale generation and wrong revision requests fail without
  evidence. Search uses a short query drawn from the delivered development source;
  no ranking quality or latency claim is made.
- Metadata PATCH appears in complete reads, filtered discovery and every active
  chunk's metadata projection. A two-document read overlapping the edit refuses
  the entire batch.
- One exact owned replacement message fails deliberately before queue send.
  Generation 1 stays readable; retry allocates generation 3, never reusing failed
  generation 2. A two-document read held across the real replacement commit fails;
  old read/search pins fail and fresh generation 3 selectors work.
- One exact owned deletion message fails deliberately before queue send. The
  record is already hidden; the overlapping batch read fails, and ten subsequent
  second-client reads return no content. Repeating DELETE queues the real purge.
  Document, all generations' chunks and Blob prefix become absent; its sibling
  remains readable.
- No session tokens are copied between clients. Three cross-client barriers and
  the subsequent denials are bounded freshness observations, not a universal
  immediate-revocation guarantee under Session consistency.

The one-shot read barrier wraps only the second API's Blob client. It pauses
after downloading actual immutable source bytes and before the existing endpoint
freshness recheck. The first API performs the real mutation. Replacement waits
for the writer's observed worker commit; it does not prime the second client's
Cosmos cache or alter a database response. Each barrier must return an error-only
409 response. Queue faults wrap only the first API's sender and match an owned
library/document/message type exactly. There is no competing receiver or injected
broker redelivery. Both controlled interventions are explicit in the receipts.

## Execute only during the coordinator's reservation

```sh
sh scripts/dev setup
sh scripts/dev test tests/test_retrieval_i1_lifecycle.py -q
sh scripts/dev lint
# Load the assigned private environment into this process. Never commit it.
.venv/bin/python -m scripts.retrieval_i1_lifecycle \
  --plan "$I1_PLAN" \
  --preflight "$I1_PRIVATE/infrastructure-preflight.json" \
  --old-docx "$R0_PRIVATE/corpus/source-1.docx" \
  --new-docx "$R0_PRIVATE/corpus/source-2.docx" \
  --out "$I1_PRIVATE/lifecycle-attempt-1"
```

The output directory must be fresh and outside every checkout. The lifecycle
phase has a 30-minute deadline, followed by at most three 3-minute cleanup waits.
No automatic run retry or resume is supported. Preserve failed attempts and
obtain coordinator review before any new attempt or held-out execution.

The preflight binds the inherited passing canary, current container incarnation
and policies, and the exact shared settings dictionary. All fourteen dictionary
values are strings, hashed with `fli.structure_lab.digest`: Cosmos endpoint and
database; Blob account and container; Service Bus namespace and queue; actual
worker workflow endpoint; embedding endpoint, deployment, revision and dimensions;
search mode, default top and maximum top. The private environment copies the
worker endpoint solely for this binding; it does not authorize local workflow use.

All API source hashes and the experiment-plan file hash bind the final receipt.
Raw HTTP source replies, identifiers, query text, Blob paths and SDK metadata
projections remain in mode-0600 private files. SDK diagnostics retain metadata
rows, deletion observations and full owned-scope cleanup observations so the
coordinator can independently re-open their evidence. `cleanupComplete` requires
absence of every owned library record, document record, chunk and live Blob.
Soft-deleted backups follow service retention, not permanent erasure.

Before replay, the coordinator must independently accept the exact receipt hash.
A complete receipt requires all thirteen gates; partial or failed receipts do
not grant permission to execute held-out tasks. The replay also excludes every
lifecycle library ID and verifies source/configuration/code bindings.

## Remaining limits

These tests do not establish production Entra authentication, broker-delivered
worker retry behavior, arbitrary remote completion/cancellation races, global
linearizable revocation, sustained concurrency or performance. No settings or
architecture change is selected. A live failure is preserved and blocks the
held-out gate until a separately reviewed resolution.

Local rehearsal uses actual API/Worker code with memory stores and a fake provider;
it checks harness control flow only and never emits a live receipt. Live results
are recorded separately after the authorized Azure run.
