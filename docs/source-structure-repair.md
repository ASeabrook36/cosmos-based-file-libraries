# Opt-in source-structure repair

The new `microsoft-indexer-source-fidelity-v1` profile repairs the source-reading
representation while retaining Microsoft's existing passage text, vectors, chunk
locations, workflow, embeddings and query policy. It is an explicit opt-in. Existing
profiles and defaults retain their prior behavior. No application was deployed,
no cloud/model calls were made, and the larger confirmation study remains unlaunched.

This is separate from the context-accounting repair. The completed comparison and
model pilot remain frozen at their original commits; their results are not rescored
or attributed to this repair.

## Behavior and trust boundary

The adapter validates the original DOCX and the source/job-bound archived provider
result before the normal immutable generation plan is written. It checks declared
Unicode-codepoint or UTF-16 span units, exact addressed paragraph text, bounds,
non-overlap, global order, table dimensions, cell coordinates/merge spans, and a
one-to-one ordered original-to-provider mapping. Original blank paragraphs are
explicit; nonempty extra or missing paragraphs fail closed. Unannotated provider
words and unrecognized residual markup also fail closed.

The canonical source now has granular main-body paragraph and table-cell sections.
Original text/run order, blank cells, horizontal/vertical merges, row-header flags,
and paragraph/list membership/depth are retained. A qualifier outside a list remains
a separate paragraph sibling; no legal or semantic interpretation is generated.
The XML-like tags in model evidence are explicitly labelled structural annotations,
and source words are escaped inside them. Source-coordinate citations are distinct
from printed clause numbers. Stored table/row/column coordinates are zero-based;
display citations use one-based source table/row/column positions.

Numbering instance/abstract definitions, qualified XML attributes, restarts and
overrides are retained in a hash-referenced audit catalog inside the same source
artifact. Effective direct/style-linked properties are preserved; `numId=0`
suppresses inherited numbering. An unresolved style-linked level stays unknown.
**Printed ordinal markers are not invented or evaluated.** Inherited default/table
style numbering that this implementation cannot establish is rejected. Cached
fields retain their visible text and field properties, without field evaluation.
Unsupported revisions, footnotes/endnotes, text boxes, nested tables and other
unsupported source constructs fail explicitly. The profile accepts DOCX only and
rejects other formats before provider dispatch.

This is main-body structural fidelity, not complete original-document fidelity or
the proposed full custom authored hierarchy. Known provider-only page headers,
footers and page numbers retain their exact spans/order but carry an explicit
unavailable original-region mapping. Visual fidelity remains unavailable. The
`complete` read flag still refers to the complete versioned text representation,
not all original Office capabilities.

## Publication, reads and lifecycle

All rendering and rich audit metadata live in the existing generation-scoped
`sections.json`, protected by its SHA-256 descriptor and the normal create-only
artifact conflict check. There is no independently mutable sidecar. Reads validate
the embedded source hash, job/profile identity, active generation, numbering catalog
and rendered-source/coordinate consistency before selecting evidence. A missing
descriptor, stale generation, wrong/missing coverage schema, missing/corrupt blob
or mismatched internal binding refuses the read; this profile never falls back to
flattened legacy chunks.

Existing permissions and post-I/O generation/content-revision/metadata/library
checks remain authoritative. Retagging does not invalidate immutable source text.
Pending replacement preserves the old active source until the new generation
commits; retry, late completion and deletion use the existing conditional lifecycle.
This repair does not resolve the known scheduled-orphan cleanup defect.

Complete and selected reads preserve source text and structure in raw, compact and
reference views. Compact/reference evidence excludes duplicate raw runs and complete
numbering definitions; those remain in the bound audit artifact. Document-level
source hash, schema, scope and limitations accompany model evidence. List identity,
level, unresolved-marker state, paragraph boundaries and cell coordinates remain
visible in the rendered source.

Microsoft passage `location` values remain unchanged. Consequently, direct
`evidenceId` passage-to-source expansion still returns `source_scope_unavailable`.
The qualified route is **search → document outline → pinned section selection**.
No duplicate-text search or inferred passage-to-cell mapping is introduced.

The source artifact can be much larger than its model projection. `fullText.tokenCount`
continues to count the rich stored artifact; new `compactSectionsTokenCount` and
`compactSectionsEstimateScope: projected_sections_only` fields explicitly describe
the model-section estimate. Compact reads/search hints use this distinct estimate;
it excludes response/tool envelopes and is not provider usage or a fit guarantee.
Full-response all-or-nothing budgeting remains authoritative.

