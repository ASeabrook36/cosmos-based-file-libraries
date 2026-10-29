# Microsoft reuse compatibility experiment

**Follow-up completed 2026-10-29:** the [deployed workflow assessment](2026-10-29-indexer-workflow-experiment.md)
and [runbook](../indexer-workflow-lab.md) supersede this document's open Indexer
deployment/lifecycle gates. This document retains the earlier extraction findings.
KBaaS external evidence retrieval remains unverified.

2026-10-28. **Partial compatibility assessment, not a preview integration sign-off.**
The six-document extraction comparison and upstream workflow inspection completed.
KBaaS external evidence retrieval, a deployed Document Indexer workflow, and their
end-to-end lifecycle/isolation tests remain unverified. The normal demo, application
ingestion, deployment and three application containers are unchanged.

## Recommendation

Reuse Document Intelligence's complete `prebuilt-layout` response as the extraction
boundary for a future adapter. Preserve its JSON before any chunking. Keep native
Office image extraction: the DOCX test contained one embedded image and neither
Document Intelligence route returned it as a figure. Validate and reconcile inferred
section boundaries before promising complete chapter reads; the nested report
exposed a real omission despite having valid element references.

Use the Microsoft Document Indexer template as an acquisition/orchestration starting
point for the next isolated workflow experiment. Its unmodified output cannot enter
the current application contract. Change the extraction action to layout, checkpoint
the full response, and replace the final projection/publication boundary with a
versioned adapter. Keep Microsoft acquisition, connector authentication, extraction,
chunking and embedding wherever their contracts pass the next tests. Do not build a
new general ingestion engine from this experiment.

Defer KBaaS adoption behind this application's external agent tools until Microsoft
provides a supported way to return identifiable source evidence outside its agent
loop, or a documented workflow bridge demonstrates that contract. This is an open
support/compatibility question, not evidence that no such API can exist. Generated
answers or direct queries against service-owned Cosmos tables would not establish
that retrieval contract.

## Scope and evidence

The [sanitized record](../../records/microsoft-reuse-20261028.json) contains the
allowlisted measurements. Raw responses, source snapshots, receipts, environment
configuration, Azure resource identities and rendered fixtures remain outside the
checkout. No new Cosmos account/container was created; no index canary was therefore
required. The existing frozen-artifact lab remains the control.

