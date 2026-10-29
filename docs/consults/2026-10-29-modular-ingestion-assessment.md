# Modular ingestion profiles and paired-library evaluation

2026-10-29. Status: assessment and implementation handoff; no application or infrastructure changes made by this assessment.

## Purpose and agreed scope

Make ingestion components selectable per library in one demo so the team can evaluate quality, operational convenience, latency and cost without rebuilding the demo for each strategy. This is an evaluation architecture, not a decision to adopt Microsoft Document Indexer or retain custom processing.

The user agreed to the modular approach and clarified that there are three strategies:

| Creation choice | Result |
|---|---|
| Current pipeline | One library using existing ingestion |
| Custom granular document pipeline | One library using structure-aware document processing |
| Microsoft Document Indexer | One library using the adapted Microsoft workflow |
| Compare custom and Microsoft | Two ordinary libraries using the latter two profiles, populated from identical source files and associated with one experiment |

The fourth choice is a creation action, not a fourth ingestion provider. The current pipeline remains available as the baseline. Make meaningful processing differences explicit, especially chunk size, and constrain conclusions to the comparison actually run.

## Evidence and current implementation

Inspected application behavior and experiment evidence at commit `4c89d60d627c69970f3b1cd01b1a498d53aeee46`. This assessment did not run new live benchmarks or inspect the operator's current Azure resources.

- [README](../../README.md): the documented demo runs the API and worker locally against Azure services. Upload saves an original to Blob, creates a document record and sends a Service Bus ingestion message. The persistent Python worker consumes it. Container Apps hosting remains planned.
- [Infrastructure](../../infra/main.bicep): provisions dependent services and identities, but does not host the normal API/worker.
- [Library API](../../src/fli/api/libraries.py): creation has no ingestion-profile field and rejects unknown fields.
- [Worker](../../src/fli/worker/handlers.py): directly dispatches to custom ingestion. Its publication method embeds chunks and writes rows as well as publishing source/artifact pointers.
- [Current chunker](../../src/fli/chunking.py): section/sentence-aware, targets 500 tokens with up to 50 tokens of whole-sentence overlap, carries bounded headings and preserves section locations.
- [Indexer experiment](2026-10-29-indexer-workflow-experiment.md) and [runbook](../indexer-workflow-lab.md): adapted Indexer output fed the existing retrieval API with real isolated stores. Sequential replay, replacement, deletion and library isolation were exercised. The normal demo was unchanged and experiment resources were removed.
- [Indexer builder](../../scripts/indexer_workflow_lab.py): the adapted workflow retains Microsoft acquisition, DI extraction, chunking and embedding. The tested token chunking is 5,000/200, substantially different from today's baseline.
- [Publication helper](../../scripts/indexer_publication_lab.py): demonstrates source binding, complete ordinal validation, deterministic IDs and conditional publication. It is a lab helper; its metadata initialization/reset behavior must not become normal application behavior.
- [Structure lab](../structure-lab.md) and [proposal](2026-10-28-document-understanding-proposal.md): custom granular processing is an isolated prototype, not an integrated upload/retrieval/demo feature. It includes structure/navigation and generated summaries beyond ordinary chunking.
- [Observability](../observability.md), [metrics](../../src/fli/metrics.py), [demo telemetry](../../src/fli_demo/telemetry.py) and [agent replay](../../scripts/evaluate_agent_retrieval.py): useful existing timing, token and RU accounting plus replay controls. Distributed collection and a durable experiment ledger remain work.

The current state is inferred from repository code/documentation, not confirmation of which local or cloud processes are running today.

## Architecture recommendation

Introduce versioned ingestion profiles and a common job/publication boundary. Suggested initial IDs are `current-v1`, `custom-structured-v1` and `microsoft-indexer-layout-v1`; final names should follow repository conventions.

Keep ingestion provider, extraction settings, chunking strategy, structural enrichment and embedding configuration separately recorded inside each profile. A simple three-choice UI can expose named compositions. Microsoft extraction/chunking may eventually feed custom enrichment; those capabilities are not inherently mutually exclusive.

### Shared contracts

1. **Input:** trusted library/document identity, immutable source reference and digest, generation, metadata version, immutable profile/version and execution ID.
2. **Output:** complete extracted source, chunk text/vectors, deterministic identity/ordinals, provenance, extraction/structure artifacts, declared capability/coverage and usage/timing.
3. **Execution:** durable job identity, start/status/completion/failure, retry semantics, progress and reconciliation.

