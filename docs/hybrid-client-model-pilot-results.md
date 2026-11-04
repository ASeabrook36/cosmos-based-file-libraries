# Client and answering-model findings

The controlled SDK comparison separates two issues: .NET 3.63.0 restores meaningful
hybrid ranking with explicit library routing, but hybrid still costs substantially
more than vector search. The matched answering pilot finds a larger completion
benefit from hybrid with the economical model, although that benefit does not
eliminate factual, citation or context-management failures.

These are exploratory results on the already-consumed eight-document, 48-question
pack. They are not fresh confirmation, a complete replication of David Ames's
setup, or grounds to change an application default. The completed earlier run,
its controls and archives remain intact. The measured producer is commit
`8ead7998b175ed5dbc80bd0bdd66516e2b519e18`, descended from completed comparison
`8f7d436987724abac1efe633d071db66f8cbf4ea`; main was not merged or deployed.

## SDK: ranking works in .NET; the cost difference remains

See the [SDK report](hybrid-sdk-comparison-results.md) for the complete matrix,
wire/plan evidence and preserved invalid first collection attempt. Both clients
used the same 36 stored chunks, embeddings, query vectors, logical SQL/parameters
and library filter. Python was `azure-cosmos==4.17.1`; .NET was
`Microsoft.Azure.Cosmos==3.63.0`, package-locked exactly.

| Client and query | Mean RU/query | Median query ms | Ranking observation |
| --- | ---: | ---: | --- |
| Python vector, explicit prefix | 11.27 | 90.4 | Exact tie-aware vector control passed |
| .NET vector, explicit prefix | 11.48 | 138.9 | Same vector ordering as Python |
| Python hybrid, explicit prefix | 9.68 | 87.1 | Broken: same ranking across all questions |
| Python hybrid, WHERE-only | 82.29 | 798.1 | Meaningful hybrid ranking |
| .NET hybrid, explicit prefix | 71.06 | 627.2 | Same ordering as .NET WHERE-only |
| .NET hybrid, WHERE-only | 70.32 | 631.7 | Same top-16 sets as Python WHERE-only |

Each cell has 96 queries: all 48 questions, twice. .NET's two hybrid routes agreed
on all 96 top-16 orders. Python and .NET WHERE-only agreed on all sets and 92/96
orders; the four adjacent-order differences have no established RRF oracle or tie
explanation. Python's cheap explicit-prefix hybrid result is not a valid quality
alternative.

.NET explicit-prefix hybrid used about 13.6% less RU and 21.4% lower median latency
than Python WHERE-only in this window, yet still used about **6.2 times its own
vector control's RU**. Correct ranking did not remove the cost difference.
Python emitted six wire query requests, including a handled 400/1004 fallback;
.NET emitted four. The larger charged difference was in the text component, not
simply the extra uncharged fallback. The differing rewrites/encoding were observed,
not separately ablated.

Explicit .NET routing bounded ranking-component effective partition-key ranges.
Its full-text statistics request still had neither the library WHERE predicate nor
bounded EPK scope. The measured container exposed only one full-keyspace physical
range. This establishes logical/wire scope, **not efficient multi-range fan-out**.
David's exact SQL, request options and partition layout remain unknown.

There were no measured logical failures, 429s or transport errors in the accepted
matrix. A preparation metadata timeout is preserved separately. The .NET observer
retained parsed rows and response-byte hashes, but decoded binary response bodies
lossily; it is not a lossless binary wire archive. Fix that collector before another
study requiring complete wire-body replay.

## Answering: fresh, matched four-cell pilot

The pilot used Python for every cell, the existing hybrid WHERE-only workaround,
unchanged tools/instructions/selectors, the same source representation and vectors,
and fresh references for both models. The actual deployments were pinned to Sol
`gpt-6.1-sol/2026-09-29` and mini `gpt-5.4-mini/2026-03-17`, with NoAutoUpgrade.
Both passed the live tool/Responses-continuity probe.

