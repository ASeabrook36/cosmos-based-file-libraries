# Two-turn granular navigation follow-up

## Result and integration recommendation

**Both original core behavior checks succeeded in this sample, but the complete
frozen follow-up did not pass. Hold unconditional integration sign-off for the
remaining outline-inspection contract defect.** Source fidelity, complete-cell
selection, exact identifier values and human citations worked. The unresolved
behavior is two ineffective outline inspections followed by a whole-document
read; a correct final answer does not demonstrate the requested granular route.

The [public receipt](../records/granular-navigation-followup-20261104.json) records
both core successes and the additional frozen failures without changing the
earlier results. One cloud execution at `753d8b2` completed exactly two turns.
All product runtime and container/lock-file bytes matched candidate `83168a8`;
only qualification tooling was added. No model rerun, runtime repair, main merge,
deployment or larger study occurred. Context accounting, index versioning and the
Microsoft provider decision remain separate.

| Check | Actual model behavior and final answer | Assessment |
| --- | --- | --- |
| Complete action cell | Search, selected condition paragraph, then selected the action-cell node; the next successful model call received exactly four source paragraphs with cell-scoped `selectionComplete=true`. The answer retained both list items, the ordinary instruction and separate qualifier, with correct table/cell/paragraph citations. | Original core gap closed in this turn |
| Explicit identifier fields | Final JSON copied `sectionId`, `containingSectionId` and `citation` exactly from the same returned rule paragraph. The explanation distinguished the paragraph's evidence identity from its authored containing section. Rendered page and authored chapter coordinates were correctly disclosed as unavailable. | Original core gap closed in this turn |
| Authored-outline navigation | Two `mode=outline, includeText=false` calls returned `disposition=inspected` and **zero outline entries**. The model then requested all 15 document atoms. | Frozen outline-before-source route failed; full-source recovery succeeded |
| Omit unrequested identifiers | Cell answer included accurate Word numbering metadata (`numId` and level), while omitting application evidence/node IDs. | Literal frozen omission clause failed; source/citation accuracy did not fail |

The model constructed the target cell node ID from table coordinates; that ID
was not present in its earlier model-visible replies. The request was correctly
pinned and the returned cell was verified, but this does not demonstrate copying
an exposed cell selector. No printed list numbers were invented. The unnecessary
OOXML detail remains a presentation issue, distinct from the corrected internal
section-field confusion.

### Concrete remaining defect and next action

The ordinary read-tool description explains that `includeText=false` inspects
response size only. The granular profile replaces that description and omits
this explanation; the flag's schema is an undescribed boolean. Consequently the
model-facing contract does not make clear that **outline entries also require
`includeText=true` or the default**. The observed two empty inspections demonstrate
the ineffective route. The omission is a plausible contributor, not a proven
isolated causal explanation of a stochastic model choice. Full-file fallback on
this tiny fixture masks the lack of successful outline acquisition.

Next, clarify this flag in the granular description/schema and verify the actual
model-facing inspection-versus-outline responses locally. Then run one separately
bounded real-model outline task, requiring nonempty returned authored entries and
a focused source read. Preserve these two core successes; do not rerun consumed
cases selectively or broaden to provider/search/index changes. The current two-
turn allowance is consumed. No new product guidance was added after this run.

The evaluation helper also had an overly permissive observation: it counted any
outline request as `outlineBeforeSourceRead`, even without returned entries.
Independent manual review caught the error and marked the route failed before
changing the helper. Post-run commit `6159ec3` requires nonempty delivered outline
entries and adds an actual inspection-to-full-read regression. Offline reanalysis
correctly reports `false`, while complete-cell delivery and exact reported IDs
remain `true`. The frozen original observation, manual override and raw evidence
are retained; this is a local evaluator correction, not a model rerun or product fix.

### Usage, overhead and cleanup

| Measured counter | Follow-up observation |
| --- | ---: |
| Model calls / physical responses | 8 / 8 |
| Provider input / output tokens | 77,268 / 4,436 |
| Cached input / reasoning output, included in totals above | 28,032 / 3,200 |
| Embedding requests / prompt tokens | 2 / 102: 98 ingestion, 4 query |
| Chunk-write RU | 1,007.45 for 15 chunks |
| Instrumented agent tool RU | 42.80 across 6 operations |

There were no failed model/embedding attempts, retries, throttles, unknown usage,
context refusals or pacing refusals. Exact model version/SKU/endpoint bindings
matched the preceding run before and after execution. The current protected
census was unchanged at both absence checks. Monetary cost and complete Azure
charges remain unavailable: the RU counters exclude preflight, metadata checks,
cleanup, other Cosmos calls, Blob and storage.

