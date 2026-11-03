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

PDF pages and PPTX slides use stored source locations. DOCX stores section-start paragraph numbers: paragraph lookup expands the containing extraction section, including the available table/body numbering; it does not promise exact paragraph boundaries. Legacy indexed artifacts preserve overlap and available precision. Outline section handles remain usable where a named source location is missing. A partial read never implies a full-file read. Every content-returning mode refuses the entire selected batch if its chosen response view exceeds the explicit budget; no silent clipping. An outline page is complete for that page, not exhaustive when `hasMore` is true.

Selected regions return up to ten `candidateImages`, caption metadata and `candidateImagesOmitted`; proximity is not proof a figure supports a claim. Stored assets can include figures beyond the semantic-description cap. Other visuals may still be absent under ingestion limits. Inspect the selected IDs directly, without re-reading the whole Office file or broad-searching for a known image. Standalone images need no external body context; other missing/legacy context is disclosed.

## Reference adapter behavior

The demo requests the supported [compact REST contract](compact-rest.md) on all four retrieval operations. Its `evidence` distinguishes generated image descriptions from original text and provides a filename/location citation. Signed links and operational metadata stay in `attachments`/`metadata`; internal layout figure locators are removed from model-facing source scopes. Raw REST remains the default. Compact budgets count only evidence JSON; raw budgets retain their original semantics. Captions inferred from adjacency remain qualified. Full/selected read text is never character-clipped.

The normal `IndexClient` now requests `selectors=handles` on the tool bundle and
every retrieval operation, following the accepted [I1 result](retrieval-i1.md).
Copy returned `documentSelector`, `outlineSelector` and `readSelector` objects;
they retain generation/content-revision pins and disclose when an outline is
needed instead of guessing source coordinates. REST defaults remain legacy, and
the reference evidence encoding remains an independent opt-in.

For an explicit rollback, set `DEMO_SELECTOR_MODE=legacy` and restart the demo
process (its client is cached), or construct `IndexClient(...,
selector_mode="legacy")`. `IndexClient.from_env(selector_mode="legacy")` takes
precedence over the environment setting. Unsupported settings fail at startup;
retrieval failures never trigger an automatic legacy downgrade. Custom adapters
that do not declare a selector mode retain their existing compatibility behavior.
The historical R0, recovery and model-comparison CLI entry points explicitly
retain legacy selectors; P1/I1 cells already pin their selected modes. Reproducing
old measurements still requires their recorded source commits and controls.

Only the leading search image is previewed automatically, preserving protection against promoting a lower-ranked distractor. Full/selected reads expose display attachments and figure candidates; they do not send every figure's pixels. Explicit inspection supplies the selected images subject to model support/context budget. Each tool reply reports `nativeImageDelivery` with requested, supplied, already-supplied and withheld IDs/reason. UI availability alone never means the model saw pixels.

A per-turn extent ledger distinguishes repeated evidence from expanded source text. Two consecutive retrievals without new extents produce actionable recovery guidance, without terminating complex research or relaxing filters. New native inputs count as progress. Overall context and twelve-round bounds remain. This is a signal to change strategy, not a semantic guarantee that every new topic or figure is useful.

The adapter passes its remaining evidence budget to compact search. An oversized
response is a budget refusal, not an empty search. It retries at most four whole-
passage requests (8, 4, 2, then 1 result), dropping neighbors on retries while
retaining the bound library, caller, document snapshot and filters. Every request
retains its own charge and latency trace. Passage text is never clipped to fit.
Malformed document selectors are rejected rather than discarded. Handles mode
rejects scalar document IDs and returns sanitized recovery guidance; callers
must copy the returned object. Only explicit legacy mode repairs a nonblank
scalar ID to the equivalent object selector. Neither mode drops supplied pins.

Before any original text or native image has arrived, the first passage response
is limited to half the remaining evidence space or 6,000 tokens, whichever is
larger, never exceeding the remaining space. This leaves room for another file or unresolved condition
without excluding the current 5,000-token Microsoft passages. Later searches can
use the full remaining budget. The answer reserve and whole-passage rule remain.
Neighbor retrieval now returns each neighbor's stored location and heading,
additively in raw and compact responses. A neighbor's citation uses that location;
legacy neighbors without one retain the explicit warning against inheriting the
ranked hit's location. The scoped query remains bound to one library, document and
generation and never selects embeddings.