All 192 turns ran once in a balanced, serial schedule with the same 64k operating
context allowance, medium reasoning, 8k output limit, 12 tool rounds and bounded
429 retries. This is not a comparison with the earlier 200k-context Sol run.
The allowance is below both models' native capacity. It is a chosen matched
operating point, not proven adequate for every task: no context-budget sensitivity
was run. Coarse whole-document reads and recovery work can exhaust it even when
the decisive source passages themselves are small. Conclusions are conditional
on this operating point and the existing agent machinery.

| Metric | Sol/vector | Sol/hybrid | Mini/vector | Mini/hybrid |
| --- | ---: | ---: | ---: | ---: |
| Answerable task completion, /40 | 36 | 39 | 30 | 36 |
| Completion + support + citations + relationships all pass, /40 | 36 | 38 | 26 | 29 |
| Mean complete required-region recall | 89.17% | 97.29% | 82.92% | 99.17% |
| Mean supported required-fact coverage | 90.00% | 97.50% | 80.42% | 93.75% |
| Appropriate missing-value abstention, /8 | 8 | 8 | 2 | 2 |
| Local context-stop final answers, /48 | 0 | 1 | 11 | 6 |

The joint pass row is an explicitly supplemental descriptive intersection of four
frozen judgments, not a replacement primary metric. Coverage is a per-question
macro mean over the 40 answerable questions. Missing-value questions have no frozen
required-region/fact IDs and are not assigned invented coverage credit.

Hybrid improved completion by **7.5 percentage points for Sol** and **15 points for
mini** on answerable questions. That is +3 and +6 answers respectively, or 8.3% and
20% relative to their vector baselines. Across all 48 questions, including missing
values, the gains were +3 and +6; the model-by-retrieval difference of gains was
6.25 percentage points. On the stricter joint pass count, gains were +2 and +3.

The interaction includes tool strategy and the shared context guard. On answerable
questions, mini had nine hybrid wins and three vector wins; five of the nine hybrid
wins avoided a vector-arm local context stop. Sol had four hybrid wins and one
vector win; its hybrid loss was the local guard on hq-38. All six missing-value
failures in each mini arm were local context stops, not fabricated missing values.

This supports an end-to-end model/tool-strategy interaction that can make hybrid's
advantage less visible with Sol. It does **not** isolate intrinsic reasoning
ability, or establish model choice as the sole reason for the previous result.
The context allowance changed from that historical run, and this reused, small
pack has one observation per cell. Mini/hybrid delivered nearly all required
regions yet still made interpretation and citation mistakes. Better retrieval
and reliable final answers are separate outcomes.

Among answerable turns, citation failures were 0/1/5/6 in table order; source
factual-support failures were 0/0/5/6. The bare context refusals have inapplicable
support/citation/relationship judgments, not passes. Four Sol/vector incomplete
answers accurately described their limited evidence, so their supported context
statements do not imply task completion. This distinction prevents misleading
accuracy denominators.

Additional explicit evidence-search replies observed locally were 24/12/81/58;
provider-received counts were 24/11/81/58. These are recovery-activity proxies,
not proof that every additional search caused a successful recovery. Automatic
document-scoped passage returns remain search evidence, not full-document reads.

## Measured usage and timing

The [scorecard](../records/hybrid-model-pilot-results-20261104.json) and
[independent usage supplement](../records/hybrid-model-pilot-usage-20261104.json)
retain the exact per-question and aggregate counters, including missingness.

| Cell, 48 turns each | Known input tokens | Known output tokens | Known tool RU | Recorded 429 wait, seconds | Median turn seconds, less recorded wait |
| --- | ---: | ---: | ---: | ---: | ---: |
| Sol/vector | 4,846,040 | 23,480 | 2,717.83 | 0 | 17.76 |
| Sol/hybrid | 3,762,001 | 21,021 | 3,877.60 | 0 | 19.51 |
| Mini/vector | 8,284,872 | 83,717 | 8,117.31 | 503.39 | 24.31 |
| Mini/hybrid | 7,583,160 | 84,764 | 14,988.60 | 130.12 | 27.16 |

The scored run made 983 logical calls and 1,096 physical Responses attempts.
Its successful-call meters report **24,476,073 input and 212,982 output tokens**,
including 17,474,136 cached input and 89,376 reasoning output tokens. These detail
counts are subsets, not extra tokens. All successful calls supplied their detail
counters. The separate tool probe used four calls, 559 input and 123 output tokens.

