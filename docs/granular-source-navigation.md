# Granular source navigation candidate

Opt-in `custom-granular-v1` connects preserved source paragraphs, table cells and
lists to authored headings through the existing demo, ingestion worker, API and
agent tools. It does not change the default provider or retrieval policy.

## Baseline and attribution

Branch `codex/granular-source-navigation` starts with reviewed integration
`7e9a31b`: main `1ddc4386361446a5122ce936cdb3ba28aa4e5919` plus source-only repair
`33402d3bf66c5ad6ba2d0b12c0c46cba66695aff`. Build-plan/spec/evidence conflicts
retained both histories. Main's selector adoption and historical replay controls
remain; full repair ancestry preserves completed experimental files.

The [pilot](hybrid-client-model-pilot-results.md), [source repair](source-structure-repair.md)
and combined report at `codex/source-context-repair` / `dc703ec` were reviewed.
Context repair `44708040dce0a6d4cd5b44205a07a5d7e54404a7` is not incorporated or
required. Its separate findings are not results for this candidate.

Index versioning at `codex/index-versioning` / `1cf7e935a16a0073093fa0f8214251a1bd252a3c`
is not incorporated. Later integration overlaps ingestion, worker publication,
reading, hints and presentation. Preserve index-bound jobs, scoped store/path
helpers, selected-index validation and `indexId`/`sourceSha256` selector pins.
The enrichment is pure over immutable source/job data and uses existing generation
paths. No new Cosmos policy or container is needed.

## Source and navigation

DOCX reuses the source repair's original paragraph/run/list/table parser. Each
paragraph becomes an atomic source section, including cell paragraphs, blanks
and empty merged-cell continuations. Coordinates, merge spans, header-row flags,
list identity/depth and numbering definitions survive. An unnumbered qualifier
stays a separate sibling. Printed ordinals and semantic qualifier scope are not inferred.

Effective authored outline levels, including style inheritance and explicit
suppression, establish sections/subsections. Bold appearance is not a heading
rule; cell headings do not open document sections. Unsupported revisions,
footnotes/endnotes, text boxes and nested tables fail explicitly. Rendered DOCX
pages and visuals remain unavailable. `complete` means the complete versioned
text representation, not every original Office feature.

Markdown retains the lossless UTF-8/ATX parser, fenced code, Unicode and decoded
character spans. Its leaves are source blocks. Chapter nodes require an explicit
authored chapter heading recognized by that parser. Cells/pages are not invented.

Source atoms and the navigation catalog share immutable, hash-bound `sections.json`.
Reads validate source/library/document/job/generation bindings, rendering,
coordinate ownership, hierarchy and numbering references. Invalid artifacts
refuse without flat-chunk fallback. Compact responses omit the audit catalog,
duplicate runs and numbering definitions. Citations distinguish source paragraph/
table coordinates from printed clauses; Markdown uses source character spans.

Use the existing `read_documents_library_*` tool and copy returned handles:

| Handle | Evidence returned |
| --- | --- |
| `outlineSelector` | Immediate child headings, tables, cells, paragraphs or Markdown blocks; drill and paginate |
| Paragraph/block `readSelector` | That complete original atom |
| Cell/table `readSelector` | Its paragraphs in original order |
| Section/chapter `readSelector` | Own content including tables, excluding authored subsections |
| `subtreeSelector` | All descendant source atoms |
| `containingSectionSelector` | Containing authored subtree; unheaded content explicitly labels the document root |
| `fullSelector` | Complete versioned document representation |

Handles retain document/generation/content-revision pins. URL-bound library scope,
personal binding, post-I/O lifecycle checks and atomic budgeting remain authoritative.
`outlineNodeId` chooses a parent; `afterSection`/`sectionLimit` page its children.
`nodeIds` selects subtrees; `nodeScope: section` excludes authored subsections.
Token estimates sum projected source atoms and explicitly exclude response/selector
overhead; they are not provider usage or fit guarantees. Final budgets count actual
emitted handles. Outlines contain authored titles and coordinate labels, not summaries.

New passages record containing-source-atom identity at publication. Search labels
the mapping as recorded; expansion checks its complete location against the
canonical immutable atom. Conflicting pointers/coordinates refuse. Normalized
chunks/heading prefixes have no claimed exact passage offsets. Missing mappings
require outlines. Microsoft passage locations and source-only repair remain
unchanged, including their unavailable direct passage-to-cell expansion.

## Ingestion changes

Choose `custom-granular-v1` in Admin Processing or library creation. The Python
provider and structural enrichment remain separate. Existing recipes/defaults,
Microsoft workflow, SQL, vector settings and context accounting stay unchanged.
Generated summaries and native visuals are outside this bounded profile.