Different providers can use different internal stages. Normalize compatibility and provenance without inventing headings, source locations, complete image coverage or structural features that a provider did not produce.

Snapshot the chosen profile into each document generation/job. Existing libraries default to the current path. Initially choose the profile at creation; changing a populated library requires explicit reprocessing and staged publication, not a metadata toggle that implies old embeddings have changed.

### Controller and publication

Retain common upload, authorization, source identity, revision allocation, retrieval and lifecycle policies. Dispatch ingestion to the selected provider through trusted job routing. Do not let both independent Blob triggers and queue consumers accidentally ingest the same upload.

Microsoft already produces vectors. Refactor publication so it can accept validated pre-embedded output without embedding it again. Validate vector dimensions, model compatibility, scope, source binding and a complete row set before publishing.

The publication boundary must preserve:

- old readable generations while replacements process;
- immutable source checkpoints and idempotent retries;
- user metadata and concurrent updates;
- permission and personal-library binding checks;
- complete-source reading and explicit limitations;
- generation/content-revision visibility;
- deletion and supersession guards, including late completions;
- inventory refresh, retagging and artifact cleanup.

Use the lab helper as evidence and reusable validation, not a drop-in application publisher. It currently resets fields such as tags/facets. Lab artifact paths also differ from the normal worker's generation cleanup paths; normalize paths or extend cleanup explicitly.

The lab used workflow-fixed library bindings and multiple workflows to test isolation. Generalize to a shared workflow receiving trusted job scope. Creating a library should not require a workflow deployment per library.

Compatible passage vectors can use the existing chunk container. New typed summary representations require the structure proposal's reader/provenance/visibility safeguards; do not insert summaries into today's source-evidence rows unmodified.

### Cloud execution

For end-to-end operational comparison, host custom processing in Azure as a persistent, queue-driven service. Container Apps is a reasonable fit for the existing Python worker. Current and granular profiles can share a worker deployment; Microsoft runs in Logic Apps behind the same application boundary.

Document Indexer is a Microsoft-supplied workflow hosted in customer Azure resources, rather than a fully operated external black-box service. Account for runtime, connectors, identity and hosting administration.

Identical hosting technology is unnecessary. Align region/network conditions where possible, model versions, resource/concurrency limits and availability expectations. Start with a warm custom worker for timing comparisons; measure cold starts separately. Audit cloud authentication: local-only development authentication must never be enabled on a hosted API. Worker managed-identity support is distinct from completing hosted API authentication.

## Comparison design

### Complete strategies and controlled components

Expose two clearly labeled kinds of experiment:

- **Complete strategy:** intended settings and capabilities for each profile. Conclusions concern the whole arrangement.
- **Controlled component:** align extraction, embedding model, approximate chunk size/overlap, retrieval configuration and other controls where technically meaningful; vary a specific stage.

A 500/50 section-aware pipeline versus a 5,000/200 token-window workflow changes more than vendor/orchestration. Matching token limits still does not match boundary behavior, heading handling or source locations. Preserve these differences in the experiment manifest and results; never attribute an observed result solely to Microsoft versus custom.

Granular processing may add summaries, hierarchy and agent navigation. Comparing that with passage-only Indexer output is a valid complete-strategy experiment, but does not isolate chunking or hosting. Add capability flags and use a common, versioned retrieval contract with typed generated summaries and genuine source evidence.

### Paired creation and reproducibility

Create two normal library records plus an experiment record. Bind the same original byte digests and equivalent metadata to both. Use stable source correspondence independently of library-local document IDs. Store profile/workflow/code versions, settings, model versions, corpus hashes and frozen document generations.

Define recoverable partial creation/upload behavior: report both library IDs and each status; retries must not create extra pairs or duplicate documents. A failure in one pipeline must remain visible rather than silently fall back to another profile.

For agent evaluation, use independent fresh sessions attached to one comparison library each, with equivalent tool descriptions, model, instructions, questions, budgets and source versions. Avoid profile names or descriptions inadvertently steering tool choice. Replay repeated runs and report variation. Preserve multi-turn context only when the case explicitly requires it.

Control shared embedding/model quota contention. Begin with isolated or alternated runs, then separately test simultaneous operation. Record caches, retries, throttles and run order. Hold corpus/background load stable for retrieval comparisons; shared-container hybrid cost can depend on broader container size.

