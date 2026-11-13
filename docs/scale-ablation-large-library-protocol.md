# Large-library extension of E68: approved protocol

Prepared and approved 2026-11-12, local time, incorporating the user's benefit-
growth comparison, bounded no-prefix diagnostic and delayed index check. This
is a new experiment; E68's entry, receipt, tables and frozen protocol remain
unchanged. No production configuration or application behavior changes are
proposed. Commit the completed E68 work locally before the extension, then
commit this approved protocol and the validated extension driver locally. Do
not reconcile with main before the run or push before the final review.

## Question and existing evidence

At 50,000 and 100,000 vectors in one library, what does the application's
library-filtered, partition-prefix-routed search cost with and without
`vectorIndexShardKey: ["/libraryId"]`, and which vector-index mode serves it?
Add 10,000 and 25,000 vectors to describe the curve if budget and time permit.

Microsoft's [performance guidance](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/vector-search-performance-tips)
recommends QuantizedFlat for scopes around 50,000 vectors or fewer and sharded
DiskANN for larger scopes. This is guidance, not a promised mode-switch threshold.
E68 measured only 1,000–5,000-vector query scopes. Earlier diagnostics on another
layout used random vectors, float32, a non-partition-key class filter, no query
partition key and one execution. They motivate this test but are not its baseline
or evidence of what this configuration will do.

## Read-only gates already completed

The [preflight receipt](../records/scale-ablation-large-preflight-20261112.json)
contains content-free observations. The corpus check preceded Azure access.

| Gate | Observed result |
| --- | --- |
| Original consumed corpus prefix | 244,773 rows; 16,303,644,419 bytes; E68 SHA256 unchanged |
| Unused passages validated | 185,000, enough for all four new libraries |
| Uniqueness | No source-passage identity or exact-text duplicate within the selected set or against E68's prefix |
| Published passage vectors | All 185,000 have 3,072 finite values; no new passage embedding needed |
| Split chunks container | Exists; 244,773 passages; E68 policy matches; one physical range |
| Unsplit chunks container | Exists; 244,773 passages; E68 policy matches; one physical range |
| Database mutations / embedding calls | None |

The two count queries reported 5.8 RU in total. Metadata calls may have additional
unrecorded charges. All runtime bindings, source identities, locators and the
ordinal manifest remain private. Only counts, generic role labels and hashes
enter public artifacts. Recheck database counts, policies, physical ranges and
unused target-library names immediately before an approved load. Any unexpected
state stops the run; do not repair, replace or rebuild automatically. If either
database disappears, stop and estimate a separately approved rebuild from E68's
actual cost and duration.

## What changes and what stays fixed

Reuse the retained split and unsplit databases; create no account, database or
container. Add identical new libraries to their existing chunks containers.
Their sole policy difference remains the shard-key property. Preserve hierarchical
partitioning, float16 storage and query conversion, 3,072 dimensions, cosine,
spherical quantization, `indexingSearchListSize=300`, scalar/full-text indexes and
all query-time defaults. Do not tune parameters after seeing results.

Use a separate experiment driver and private checkpoint directory. Reuse the
application query renderer and existing measurement helpers without changing
production code or E68's measured source files. The filter-only and global
exceptions live exclusively in the disposable experiment driver. Implement and
locally verify that driver against this approved protocol before any writes.
The containers already passed their E68 canaries; no new container is being
created. Preserve those receipts and verify stored policies again.

## Corpus, assignments and questions

Use only the validated unused part of the same public corpus, strictly after
the actual E68 prefix of 244,773 passages, including its lag library. Freeze a
private ordinal-to-library assignment before the first write, using seed
20261029 and generic new-library roles. Assign source passages to all four
potential libraries once; omitting an optional library does not change either
mandatory library's membership.

| New library size | Priority | New passages per arm |
| --- | --- | ---: |
| 50,000 | Required | 50,000 |
| 100,000 | Required | 100,000 |
| 10,000 | Optional, currently expected to fit | 10,000 |
| 25,000 | Optional, currently expected to fit | 25,000 |

