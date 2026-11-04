# Hybrid versus vector-only: completed quality comparison, 2026-11-03

**Hybrid found more of the required evidence in the first search. It did not
improve final answer quality in this bounded, large-context pilot.** Keep current
application defaults; retain hybrid as an opt-in retrieval-quality candidate.
The next research step is source-structure fidelity and a more discriminating
quality confirmation, before efficiency or physical-index tuning. No R3 begins.

The [acceptance receipt](../records/hybrid-quality-completion-20261103.json)
accepts the immutable sanitized exports below. Their internal candidate labels
record export provenance; the acceptance receipt supplies the final decision and
hashes. The [frozen experiment contract](hybrid-quality-comparison.md) remains in
force. All 48 confirmation questions are now consumed evidence.

## Scope and controls

The branch resumed from `24e7db9`, retaining application baseline
`3980333ee8453af48c7133cab24522868701d1cc`. Measured implementation was
`0498d0277a45f3d844138b002b95c2efd51ffeee`. It was **not rebased onto main**
`1ddc438`. Application code, Cosmos policies, dependency lock, source manifest,
tasks and frozen plan remained unchanged. The final publication changes only
receipts and documentation.

All eight original DOCX files published once at generation 1 using the bound
Microsoft layout workflow: **36 chunks, seven multi-chunk documents**. Original
bytes, metadata, provenance, generations, stored rows, actual embedding/answering
deployments and loaded API code/settings were checked before and after measurement.
Both retained source archives contain the same 56 files and 42,244,514 bytes;
all were hash-verified and the final live inventory/attempts reconciled unchanged.
See the [ingestion receipt](../records/hybrid-quality-ingestion-20261103.json).

The only comparison treatment was current vector versus current hybrid search,
including their required routing distinction. Both used the same stored vectors,
source visibility, top eight, candidate window sixteen, neighbor expansion, compact
evidence and selector handles. The direct pass shared cached query embeddings;
adaptive answering retained its actual chosen queries and the same embedding
deployment across arms. Vector supplied the library
partition prefix; hybrid retained single-library equality but omitted that explicit
partition option. Tokenization, default RRF weights and float16 conversion stayed
fixed. Two separate preparation questions preceded the one alternating 96-search
schedule. No miss was selectively rerun.

Answering used **gpt-6.1-sol, revision 2026-09-29**, Responses, medium reasoning,
8,000 output tokens, three attempts and 200,000 context tokens, with storage and
automatic truncation disabled. There were 48 questions, two independent
conversations per arm/question, and **192 completed turns**. Arm order reversed
between repeats. Repeats are correlated observations of the same questions, not
new independent samples. Both APIs and provider work stopped after finalization.

## Direct evidence retrieval

[Source-reviewed direct receipt](../records/hybrid-quality-direct-20261103.json).
The denominator is 40 answerable questions; the eight missing-value questions
have **null** required-source recall, not perfect recall.

| Measure | Vector | Hybrid |
| --- | ---: | ---: |
| All required evidence in initial results | 24/40 (60%) | 36/40 (90%) |
| All required evidence after neighbor expansion | 30/40 (75%) | 37/40 (92.5%) |
| Initial macro required-region recall | 62.08% | 90.83% |
| Expanded macro required-region recall | 75.83% | 93.33% |
| Initial macro requested-fact recall | 62.50% | 91.25% |
| Expanded macro requested-fact recall | 76.25% | 93.75% |

On paired source recall, hybrid improved 12 questions initially and seven after
expansion, with zero losses; 28 and 33 tied respectively. This is a material
retrieval improvement on this corpus. It is not ruled out by a 20% efficiency
threshold. The earlier accepted I1 input reduction of 18.8542783% is unchanged.

Hit usefulness was only partially judged. Of 320 initial hits per arm for the 40 answerable questions, vector
had 43 useful, 224 irrelevant and 53 unjudged; hybrid had 54, 218 and 48. Do not
count unjudged hits as irrelevant or claim fully measured precision. Helpful
absence context was present for six of eight missing-value questions with vector
and seven with hybrid. Family results overlap and retain their own denominators
in the receipt; they are not a validated conditional-routing policy.

## Final answers and source fidelity

[Answer receipt](../records/hybrid-quality-answering-20261103.json). Each arm has
80 answerable turns from 40 questions, and 16 missing-value turns from eight
questions. All 192 answers were manually reviewed against original sources.