The other 113 attempts were 429s, all on mini, with unreported usage. Their usage
is unknown, not zero. Conservatively retained reservations brought the input
guard to 31,708,073 of 32,000,000 units; reservations are neither actual consumption
nor charges. No bound was raised and no answer turn was selectively rerun.

Recorded 429 sleeps totalled 633.51 seconds. Elapsed-minus-wait still includes
retry requests, provider queueing, tools and local work; it is not pure inference
time. Mini/hybrid's shorter summed wall time mainly reflects fewer recorded waits:
its summed elapsed-minus-wait was slightly higher than mini/vector. This pilot
does not establish an intrinsic model or retrieval latency ranking.

Known pilot tool charges totalled 29,701.34 RU, but 68/192 turns had incomplete
charge attribution, including 78 unknown tool-trace charges. Complete RU totals
are therefore null. Separately, accepted SDK measurements/reference/warmup/snapshot
reads recorded 26,787.33 RU; the preserved invalid first SDK collection recorded
11,957.61 known RU; ingestion recorded 26,005.97 instrumented chunk-store RU.
Those non-overlapping measured buckets total 94,452.25 known RU. They omit other
preflight, source/archive, metadata, lifecycle and uninstrumented operations.
Embedding/extraction/provider-retry usage and a complete Azure bill remain unknown.

Applying the checked public OpenAI token-price assumptions to known successful
Responses usage gives **$11.65**: $7.00 for Sol and $4.65 for mini, plus approximately
$0.001475 for the probe. Cached reads use the cached-input rate; other input,
including reported cache writes, uses the ordinary input rate. This is an
illustration, **not measured Azure spending**. It excludes unknown 429 usage,
embedding, extraction, database/storage, hosting and lifecycle costs. The linked
[planning proposal](source-structure-confirmation-proposal.md) records the price
sources and the limits of extrapolating these measurements.

## Context, source fidelity and review limits

There were 18 final local context stops. Seventeen left only no-body replies
unsubmitted; one Sol/hybrid turn retrieved an outline and source-bearing search
locally, then stopped before submitting them. Its reconstructed guard was
64,843 against 64,000. The tool payloads fit their individual allowances, but
enclosing transcript/serialization overhead pushed the next guard over its limit.
This is a shared agent-budget limitation triggered by the model's tool sequence,
not evidence that the model read those sources and reasoned incorrectly.

The frozen producer's verifier assumed every locally saved reply reached the
provider. A separately versioned, independently reviewed offline analysis proves
the actual submitted prefix and excludes all 22 unsent replies. The original
answers, producer, controls and rubric were not changed or rerun. All 192 cells
passed final execution checks under that narrow analysis; 174 also passed the
original verifier unchanged. Exact source-bearing exceptions require bound source
proofs; arbitrary source tails remain rejected. Helpers and synthetic regressions
are in [tools/pilot_analysis](../tools/pilot_analysis/README.md).

Three assistant reviewers produced 192 source-based judgments with model/retrieval
labels hidden; answer style could still reveal a model. The coordinator reviewed
failures and ambiguities, and a second blinded pass covered 28 turns. All 26
provisional batches were rebound to finalized packet/source bytes. Original
reviewer checkpoints, six explicit field adjudications and alternative readings
remain archived. The coordinator had access to instrumentation labels; this was
not a fully blinded human study.

Strict clause-number citations and source qualifier scope matter to these results.
Recorded sensitivities include coarse document/heading findability, whether
"during" means "for" a covered period, implicit deadline origin, and one ambiguous
software-notice ownership scope. These can change individual judgments; the tiny
paired counts should not be promoted into a general accuracy estimate.

Both the completed prior comparison and this pilot used improved selectors with
**Microsoft's one-canonical-section representation**, not the full custom hierarchy.
The [structure reproduction and proposal](source-structure-confirmation-proposal.md)
shows ten original table-cell paragraphs preserved in raw extraction annotations
but flattened in the published source. The final unnumbered qualifier loses its
separation from a nested list. All four current answers received the words; none
received that complete original structural relationship. Two interpreted the
qualifier incorrectly. Authored hierarchy, list numbering and granular navigation
therefore remain untested as a complete source-preserving solution.

