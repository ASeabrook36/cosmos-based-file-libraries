# Source fidelity and larger confirmation proposal

This is an offline follow-up to completed commit
`8f7d436987724abac1efe633d071db66f8cbf4ea`. It reproduces the known structure loss
and prepares a study; **it does not repair or deploy the application, change the
current client/model comparisons, or authorize the larger study**. The earlier
48 questions are consumed development/regression evidence.

## What the completed result actually exercised

The completed hybrid study used improved selector handles and compact evidence,
but its `microsoft-indexer-layout-v1` recipe stored the full provider text as one
canonical section per original. Its roughly 5,000-token search windows differ
from the custom strategy's 500-token target/50-token overlap. An outline or selected
read of that sole section does not establish successful fine paragraph/chapter
navigation. The recorded full/selected distinction remains valid, but should not
be read as a demonstration of the complete custom document hierarchy.

The current `custom-structured-v1` supports authored DOCX Heading 1–9 and Markdown
ATX hierarchy. It is not already a universal fidelity fix: its DOCX adapter stores
an entire table as rows of `cell.text` joined with separators. It does not retain
an explicit table/list block graph or automatic numbering properties. Main-body
paragraph/table support does not include headers, footnotes, text boxes, revision
interpretation or arbitrary visual heading inference. See [modular ingestion](modular-ingestion.md).

## Reproduction: the raw provider retained useful structure

The [sanitized offline receipt](../records/source-structure-reproduction-20261104.json)
binds the exact original and archived extraction/result/sections hashes. The two
retained before/after source archives were byte-identical for the three provider
artifacts inspected. No service or model was called.

For original region `hq-05:t0:r90:c1`, used by question hq-40:

| Layer | Observed representation |
| --- | --- |
| Original DOCX table cell | Ten ordered paragraphs: eight have numbering properties, two do not. The final qualifier is a separate unnumbered paragraph after the last numbered item. |
| Archived Document Intelligence annotations | All ten paragraphs survive individually and in order; each span addresses its exact text in the provider content. Each maps to its original paragraph under the already-frozen whitespace normalization. |
| Provider Markdown/HTML cell | All normalized words survive, but there are zero internal paragraph, break or list tags. The paragraph boundaries become ordinary spaces. |
| Workflow result and published canonical section | Both contain exactly that same flattened provider content. The one published section has no authored node ID. |

The normalized cell digest remains
`e25afd76ed96eadad2c0890672a3629cfe0392afeb89de64e988b95a0239ecc6`.
This shows why word matching alone missed the problem. It also narrows the
failure location: the raw provider annotations retained paragraph boundaries that
the published evidence did not expose. It would be inaccurate to say that every
layer of the Document Intelligence result lost every boundary.

The original paragraphs carry two numbering IDs and levels 0 and 4. Provider
paragraph entries do not expose corresponding list roles. Paragraph boundaries
are recoverable from the retained annotations; complete numbering/list semantics
need source-bound OOXML metadata or an explicit unsupported/unknown status.
The probe does not infer which legal qualifier applies to which clause. That
source-based judgment remains the independently adjudicated result of the completed
study, with its recorded lenient sensitivity preserved.

The separate hq-01 failures were wrong clause-number citations. They do not,
by themselves, establish whether extraction, tool presentation or the answering
model caused the error. Reproduce and trace those failures separately before
claiming that a table-boundary repair fixes them.

## Narrow repair to qualify before a larger study

Prepare a new opt-in versioned source representation; keep existing recipes and
archived results immutable. The first repair should preserve source structure at
the provider-to-canonical-source mapping, independently of query vectors:

1. Retain the hash-bound layout paragraph/table annotations in the generation
   plan, including their declared string-index units. Verify bounds, order,
   non-overlap, cell membership and exact addressed text before using them.
2. Map table cell paragraphs into explicit ordered source blocks. Preserve row,
   column, blank cells, merged-cell spans, paragraph boundaries and table headers.
   The last unnumbered paragraph stays a separate sibling after the numbered
   items. Do not append a generated explanation of what the qualifier means.
3. For supported DOCX, bind those blocks one-to-one to original OOXML coordinates.
   Preserve effective `numId`/level and numbering definitions/overrides, source
   paragraph order, and original run text. Render list markers only when the
   ordinal can be resolved deterministically from the actual numbering model;
   otherwise expose that limitation and retain the underlying properties. Never
   infer nesting from the number of spaces or guess clause numbers from neighbors.