## Retained-source qualification

The [sanitized receipt](../records/source-fidelity-qualification-20261104.json)
re-runs the original loss probe against immutable archived bytes, then applies the
new adapter to a deep-copied provider envelope with a **counterfactual local profile
and recomputed execution ID**. It does not claim the provider emitted the new recipe.
Provider-facing controls, all archived passage text/locations and vectors are exact
before/after matches. No source or query embeddings are generated.

For the consumed `hq-05` original, the adapter preserves 682 main-body paragraphs,
474 table cells and five distinctly labelled provider-only paragraphs. The known
`table 0 / row 90 / column 1` region retains all ten original paragraphs, eight with
numbering properties, followed by the final separate unnumbered qualifier. The
old flattened artifact still reproduces the original failure. An independent
ZIP/XML review compares all original paragraph/run text, cell coordinates, the
target list properties and all 47 numbering definitions; its receipt is retained
privately by the coordinator. These are structural regressions, not answer-quality
or semantic-qualifier judgments.

Measured sizes use `cl100k_base`, actual repaired sections and a fixed generic
envelope. They are local serialization counts, not live provider context or billing:

| Scope | Rich artifact | Projected sections only | Raw envelope | Compact envelope | References envelope |
| --- | ---: | ---: | ---: | ---: | ---: |
| Whole retained source | 368,935 | 76,052 | 409,908 | 86,603 | 78,160 |
| Known qualifier cell | 2,179 | 553 | 2,870 | 876 | 892 |

The complete file still exceeds the pilot's 64k operating context; granular source
selection is necessary at that setting. Raw full reads can exceed the service's
200k response ceiling, while compact full reads fit that service ceiling. A future
study must freeze an adequate common model context and account for complete tool
envelopes. No efficiency gain or model correctness gain is inferred from these sizes.

## Executable local checks

Use a fresh worktree environment; setup installs only local dependencies and does
not provision services or source cloud credentials:

```sh
sh scripts/dev setup
sh scripts/dev test -q tests/test_source_fidelity.py tests/test_source_fidelity_lifecycle.py tests/test_qualify_source_fidelity.py
sh scripts/dev lint
sh scripts/dev privacy --deny-file "$PRIVATE_DENY_FILE"
```

The added suite has 68 passing cases: positive/negative source mapping, Unicode
units, qualifiers inside/outside lists, repeated paragraphs, split/hyperlink/content
control runs, blank/merged cells, inherited/suppressed numbering, restarts/overrides,
namespace preservation, fields, exact projections, missing/corrupt bindings,
personal-library denial, stale selectors, oversized multi-document batches,
replacement/metadata/deletion races, retry after artifact write, and unchanged
provider rows/query semantics. The existing 25 modular-ingestion cases also pass.
Full-repository validation is coordinated separately; frozen comparison harnesses
must continue rejecting changed measured code.

Reproduce the retained-source qualification without network/model calls. Inputs
must be the hash-bound private envelope from the earlier reproduction; output must
be a new file outside Git:

```sh
.venv/bin/python -m scripts.qualify_source_fidelity \
  --inputs "$RETAINED_PROBE_INPUTS" \
  --output "$PRIVATE_RUNTIME/new-qualification.json" \
  > "$PRIVATE_RUNTIME/new-qualification-public.json"
```

For a later explicitly scheduled isolated live gate, create a library with
`ingestionProfile: microsoft-indexer-source-fidelity-v1`, ingest supported originals,
and use the ordinary authenticated search/outline/selected/full endpoints. Before
claiming live validation, verify actual provider-version/source binding, immutable
publication, metadata/replacement/retry/deletion races and owned cleanup against
Azure. No live resource or spending requirement was incurred in this repair.

## Remaining gates

Live distributed publication/consistency and worker/provider behavior are untested
for this new profile. Broader DOCX coverage, printed numbering labels, original
header/footnote/visual fidelity, direct passage-to-cell expansion, complete custom
hierarchy and model interpretation of the repaired representation remain open.
The larger study must decide whether its citation tasks require a deterministic
numbering renderer, validate context accounting separately, and freeze new sources,
questions and controls before launch. None of the proposed 40 new documents or
180 new questions was selected, authored or consumed here.