The new recipe intentionally changes passages: the existing sentence chunker runs
separately per paragraph/block, targeting 500 tokens with up to 50 tokens of
within-atom overlap and the existing bounded heading prefix. It never crosses
atoms. Empty atoms stay readable without standalone vectors. Changed passages
require new embeddings using the same model/dimensions. More granularity may mean
more vectors and ingestion cost; no cost/recall improvement is claimed. Existing
worker measurements remain the measurement path.

Upload originals into a library using the new profile to obtain its capabilities.
This requires new ingestion/embedding; existing artifacts are not rewritten.
Old snapshots/citations keep their stored representation. Python retries preserve
immutable publication and avoid duplicate chunks but may re-embed; no embedding
cache guarantee is added.

## Local validation and live follow-through

The subsequent [bounded live result](granular-navigation-live-qualification.md)
records all ten mechanics gates passed at `821ea25`, six actual model turns and
verified isolated cleanup. All six preserved core source facts/human citations;
complete-cell selection and internal-ID reporting require two targeted model
checks after a local guidance mitigation. The subsequent
[two-turn follow-up](granular-navigation-followup.md) demonstrates those core
behaviors, while failing the additional frozen outline-navigation/identifier-
omission criteria. Integration remains on hold for the specific outline-inspection
contract issue, with response overhead retained as the next presentation-efficiency
work. The preparation and local evidence below are retained as prior checkpoints.

The [validation receipt](../records/granular-source-navigation-20261104.json)
records actual local checks and review corrections. API/client and scripted-agent
tests cover model-facing responses, qualifiers, mappings, Markdown, malformed
artifacts, stale selectors, permissions/lifecycle races, budgets, retry,
replacement and deletion. They establish mechanics, not autonomous model quality
or distributed Azure behavior.

Final local validation passed **1,613 tests**, lint, formatting and reference
privacy checks (14 existing test-client deprecation warnings). The 120 new focused
checks comprise 58 source, 45 API/scripted-agent and 17 qualification-helper cases.
All seven existing valid recipe variants and four existing tool bundles were
compared with the integrated baseline and remain identical. The normal answering
adapter, query policy, original source-repair implementation and frozen experiment
controls/receipts remain unchanged by this incremental implementation.

Prepare fresh private controls after source changes settle:

```sh
sh scripts/dev setup
sh scripts/dev check -q
.venv/bin/python -m scripts.qualify_granular_navigation prepare --output "$PRIVATE_QUALIFICATION"
.venv/bin/python -m scripts.qualify_granular_navigation verify --state "$PRIVATE_QUALIFICATION"
.venv/bin/python -m scripts.qualify_granular_navigation qualify-local --state "$PRIVATE_QUALIFICATION"
```

The helper freezes candidate/profile hashes, three small authored mechanics
fixtures, private response captures and a live plan. It rejects reuse, drift and
`--live`: this local helper does not execute cloud mutations. The separate reviewed
`scripts/granular_live.py` runner and bounded continuation subsequently completed
the linked live qualification. Fake-service
RU/provider usage remains unavailable. These fixtures do not consume the larger
confirmation corpus.

The [prepared plan receipt](../records/granular-navigation-preparation-20261104.json)
and [local qualification receipt](../records/granular-navigation-local-qualification-20261104.json)
bind 35 captured tool replies and successful in-memory cleanup. On that small
fixture, actual compact evidence JSON measured 1,602 local tokens for one paragraph,
3,573 for its four-paragraph cell, 2,082 for a section's own two atoms, 8,838 for
its 13-atom subtree and 9,881 for the 15-atom document (`cl100k_base`). These include
handles/metadata and show substantial remaining envelope overhead. They are not
provider tokens, model-quality results, latency or billing. Broader long-document
fit and presentation efficiency remain unqualified.

The prepared live gate required announcing the cloud window under existing isolated-resource
authorization and verifying identity, resource policies/incarnation, current health,
deployment versions and protected scopes. Prior temporary models/evaluation scope
were deleted; old receipts do not prove present availability. New containers require
the repository canary. Access/capacity checks and the announced live window are
now recorded in the linked follow-through; none ran during the local preparation.

The plan caps work at three libraries, four documents, five ingestion generations,
64 chunks, 50k source embedding tokens and 20 logical embedding calls. A separately
metered model gate permits six turns, 36 logical calls/48 attempts, common 200k
context/8k output allowances, and 2M input/200k output reservation ceilings. Keep
reservations, unknown failed-attempt usage and measured provider counters separate.
Execution/cleanup windows are 30/10 minutes.

The no-deployment runner composes candidate API/worker code locally with
real Cosmos/Blob/embedding services and an exact-owned private message ledger.
Review write-ahead ownership and cleanup before use. Then qualify immutable
publication, independent-client races, navigation and actual model answers/citations;
archive and verify owned absence twice, 60 seconds apart. Preserve shared queues
and protected scopes. Hosted execution, broker redelivery, production auth,
broader scale/concurrency and the known scheduled orphan-container defect remain
separate gates. The bounded cloud calls are now complete; application deployment,
main merge and the larger study have not occurred.
