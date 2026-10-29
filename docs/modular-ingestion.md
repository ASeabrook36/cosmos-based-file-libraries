# Modular ingestion vertical slice

2026-10-29. Implements the first local slice of the
[assessment](consults/2026-10-29-modular-ingestion-assessment.md).
No Azure deployment, live ingestion, agent-model evaluation or scale measurement
was performed for this change. Existing scale-probe work is outside this branch.

## Available compositions

The existing Admin, Library Performance and agent replay are extended; there is
one demo and one retrieval/publication contract.

| Creation action | Representation and limitations |
|---|---|
| `current-v1` | Existing formats, native/layout extraction, 500/50 section/sentence chunking, existing bounded image enrichment. Legacy libraries default here. |
| `custom-structured-v1` | First complete-strategy slice: UTF-8 Markdown ATX and DOCX Heading-style hierarchy, exact extracted source blocks, source subtree reads and 500/50 section/sentence chunks. Other formats fail as `structured_format_unsupported`. DOCX covers main-body paragraphs/tables with explicit Heading 1–9 styles; headers, footnotes, text boxes and revision interpretation are outside this adapter. No generated summaries or summary-vector search. |
| `microsoft-indexer-layout-v1` | Shared adapted Indexer layout workflow, 5,000/200 token windows, Microsoft embeddings accepted by the common publisher. Full provider text is one canonical section; no authored chapter inference, copied figure crops or native Office visuals. Requires the workflow configuration and an explicit embedding model revision. |
| Compare custom and Microsoft | Two normal shared libraries and a resumable experiment control record; identical original bytes and initial metadata, independent status/failure. |

Profiles separately record provider, extraction, chunking, optional structural
enrichment, embedding model/revision/dimensions, workflow version, code revision,
capabilities and limitations. Selection is immutable at creation. It is not a
metadata toggle that re-embeds existing files. Each generation snapshots its
recipe, source path/hash, metadata version and deterministic execution ID.
`FLI_CODE_REVISION` records the implementation revision; absent revisions remain
unavailable. Existing libraries without profiles retain current processing. The current email
exception remains explicit in the recipe: messages up to 700 tokens form one chunk.
Controlled comparisons always apply the document chunker rather than that exception.

Use **DOCX** for the first complete-strategy matched corpus. Markdown remains a
custom-only input: it is absent from Microsoft's
[layout model input list](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0).
The local Microsoft tests use synthetic provider responses; they do not establish
cloud acceptance of Markdown or any format beyond the documented/live-tested path.

The controlled-component pair sets both sides to the same pinned
`prebuilt-layout` extraction and model settings, 500/50 token limits, no hierarchy,
no proposals and no visuals. Custom processing asks the shared workflow for
**extraction only**, then chunks/embeds in Python. Microsoft processing uses the
workflow's token chunker and embeddings. This supports comparing source passage
representations with different boundary algorithms. Equal token limits do not
mean equal actual chunk distributions. Hosting, orchestration, embedding batching
and independently executed extraction calls still differ: operational results
cannot be attributed solely to chunking or vendor. Controlled replay verifies equal complete provider-text digests and matching
`cl100k_base` chunking tokenizers before it runs. Verify actual extractor/model
configuration in the live acceptance run. Complete-strategy mode deliberately varies more stages.

## API and recovery

`POST /v1/libraries` accepts `ingestionProfile` as one of the three profile IDs,
defaulting to `current-v1`. Existing endpoints and permission checks apply.

`POST /v1/experiments` accepts:

```json
{
  "experimentId": "<client-generated UUID retained across retries>",
  "name": "Matched reports",
  "description": "Reports",
  "comparison": "complete-strategy"
}
```

For a controlled pair use `comparison: controlled-component` and
`component: chunk-boundaries`. Both profiles are frozen in the control record at the first request. A retry after
a configuration change keeps those original profiles. Both IDs and creation states are returned even
when only one library was created. Repeat the same request to resume. Reusing an
experiment ID for different settings returns `409 idempotency_conflict`.

`POST /v1/experiments/{experimentId}/sources/{sourceId}` accepts the normal
multipart `file` and optional JSON `metadata`. The client retains a UUID source ID;
bytes, name, content type and initial metadata are fingerprinted together. The
server uses deterministic, separate document IDs and immutable original paths on
each side. Responses contain each library/document ID and status, including
`enqueue_or_storage_failed` and `unavailable`. Retrying after an ambiguous send
can enqueue a duplicate message, but cannot duplicate a document or change its
bytes. Retrying a failed ingestion does not silently select another provider;
normal replacement/reprocessing remains the recovery path for terminal failures.

`GET /v1/experiments/{experimentId}` returns profiles, creation progress,
comparison controls, member availability and per-source ingestion status. Control records use the existing
`documents` container in an `experiment:<UUID>` partition, accessed by point
reads. This namespace cannot alias a library's GUID partition. Control records
store IDs, source digests and fingerprints, **not** duplicate names, descriptions,
source text or user metadata. Deletion receipts prevent pair retries recreating
purged members/documents. Shared-library authorization remains the platform's
responsibility; no attachments or permissions are copied into the index.