No source passage belongs to two libraries within an arm, or overlaps E68's
loaded passages. The same source passages, assignment and deterministic item
identities are intentionally used in both arms. Preserve E68's ten-passages-per-
synthetic-document construction and seeded membership assignment. Published
passage vectors are reused through E68's existing payload/storage path; do not
generate, randomize or re-embed them. Hash the selection, assignment, measured
source files and vectors, and verify them again after execution.

For each new library select twenty source-sentence questions using E68's
deterministic selection method and seed. Embed those questions once with the
same model/revision and float16 conversion, then share frozen query vectors
between arms and routes. These are real query embeddings, not replacements for
the corpus's published passage vectors. Record model metadata and actual usage;
stop if the expected model binding cannot be verified. Reuse E68's frozen twenty
questions for `scale-p01` (1,000 vectors) and `scale-p10` (5,000 vectors), and its
original fixed first-probe question for the global control.

## Load order and a common measurement inventory

Before loading, collect a fresh baseline for the existing 1,000- and 5,000-vector
probes: twenty questions, three ANN repeats and one exact reference per question,
in both arms. Keep this separate from the historical E68 baseline.

Load the 50,000- and 100,000-vector libraries first, followed by the optional
10,000 and 25,000 libraries only while forecasts leave room to finish the
required measurements. Reassess optional inclusion before its first write,
using observed load charges/rate and remaining required work, without using any
large-library search outcome to choose the sizes. At every batch checkpoint,
update the projected total cost and elapsed time.

Use E68's paced, resumable loading approach, writing each identical batch to the
two arms in turn. Never run a search during a load in either arm. Do not overlap
the two arms' measured queries. Record retries, charges and write times.

After all selected loads finish, verify total inventory and every affected
library count. Measure every library at this same final inventory:

- Mandatory pair only: 394,773 passages / 1,016 libraries per arm.
- All four additions: 429,773 passages / 1,018 libraries per arm.

Report the actual inclusion and inventory if only one optional size fits. If
either container gains a physical range, stop for a revised design rather than
mixing the one-range and multi-range questions.

## Settling and actual index modes

Allow a shared thirty-minute quiet period after the last write. Then poll one
fixed question per measured library/arm, alternating order. Require three
successive complete polls with valid ten-result lists, unchanged observed mode
and RU within 10% across adjacent polls; allow at least sixty seconds between
poll rounds. The shared readiness window ends sixty minutes after the last
write, followed by five minutes of settling if stable. Count all diagnostic
queries and waits in the budget and elapsed time.

Neither cheap RU, few documents read, `/libraryId` in a report nor DiskANN graph
engagement is a readiness requirement. QuantizedFlat and stable scan-like paths
are possible results. Distinguish an explicit report with no utilized vector
index from a missing report; retain every HTTP-response report and missing-header
count. Report service-provided build progress if present, but do not treat an
ordinary scalar-index progress value as proof the vector graph is complete.

If results or modes do not stabilize within the window, stop for a decision
about more waiting; retain a partial preflight/readiness receipt and do not start
the full measurement batch under an unannounced revised rule. Replica-dependent
mode mixtures are possible observations, not proof of a broken index. The
stability gate is conservative; it does not prove graph completion. A stable
scan without a vector-specific completion signal
can be measured, but its conclusion is explicitly time-bounded: the experiment
cannot distinguish a lasting planner choice from unfinished graph construction.
Do not quietly label it a permanent inability to use DiskANN.

Run the immediate fixed sentinels after all routed/global measurements and
before the no-prefix diagnostic, then repeat the identical set at least twelve
hours after the last acknowledged write, as specified below.
Preserve any mode changes, material RU movement or recall failures, and qualify
steady-state conclusions; do not select the cheapest snapshot or silently
restart the benchmark. Initial results remain provisional until the delayed
check and review are complete.

## Query matrix and repetitions

All primary searches are the unmodified application `render_search` candidate
query for requested `top=5`: `TOP 10`, application projection, library equality,
float16 query and `partition_key_for` library prefix. This measures the Cosmos
candidate call, excluding full API permissions, embeddings and visibility work.

The phase order is fixed: fresh small-library baseline; loads; readiness; all
routed ANN and library exact references; global ANN and global exact references;
immediate routed/global sentinels; no-prefix diagnostic last in the initial
work; waiting; identical delayed routed/global sentinels. The no-prefix phase
cannot delay or consume the budget reserved for the routed/global coverage.

