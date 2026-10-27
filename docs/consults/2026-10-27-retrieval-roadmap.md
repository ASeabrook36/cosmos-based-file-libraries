# Planned retrieval and integration development — 2026-10-27

**Status: user-requested roadmap; not implemented by this documentation change.** This updates the earlier [issue #2 decisions](2026-10-27-retrieval-followups.md). The user selected the order below and values integration simplicity, preserving vague-image recall, and summaries that help both agents and people. Priority remains supported user results, then time, then model/embedding/vision tokens and Cosmos RU. See [E53](../evidence.md#e53-user-prioritized-the-next-integration-and-retrieval-roadmap-2026-10-27).

| Order | Planned work | Main responsibility | Existing-file impact |
|---|---|---|---|
| 1 | Compact REST responses with separate evidence and attachments | Library API/presentation and platform adapter | No ingestion or index change expected |
| 2 | Section structure, generated section summaries and section-aware image/text retrieval | Library ingestion, stored provenance, indexing and retrieval | Enrichment/backfill design required; no automatic reprocessing |
| 3 | Document summaries for semantic file selection and interface previews | Library ingestion/storage/discovery and consuming interfaces | Summary generation and indexing/backfill required |

These are planned library capabilities, not extra answering-time agent critics, typo classifiers or subject-matter sharing gates. The [library/agent boundary](2026-10-27-library-agent-scope.md) still applies. The current API remains as specified in [agent-retrieval.md](../agent-retrieval.md).

## 1. Make the compact response a reusable REST contract

The demo already projects raw service replies into model-facing evidence and separately held image attachments. Make that available as a supported, opt-in service response so another platform can integrate without copying demo internals. `view=agent` and a `{content, attachments}` envelope are candidate names/shapes, not finalized API syntax.

- Specify evidence, attachment and operational-metadata parts across search, discovery, reads and image inspection. Keep citations, source scope, completeness, limitations and all selected source text intact.
- Keep signed image links and operational details with the platform. Supply returned references in model-facing text; delivering native pixels and displaying an attachment are separate platform actions.
- Define content-only service budgeting, attachment/native-image accounting responsibilities and refusal/continuation behavior. Preserve all-or-nothing full/selected reads and raw REST compatibility.
- Publish integration examples and contract tests for native-image and text-only consumers, stale references, permissions and exact source-text preservation. A URL in JSON alone is not native visual input.

**Expected benefit:** simpler integration and fewer unnecessary model input tokens, potentially reducing model time/cost. No inherent database RU reduction. E45's 30.6–33.4% savings concern projected response text on small fixtures, not universal end-to-end savings.

**Completion evidence:** the demo consumes the supported response rather than a divergent convention; another minimal consumer can retrieve evidence and resolve/display images from the documented contract. Compare response size and actual model usage without reducing evidence coverage. No re-upload should be required.

## 2. Use generated section summaries for image and passage discovery

The aim is better contextual meaning, not simply fewer characters. The user specifically proposed a generated section summary shared by its images and text passages, and accepts additional ingestion model/embedding cost when it improves discovery. A heading alone may not describe the ideas covered by a section.

### Establish source structure and membership

[Microsoft's Document Intelligence layout documentation](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0#sections) describes sections/subsections with element relationships. Its [figure output](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0#figures) includes locations, spans, related elements and captions. These are structural inputs for associating figures with sections, not guarantees of perfect membership or generated topic understanding.

Current PDF code reconstructs headed text sections but selects figure context mainly from bounded same-page text and a same-page heading (`src/fli/figures.py`, `text_sections` / `extract_pdf_figures`). It does not yet use a full section hierarchy for figure membership. Retain DOCX/PPTX native heading/slide extraction: Document Intelligence's [documented Office support](https://learn.microsoft.com/en-us/azure/ai-services/document-intelligence/prebuilt/layout?view=doc-intel-4.0.0#pages) does not extract embedded images.

- Preserve section identity, hierarchy, heading path and source ranges. Associate passages and figures with containing sections using structural evidence, rather than treating page membership as section membership.
- Handle multiple sections on one page, multi-column layouts, multi-page sections and sections crossing bounded layout-analysis batches. Disclose inferred/unresolved associations and retain a conservative fallback.
- Distinguish attributed captions, section membership and broader topical context. An image in a section does not support every claim in that section.

### Generate and reuse section summaries

- Generate a bounded, source-grounded overview from each section's content, not only its heading or opening. Cover important topics/distinctions and disclose missing source coverage. Large sections need bounded, source-traceable summarization rather than silent truncation.
- Store each summary once, linked to its section/source version and generation method/version, with coverage status. Label it as generated discovery context, separately from original text and captions.
- Expose it alongside associated image evidence, in section outlines/previews and beside passage hits when useful. Preserve detailed image descriptions, original captions/headings and source references; keep fuller source context available for vision description and source expansion.
- Plan semantic section discovery using summary embeddings. Compare including summary meaning in image embeddings with separate section-summary candidates followed by image/passage expansion. Choose the representation from evaluation; extra summary results need explicit kinds/budgets so they do not crowd out source passages or images.
- Let an agent select returned section references to scope later text/image searches or reads. Summaries help choose or narrow searches; they must not become a compulsory filter that silently excludes relevant material elsewhere. Broader document/library search remains available and selected scope is explicit.
- Expand summary candidates to original evidence for factual claims/citations. Resolve references, versions and permissions server-side under the existing library binding rules.

**Evaluation before rollout:** compare current nearby-text inputs, shorter local context and section-summary approaches on labeled vague, topic-only, caption-only and visually specific image questions, plus passage/section questions. Include vague headings, minor topics omitted by summaries, uncertain structure and different subjects on the same page. Measure image/text recall separately, wrong-section associations, duplicate result slots, answer quality, tool rounds, native-image delivery, latency, RU and model/embedding tokens. Use vector-only exact references for approximate-index recall as well as human relevance labels.

Keep current embedding defaults until comparisons support a change without sacrificing topic-only image or ordinary text recall. Extra ingestion cost is acceptable for quality. Shorter input does not reduce fixed vector dimensions or guarantee cheaper queries. Cache reusable summaries; invalidate them on relevant source changes; preserve text-first publication and disclose enrichment failures. Design publication/count fences, retries, metadata updates and opt-in legacy backfill before adding indexed row types. No default change, container/vector-policy change or bulk reprocessing occurs in this documentation update.

## 3. Provide document summaries for agents and people

**Planned product capability, not contingent solely on proving today's file selection inadequate.** The user identified two independent uses: semantic document selection by agents and a quick overview for any interface to display to a person. Retrieval effectiveness still needs evaluation before making a new ranking path the default.

The existing per-document `description` is an opening-text discovery hint (spec §9.1), not a verified whole-document summary. Do not silently relabel it. Reuse section-summary infrastructure where appropriate, recording source coverage so aggregation does not overclaim completeness.

- Generate/store a concise, readable overview of subjects, purpose and scope from available document content. Represent topics beyond opening pages, label missing coverage and retain source-version provenance and generated status.
- Expose it in document metadata/catalogue/detail responses for file browsers, upload results and agents. Persist readable text independently of its vector; each consuming interface controls presentation.
- Embed it for semantic document discovery, returning explicit document candidates distinct from passage/image evidence. Retain filename/metadata discovery, scoped passage search and source reads. A summary must not become the only way to find a file.
- Specify publication, generation cost, retries, replacement/deletion, retagging, legacy reads and backfill. Reuse section work without blocking early text availability on summary completion; distinguish provisional from complete coverage.

**Completion evidence:** previews are useful and faithful to declared coverage; broad and minority-topic questions find the right files and expand to original evidence. Compare selection recall, repeated searches, latency and model/RU costs against existing discovery. Record ingestion model/embedding tokens, added storage/write RU and backfill costs separately from any answering-time savings. No automatic index/container migration is approved by this roadmap.

## Evidence and implementation boundaries

E45–E52 and the [test review](2026-10-27-agent-retrieval-test-review.md) establish implemented mechanisms and observed limits. They do not establish gains from section/document-summary vectors: those paths have not been built or evaluated. This roadmap supersedes the original summary-vector prerequisite and broad image-context deferral as product planning, while retaining rollout evaluation gates. It records priorities and direction, not new measured gains or deployment authorization.