Custom outlines expose `nodeId`, `parentNodeId` and Unicode character extents in
section locations. Existing `documents:read` selected mode additionally accepts
`nodeIds` with the outline's `generation` and `contentRevision`. It expands whole
source subtrees, deduplicates selections, and retains all-or-nothing evidence
budgets. This returns source text, never generated summary evidence. Old
section/evidence/page selectors keep their behavior. Unsupported hierarchy and
missing/corrupt source artifacts are explicit limitations/errors.

## Shared publication and attempts

The queue chooses a document generation; its stored job selects the provider.
Provider-supplied row scope/locations do not control publication. Microsoft
results require a matching complete job, original bytes, source hash,
independently configured model revision, finite compatible vectors and a complete
zero-based ordinal/count set. Only after validation are rows assigned trusted IDs,
metadata and source provenance. Microsoft vectors are not embedded again.

The normal conditional publication, active/pending generations, immutable plans,
create-only chunk writes, retagging, refresh and purge paths are reused. A PATCH
that races processing retains current user fields; session-ordered retag also
repairs already-written chunks. A failed replacement retains the previous active
generation. Late deleted/superseded output cannot move the active pointer.

Attempts have immutable Blob start/finish events under
`library/document/attempts/generation-attemptId/`. The protected library metrics
endpoint reads a bounded sample across processes. Start without finish means
interrupted/unknown, not success. Events include execution/profile/source
correlation, delivery number, broker enqueue-to-delivery time when supplied,
local embedding usage where reported, known chunk-write RU and completeness.
Enqueue-to-delivery on a retry includes elapsed time since original enqueue; it
is not pure waiting time. Failed writes leave charge incomplete. Event storage
has its own unmetered Blob cost. Attempt records survive obsolete-generation
purges and are deleted with the document/library.

Generation metrics still describe the published representation. Attempt metrics
must be used for repeated work. Workflow result usage and cache reuse are labeled
separately; the built-in connector currently supplies no counters through this
adapter. Tokens/requests/retries for that work remain **unavailable**, not zero.
Metadata/cleanup RU, hosting allocation, billed charges and index readiness are
also unavailable. Full-read/chunk and `sourceTextTokensEstimate` counts are tokenizer estimates; actual
model usage continues to come from provider counters. No dollar costs are inferred.

## Offline cloud preparation

- `infra/worker.Dockerfile` builds the existing Python queue worker as a non-root
  process. The image was not built or pushed in this session.
- `infra/modular-validation-hosting.bicep` creates an isolated registry and Container Apps
  environment, with managed-identity image pulls.
- `infra/modular-worker.bicep` prepares one warm Container App with managed
  identity, no ingress and bounded lanes/writes. It takes explicit existing
  service endpoints, database/queue and immutable image digest. It creates no
  account, container, role assignment, model deployment or scale-test data.
- `scripts/shared_indexer_workflow.py` verifies the existing pinned Microsoft
  template hash and builds a single request-driven stateful workflow. It retains
  Microsoft Blob acquisition, cached layout, chunking and embeddings; emits an
  immutable result into the normal generation text prefix; and has **no Blob
  trigger and no Cosmos writer**. The extraction-only branch has no chunking or
  embedding action. The controller remains the sole publisher.