| Shape | Libraries | Questions / repeats per arm | Reference |
| --- | --- | --- | --- |
| Application: library filter + partition prefix | Every selected new library and existing 1,000 and 5,000 probes | 20 / 3 | Forced exact for each question/library/arm |
| Diagnostic, run last: library filter, no partition prefix | Existing 5,000; new 50,000 and 100,000 | Same 20 / 3 by default; first 5 / 3 in both arms if the full-container trigger below fires | Same eligible-set exact references as the routed shape |
| Global: no library filter, no partition prefix | Whole final container | E68's one fixed question / 3 | One global exact search per arm |

The routing diagnostic retains the same SQL, projection, parameters, `TOP 10`
and defaults as the primary request; only the partition-prefix request option
is omitted. It answers the routing question separately from the shard-key
comparison. Audit result ownership as well as recall.

Complete all routed ANN searches, their exact references, the global ANN control,
its global exact references and the immediate fixed sentinels before starting
any no-prefix diagnostic. For each diagnostic library, first run its first
frozen question three times in
each arm. If any of those first-question measurements in either arm reports a
retrieved-document count at least as large as the verified final container count, retain
that measured question and cap the diagnostic at the first five frozen
questions, three repeats each, for **both arms of that library**. Record the
triggering arm, repeat, measured count, inventory count and the resulting
expected coverage before attempting question ordinal 5 or above. The first
question counts toward the five; it is not discarded as an unreported pilot.
This fallback is authorized by this protocol and requires no additional user
question solely to use it. General budget, time and error guards still apply.

For a capped diagnostic, report its five-question result beside the routed
result restricted to those same first five questions and repeats. Keep the full
twenty-question routed result alongside, clearly labeled. Do not compare a
five-question no-prefix median with a twenty-question routed median as though
they were a paired routing effect. Uncapped libraries retain twenty questions;
do not use different question sets in the two arms or reduce routed coverage.

Keep E68's global ID-only projection for continuity. Add a forced-exact global
reference for its fixed question so this run can report global recall, which
E68 did not measure. The global control still differs in projection and eligible
answer set from the application request; its contrast is not a pure filter-only
effect or an equivalent application operation.

Alternate arm order within each question, repeat and shape using a frozen parity
schedule. The no-prefix phase always follows the completed routed/global phase;
do not interleave those phases to balance their execution order. Preserve that
order effect as a limit on the routing comparison.
Run each filtered exact reference once, retaining it for all three ANN repeats
and both routing shapes. Keep errors, retries and first-call observations;
do not rerun selectively to improve medians or quality.

With all optional points, the final-inventory workload is 720 routed ANN calls,
up to 360 no-prefix ANN calls, 240 library exact calls, six global ANN calls and
two global exact calls. If all three no-prefix libraries trigger the fallback,
that phase has 90 calls instead of 360. The fresh small-library baseline adds
240 ANN and eighty exact calls. Readiness, immediate sentinels, delayed sentinels
and inventory checks are additional and budgeted.

## Delayed check without further writes

Freeze the UTC timestamp of the final acknowledged write across both arms and
the final inventory immediately after loading. Make no further data writes or
policy changes after that point. The delayed check runs no
earlier than twelve hours after this timestamp, on the unchanged loaded data.
Waiting for this fixed check and executing its bounded sentinel set are the
only exceptions to the initial run's twelve-hour elapsed guard; the $20
extension budget, its reserve and all other limits still apply. Do not reset
the original elapsed timer or charge ledger.

For both the immediate and delayed sentinel sets, run every measured library's
frozen question ordinal 0 through the application's routed shape three times
per arm. Also repeat the fixed global ANN question three times per arm. With
all four additions each set is 36 routed queries plus six global queries,
**42 sentinel queries**; mandatory additions alone require 30 per set. Run the
immediate set before the no-prefix phase. No no-prefix sentinel or new
exact-reference query is needed:
the corpus is unchanged, so retain the original exact references. Use the same
frozen arm-order schedule and record every late query's age since the final
write, RU, documents, client latency, recall, errors and actual index reports.
Immediately before the delayed set, perform fresh read-only inventory, stored-
policy and range checks against the frozen final state. If either arm's total
or per-library counts, policy or physical-range inventory changed, stop and retain
the mismatch rather than mixing it into the delayed comparison. These checks
are additional to the 42 sentinel queries and remain budgeted.