4. Carry trusted source coordinates and source/generation/content-revision
   identity through complete, selected and search-expanded evidence. A complete
   section must not be mislabeled a complete document. Missing or ambiguous
   mapping is a declared capability gap, not silent complete-region credit.
5. Initially qualify the source-reading repair against the existing frozen
   passage vectors and rankings. If new rendered passage text later changes
   embeddings, that becomes a separately versioned representation experiment.
   Do not attribute that second change to the SDK or budget model.

This is a proposed implementation boundary, not a claim of a completed fix.
`validate_microsoft` currently selects `analyzeResult.content` for its canonical
section and deliberately discards untrusted row locations. The repair must replace
that omission with validated source mapping, never blindly trust provider IDs,
locations, library scope or generation. Use the existing conditional publisher,
immutable plans, permissions, metadata and deletion/retry paths.

Acceptance before any larger confirmation:

- Reproduce hq-40 from original bytes and the retained provider annotations; verify
  every ordered paragraph and numbering association independently, then inspect
  the rendered table against the original. Keep the old failure receipt intact.
- Synthetic positive/negative fixtures must cover a qualifier outside a nested
  list, a qualifier inside a list item, split runs/content controls, blank and
  merged cells, a repeated identical paragraph, numbering restarts/overrides,
  inherited numbering, Unicode offsets, malformed or crossing spans, and ambiguous
  original-to-provider mappings. A plausible-looking reconstruction is insufficient.
- Every accepted block must have original provenance, unchanged text/order and
  generation binding. Reject stale selectors, foreign personal-library access,
  missing/corrupt source artifacts and oversized all-or-nothing selections.
- Validate replacement, metadata races, retry/reuse, deletion and late provider
  output through the existing lifecycle. The known scheduled-orphan defect is
  still open; a source-format repair does not fix it.

The committed [probe](../scripts/source_structure_probe.py) and its synthetic
regressions establish the observed loss and fail-closed input mapping. They do not
implement a renderer, numbering engine or the full acceptance suite above.

## Recommended larger study: 40 documents, 180 fresh questions

Use **40 authentic, new documents**: 30 evidence-bearing sources and 10 realistic
near-duplicate/topic distractors, across five domain families of eight documents.
Target 28 DOCX and 12 Markdown sources, both within the authored-hierarchy adapter's
explicit scope. Candidate domains are procurement/contract terms, operational
policy, technical/reference documentation, investigation/regulatory reports and
procedural/educational guidance. Verify availability, licensing, original bytes and
actual authored structure before selecting the final inventory. No source or fresh
question has been selected or authored by this proposal.

Coverage minima overlap; they are not separate document counts:

- Sixteen documents above 20k source tokens, including six above the proposed 64k
  context budget, so full-file reading cannot make navigation trivial.
- Twenty-four documents with at least four authored heading nodes; twelve with
  three heading levels. Keep authentic headings and formatting; do not retrofit
  artificial structure to make the adapter pass.
- Sixteen with tables, ten with nested lists/qualifiers, eight with merged cells
  and six with blank form fields. Include similar terms with different scope and
  version/date attribution across source files.

This primary study is not a PDF/image, spreadsheet or arbitrary Office-fidelity
claim. Those need a separately specified capability suite. Preserve their exclusion
in the decision rather than treating missing support as success. If authentic
source selection cannot meet the coverage minima inside 40 files, adjust within
30–50 before freeze and recompute review/cost totals; do not pad sources.

The 180 primary task strata partition the questions:

| Primary task family | Questions |
| --- | ---: |
| Precise fact / identifier | 36 |
| Exception / qualifier | 36 |
| Selective paragraph / complete section | 30 |
| Cross-document attribution | 30 |
| Table / form relationship | 18 |
| Missing answer / actual value | 30 |
| Total | 180 |

Among the 150 answerable tasks, target 54 exact-wording, 66 paraphrased and 30 mixed
or neutral questions. These wording tags cross the primary strata. At least 60
questions should have decisive evidence fitting within roughly 12k tokens while
the containing document exceeds 48k, creating a genuine granular-navigation
opportunity. Missing-answer questions require verified absence within the frozen
library and explicit abstention criteria, not an empty expected answer string.
Keep question clusters with repeated facts/documents identifiable; they are not
independent samples merely because their wording differs.