| Reviewed dimension | Vector | Hybrid |
| --- | ---: | ---: |
| Answerable task completion | 80/80 | 79/80 |
| Answerable factual support | 80/80 | 79/80 |
| Answerable citation accuracy | 80/80 | 77/80 |
| Answerable source relationships | 80/80 | 79/80 |
| Missing-value appropriate abstention | 16/16 | 16/16 |
| All required original regions delivered | 78/80 | 78/80 |
| Macro required-region recall | 99.17% | 99.17% |
| Macro requested-fact coverage | 100% | 99.375% |

The strict paired result is one vector win and 79 ties for completion/support/
relationships, and three vector citation wins and 77 ties. There are no hybrid
answer-quality wins in this matrix. The two citation-only failures are hq-01 in
both hybrid repeats: the requested meaning was correct but clause numbering was
misattributed. The third failure is hq-40 repeat one, a qualification attached to
the wrong part of a definition.

**Sensitivity:** hq-40's overlapping exclusions make the practical completion/fact
judgment sensitive to interpretation. A reasonable lenient interpretation would
raise hybrid completion and requested-fact coverage to 80/80 and 100%. The sealed
primary judgments above remain unchanged. That sensitivity neither repairs the
local qualification/citation/relationship error nor erases hq-01's two citation
errors. Do not infer a robust answer-quality advantage from this single strict
completion difference.

Both arms share a separate source-fidelity limitation. In hq-40, extraction
flattened a definition's paragraph/list structure inside a table cell. Its words
survived, but the complete relationship between a global qualifier and the list
did not. The complete definition region is uncredited in all four turns, including
complete-file reads. Requested fact sufficiency and faithful delivery of a whole
original region are distinct measures. This is a controlled follow-up candidate,
not an architecture fix implemented here.

Adaptive search and additional reads brought the final answers near the ceiling.
The [separate finalized trace receipt](../records/hybrid-quality-traces-20261103.json)
records source bodies through search in 96/96 turns per arm, selected reads in
45/96 vector and 47/96 hybrid turns, and full reads in 8/96 and 9/96. Do not infer
mode usage from empty required-region sets. Full-read coverage beyond fixed direct
expansion was only the hq-40 definition **label**, not its complete body. The sole
full-read-only region in actual agent evidence was vector hq-39 repeat one; it was
already available in fixed direct-expanded evidence. Set differences do not prove
causal necessity. Full reads alone did not establish the explanation for recovery.

Three reviewers used opaque, arm-hidden answer packets in disjoint case groups.
The coordinator independently reviewed all failure cells, both quality-disagreement
cases and the cross-document cases before unblinding. An additional reviewer
checked 72 cells for channel/structure consistency. The coordinator removed complete
hq-40 definition-region credit consistently across all four cells. Judgments were
sealed before arm maps were opened. This is assistant review with independent
adjudication, not a fully blinded human study. An independent final audit recomputed
9,223 arithmetic, binding, raw-result and trace checks without regrading labels.

## Usage: descriptive, not an efficiency decision

| Recorded measure | Vector | Hybrid |
| --- | ---: | ---: |
| Direct search mean instrumented API RU (48/arm) | 39.0775 | 108.4277 |
| Direct wrapper mean elapsed milliseconds (48/arm) | 389.42 | 1,172.36 |
| Answer provider input tokens, all 96 turns | 13,359,901 | 12,786,429 |
| Answer provider output tokens | 43,298 | 43,688 |
| Successful logical model calls | 362 | 368 |
| Recorded rate-limit retries | 302 | 295 |
| Recorded rate-limit waits, milliseconds | 2,286,847.5 | 2,449,756.6 |
| Known answering retrieval RU subtotal | 3,057.23 | 6,026.89 |
| Answer turns with incomplete retrieval-charge flags | 9 | 10 |

Hybrid used **4.29% fewer recorded answer-input tokens**. Rate-limit waits were
substantial; caching and provider placement were uncontrolled. Successful logical
model calls are not all HTTP requests. Complete retrieval RU totals and billed
costs are unavailable; instrumentation may omit query-plan/component charges even
where its local completeness flag is true. Do not sum overlapping API/chunk RU,
attribute elapsed differences solely to ranking, or claim production latency or
billed savings. Tool budget refusals numbered six and nine; all triggered automatic
document-scoped passage recovery. No remaining-context withholding was observed.
Largest single-call inputs were 141,566 and 141,373 tokens, within matched capacity.

Preparation is separate: the 48 cached question embeddings took 49 provider
requests, 1,302 prompt tokens and one retry. Successful ingestion recorded eight
terminal published attempts and 26,006.35 known chunk-store RU, including collision
reads; provider embedding request/token/retry meters were unavailable. Earlier
failed-attempt counters remain in their dated record and are not hidden or merged
into comparative retrieval numbers.