Compare the delayed sentinels with the initial measurements of the identical
questions and repeats, separately from the twenty-question primary summaries.
A mode change, material RU change or changed recall narrows a steady-state
claim; retain both observations rather than replacing the first with the late
one. Stable sentinels provide a bounded later observation, not proof that every
graph is complete. A changed sentinel does not authorize a new full benchmark,
retuning or a new wait cycle automatically.

Reserve enough of the existing extension budget to finish this delayed check
before admitting optional loads or spending on the no-prefix phase. Stay with
the run until the delayed check is complete, or an explicit budget/error stop
requires a user decision. An initial report is provisional, not completion of
the experiment.

## Recorded metrics and predeclared interpretation

For every query retain RU, Cosmos retrieved-document count, client elapsed
milliseconds including retries, result count, errors, retry charges, observed
physical-range count and every utilized-index type/shard-key report. Full texts,
query vectors, result IDs and raw bindings stay private. Index types are observed
effects: report QuantizedFlat, DiskANN, explicit no-index reports, missing reports
and mixtures by repeat; do not substitute the configured `diskANN` policy for
what Cosmos actually reports.

For each library, arm and filtered shape use E68's primary statistic: the median
over the declared question set of its three-repeat medians. Primary routed
summaries always use twenty questions; a triggered no-prefix diagnostic and its
paired routed subset use the same first five. Publish all three run
medians/ranges, absolute RU differences, unsplit/split ratios and signed
percentage differences. Do the same for documents and latency, keeping their
interpretations separate from RU. Global values are the three-repeat medians
of the one fixed question. Three repeats describe observed stability, not a
confidence interval or build-to-build equivalence.

Quality acceptance requires every expected query/metric, ten unique results,
correct library ownership and mean recall@10 at least 0.95 for every library,
arm, filtered shape and repeat against the frozen exact reference. The global
control's recall is evaluated against its own global exact set and reported
separately; it is not an acceptance gate for the per-library comparison. With
one global question, recall moves in ten-percentage-point steps, so do not infer
a global quality distribution from its three repeats. Preserve and report any
quality failure; lower cost with lower quality is not silently called equal.

The main size-dependent benefit comparison uses the routed application shape
at the common final inventory. Let `Delta_N = unsplit_RU_N - split_RU_N`, in RU
per search, for each tested library size `N`. Use the final-inventory 5,000-vector
control to obtain `Delta_5k`; do not substitute its E68 or pre-load value. For
each larger size publish `Delta_N`, `Delta_N - Delta_5k` and, when the baseline
is positive, `100 * (Delta_N / Delta_5k - 1)`. Also publish the two arms' absolute
RU, their ratio, the percentage saving relative to unsplit RU and dollars saved
per million searches at the stated planning rate.

Predeclare the point classifications relative to a positive `Delta_5k`:

- **Benefit grew:** `Delta_N > 1.2 * Delta_5k`.
- **Benefit held:** `0.8 * Delta_5k <= Delta_N <= 1.2 * Delta_5k`.
- **Benefit shrank:** `Delta_N < 0.8 * Delta_5k`; disclose any reversal to an
  unsplit advantage explicitly.

Distinguish those point descriptions from robust classifications. For each
size derive a conservative observed difference range from the three run
medians: `[min(unsplit_runs) - max(split_runs), max(unsplit_runs) -
min(split_runs)]`. If the entire 5k difference range is positive, the observed
ratio range is the minimum and maximum of the four endpoint quotients
`low_N / low_5k`, `low_N / high_5k`, `high_N / low_5k` and
`high_N / high_5k`; this also handles a negative large-library benefit. Call
growth robust only if that range is wholly above 1.2, held only if wholly
within 0.8–1.2, and shrinkage robust only if wholly below 0.8. Otherwise call
the band comparison borderline and show the point description and ranges. If
`Delta_5k` is nonpositive, report the absolute differences and any sign changes
without a relative-growth percentage. If its point value is positive but its
range includes zero, label any point percentage unstable and the classification
borderline; do not calculate a proportional range across a zero denominator.
These ranges describe the observed repeats and are not confidence intervals.
All benefit classifications require the relevant routed measurements to meet
the quality floor, with complete metrics and usable diagnostics.