The read-only readiness check preceded provisioning. The subscription was enabled,
effective subscription permissions included wildcard actions, Cognitive Services was
registered, and Workflow Standard was available in the deployment region. Web and
Logic providers were still unregistered, with no Standard workflow host. Existing
deployments remained the embedding model and gpt-5-mini. Unused gpt-4o-mini quota
was available for GlobalStandard and DataZoneStandard; quota is not deployment
capacity. The current [Standard agent model list](https://learn.microsoft.com/en-us/azure/logic-apps/create-autonomous-agent-workflows#supported-azure-openai-service-models-for-agentic-workflows)
does not list gpt-5-mini.

Only a separate S0 Document Intelligence account and its dedicated resource group
were created. Local key authentication was disabled; local development calls used
the operator's Entra identity with Cognitive Services User. No existing demo account,
identity assignment, workflow provider registration or model deployment was changed.
Cleanup status is recorded separately in the measurement record.

### KBaaS external retrieval gate

The current [KBaaS guide](https://learn.microsoft.com/en-us/azure/logic-apps/create-knowledge-base-agentic-workflow)
describes portal setup and retrieval inside a Logic Apps agent loop. Its listed
management REST operations enumerate/view/delete knowledge bases and sources; they
do not define a query returning externally consumable evidence. The guide also
limits this preview to text parsing and default chunking. This supports investigating
KBaaS for managed ingestion and chunk-summary discovery, but does not establish
chapter navigation, visual evidence, complete source reads or this API's isolation
semantics.

No KBaaS host, connection or dataset was deployed. The experiment stopped that
branch at the external-contract gate rather than paying for hosting and another
model before identifying the evidence operation to test. Therefore runtime preview
availability, actual KBaaS schemas, retrieval output, source-reference resolution,
replacement/delete behavior and operating cost remain unverified. Management REST
documentation and named container purposes are not runtime measurements.

### Document Indexer workflow inspection

Inspected Microsoft's [Blob + Document Intelligence Cosmos template](https://github.com/Azure/LogicAppsTemplates/blob/e6b7d87e120a400b3e321caacf858c08fa4d060f/ingest-doc-blob-document-intelligence-cosmos/default/workflow.json),
pinned to commit `e6b7d87e120a400b3e321caacf858c08fa4d060f`, with byte SHA-256
`52a2ff79422ea461cb38c1ebc4e2b17351b893a87c1a15b8ea46b3eb6b386e55`.
The [product guide](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/document-indexer)
identifies this as a customizable Standard workflow template. These findings apply
to this pinned Blob/OCR variant, not every template or future portal release.

The action calls `prebuilt-read`, API `2024-11-30`, requesting Markdown. It feeds
only `analyzeResult.content` into token chunking: 5,000 tokens with 200 overlap,
using `cl100k_base`. The Select action projects exactly these seven fields:

| Field | Template value |
|---|---|
| Configured text field | One chunk string |
| Configured vector field | Corresponding embedding |
| `documentName` | Trigger name |
| `fileType` | Blob path, despite the field name |
| `id` | A fresh `guid()` |
| `chunkNumber` | One-based chunk ordinal |
| `totalChunks` | Number of chunks |

This is the statically established emission projection, **not records observed from
a deployed workflow**. It contains no section/element graph, source-span map,
library binding, document generation, publication manifest or image asset pointer.
The trigger watches blob additions/modifications; no delete trigger is defined.
Fresh GUIDs imply that retries/replacements need explicit deduplication and old-row
cleanup. Actual duplicate behavior, connector retries and delete convergence were
not tested. The configurable template can be extended; these observations do not
establish product-wide limitations.

Switching only the model to layout would still discard the JSON relationship graph
at `Chunk_text`. Persist the full response before that action. Feeding these rows
directly into application `chunks` would also violate its library/generation schema
and source/image conventions. Do not relax the existing container/query invariants
to accommodate the template.

### Live extraction comparison

Six small synthetic cases: a two-page report with nested numbered headings and a
bar figure/caption; the unchanged structured DOCX review fixture with one embedded
image; an image-only scanned PDF; and PDF renderings of the existing research,
play and opinion fixtures. Five PDFs total seven pages; the DOCX reported one page
unit. PDF renders and the downloaded figure were visually inspected. These are
functional cases, not varied real-world quality or retrieval benchmarks.

Both models received the same complete file bytes, requested Markdown and Unicode
code-point spans, and had bounded input/polling. Layout PDF requests additionally
enabled figure output. All twelve analysis requests succeeded. All 28 literal
text-presence checks passed after whitespace/case normalization. This checks
selected facts, not complete transcription accuracy.

| Case | Read sections / figures | Layout sections / figures | Additional observation |
|---|---:|---:|---|
| Nested PDF | 0 / 0 | 9 / 1 | Caption and figure elements present; chapter boundary defect below |
| DOCX | 0 / 0 | 6 / 0 | Three native heading paragraphs and one embedded image in source |
| Scan | 0 / 0 | 1 / 0 | Marker and missing-data sentence recovered |
| Research | 0 / 0 | 10 / 0 | Two pages processed, including the final paragraph |
| Play | 0 / 0 | 6 / 0 | Act II selection included the final funding decision |
| Opinion | 0 / 0 | 9 / 0 | Dissent selection included its timestamp-validation qualification |

All 103 layout section-element references resolved to existing array entries. That
does **not** prove correct ancestry. Four explicit section-span probes gave:

| Selection | Layout result | Read result |
|---|---|---|
| Chapter 1, including its limitations | **Failed:** limitations were a sibling outside the chapter span | No section graph |
| Chapter 2, including its limitations | Passed the include/exclude probes | No section graph |
| Act II, including the closing decision | Passed the include probe | No section graph |
| Dissent, including the qualification | Passed the include probe | No section graph |

The diagnostic reads provider section spans without repairing them. It does not
implement an application source-read API, token budgets or revision handles. The
chapter-one failure is why arbitrary provider subtrees must not yet be labelled
complete authored chapters.

One additional layout analysis on the two-page nested report enabled a real figure
GET. Its PNG resolved successfully, was 11,744 bytes, and visual inspection confirmed
the two bars and their labels. The caption remained a separate JSON association.
The figure asset hash is in the record. This validates that source link in the
direct extraction API, not its preservation through Document Indexer.

## Capability and gap assessment

| Agent-facing requirement | KBaaS evidence | Pinned Indexer / measured extraction | Integration disposition |
|---|---|---|---|
| Passage retrieval | Agent-loop retrieval documented; external evidence unverified | Chunks/vectors projected; workflow search not tested | Keep current bounded Cosmos source search |
| Semantic file discovery | Chunk summaries documented; whole-document summaries unverified | No document-summary stage in template | Retain control; evaluate reusable summaries separately |
| Outline and chapter/section finding | Unverified | Layout graph measured; read graph absent | Preserve full layout JSON; validate boundaries |
| Complete document/selected source reads | Unverified external contract | Content exists before chunking; no complete-read manifest emitted | Retain immutable source artifact and all-or-nothing budget contract |
| Images/captions and visual selection | Text-only parsing documented | PDF layout figure GET worked; Office image omitted | Reuse layout figure extraction plus existing native Office path |
| Add/replace/delete/retry | Management deletion documented, runtime unverified | Add/modify trigger, random IDs, no delete trigger; lifecycle untested | Require stable identities, generation publication and purge tests |
| Library and personal-agent isolation | Per-agent external evidence path unverified | No library discriminator in projection | URL-bound authorization remains owned by this API |
| Inventory and overview refresh | Hierarchical/library summaries unverified | No overview stage in pinned workflow | Keep owner description; test revision-dependent overview adapter |
| Model/tool routing quality | Not measured | Not measured | Run held-out agent evaluation before claiming quality gains |

No live add/replace/delete, wrong-library/agent, stale handle, interrupted workflow,
or overview-refresh test passed in this experiment: none was run against a deployed
preview. The existing lab lifecycle demonstrations are controls, not evidence for
Microsoft preview behavior.

## Cost and operating evidence

The initial read analyses totaled 16.805 seconds and layout analyses 31.241 seconds
across six sequential requests each. These are small-run client timings including
polling, not p95 latency estimates. The extra figure analysis took 5.921 seconds;
its asset-download latency was not separately measured. Initial trials analyzed
16 reported page units; the figure retry added two, for **18 page units, 13 analysis
requests and one figure GET**. There were no embedding, summary, workflow or Cosmos
requests in this comparison.

Actual invoice charges have not been observed. No dollar estimate is substituted
for them. A full reuse cost comparison still needs Standard hosting, managed
connector/action usage, model usage, Cosmos writes/index readiness/query RU, retained
artifacts, retries, deletion and external evidence serving. The extraction-only
timings cannot choose an ingestion provider or storage layout.

## Reproduce and complete the remaining gates

The [runbook](../microsoft-reuse-lab.md) covers private directories, readiness,
isolated extraction provisioning, pinned template inspection, corpus generation,
live requests, offline replay and cleanup. Drivers have no imports from the normal
API/worker/demo. Local tests cover bounds, private paths, source integrity, graph
checks, section omission detection and resume behavior. Final validation passed
677 tests, including 11 new probe tests, with the same 14 existing deprecation
warnings. Repository lint/format, Bicep compilation, extended privacy checks and
offline response/figure replay passed. The isolated resource group deletion was
confirmed; no workflow host was left running.

The next bounded work is a **deployed Microsoft workflow test**, not more custom
format parsers. Deploy the inspected template in a dedicated host/database after
rerunning readiness and obtaining a supported KBaaS evidence-operation contract if
testing that branch. Every newly created Cosmos container must pass the repository
canary before use. Export the deployed definition and compare it with the pinned
source. Capture actual emitted items, connector identities, policy and partition
keys privately; publish only reviewed aggregate evidence.

First test the unchanged template, then the minimal full-layout preservation adapter
on the same six files. Run add, retry, same-name/same-count replacement and deletion;
resolve every retained source/figure reference. Test two isolated libraries and a
wrong personal agent through the authorized URL boundary, stale source handles,
partial enrichment and overview invalidation/refresh. Record Microsoft defaults
separately from adapter behavior. Integrate only that proven contract behind opt-in
REST; retain the normal worker/demo and frozen control until those checks pass.