For the six unique tool replies, local `cl100k_base` estimates were 26,613 evidence-
JSON tokens, 725 rendered-source tokens, 10,600 selector-removal delta tokens and
15,127 payload tokens with source and selectors removed. Whole tool-message JSON
was 28,507 tokens; the 12 repeated exposures in subsequent requests totaled 52,803.
These components are **non-additive** and are not provider usage or billing.
Together with the earlier six-turn measurements, they retain response-envelope,
selector and unnecessary source-metadata overhead as the next presentation-
efficiency issue. No representation compression or context-accounting change was
bundled into this follow-up.

Normal owned lifecycle cleanup passed. Independent checks **65.31 seconds apart**
each verified one library 404, one document 404, 15 chunk 404s, two empty scoped
queries and one empty Blob prefix. All 14 protected libraries and 48 documents
were unchanged; the final 179-event ledger has no unresolved mutations or pending
messages. All **999 files** in the previous private evidence directory were
rehashed unchanged. Soft-delete retention/backups and indefinitely late external
writers remain outside current-item absence proof.

The live runner passed **194 focused local tests** before execution. The later
helper correction passed **78 focused tests**, lint, formatting and privacy checks;
it changed no runtime file. The earlier 1,730-test live-baseline result is retained,
not presented as a new full-suite run. Private archives contain source publications,
actual model inputs/answers, provider attempts, both cleanup checks and independent
source/mechanics/measurement reviews. Public receipts contain sanitized counts,
assessments and hashes only. Hosted execution, production authentication, broker
redelivery, large-document/scale behavior and general answer quality remain untested
by this small no-deployment follow-up.

## Preserved preparation contract

This separately authorized qualification closes the two model-behavior gaps in
the [completed bounded run](granular-navigation-live-qualification.md). It starts
from `83168a8`, with product runtime byte-identical to that candidate. The original
six turns, failed first attempt, successful continuation, usage and cleanup
evidence remain immutable. Context-accounting repair, index versioning, the
Microsoft provider decision and larger confirmation remain separate.

## Frozen checks

1. Repeat the earlier cell question. Inspect an actual model-selected cell node,
   returned cell-scoped completeness and the exact four source paragraphs.
   The answer must preserve two list items and the separate ordinary qualifier,
   avoid invented printed list numbers and cite the correct table/cell source.
   Bulk paragraph handles alone do not pass complete-cell navigation.
2. Ask for the damaged-vessel rule through its current authored outline and
   explicitly request a JSON object containing `sectionId`, `containingSectionId`
   and `citation`. Compare those values with the same paragraph actually returned
   to the model, and independently review the explanation and unavailable page/
   chapter disclosure. Correct copied fields do not excuse contradictory prose.

Only the question and current document pin enter the agent turn. Source-derived
expected IDs and review criteria remain private. Automated observations establish
actual calls/delivery/field equality; final answers require independent semantic
review. This repeats consumed mechanics sources and is not a fresh benchmark or
a causal comparison proving that wording alone changes model behavior.

## Bounds and controls

The runner creates at most one new shared library, one DOCX document, one generation
and 16 chunks on existing isolated resources. It uploads the exact original DOCX
bytes, including its original ZIP metadata. Source publication is verified before
answering. The reviewed ownership ledger, conditional cleanup, private queue and
independent-client absence checks are reused; shared queues are never accessed.

Fresh preflight and before/after deployment checks must match the preceding
GPT-5-mini `2025-08-07` and embedding version/SKU/binding exactly. Per-turn controls
remain vector search, compact responses, selector handles, legacy context
accounting, 200k context, 8k output, medium reasoning, 12 tool rounds and the
45k-per-minute local pacing reservation (37k serialized input ceiling).

Exactly two fresh turns are allowed, with 26 logical calls (including a possible
final-only call after 12 rounds per turn), 36 physical attempts, 2M input and 200k
output including unknown-attempt reservations. Embedding retains the reviewed
20-logical-call, three-attempt and 50k-local-input-token ceilings, shared across
ingestion and search. Execution and cleanup are capped at 20 and 10 minutes.
No automatic rerun, altered question, raised ceiling or selective replacement is
permitted after a turn starts.

Writers must quiesce before evidence archival and normal owned lifecycle cleanup.
Exact owned point absence, empty scoped queries and empty Blob prefixes are
verified twice at least 60 seconds apart; protected snapshots must be unchanged.
Unknown writes stop automatic cleanup. Private source/tool/provider evidence is
hash-archived outside Git; public results contain measurements and hashes only.
Measured provider usage, local serialization estimates and partial retrieval RU
remain distinct. Source/envelope/selector overhead is retained as the next
presentation-efficiency issue, without changing representation during this run.

Entry point: `scripts/granular_followup.py` (`prepare`, `preflight`, `execute`).
This runner performs no main merge, application deployment, resource provisioning,
provider switch or larger study.
