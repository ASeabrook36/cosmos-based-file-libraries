# Image ingestion and retrieval readiness review — 2026-10-27

The current design supports semantic retrieval of text and image meaning, followed by an answer that can place a selected image and its stored caption beside supporting text. This review found no need to change the Cosmos containers, partition keys, vector policies or embedding model. It found small processing and disclosure fixes, implemented before preparing the change for commit.

## Decisions checked

| Area | Current behavior and decision |
|---|---|
| Figure isolation | PDF layout supplies individual figures, including vector charts; body text and tables remain text. There is no page-rendering fallback. Office extraction handles supported native picture shapes. |
| Captions | Associated source text is stored with each image before publication and included in searchable image text. Ordinary text retains captions too. This supports both text and image retrieval without a separate caption model call. Inferred associations remain labelled. |
| Cosmos | Descriptions, vectors and filter metadata share existing chunk rows. Queries preserve bounded `TOP`, one-library equality filters, float16 conversion and the measured vector/hybrid routing. Pixels and manifests stay in Blob. No extra image database, join or container is needed. |
| Publication | Immutable preparation, description checkpoints, create-only chunks and atomic document pointers protect concurrent/retried work. Text becomes searchable before model descriptions finish; enrichment does not re-embed or rewrite published text chunks. |
| Retrieval | Search returns ranked candidates, including image links/captions. A stored PDF page read is used only when additional figure/page evidence is needed. It performs no layout analysis, vision calls, embeddings or storage writes. No post-draft caption recovery or answer revision remains. |
| Answer presentation | Consuming agents can insert a helpful figure into an explanatory answer without a separate picture request. The demo resolves only returned image IDs and renders captions by default, with an explicit omit option. Unselected candidates remain diagnostic text. |
| Isolation | Personal binding, source/generation integrity, read-link path checks and final lifecycle checks apply to visual evidence. Signed links remain short-lived bearer capabilities; already issued links cannot be revoked by merely replacing a document. |
| Bounds and telemetry | Source storage and semantic-description caps are separate, with omissions disclosed. Vision, layout and Cosmos writes have shared process-local limits. Content-free metrics distinguish retrieval time from model/whole-answer time. |

## Changes from this review

1. Image enrichment previously downloaded every retained crop into a list and attempted a second conditional upload of each already-stored image. It now loads one crop at a time, releases its bytes after description and reuses its Blob reference. A regression check refuses another image download until the previous one was described and refuses image writes during enrichment.
2. PDF image normalization and text-section assembly now run off the async event loop, so CPU work does not block other library lanes. Page metadata is assembled once per layout batch.
3. Files with legacy visual omissions no longer report `visualsComplete: true`. Full-read notes distinguish extra stored PDF figures from unindexed Office pictures, which the current direct-read API cannot return.
4. Tool and demo guidance explicitly allow useful figures alongside text explanations, favor existing evidence over another figure lookup, and avoid unnecessary verbatim caption repetition in the demo answer.
5. A real-model replay selected a same-subject picture from the wrong page despite having the matching passage. Automatic native previews now respect the overall ranking: a lower-ranked image is not promoted over a leading text result. All candidate descriptions/links remain available, and source-matching guidance directs the agent to the actual passage page. A scripted regression checks preview behavior with both text and image leaders. This reduces an anchoring risk; it does not make model routing deterministic.
6. The unused PDFium dependency from the superseded full-page renderer is removed. The README, API/data-model specification, image guide, build plan and evidence now describe the final stored-caption and text-first behavior together.

## Validation

All 581 local tests passed on the development Python 3.14 environment. They cover image/text ingestion, captions, limits, description checkpoints, repeated/overlapping attempts, metadata edits, replacement/deletion, personal isolation, full-read completeness/budgets and default/omitted caption display. Lint, formatting, wheel packaging/content inspection, documentation links and the private reference-privacy check passed. CI retains the Python 3.12/3.14 matrix; this local result is not a substitute for that remote run.

The earlier real Azure and real-model evidence remains valid for unchanged storage/query behavior: [conditional publication](../../records/phase3b-hardening-conditional-20261026.json), [four-format ingestion/model check](../../records/phase3b-hardening-smoke-20261026.json), [figure isolation](../../records/phase3b-figures-20261026.json), [text-first ingestion](../../records/text-first-ingestion-20261026.json), and [stored image/caption pairs](../../records/ingested-image-captions-20261027.json). A final real-model replay selected the expected later-page illustration using search plus a stored-page read; the diagram/caption and explicit caption-omit cases each needed only search. A subject-only explanatory question did not choose an illustration in this sample, so proactive illustration quality remains an evaluation item. No caption-revision calls occurred. The final review check is recorded separately in [readiness evidence](../../records/image-readiness-review-20261027.json). Private source text, answers, runtime IDs and signed links remain outside this reference repository.

## Remaining limits

The README and image guide distinguish configurable prototype defaults, current implementation scope, and evidence/isolation properties that production must preserve. The caps, Streamlit conventions, local hosting and current targeted-read scope are not intrinsic Cosmos constraints.

These are disclosed limits and evaluation work, rather than reasons to replace the current architecture:

- Figure/caption detection is learned and can be wrong. Broader real-corpus recall, citation accuracy and agent illustration-selection evaluation remain necessary.
- Only the first configured image-description count participates in semantic image search. Additional stored PDF figures can be read by page. Unindexed Office pictures currently require a higher description cap and reprocessing, or PDF export.
- A text hit does not automatically attach nearby images. Explicit source-region-to-text-chunk expansion could improve targeted evidence gathering later, without a cross-library query or new vector store.
- PDF layout still precedes first text publication and resubmits source bytes per page-range batch. Stored-page reads select from document-level text/asset artifacts. These are candidates for optimization after representative latency/cost measurements.
- Search previews send an image automatically only when it leads the overall result ranking; lower-ranked candidates remain available as descriptions/links. Fine-grained visual selection and image-token accounting remain consuming-platform decisions. Model calls can dominate total response time.
- Checkpoints reuse completed expensive work; simultaneous cache misses can still duplicate vision calls, and downstream embedding failures can repeat embedding requests.
- Governors are per process. Aggregate worker capacity, large-library throughput, cross-instance Session-consistency visibility and broader scale/quality evidence remain Phase 4/5 work.

No production-readiness or universal latency/quality claim follows from these smoke checks. The feature is ready for the repository's prototype commit and subsequent CI review with those limits stated.