After an oversized document read, the adapter may recover passages from up to
four returned authorized document snapshots, using the current question and at
most two requests per file (2 then 1 passage). Recovery respects both the remaining
context and an explicit read budget. The original read remains incomplete, with
false full/selected completeness flags; recovered passages cannot establish
whole-file coverage. Permission, stale-reference and missing-source errors do not
trigger this fallback. Refusal metadata is compacted without discarding previously
delivered source bodies, and terminal synthesis checks the context reserve too.

A bounded in-memory session ledger retains up to 32 document reference entries, with at most eight section/passage/image handles of each kind per entry. It stores source references, not a previous assistant answer as evidence. Follow-ups receive these hints and must revalidate through authorized tools; no cached source text is injected as current evidence. No conversation content is written to the index.

The answerer must address conditional interpretations, correct mistaken premises, verify statistical context and distinguish conceptual/attitudinal evidence from causal productivity claims. It should deliver useful plural visuals immediately. A bounded repair step detects missing requested image placements, unreturned image IDs and research answers without original body evidence. It uses the existing answering model/tools, at most twice within the unchanged round/context bound, and reports those model calls as `answer_repair`. It is not a relevance judge, a universal intent classifier, or a guarantee against unsupported conclusions. Extra calls/tokens are included in the measured cost; cheaper incomplete answers do not pass.

Substantive answers following discovery also require delivered original source
text or supplied native-image evidence. Catalogue descriptions and outline headings
alone trigger bounded recovery and, if it fails, an explicit limitation on both
normal and terminal answer paths. Pure catalogue questions remain answerable from
metadata. For explicit both/each-file requests over a small identified set (up to
four files), a partial source set cannot pass as a complete comparison. This is a
mechanical, heuristic evidence gate: it does not certify relevance, accurate
citations, correct claims or exhaustive absence.

The answering instructions require targeted searches for unresolved parts of a
compound question and prohibit inventing missing source rules or calculations,
even as tentative interpretations. Numeric uses of “figure” do not by themselves
request an image. These instructions reduce specific observed mistakes; live
replay still found inaccurate attributions and unsupported judgments despite
delivered original evidence. See E62 for the measured limits.

## Deployment and validation

API and consuming adapter/tool definitions need refreshing; worker/storage/index policy changes and re-ingestion are unnecessary for compatible stored artifacts. Missing old manifests/provenance may explicitly require reprocessing that document. The November validation separately recovered an isolated account after E17's service failure; that operational recovery and temporary test throughput increase are not requirements of the adapter change. Production logging and vector policies are unchanged.

The 50-image semantic-description cap was **not the principal cause** of the observed agent failure. Relevant evidence and useful figures were already stored/indexed. The cap-independent fixes are mode separation, scoped evidence retrieval, source expansion, direct inspection of known images, progress/follow-up handling and answer deliverables. Access to retained images beyond a configured cap is an additional capability; uncapped production semantic ingestion does not remove the other requirements.

Run local tests, then `python -m scripts.evaluate_agent_retrieval --help` for opt-in real-model replay. Use existing frozen sources, the same deployment, budget and question order; supply credentials externally. Captures must be outside the repository. The harness refuses nonempty output directories, saves case definitions and each completed turn, and verifies document metadata, source versions, library/agent settings and tool context stayed unchanged. It removes attachment credentials from captures. Lexical matches and delivered-source indicators are explicitly separate from semantic task/citation judgments; failed-attempt usage remains unavailable where it was not returned. The [expanded case pack](testing/2026-11-01/README.md) covers factual distinctions, unavailable data, both-file evidence, metadata-only requests and follow-ups; a separate held-out pack adds blank forms and table relationships. Review/sanitize before publication. Mechanism checks and single small-corpus replays are not broad recall, causal speed/cost benchmarks or blanket answer-quality signoff.
