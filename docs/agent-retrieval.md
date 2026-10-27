# Agent evidence tools

The natural-test fixes add capabilities over the existing Cosmos/Blob artifacts. The priority is supported user intent, then time, then model/embedding/vision tokens and RU. The [strategy](consults/2026-10-27-agent-retrieval-strategy.md) was authorized for implementation after the original review. [E49](evidence.md) records validation and limits.

## Four actions per attached library

| Tool | Input / purpose | Evidence contract |
|---|---|---|
| `find_documents_library_<name>` | Default `mode: catalogue`, exact names/metadata with paging; `mode: semantic_candidates` plus `query` for vaguely described files | `resultUnit: documents`. Semantic candidates have a 40-passage window, dedicated selection status and `exhaustive: false`; no all-files claim. Catalogue includes unreadable files. |
| `search_library_<name>` | Natural information query; optional `document: {documentId, generation?, contentRevision?}`; explicit constraints only | `mode: evidence_search`, `resultUnit: passages`; multiple passages per file. Mixed text/image default. Returns snapshot-bound `evidenceId`, source location, parent size and content revision. |
| `read_documents_library_<name>` | `mode: full` (default), `outline`, or `selected` | Full remains all-or-nothing. Outline pages contain handles/headings/locations/token estimates. Selected returns exact stored regions and associated image candidates, with `selectionComplete` distinct from `documentComplete`. |
| `inspect_images_library_<name>` | `{documentId, generation?, contentRevision?, imageIds}` across supported formats, **or** existing PDF `pages` selection | Stored crops, caption provenance, generated descriptions where available and original body context where resolvable. No layout/vision/embedding calls or ingestion writes. |

The platform binds each tool to one authorized library and supplies the calling agent ID. Neither is a model argument. REST `distinctDocuments` remains compatible, but the search tool no longer exposes that switch. Semantic file finding owns grouping; a successful single-file candidate selection is not labelled a thin passage search. Refresh cached tool bundles on upgrade. The reference adapter still routes older `get_images_library_…` names; the four-tool bundle now emits `inspect_images_library_…`. The existing `/documents:images` PDF body remains valid.

## Expand a hit without loading a report

Search inside a returned document:

```json
{"query":"What evidence concerns retained context and collaboration?", "document":{"documentId":"returned-id","generation":1,"contentRevision":2}}
```

Expand a returned hit through `POST …/documents:read`:

```json
{"mode":"selected","documents":[{"documentId":"returned-id","evidenceIds":["returned-evidence-id"]}],"maxTokens":12000}
```

Or request `mode: outline`, `sectionLimit: 100` (maximum 200), and zero-based `afterSection` (default 0). Each document returns `hasMore`/`nextAfterSection`. A selected read accepts `sectionIds`, `evidenceIds`, `pages`, `slides`, or `paragraphs` on each document selection. Multiple selectors form a union. Full and outline reject region selectors rather than silently changing scope. Source locations that cannot be resolved return `source_scope_unavailable`; use outline section handles instead.

References are document/snapshot locators, not permission tokens or Blob paths. The server resolves the index against the selected authorized document and validates active generation, content revision and snapshot digest. A same-generation enrichment can change section positions: old references are explicitly rejected rather than silently pointing at another section. Search-hit expansion resolves only the selected chunk locations using a bounded `SELECT TOP n`, full document partition key and library equality, then reads the stored extraction; it does not fetch every chunk or embed again.

PDF pages and PPTX slides use stored source locations. DOCX stores section-start paragraph numbers: paragraph lookup expands the containing extraction section, including the available table/body numbering; it does not promise exact paragraph boundaries. Legacy indexed artifacts preserve overlap and available precision. Outline section handles remain usable where a named source location is missing. A partial read never implies a full-file read. Every content-returning mode refuses the entire selected batch if its raw response exceeds the explicit budget; no silent clipping. An outline page is complete for that page, not exhaustive when `hasMore` is true.

Selected regions return up to ten `candidateImages`, caption metadata and `candidateImagesOmitted`; proximity is not proof a figure supports a claim. Stored assets can include figures beyond the semantic-description cap. Other visuals may still be absent under ingestion limits. Inspect the selected IDs directly, without re-reading the whole Office file or broad-searching for a known image. Standalone images need no external body context; other missing/legacy context is disclosed.

## Reference adapter behavior

The compact projection distinguishes generated image descriptions from original text and provides a filename/location citation. It removes signed links and internal layout figure locators from model-facing source scopes. Captions inferred from adjacency remain qualified. Full/selected read text is never character-clipped.

Only the leading search image is previewed automatically, preserving protection against promoting a lower-ranked distractor. Full/selected reads expose display attachments and figure candidates; they do not send every figure's pixels. Explicit inspection supplies the selected images subject to model support/context budget. Each tool reply reports `nativeImageDelivery` with requested, supplied, already-supplied and withheld IDs/reason. UI availability alone never means the model saw pixels.

A per-turn extent ledger distinguishes repeated evidence from expanded source text. Two consecutive retrievals without new extents produce actionable recovery guidance, without terminating complex research or relaxing filters. New native inputs count as progress. Overall context and twelve-round bounds remain. This is a signal to change strategy, not a semantic guarantee that every new topic or figure is useful.

A bounded in-memory session ledger retains up to 32 document reference entries, with at most eight section/passage/image handles of each kind per entry. It stores source references, not a previous assistant answer as evidence. Follow-ups receive these hints and must revalidate through authorized tools; no cached source text is injected as current evidence. No conversation content is written to the index.

The answerer must address conditional interpretations, correct mistaken premises, verify statistical context and distinguish conceptual/attitudinal evidence from causal productivity claims. It should deliver useful plural visuals immediately. A bounded repair step detects missing requested image placements, unreturned image IDs and research answers without original body evidence. It uses the existing answering model/tools, at most twice within the unchanged round/context bound, and reports those model calls as `answer_repair`. It is not a relevance judge, a universal intent classifier, or a guarantee against unsupported conclusions. Extra calls/tokens are included in the measured cost; cheaper incomplete answers do not pass.

## Deployment and validation

API and consuming adapter/tool definitions need refreshing; worker/storage/index policy changes and re-ingestion are unnecessary for compatible stored artifacts. Missing old manifests/provenance may explicitly require reprocessing that document. No bulk migration, container/vector change, embedding change, cap/quota increase or production logging change is part of this work.

The 50-image semantic-description cap was **not the principal cause** of the observed agent failure. Relevant evidence and useful figures were already stored/indexed. The cap-independent fixes are mode separation, scoped evidence retrieval, source expansion, direct inspection of known images, progress/follow-up handling and answer deliverables. Access to retained images beyond a configured cap is an additional capability; uncapped production semantic ingestion does not remove the other requirements.

Run local tests, then `python -m scripts.evaluate_agent_retrieval --help` for opt-in real-model replay. Use existing frozen sources, the same deployment, budget and question order; supply credentials externally. Captures must be outside the repository. The harness saves each completed turn and verifies active generations/revisions/artifact hashes stayed unchanged. Review/sanitize before publication. Mechanism checks and single small-corpus replays are not broad recall, causal speed/cost benchmarks or blanket answer-quality signoff.