## Resource closure

Both owned temporary answering deployments were deleted and verified absent;
the two pre-existing deployments were preserved. The owned evaluation library,
eight documents and 36 chunks were purged only after the pre-cleanup archive passed.
An independent verifier made 90 exact point-read HTTP 404 checks, four scoped
empty-query checks and two live blob-prefix checks across immediate and
60-second-late passes. The five protected R0/P1/I1 libraries and their 24 documents
remained unchanged.
Earlier failed/fenced scopes and ongoing scaling resources were not modified.

The [closure receipt](../records/hybrid-client-model-cleanup-20261104.json) binds
cleanup and the verified private archives before and after deletion. Raw originals,
requests, answers, reviews and diagnostics remain outside Git; known credentials
and their encodings were scanned and excluded. The archives are durable local
copies, not offsite backups. Live absence does not establish removal from
soft-delete/version/backup retention or guarantee against indefinitely late output.

## Validation and reproducibility

The initial repository suite passed 1,397 tests; lint/format and reference-privacy checks
passed. The separate private archiver suite passed 13 tests. The new 65 delivery
analysis regressions are synthetic and require neither private sources nor cloud
credentials. Application source, container definitions, application runtime dependencies,
frozen experiment dependency controls and the completed hybrid benchmark pack are
unchanged from the completed baseline.

Post-measurement CI exposed two test-environment gaps: the general development
install selected OpenAI 3.45.0 while plan tests inherited the live pilot's 3.37.0
gate, and the exact-vector reference test needed NumPy. NumPy is now a development
dependency. Plan unit tests explicitly control version metadata, with two added
tests proving either SDK's drift is rejected. Live version gates and all 112 frozen
producer files remain unchanged; no experiment was rerun for this CI repair.

The [review receipt](../records/hybrid-model-pilot-review-20261104.json) binds the
finalized run, accepted source judgments, delivery correction and independent
arithmetic audit. Restore the private evidence dependencies at their bound paths
and use the frozen producer checkout to reproduce scoring; this command makes no
model calls and requires a new output filename:

```sh
"$FROZEN_CHECKOUT/.venv/bin/python" "$RESULTS_CHECKOUT/tools/pilot_analysis/analysis_v4.py" score \
  --producer "$FROZEN_CHECKOUT" --run "$PILOT_RUNTIME/scored-run" \
  --acceptance "$PILOT_RUNTIME/analysis-v4-acceptance.json" \
  --case-proof "$PILOT_RUNTIME/analysis-v4-hq38-proof.json" \
  --gold "$PILOT_RUNTIME/gold.json" \
  --reviews "$PILOT_RUNTIME/reviews-adjudicated-list.json" \
  --output "$PILOT_RUNTIME/new-scorecard.json"
```

Retained evidence also includes the invalid SDK collection, original verifier
failures, source-publication caveat and local coordinator preflight/serialization
errors. None was filled with a selective answering rerun. Credentials remain
excluded; later live reproduction requires fresh authorization and credentials.

Open gates include multi-range SDK routing/fan-out, end-to-end answering with the
.NET client, context-budget sensitivity, and the proposed source-fidelity/hierarchy
implementation and its permission/replacement/retry/deletion checks. The known
scheduled-orphan cleanup defect remains open. No new physical-index R3 comparison,
fresh confirmation corpus, or larger answering study was launched.

## Next step

Qualify an opt-in source-fidelity repair before launching fresh confirmation:
preserve validated paragraph/list/table structure and original numbering/locations,
initially keeping existing passage vectors fixed. Independently qualify tool-envelope
context accounting so nominally fitting reads cannot cause an unanticipated guard
stop. Keep those implementation effects separate from the SDK/model measurements.

The prepared larger study proposes 40 new documents and 180 fresh questions,
including long sources, distractors, table qualifiers, cross-document attribution,
exact/paraphrased wording and missing answers. It would compare flat block versus
authored hierarchy navigation crossed with vector versus hybrid on identical
source-preserving passages/vectors, with one pinned primary model. It has not been
launched. This pilot favors Sol as the provisional primary model for that study;
mini's lower token price did not make its present end-to-end behavior an adequate
substitute for source-faithful confirmation.