Gold must bind required original regions and relationships, qualifiers, table
associations, answer components and citation locations before any measured
retrieval. Independent original-source review precedes the freeze. Keep raw
sources/gold private; the answering model receives only the question and normal
library/tools, never family labels or expected regions. The old eight-document
pack stays entirely outside fresh confirmation.

## Representation and four attributable cells

The primary candidate is **proposed `custom-structured-v2-source-fidelity`**:
authored heading ancestry plus source-preserving paragraph/list/table blocks and
explicit original locations. It must pass the repair qualification above first.
Retain the existing custom passage target/overlap recipe, measure the actual
chunk distribution, and freeze one set of passage texts/vectors. No generated
summary vectors or new physical layout are required.

Compare these four cells on that identical extraction, source block inventory and
passage/vector set:

| Navigation view | Vector | Hybrid |
| --- | --- | --- |
| Flat block navigation | Cell A | Cell B |
| Authored hierarchy and subtree navigation | Cell C | Cell D |

The flat view retains the exact same words, source blocks, passage search,
original locations and ordinary block selectors, but does not expose authored
parent/child traversal or subtree selection. The hierarchical view exposes those
relations and subtree reads. Both advertise truthful capabilities through matched
tool contracts; neither gets invented summaries or better source text. Keep
source labels/selector metadata differences limited to the declared navigation
factor. Direct retrieval results must be identical across navigation views for
the same search mode before answering can proceed.

This is **not** a Microsoft-versus-custom complete-strategy comparison. The former
one-canonical-section Microsoft run remains a historical reference, not a fifth
matched cell. Comparing it directly with a custom hierarchy would also change
extraction, passage boundaries and embedding inputs. Such a provider comparison
needs a separate design after this capability contract is chosen.

Freeze one primary answering model after reviewing this session's matched
Sol/mini pilot, and pin its actual version. Keep the selected client's version,
query/routing controls, embedding model, context, instructions, tools and output/
attempt limits common across all four cells. The provisional context is 64k;
any change before freeze must apply to every cell and be justified by supported
capacity and actual reserve requirements. Context refusals remain a distinct
outcome, not an index-quality failure. A second model across the full study would
double the matrix and needs explicit scope/cost authorization.

The completed [matched pilot](hybrid-client-model-pilot-results.md) provisionally
favors Sol `gpt-6.1-sol/2026-09-29` as that primary model: the descriptive joint
completion/support/citation/relationship count was 36/38 of 40 for vector/hybrid,
versus mini's 26/29. This is a choice for reliable measurement, not a general model
ranking. The pilot's 64k setting was a matched operating point, not demonstrated
adequacy: 17 mini turns and one Sol turn stopped at the local guard. Five of mini's
nine answerable hybrid wins avoided vector-arm context stops. Qualify actual
source-read and enclosing tool-message budgets separately before freezing the
new study; do not attribute a simultaneous budget fix to source hierarchy.

Use one full balanced, serialized pass: **720 answer turns**. Preselect a
stratified 60-question repeat subset before answering for **240 additional turns**,
reversing/balancing order. The primary analysis uses the first pass only; repeated
cells are a separate robustness analysis. Total: **960 turns, 180 unique questions**.
This avoids counting repeats as new evidence and limits review work while checking
stochastic sensitivity. Never rerun only failed cases or pick repeat cases from
observed outcomes.

Measure source recall initially and after expansion, then final task completion,
factual support, citations, relationships, abstention and complete-region delivery.
Report successful and failed navigation, recovery searches, whole-file fallbacks,
selection size/coverage and budget refusals. Track actual node/subtree access so
fine navigation is demonstrated rather than inferred from tool availability.
Report paired question counts and domain/source-cluster sensitivity, with overlapping
secondary tags explicit. Do not treat 180 purposeful questions as a guaranteed
statistical power calculation or a general enterprise accuracy estimate.

## Review effort, usage scenarios and launch gates

The [machine-readable proposal](testing/source-confirmation-proposal/plan.json)
and [offline calculator](../scripts/plan_source_confirmation.py) make the counts
and assumptions executable. For 960 answer turns, illustrative cumulative model
input/output scenarios are 38.4M/0.48M, 96M/0.96M and 192M/1.92M tokens. Cumulative
input sums repeated calls; it can exceed the per-call context budget.