The small vector queries reported Cosmos `RetrievedDocumentCount=36` chunk rows
from eight originals, with 16 output candidates before the API retains top eight;
actual ANN index engagement/steady state is unknown. Hybrid callbacks include handled HTTP
400 followed by successful requests; direct traces do not establish their substatus.
The separate smoke observed handled 400/1004 query-plan fallback. Neither should
be conflated with the prior E17 failure.

## Recovery, preservation and cleanup

The bounded fresh Azure check passed against the retained container. Fresh smoke,
mounted workflow and restart guards passed before eight-source ingestion. No new
Cosmos account or capability enablement was retried. Recovery cause is unknown;
new-account provisioning remains a separate untested gate.

The [cleanup receipt](../records/hybrid-quality-cleanup-20261103.json) records
ordinary deletion of only the successful new evaluation library, independent
point/scoped/live-prefix absence checks, and removal of only the temporary owned
answering deployment. Protected R0/P1/I1 snapshots and pre-existing model deployments
remain unchanged. The earlier failed scope remains fenced and explicitly protected;
its complete cleanup is unresolved. Raw evidence and both source archives were
copied into a verified private durable archive before deletion, outside Git.

The first independent verifier stopped after 45 strict owned point 404s because
its transport guard rejected an SDK partition-routing metadata `304 Not Modified`.
That incomplete attempt is retained. A separately reviewed verifier correction
permits only the conditional, empty partition-range metadata response; it never
credits a 304 as item or prefix absence. Its 89 local tests passed before the
read-only verification was repeated. No DELETE was repeated.

Ordinary worker purge and independent post-cleanup checks do not establish the
stronger exceptional-recovery ordering of per-chunk 404s before deleting blobs.
Finite late checks establish live namespace absence during their window, not
indefinite absence. Soft-delete/version/backup retention remains in force. M1's
scheduled orphan discovery/configured-container defect is unchanged.

## Reproduction and next research handoff

Local setup remains reusable and contains no automatic cloud provisioning or
credentials. The measurement changes passed 1,264 tests in the full check; the
later focused run passed 40 tests. Final publication checks cover JSON, frozen
hashes, receipt linkage, formatting and private-identifier exclusion.

To rescore the finalized run offline, use measured commit `0498d0277a45f3d844138b002b95c2efd51ffeee`
or byte-identical measured code and the retained private runtime with its original
artifact bindings. Choose a new output filename; do not reset execution ledgers.

```sh
sh scripts/dev setup
sh scripts/dev check
# HQ_PRIVATE points to the retained private runtime, outside every Git checkout.
.venv/bin/python -m scripts.hybrid_quality_replay score \
  --source "$HQ_PRIVATE/replay" --gold "$HQ_PRIVATE/gold.json" \
  --reviews "$HQ_PRIVATE/answer-reviews.json" \
  --output "$HQ_PRIVATE/new-score-recheck.json"
```

The live handoffs remain documented for reproducibility, not authorization to
reuse these consumed questions as fresh confirmation. Rescoring makes no model
calls. The original absolute private bindings and hashes remain evidence; relocating
an archive is not permission to rewrite or bypass them.

Next, independently reproduce the confirmed table-qualifier structure loss and
propose a narrowly scoped structure-preservation candidate on a new branch.
Investigate the clause-number citation errors separately; their causal dependence
on extraction versus answering has not been isolated. Use
this pack only for development/regression. Before a new confirmation, freeze a
broader, harder source/task pack and versioned controls: long and varied originals,
cross-document attribution, exceptions/table relationships, exact and paraphrased
queries, and missing actual values. Keep the answering model/context matched and
measure initial recall separately from adaptive final answers. Acceptance requires
faithful original structure, no permission/metadata/provenance/lifecycle regression,
complete paired coverage and source-based adjudication, with quality ahead of cost.
Any current/new representation × vector/hybrid comparison needs independent cells
so formatting and ranking remain attributable. Integrating onto main requires a
new explicit baseline, never retrospective rebasing of this result.

Still open: library-spanning physical ranges/global top-k merge, large-library
DiskANN crossover/engagement, hybrid scale/concurrency/hot ranges/split transients,
complete cost telemetry, broader non-DOCX/image quality, hosted Entra authorization,
real broker redelivery, sustained replacement/deletion and cross-instance revocation.
R1 already measured outside-range hybrid fan-out; this result does not erase it.
Future versioning integration must reconcile evidence identifiers and request-scoped
search instrumentation. No physical layout, provider or architecture winner follows.