The following twofold-benefit bar is a **separate** cost-ratio description for
each mandatory size and route, not the test of whether the absolute sharding
benefit grew with library size:

- **Strong measured RU benefit from sharding:** unsplit primary RU is at least
  twice split RU, the smallest unsplit run median is at least twice the largest
  split run median, and both meet the quality floor with complete metrics and
  usable diagnostic evidence. Report recall differences rather than claiming
  equal accuracy merely because both pass the floor. An all-mandatory-sizes
  statement requires both 50,000 and 100,000 to qualify.
- **No material observed RU difference:** unsplit/split primary RU is between
  0.8 and 1.2 and every corresponding run-median ratio is also within that band,
  with both meeting the quality floor. This is RU similarity on this workload;
  latency, documents read, recall or index modes may still differ.
- **Other outcomes:** an unsplit/split RU ratio between 1.2 and 2.0, a ratio
  at least 2.0 that fails the repeat-range criterion, a benefit at only one size,
  a benefit only without routing, an unsplit advantage,
  changing modes, low recall,
  incomplete diagnostics or unresolved build lag receives a numerical, bounded
  account rather than being forced into either category.

Report each route separately. If removing the prefix makes the unsplit request
materially dearer while the same filtered request with a prefix remains cheap,
that is evidence the request's routing matters at that measured size; it does
not prove the SQL filter alone is sufficient or insufficient in every layout.
The main conclusion concerns the routed application shape; a benefit confined
to the no-prefix diagnostic does not establish an application dependency.
No-prefix comparisons use their declared common question set and remain a
secondary diagnostic; they cannot replace the routed `Delta_N` comparison.

For the existing small probes, publish post-load minus fresh pre-load RU,
documents and latency, as absolute and signed percentage drift, with the old
E68 values alongside. Call RU stable only if both small probes in both arms
remain within 20% of their fresh baselines and pass the quality floor. Report
actual drift even when it is far below that band. A moving small control limits
the growth/causal interpretation; do not hide it behind the large-library result.

Report the first tested library size and query shape in which DiskANN is observed.
If it appears at 100,000 but not 50,000, describe those observed points; do not
claim an exact or universal crossover threshold. A lower RU ratio does not by
itself decide whether preview dependence is worthwhile for a real traffic volume:
also show absolute savings and dollars per million searches at the stated rate.

## Cost, unattended duration and stop conditions

The user confirmed a **$20 budget for this extension alone**, excluding E68's
already incurred cost, and a twelve-hour elapsed limit for the initial work,
with the fixed delayed-check exception defined above.
Include this preflight, loads, diagnostics, exact references, failed attempts,
query embeddings and incremental storage in the accounting where observable.
State unobserved charges explicitly. Dollar arithmetic below uses the same
illustrative $0.25 per million RU as E68, not verified account-specific pricing.

Start the elapsed execution timer with the first approved run action, including
question preparation/embedding, repeated preflight and the fresh baseline. It
includes all subsequent loading, waiting, queries and retries. Protocol review,
driver development and time awaiting the initial approval are outside that
execution timer; preflight request charges still enter the new cost ledger.
Preserve the original start and charge ledger across checkpoints. A stop for a
limit decision is not permission to reset either counter and continue.

| Coverage | New vectors written across arms | Projected writes | Timed writes from E68's observed rate |
| --- | ---: | ---: | ---: |
| Required 50,000 + 100,000 | 300,000 | 27.56 million RU / $6.89 | 3.4 hours |
| All four new sizes | 370,000 | 33.99 million RU / $8.50 | 4.2 hours |

Allow 15%–25% variation in loading and graph construction. The all-four planning
forecast is **about $8–12 of new request charges and six to nine hours for the
initial work**, including checks, references and the shared settling window
(thirty to sixty minutes for readiness, then five minutes if stable). The
central library-sized-scan extrapolation is roughly $9.4 of known
requests. Model/storage/unobserved costs use the remaining allowance; this is
not a complete bill or provider-enforced cap. The delayed check is additional
elapsed time: with roughly four to five hours of loading, its earliest start is
roughly sixteen to seventeen hours after execution begins. Its query charges
and the intervening incremental storage remain in the same $20 budget.