Using the coordinator's provisional uncached public-list-price assumptions, answer
model token charges alone are:

| Primary model | Low | Central | High |
| --- | ---: | ---: | ---: |
| Sol | $81.60 | $201.60 | $403.20 |
| Mini | $30.96 | $76.32 | $152.64 |

The coordinator checked the [Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
and [mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) official model
pages on 2026-11-04. The uncached input/output assumptions per million tokens are
$2/$10 and $0.75/$4.50, respectively.

These are alternatives for one primary model, not measured Azure bills or a launch
budget. They omit reviewer-model calls, failed-request usage, embeddings, database
reads/writes/storage, Blob operations, extraction/workflow and standing hosting.
Cached usage must be accounted separately from uncached tokens when actual meters
exist. Replace the per-turn assumptions with this session's completed model-pilot
usage and verify the actual regional Azure tariff/quota before approval. Source
and query embedding tokens, actual chunk counts and RU must be forecast from the
new frozen corpus, not extrapolated linearly from 36 old chunks. A true total is
unavailable until these items have rates and quantities; keep known subtotals.

The [pilot calibration](../records/source-confirmation-pilot-calibration-20261104.json)
now adds an observed-workload reference while retaining the conservative scenario
range above. Across 96 turns per model, known successful calls averaged 89,667 input /
464 output tokens for Sol and 165,292 input /1,755 output for mini. Cached input was
65.2% and 74.8% respectively; mini also had 113 failed 429 attempts with unknown usage.
Scaling only those observed successful-token quantities to 960 turns gives:

| Same-workload calibration | Observed cache fraction | No cached-input discount |
| --- | ---: | ---: |
| Sol | $69.98 | $176.61 |
| Mini | $46.52 | $126.59 |

These are public-list-price illustrations, not a new budget or prediction for the
fresh corpus. Repeated questions over eight sources can exaggerate cache reuse;
40 new longer documents, source-preserving blocks, different navigation and a
different question distribution can change token and retry costs. Keep 40 sources /
180 questions as the provisional coverage/review compromise; the pilot does not
justify expanding it or freezing a total spend ceiling before source selection.
No new document or confirmation question was consumed to make this calibration.

Planning manual-equivalent review is roughly **144–201 hours**: 12 minutes to author
and six minutes to independently validate each gold question; 3–6 minutes per
answer turn; at least 20% additional independent answer review; a 12-hour
adjudication allowance; and 20 hours for source audit/reporting. All failures and
disagreements require independent review even if that exceeds 20% or the allowance.
Assistant review may reduce elapsed time but is not a fully blinded human study;
freeze the actual human audit, reviewer independence and adjudication policy before
launch. The fixed 60-question repeat saves 480 answer reviews compared with two
full passes, while preserving a declared robustness sample.

That total separates into 54 hours for gold authoring/validation, 58–115 hours for
960 primary/repeat answer reviews plus 192 additional independent reviews, and
32 hours for adjudication/source audit/reporting. These are planning assumptions,
not measured reviewer productivity or a requirement to spend that many human hours.

Before launch, name the new implementation baseline explicitly, qualify the source
repair, freeze authentic sources and untouched fresh gold, bind the client/model
and resource limits, review full cost ceilings, validate permissions/lifecycle and
owned cleanup, and reserve non-overlapping cloud windows. Existing R1 fan-out and
split-library/ANN/concurrency questions remain separate. No R3 starts from this
proposal. The larger study is not authorized by the current bounded pilot request.

## Local commands and handoff

```sh
sh scripts/dev setup
sh scripts/dev test -q tests/test_source_structure_probe.py
sh scripts/dev lint
.venv/bin/python -m scripts.source_structure_probe \
  --inputs "$STRUCTURE_PRIVATE/probe-inputs.json" \
  --output "$STRUCTURE_PRIVATE/new-probe-private.json"
.venv/bin/python -m scripts.plan_source_confirmation \
  --plan docs/testing/source-confirmation-proposal/plan.json
```

The private input envelope binds original/extraction/result/sections files by
SHA-256, public source label, table coordinates and frozen normalized region hash.
It must refer outside every Git checkout. The probe writes exclusive private raw
review detail and emits only its sanitized receipt to stdout. The calculator
has no execution path, credentials or cloud dependency. Neither command authors
new confirmation questions or consumes held-out evidence.