- `infra/shared-workflow-auth.bicep` prepares authentication for the dedicated
  Logic Apps Standard host, requiring HTTPS/Entra and the worker's client and
  principal IDs. Its schema follows the
  [App Service auth settings reference](https://learn.microsoft.com/en-us/azure/templates/microsoft.web/sites/config-authsettingsv2).
  It does not implement hosted FastAPI authentication. `AUTH_MODE=dev` remains
  local-only and hosted API Entra support remains Phase 5.

Use an isolated environment/database/queue for any live validation while scale
measurements are running. No live account, credential, quota or provider check
was attempted here; this is deliberate isolation, not a claim that access failed.
Fleet access, if later needed, must use nervus.

Keep packages, runtime parameters, connection bindings, source snapshots and raw
telemetry outside the repository. The builder accepts either an output workflow
JSON or a complete Standard ZIP when private runtime configuration is supplied:

```sh
python -m scripts.shared_indexer_workflow \
  --template "$PRIVATE_WORK/pinned-workflow.json" \
  --runtime-config "$PRIVATE_WORK/shared-runtime.json" \
  --output "$PRIVATE_WORK/shared.zip"
```

Runtime JSON has `parameters` and `connections`, as in the existing
[Indexer runbook](indexer-workflow-lab.md). Bind `AOAI_Emb_Dep_shared`,
`EmbeddingModel`, `EmbeddingModelRevision`, `EmbeddingDimensions` and
`SourceContainer`. Required connections are `blob-connection_shared`,
`aoai-connection_shared` and `formrecognizer_shared`, all with managed identity.
The package needs no Cosmos connection. Model revision must identify the actual
model behind the deployment, not echo an unverified job claim. Reuse the isolated
host/storage/connection provisioning from the runbook, with the new authentication
module applied **before** enabling this workflow. Set `disableTriggerSasAuth=true`
on the dedicated host so SAS cannot invoke triggers. This host property is
[documented by the Microsoft Logic Apps team](https://techcommunity.microsoft.com/blog/integrationsonazureblog/trigger-workflows-in-standard-logic-apps-with-easy-auth/3207378/replies/4219276/).
Configure the dedicated Entra audience to issue v2 access tokens, matching the
authentication module issuer. Require live positive and negative authentication checks.

Worker/API settings:

| Setting | Meaning |
|---|---|
| `AOAI_EMBEDDING_MODEL_REVISION` | Configure before library creation; immutable snapshots with an unavailable revision require a new library after configuration. Required before Microsoft publication; match the workflow's independently configured version. |
| `FLI_CODE_REVISION` | Build/commit revision for reproducibility; absent means unavailable. |
| `MICROSOFT_WORKFLOW_ENDPOINT` | HTTPS Entra-authenticated invoke endpoint; SAS/query credentials are refused; an `api-version` query parameter is permitted. |
| `MICROSOFT_WORKFLOW_AUDIENCE` | Entra resource audience; configured together with the endpoint. |

The bridge submits the trusted job and polls its immutable result for up to five
minutes per delivery. A stored result avoids resubmission. Ambiguous HTTP failures
or overlapping submissions can repeat remote work before the result exists;
exactly-once billing is not promised. A bad/partial result never publishes.
Remote failure details, cancellation, long-running jobs beyond delivery/lock
budgets and provider-directed backoff still need live integration work.

A workflow can write artifacts after deletion/purge or after the controller's
final timeout. Schedule the provided managed-identity reconciliation enqueue
command for **both retained experiment member IDs, including deleted members**
while remote runs may finish:

```sh
python -m fli.reconcile --library-id "$CUSTOM_LIBRARY_ID" --library-id "$MICROSOFT_LIBRARY_ID"
```

The existing worker handles `reconcileLibrary`: it purges missing/deleted scopes,
cleans obsolete generation artifacts and resumes pending jobs with complete
results. It never reopens a terminally failed document. Scheduling is not deployed
by this slice. Retiring that schedule requires confirming no remote execution
can still write. This is necessary cleanup, not a new ingestion source.

Before deployment, the remaining exact gates are: select isolated resources and
immutable image; build/push the image; verify managed-identity data/registry roles;
provision/bind a shared workflow host, connections and Entra audience/principal;
verify actual model revisions and regional quota; test a token-only invocation
without SAS (the Standard trigger/authentication interaction is untested); wire
reconciliation scheduling; validate concurrent/late workflow completion and
cross-instance consistency. The Bicep and package builder compile/materialize
locally; that does not prove those Azure runtime gates.

No new container policy is needed. If live validation creates any new
account/container, the repository's real-text canary is mandatory before use.
Do not use or mutate the ongoing scale measurement containers.

## Matched replay and quality

The existing `scripts/evaluate_agent_retrieval.py` adds `--experiment-id`,
`--repeats`, `--index-ready` and `--run-controls`. It checks source correspondence,
byte hashes, metadata, readiness and equivalent tool names/descriptions. It uses
one library and a fresh model client per run, alternates run order, resets history
between cases, retains multi-turn history only within an explicit `questions`
case, and rejects changed source snapshots. Retained source versions and profile
settings are recorded in private artifacts. `--index-ready` is a recorded operator
observation, not an automated readiness test.

Run controls JSON requires `modelRevision`, `searchMode` and `backgroundLoad`;
record cache state, regions, resource limits and quota contention too. Keep source
load stable; the existing shared-container hybrid cost concerns still apply.
Add common `sourceFacts`/regions to cases. `quality-review.json` leaves relevance,
citation and task judgments unreviewed until assessed against those common source
facts. It never substitutes provider chunk IDs, latency or exact-vector canary
recall for answer quality. Existing agent usage includes actual cached/reasoning
token details when available, tool/model rounds and budgets. No real-model replay
or reviewed quality score was produced here.

## Validation and remaining milestones

Local tests exercise normal upload/search/outline/subtree/full-source flows,
source/job/vector validation, profile snapshots, personal-library backstops,
partial pair creation/queue recovery, replacement/deletion/late completion,
reconciliation, attempt receipts, independent replay and workflow transformation.
The original current/image/lifecycle/query tests continue to apply. Infrastructure
is compiled locally and the builder was run against the exact pinned template.

Planning estimates remain judgments: first local slice 3–5 engineering days;
cloud-validated comparisons about 10–20 days total; broader format/structural and
visual coverage about 20–35 days total, depending on corpus and quota. This slice
is not completion of all three strategies at feature parity. Next milestones are
live hosting/workflow validation, representative matched ingestion and reviewed
agent replay, then PDF/native structural adapters, typed generated summaries and
summary discovery. Distributed dashboards, operator intervention accounting,
remote per-attempt usage/cancellation and full visual parity remain open.

The Streamlit pages were updated in code; interactive browser QA remains unrun.
