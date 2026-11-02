# I1 selector-only held-out replay

The frozen confirmation completed all **64 turns**: all 16 held-out tasks, two
cells and two repeats in reversed cell order. It retained the current physical
foundation, Microsoft extraction profile and compact evidence format. The sole
candidate change was selector handles. The
[predeclared plan](testing/retrieval-i1-controls.json) was committed before any
answering run; the measured implementation is
`0c7e6a2e9e2034a33939121627ed3684e44362a7`.

[Source-based scoring](../records/retrieval-i1-scorecard-20261102.json) found no
observed source or answer-quality regression. Both cells passed 32/32 completion,
material factual support and citation judgments, and 10/10 applicable relationship
judgments. Each delivered all 232 required source-region occurrences. Review
checked exact region identities, ordered sections, merged table headers and the
four blank-placeholder occurrences per cell against original source locations.
Baseline and selector reviews came from separate assistants, independently of
the implementation owner. This is unblinded assistant review, not a general
accuracy estimate or statistical equivalence result.

| Across both repeats | Baseline | Selector handles |
| --- | ---: | ---: |
| Provider input tokens | 709,160 | 575,453 |
| Provider output tokens | 12,383 | 12,764 |
| Model calls | 104 | 104 |
| Unsupported expansion errors | 4 | 0 |
| Read budget refusals | 0 | 0 |
| Provider retries / rate wait | 0 / 0 | 0 / 0 |
| Maximum provider input | 28,836 | 13,925 |
| Complete retrieval RU | unavailable | 379.80 |

Provider input fell **18.8542783%**, below the frozen 20% efficiency threshold.
The separate usability/no-regression gate passed: selector handles avoided the
four `source_scope_unavailable` requests made by the baseline on tasks `r0-38`
and `r0-39`, once each per repeat. Those baseline errors recovered and did not
cause failed answers. This supports a bounded usability finding; it does not
change the failed efficiency decision or automatically promote a default.

Serialized tool-evidence estimates were 404,174 versus 246,884 tokens, including
evidence repeated across model calls. This reflects different tool paths with
the same compact format. It is distinct from provider input usage and is not a
reference-format result. Baseline RU has only a known subtotal of 397.72 because
failed requests lack complete charges. Observed total times were 352.83 and
381.84 seconds. Cache state and backend placement remain uncontrolled; these
measurements establish no latency or billed-cost benefit.

## Controls and execution evidence

The [runtime receipt](../records/retrieval-i1-runtime-20261102.json) preserves
per-cell input/output, cache, reasoning, retry, context and retrieval counters,
plus hashes of private captures. Every cell used Sol `2026-09-29`, Responses,
medium reasoning, an 8,000-token output reserve, three provider attempts and a
200,000-token context allowance per independent task. No provider truncation,
conversation storage, case exclusion, prompt edit or model rerun was used.
The largest observed input was 28,836 tokens; this is not a tested 200k input.

Before execution, the runner required all 13 accepted live lifecycle checks,
their raw receipt hashes and a separate coordinator acceptance bound to the
exact plan, source implementation, settings, evaluation library, output and
single-use execution ledger. Lifecycle and evaluation libraries were separate.
The lifecycle scope used real Azure data planes and local development-auth API
instances. Production Entra authentication and broker worker redelivery remain
untested.

The dedicated API emitted fresh before/after receipts from its live application
settings and process, including source hashes, effective query controls and
environment/import assertions. Actual answering deployment receipts bound the
invoked endpoint/deployment, model revision, upgrade policy and SKU. API, source,
model and code bindings stayed unchanged. The post-run model and API captures
followed a preserved completion timestamp. Finalization reopened every cell's
questions and snapshots before accepting completion.

All 208 actual provider inputs were captured privately. Scoring verifies every
earlier compact tool sequence is a prefix of the final source sequence and binds
that final sequence to each frozen question and canonical evidence. Reviews use
exact delivered-region sets; equal counts cannot conceal a lost region. Blank
paragraphs/cells require explicit source-coordinate review. Unknown applicable
quality or relationship judgments cannot establish a passing comparison.

The owned API was stopped after finalization. Cloud cleanup and preservation of
unrelated resources are coordinated separately. Raw sources, model messages,
answers, environment identifiers and credentials remain outside the repository.

## Local reproduction and remaining decision

The unchanged measurement checkout supports independent scoring after cloud
cleanup. Source and script hashes are deliberately checked; use the measured
implementation or a receipt-only descendant with the same code. The consumed
execution ledger preserves the single held-out run, including any partial
attempts; the driver offers no automatic rerun or resume.

```sh
sh scripts/dev setup
sh scripts/dev test -q
sh scripts/dev lint
sh scripts/dev privacy --deny-file "$I1_PRIVATE/private-deny.json"
.venv/bin/python -m scripts.retrieval_i1 score \
  --source "$I1_PRIVATE/replay" --gold "$R0_PRIVATE/gold.json" \
  --reviews "$I1_PRIVATE/reviews.json" --output "$I1_PRIVATE/recheck.json"
```

Each review row names `repeat`, `cell`, `case`, `turn`, the four R0 quality
dimensions, `deliveredRegionIds`, `blankRegionIdsVerified`,
`relevantSourceCoverage` and allowlisted `failureCategories`. Private notes and
reviewer identities support adjudication; the exporter retains only defined
counters and judgments.

The measurement checkout passed **1,083 tests** with 14 existing warnings, plus
lint, formatting, diff and private-identifier checks. No application architecture,
production default, R0/P1 development-only guard or frozen benchmark label changed
in this extension. A coordinator decision may consider selector usability
separately while retaining the failed 20% efficiency result. Broader formats,
production authentication, uncontrolled multi-instance races, load, broker
redelivery and a library spanning physical ranges remain outside this run. A later
foundation/format change still requires the roadmap's full four-cell integration
comparison; this confirmation supplies no reason to start R3.