There is meaningful time uncertainty. The uncapped 360-call no-prefix block
would take roughly 5.6 hours at E68's extrapolated whole-container scan timing.
If each library's first question triggers the declared fallback, that block is
instead 90 calls, roughly 1.4 hours at that timing. Other slow behavior can still
approach the initial twelve-hour limit, and scan-like primary queries may make
completion within it impossible. These forecasts extrapolate E68's client scan
timings and are not upper bounds for the larger workload.

Keep a new extension-only charge ledger and elapsed timer. Before each write
batch, optional library and query block, forecast all remaining required work
using the observed charges/rate and query timings as they become available.
Retain checkpoints and **stop to ask the user before launching further work if
the projected total exceeds $20 or the initial work exceeds twelve hours**.
The approved delayed check has only the elapsed-time exception defined above.
Reforecast after the first measured question of each previously unseen
size/shape; do not discover an
overrun only after completing all twenty questions.

Use **64 million known RU ($16 at the planning rate)** as an additional pause
threshold, retaining at least $4 for model/storage/unobserved charges within the
$20 ceiling. Pause earlier if those other costs or the forecast require it.
At twelve hours stop initial work safely even if the estimate was too optimistic;
only the fixed delayed check is exempt from that elapsed guard. Include in-flight
retry limits and incomplete-query handling so a slow request cannot quietly
bypass the applicable guard. Never raise the monetary limit, reduce primary
repeat coverage, tune away a bad result or drop an already measured point
without the user's decision. The declared paired five-question no-prefix
fallback is the sole authorized coverage reduction and needs no new approval.
A capped run is published as partial if it cannot be completed.

## Deliverables, limits and retention

Create a new evidence entry at the next unused number, its own sanitized JSON
receipt and full measurement tables. Keep E68's entry and record exactly as they
stand. Bind the new record to E68's receipt hash, this protocol's hash, the
content-free preflight, code/input/question hashes, canary provenance, baseline,
inventory, cost ledger and independent recomputation.

For each tested size, show split/unsplit by library-scoped/global request in RU,
documents read, client latency, recall and observed index modes. The global
question is one common control at one final container inventory: if its values
are repeated beside multiple library sizes, explicitly mark them as the same
shared observation, not independent per-library measurements. That fixed global
question matches only the old first-probe question, not the twenty-question set
for a new library. Do not present a global/new-library median ratio as a matched
scope-omission effect. Add the separate
5,000/50,000/100,000 prefix-on/prefix-off table and small-probe drift table.
Provide one publication sentence bounded by the actual sizes, routing, quality,
index modes and inventory observed. It must state whether the absolute
sharding benefit grew, held or shrank relative to the final 5k control, or that
the predeclared comparison was borderline/inconclusive; do not write its
conclusion in advance. Separate the twofold cost-ratio description from that
size-dependent conclusion, and reconcile it with the delayed sentinels.

After the delayed check, independently recompute the public receipt from the
private raw evidence, review the results and reconcile the evidence entry,
tables, cost/coverage account and affected documentation with what actually
ran. Preserve deviations, triggers and partial results rather than rewriting
the approved protocol to match them. Commit the reviewed result locally, then
reconcile E68 and the new entry with the latest main once in an isolated local
integration checkout/branch. Preserve newer main work, validate the combined
tree and show the resulting changes to the user before any push. Do not merge
into main or push before that review. Retain the databases until the user's
separate deletion instruction.

The corpus is real but library membership and document grouping are synthetic.
There is one library per new size, one retained build per arm, one insertion
history and, subject to verification, one physical range. The test cannot establish
all workplace-library distributions, a universal graph crossover, concurrency,
multi-range behavior, semantic answer relevance or an entirely GA configuration.
Spherical quantization remains unchanged. Missing or changing diagnostics and
unresolved graph-build lag narrow what can be concluded.

Keep raw evidence, identifiers, corpus locators and ownership bindings privately
outside every checkout, with durable copies excluding credential caches and the
original corpus. Do not name the source project. **Delete neither database,
create no replacement resources and push nothing as part of this protocol or
run.** Separate instructions govern publication and eventual cleanup.
