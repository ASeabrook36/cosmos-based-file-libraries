# Proposed agent retrieval strategy after natural-language testing

**Later planning update (E53):** the capability package below is implemented. For planned compact REST responses, generated section summaries for image/passage discovery, and document summaries for agent selection plus interface previews, use the [follow-on roadmap](2026-10-27-retrieval-roadmap.md). Its user-approved priorities supersede this historical proposal's exclusions of summary-vector work; no current embedding default has changed.

**Status: authorized for implementation after review; capability package implemented.** See the [current contract](../agent-retrieval.md) and E49 for validation and remaining model-quality limitations. The proposal below preserves the original rationale. This document records the recommended next work. It does not change the current API contract or authorize a deployment. Priority is (1) the best supported answer to the user's actual request, (2) retrieval/response time, and (3) cost in model/embedding tokens and Cosmos RU. A cheaper incomplete answer does not pass.

The user is correct that practical improvements are available in both tool presentation and capabilities. The evidence does not justify replacing the Cosmos/Blob architecture. It does show that passing deterministic API tests is insufficient: the new document-grouping option can be used counterproductively by a real agent. We should own that interface weakness rather than attribute every failure to model variability or prototype quotas.

## Evidence and scope

The [day's test review](2026-10-27-agent-retrieval-test-review.md) links all public records and distinguishes findings from unproven performance claims. In particular:

- [E45](../evidence.md): compact model evidence preserved content and reduced small-fixture response text by 30.6–33.4%; interleaving and bounded concurrency passed local/live mechanism checks.
- [Paired manual runs](../../records/library-layout-manual-20261027.json): sixteen answers across separate and combined library configurations; seven requested image placements appeared in each. Different tool counts, model paths, independent vision descriptions and quota waits prevent a general library-organization verdict.
- [Casual cases 1 and 2](../../records/casual-image-reference-20261027.json): the right diagram and map were found, but referent ambiguity, caption qualification and avoidable text-only searches remained.
- [E46 and the three-turn case](../../records/casual-retrieval-friction-20261027.json): eleven grouped searches each returned one passage within a known report; the full read exceeded the demo budget; useful image candidates required further user prompting; the answer did not substantiate the requested statistics/context claim adequately. Read-only diagnostics verified relevant later text and images were stored and retrievable. Ungrouping alone did not solve poor query formulation.

This is an evidence-backed implementation proposal, not a broad recall score, a guarantee about model reasoning, or a new storage specification. No source-specific page numbers, question strings or fixture answers belong in production routing rules.

## 1. Make file finding and evidence search different actions

**Recommended decision:** remove the document-grouping switch from the ordinary agent passage-search schema. Keep the existing REST option compatible, and expose semantic file selection through the file-finding capability with an explicit candidate scope. This is a proposed tool-contract change for review.

Keep four conceptual actions per library rather than adding one tool per failure:

| Action | Proposed responsibility | Clear result contract |
|---|---|---|
| `find_documents` | Exact name/metadata catalogue, or explicitly requested semantic candidate finding | `resultUnit: documents`; `mode: catalogue` or `semantic_candidates`. Only catalogue enumeration can claim all matches after paging. Semantic candidate finding remains bounded/non-exhaustive. |
| `search_library` | Ranked passages/images for answering a question; optionally scoped to an identified document | `resultUnit: passages`; document scope, evidence kind, clipping/omissions and stable evidence IDs remain explicit. It cannot silently collapse to one passage per file. |
| `read_documents` | Entire files, an outline, or explicitly selected source sections/pages/slides | Whole-file completeness retains its existing meaning. Selected reads identify the exact scope and whether that selection is complete, without claiming the whole document was read. |
| `inspect_images` (extension/successor to current PDF page-image tool) | Selected returned image IDs, their stored captions and supporting source regions across supported formats; current PDF-page selection remains compatible | IDs and source provenance, exact associated source text, generated descriptions and actual native-input delivery are distinguished. No answer-time caption repair, new vision descriptions or ingestion writes. |

Names are illustrative extensions of existing tools; final wire schemas and compatibility aliases are part of implementation review. Preserve the current exact-name/metadata catalogue semantics when no semantic selection is requested. Do not turn a bounded semantic candidate set into an exhaustive file list.

Return an explicit retrieval mode/result unit in the compact projection. A successful one-file selection should not be described as a thin passage search. Preserve a dedicated selection status, candidate-window limit and a clear next step to search evidence within the identified document. Legacy passage dispositions can remain compatible on the existing REST path.

Queries should describe the information needed in natural language. Put the selected document in a document selector, not a long list of repeated filename/topic terms or web-search operators. Metadata filters narrow explicit constraints; they must not be inferred as mandatory simply because an agent guesses a topic tag. Mixed text/image retrieval remains the default; intentional text-only filters must disclose excluded visual evidence. No silent rewriting of explicit filters.

**Why this serves the priorities:** more relevant evidence first; fewer ineffective model rounds second; less repeated query/model input and lower RU third. The observed eleven grouped searches become an explicit regression case. Merely changing a boolean default is not sufficient.

## 2. Add document-scoped search and bounded source reading

Add an optional document selector (and pinned generation when supplied) to passage search. It remains within the tool's platform-bound library. Reject stale or foreign selectors; never let an agent supply a library identity or expand its authorization. The existing `/documentId/?` index path supports a document equality filter; validate static SQL and real query routing before rollout. No new vector index, container or embedding model is proposed.

Expose a lightweight outline of stored extraction sections with headings, source locations and token estimates when useful. Search hits should carry resolvable source references so an agent can normally expand a hit without a separate outline request.

Extend source reading to request bounded ranges or explicit sections: PDF pages, PPTX slides and stored DOCX sections/paragraph locations at the available extraction precision. A selected region returns its stored text and associated image references/captions together, plus scope and omission metadata. Do not imply exact layout/paragraph boundaries where legacy artifacts do not retain them. A large selection follows an explicit budget and continuation/refusal contract; it is never silently clipped.

Whole-file reads remain all-or-nothing. Keep legacy `complete` semantics unchanged. Selected reads use explicit `selectionComplete` and `documentComplete` distinctions. After a whole-file budget refusal, return available bounded-reading actions and size/outline hints; do not instruct the agent to retry the same oversized request unchanged or claim a section read satisfies an explicit whole-file request.

Create generation/snapshot-bound evidence handles for passages, sections and images. Text references must survive image interleaving within a generation where the text itself is unchanged, or else explicitly bind to a content revision and reject stale references. Resolve and validate those references server-side; do not accept raw Blob paths. Derive usable references at read time for compatible stored artifacts where possible. Assess exact legacy coverage before promising universal no-reprocessing support; missing provenance must be disclosed rather than invented.

**Example intended behavior:** identify the relevant report, retrieve passages on context/memory/co-creation, expand their source regions for qualifications, and obtain figures from those regions. Do not repeatedly reload introductory snippets or require the entire approximately 98k-token projected report for a focused meeting brief.

## 3. Make visual inspection a direct capability across formats

An agent must be able to select image IDs it has already received and request their pixels plus stored context without reading a complete Office document or re-running broad semantic searches. Extend the existing image/caption manifest reader for supported Office/standalone assets as well as PDF figures, preserving existing page-based PDF access beyond the description cap.

Use the same source-region expansion path to associate returned body evidence with candidate figures. Return candidate associations when a page contains several images; do not assert that every same-page image explains every passage. Exact caption association remains separate from contextual proximity.

The reference adapter should supply native inputs for the specifically selected images when the deployed model supports them and the remaining budget permits. It should report which IDs were actually supplied and which were withheld, not imply visual inspection merely because an image is available to the UI. Selective inspection preserves E44's protection against promoting a lower-ranked distractor automatically over a leading text hit. Do not send all candidate images or the whole report's figures by default.

Keep generated descriptions, original page/section text, associated source captions, visible/transcribed image text and model interpretation conceptually distinct. A caption inferred from adjacency retains its qualifier; words printed inside a map are not relabelled as an externally associated source caption. Preserve the actual source text and absence/status of captions. Signed links/geometry remain platform attachments, not ordinary model tool text.

For plural image requests, select and display multiple relevant candidates in the initial answer when the source supports them. Explain briefly why each is useful and what it does not establish. If only one candidate is relevant, say that rather than padding the answer with unrelated charts. Never show every retrieved candidate as if selected. Keep original retained crops for display/download with citation metadata; do not promise an upscaled 'high-resolution export' beyond the stored source quality or imply reuse permission was verified.

**Case coverage:** casual colour/shape comparisons use the actual selected diagram; map-location questions can inspect the map directly; the meeting question receives selected visuals with context instead of a menu offering to fetch them later.

## 4. Add evidence-progress recovery in the reference adapter

Track returned evidence handles, source versions, content extents and native images delivered during a turn. A different wording that returns the same evidence is not automatically progress. Reading an unclipped section or supplying previously withheld pixels can be progress even if the source ID is unchanged.

Recommended policy: after two consecutive retrievals add no new evidence/extent needed for the task, return an actionable progress signal to the model. Require a material strategy change where a gap remains: scope to the file, change from file finding to passage search, expand a source region, inspect a figure, or clarify a material ambiguity. Do not secretly relax user filters or stop all complex research after two calls. The overall context/round bounds remain safety limits, not substitutes for strategy recovery. Raising the 12-round ceiling is not the first fix.

Use a small, bounded session evidence ledger for follow-ups: document/generation/revision references, selected images, inspected regions and provenance. Reuse authorized content when current and within budget; re-resolve/revalidate source handles before treating cached evidence as current after replacement/deletion. Fresh links/native inputs may be needed even when a rendered picture remains cached. A previous assistant assertion is not retained source evidence. Keep this behavior in the consuming-platform/reference adapter; no conversation-content logging to the index is proposed.

Record content-free fields sufficient to diagnose mode, scope, result overlap, selected/supplied image counts, refusal reasons, tool/model calls, provider waits and remaining budget. Keep source text, prompts, generated descriptions and SAS credentials out of routine telemetry. Correlate turns by private session/turn identifiers; sanitize public records. Explicit user-authorized test captures can remain separate from normal telemetry.

## 5. Tighten answer requirements around ambiguity, statistics and visuals

This is an integration contract and evaluation requirement, not a new ingestion-time summarization model or a promise that prompting alone guarantees reasoning.

For an ambiguous remembered item, state a useful conditional interpretation and answer it when the source can cover the alternatives. Ask only when the ambiguity materially changes the answer and cannot be handled safely. Do not silently normalize every word/number. The diagram comparison should answer the size question directly, without an acknowledgment that reads like the opposite answer. The map answer should explicitly address the plausible plot-number interpretation and correct the mistaken waiting-list premise while remaining limited to what the map shows.

For a request combining research, statistics and reusable visuals, plan and fulfill those deliverables together:

1. Retrieve the relevant claims/studies and their source regions before forming a persuasive conclusion.
2. For each cited statistic, identify the value/unit, population or sample, comparator/task, source location and important qualification. Do not confuse studies with effect-size counts, correlation with causation, or a general human-AI result with a memory/context intervention.
3. Use native inspection/source expansion for numerical image evidence when available; a generated visual description alone is not an exact transcription guarantee.
4. Show multiple selected, relevant visuals with their stored captions/provenance and a short explanation of how they bear on the argument.
5. Say when the report supports a narrower claim or does not provide the requested direct quantified support. Include material contradictory/mixed findings. Do not manufacture a favourable statistic or substitute attractive but unrelated pictures to satisfy a presentation request.
6. Disclose what was searched/read and remaining limits. Do not ask routine permission to fetch evidence or images the user has already requested.

Use deterministic checks for valid returned image IDs, source/snapshot validity, caption status, budgets and completeness. Evidence relevance, ambiguous intent and scientific claim strength still require model judgment and reviewer/evaluation evidence. Do not install a source-page whitelist or canned answer for the observed examples.

## 6. Validation and release gates

Freeze the current source bytes, active generations and stored descriptions for an adapter before/after comparison. Do not re-ingest the corpus or change model/quota settings mid-comparison. If new provenance requires new ingestion, evaluate that separately and record the confound. Keep the original captures/records immutable.

| Case family | Required result |
|---|---|
| Casual remembered diagram | Right picture and conditional referent; direct, source-supported comparison; inferred captions remain qualified. |
| Ambiguous map location | Right map, explicit handling of the plausible plot number and waiting-list correction; no invented plumbing/access facts; no repeated text-only strategy when the needed evidence is visual. |
| Research + statistics + plural images | Relevant source regions, defensible numerical citations when present, qualifications/counterevidence, useful images delivered without repeated requests, and no overstated whole-file coverage. Absence of direct quantitative support must be reported honestly. |
| Follow-ups | Previously selected sources can be recovered safely without treating old assistant prose as evidence or losing image IDs/source generation. |
| Large/legacy files | Full-read refusal remains honest; bounded reads identify selection scope; missing provenance and omissions are explicit. |
| Isolation/lifecycle | Personal binding and one-library queries persist; stale/foreign image/section/document IDs, deletion/replacement races and mixed revisions cannot publish evidence. |

Run unit/static/API checks for new selectors, literal TOP bounds, float16 conversion/routing, generation and revision fences, manifest integrity, budget scope and result-mode signalling. Then replay the natural cases without coaching tool sequences, plus held-out paraphrases, misleading premises, near-match images, multi-file variants and negative-evidence requests. Accept alternative valid source evidence; do not require fixed ranked result lists or exact canned wording. Evaluate vector recall against exact references where applicable; real-model answer quality is a separate gate.

Score intent coverage, source accuracy, numerical citation accuracy, figure relevance, caption provenance and delivered visual completeness first. Only then compare model rounds, retrieval/total latency, provider waits, input/output/embedding/vision tokens and RU. Use repeated runs and report variability; cached-input/reasoning subsets must not be double-counted. No speed/cost gain is accepted in exchange for a worse answer. Avoid routine extra LLM extraction or judge calls where existing stored evidence/deterministic checks suffice; use additional source reading/inspection when answer accuracy requires it.

Roll out in small validated steps: mode separation and scoped search first; bounded source and selected-image reading next; progress/session evidence behavior and end-to-end answer validation alongside them. Update the specification and integration guides in each implemented change. Refresh the reference adapter as well as API/worker. Current active generations remain readable; no silent bulk migration.

## What is intentionally not in this proposal

No Cosmos container, partition/vector policy, embedding model, summary-vector row, default image embedding-text change, bulk reprocessing, cross-library ranked query, automatic all-images gallery, or blind context/round/quota increase is proposed. These tests do not justify those changes. Caption pairing at ingestion, text-first publication, all-or-nothing full reads, query invariants and platform-bound libraries remain requirements.

## Package subsequently authorized for implementation

The recommended package is **task-specific tool semantics with four conceptual actions, document-scoped passage search, explicitly bounded source reads, selected-image inspection across supported formats, and source-aware progress/follow-up handling in the reference adapter**. This is a coherent capability/contract change, not just a longer system prompt. Exact limits, wire schema aliases and compatibility rollout should be finalized before implementation, with evidence from the test families above as the acceptance gate.