Wait for index readiness before steady-state retrieval measurement. Also measure upload-to-publication and upload-to-search-readiness as separate user-visible timings.

### Measurements

| Area | Required observations |
|---|---|
| Ingestion | Queue wait, extraction/chunking/embedding time, time to text and complete enrichment, index readiness, failure/retry counts and operator intervention |
| Representation | Chunk size distribution/count, overlap, source-token count, locations/headings, missing images/structure, summary generation |
| Embedding | Actual provider input tokens, requests, model/version/dimensions, throttles and repeated work |
| Cosmos | Write/retrieval RU, metadata/publication/cleanup RU where available, throttles and storage |
| Agent | Cumulative input/output and cached/reasoning breakdown where reported, model/tool rounds, repeated retrieval, returned evidence tokens, latency and budget failures |
| Quality | Source-evidence recall/coverage, document selection, citation correctness, task success and visual/structural coverage |
| Other costs | DI pages/usage, Logic Apps hosting/actions/connectors, worker hosting, Blob/queue/storage, enrichment models |

RUs are Cosmos Request Units. Dollar conversion depends on account billing/capacity mode; shared fixed hosting costs require a declared allocation. Keep measured usage, estimated charges and allocated hosting costs distinct.

Ground-truth relevance must use common source passages/regions/facts, not provider-specific chunk IDs. Approximate-vector recall against exact search is an index diagnostic, separate from retrieval relevance and agent answer quality. The earlier canary's recall is not evidence that either ingestion strategy improves answers.

Live timings/usage can update as runs complete. Recall and answer quality require labeled cases or reviewed outcomes. Extend the existing agent replay harness rather than infer quality from empty-search or latency counters.

Current metrics focus on active generations and omit some earlier failed attempts and ancillary operations. Add an attempt-level experiment ledger and correlation across API, worker and Logic Apps. The local JSONL journal is not automatic distributed collection. Preserve unknown values as unavailable, not zero; model tokens for multi-library turns cannot be allocated accurately by guesswork.

## Coverage risks and effort

The tested Indexer adapter preserves full provider text and layout JSON, but copies no PDF figure crops, misses native Office visuals and exposes full provider text as one canonical section. Logical authored-section coverage also has a known failing probe. These are capability gaps to disclose or integrate, not reasons to prevent modular selection.

The granular custom option also needs normal API/worker integration, typed summary/source handling and representative format tests. Do not advertise it as complete merely because its UI option exists.

Planning judgment from inspection:

- Existing current-versus-Indexer text demo: roughly 4–7 engineering days.
- A more trustworthy two-provider comparison with lifecycle integration and measurements: roughly 10–20 engineering days total.
- These figures exclude completing the granular document-understanding system and full visual/native-format parity. Three-profile scope needs a new estimate after an initial vertical slice. The earlier structure proposal has its own broader estimate; do not add it mechanically without accounting for overlap.
- Quota, authentication, hosting, connector output/usage visibility and concurrency checks can affect delivery.

## Suggested implementation sequence and acceptance

1. Inspect current checkout and instructions; establish current tests/status and write a brief revised milestone plan.
2. Define profile/job/result/capability/experiment contracts; add backward-compatible library selection.
3. Implement paired creation and reliable routing with normal upload semantics.
4. Refactor shared publication; integrate adapted Indexer output and existing worker without duplicate embeddings.
5. Connect a narrow granular vertical slice from real uploads to source navigation/typed retrieval. Define supported formats and limitations before broadening.
6. Prepare cloud worker/Logic Apps integration and distributed correlation/measurement. Use proper hosted authentication.
7. Extend performance UI and replay with profile differences, paired progress, controlled settings and source-based quality scoring.
8. Validate lifecycle/isolation and paired failure recovery, then run representative controlled cloud comparisons when resources are available.

Acceptance includes all four creation choices, repeatable same-source pairs, persisted profile versions, visible failures/coverage, compatible retrieval, retained metadata, duplicate/replacement/deletion safety and honest usage accounting. Use focused meaningful tests plus repository-required checks. New queried Cosmos containers require the established real-text canary; preserve existing query/vector/permission invariants.

Follow repository validation requirements, update the spec/build plan with implementation decisions, and report tested behavior, remaining gaps and any exact cloud blockers. Continue through useful local implementation and checks when cloud credentials or quota are unavailable; do not claim unrun live results.
