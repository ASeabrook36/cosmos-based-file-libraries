# Hybrid versus vector-only source and answer quality

This experiment starts from reviewed integration commit
`3980333ee8453af48c7133cab24522868701d1cc` on
`codex/hybrid-quality-comparison`. Recall is the primary objective. No further
format, representation, model or efficiency tuning belongs in this comparison.
R0/I1 questions are consumed development/regression evidence; confirmation uses
new original sources and questions frozen before measured retrieval or answering.

## Restart status: fresh health gates passed

On 2026-11-03 the bounded [read-only recheck](../records/hybrid-quality-health-recheck-20261103.json)
succeeded on both the formerly failing scoped query and the exact formerly failing
chunk read, with unchanged container identity and policies. No account creation or
capability-enable retry was made. The cause of the earlier failures remains unknown.

The [restart receipt](../records/hybrid-quality-restart-20261103.json) records
fresh real-text writes, point/scoped reads, vector and hybrid queries, exact-owned
cleanup and a late absence check. All 19 actual mounted-workflow/worker/model
binding checks passed, and protected R0/P1/I1 snapshots remain unchanged.
The new [executable restart gate](hybrid-quality-restart.md) binds these receipts
and a predeclared partial-ingestion cleanup plan before any new source upload.
Application code, policies, dependency lock and frozen comparison controls remain
unchanged; main's selector integration was not rebased into this branch.

Confirmation has not started at this checkpoint. The smoke used separate real
text and does not establish corpus readiness, recall, answer quality or steady-state
performance. The next step is the unchanged eight-source ingestion and preparation
gate, followed by the 96-search direct comparison and 192-turn answering schedule.

## Initial preparation: blocked before confirmation

The [sanitized receipt](../records/hybrid-quality-preparation-20261102.json)
records **zero of 96 planned direct searches and zero of 192 planned answering
turns**. All 48 questions remain unconsumed confirmation data. There are no paired
quality wins, losses or ties, and no evidence here selecting either ranking mode.
The next step is to restore a healthy instance of the same foundation and execute
this unchanged pack. No R3 or efficiency-tuning comparison was started.

Preparation encountered two service failure types across these attempts:

1. The retained isolated account returned HTTP 500 with the previously recorded
   E17 `float16` policy-conversion error. An ordinary owned-library `SELECT TOP 1`
   metadata query also failed, while container-policy readback worked and a
   nonexistent-item point read returned 404. This is below ranking/presentation;
   it establishes neither the cause nor a vector-versus-hybrid result.
2. Two atomic creation attempts for the same planned replacement account used the
   same proven ARM template, region, serverless mode and container definitions.
   Both were rejected with `Invalid capability EnableNoSQLVectorSearch`, including
   the retry after the operator removed a subscription spending limit.
3. A separately reviewed two-stage attempt created that same empty account
   successfully, then received the identical capability error on Microsoft's
   documented enablement update. The update explicitly retained serverless.
   No database, container, role, queue or worker was provisioned for that target.
   No fresh canary or answering deployment ran. Guarded deletion of the empty
   owned account followed. Independent HTTP 404 checks and the later exact-target
   verifier confirm its absence; the planned queue and worker are also absent.

The actual subscription readback after the billing change was `Enabled` with
`spendingLimit: Off`, and unchanged ARM-template validation passed. This establishes
that the change was visible; it does not establish that all resource providers had
recovered. The initial and later scoped data-plane checks still returned E17,
including a final check 30.22 minutes after the first post-change check.
Microsoft documents both the [feature enablement
sequence](https://learn.microsoft.com/en-us/azure/cosmos-db/vector-search) and a
possible [resource-management delay after
reactivation](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/subscription-disabled#after-reactivation).
The receipts do not establish whether billing reactivation caused or resolved any
part of the backend fault. No feature, index type, model or source control was
substituted to obtain an evaluation result.

All eight source uploads were acknowledged. Seven source-bound Microsoft plans
finished, containing 33 possible generation-1 chunk IDs; only three documents
published successfully before fencing, reporting six published chunks in their
document counters. This is not a verified inventory of partially written chunks. The failed run retains all 26 observed
worker ingestion attempts: three published, 21 errored and two stopped. Known
instrumented chunk-store charge across those attempts is 28,450.86 RU. The counter
includes collision-verification reads as well as writes. Three attempts have
complete charge visibility, 21 partial and two unavailable. Eighteen attempts have
broker delivery numbers above one; that is distinct from provider retry counts.
Microsoft embedding request/token/retry meters
were unavailable on all seven results. These are preparation counters, not billed
cost totals or retrieval performance.

The protected R0/P1/I1 snapshots and existing worker configuration still match.
The actual embedding deployment is unchanged. The ordinary DELETE fence hides
only the failed new library. The reviewed [exact-scope recovery](hybrid-quality-failed-scope.md) proved the
observed generation-one candidate universe from retained plans and terminal
attempts. Its live precheck found two chunks, then stopped on the third point
read with the same HTTP 500 before any delete attempt. Thirty candidates were not
reached. The failed library remains fenced, with live originals/checkpoints and
private archives retained; complete chunk/record/blob absence and late cleanup
checks remain unverified. Failed SQL or point reads are never an empty inventory.

## Cross-session consistency review

A read-only review with the modular-ingestion, C1/M1 and index-versioning chats
confirmed the [deployment and process comparison](../records/hybrid-quality-consistency-review-20261102.json).
The earlier successful recovery and this run both used Azure CLI `az deployment
group create` with compiled ARM JSON. Historical traces identify CLI 2.86.0;
the current installed executable is also 2.86.0. The copied ARM template came
from Bicep 0.48.1.52935. Parsed templates match after only nested-deployment name
substitution; parameters differ only in account name. Declared Cosmos resource
APIs remain 2025-10-15 and nested-deployment APIs 2025-04-01. No per-invocation
executable hash or outer CLI-selected HTTP API version was captured.

Both atomic submissions succeeded at the CLI boundary, then failed in Azure's
recorded account-creation operation. The staged capability update was a new
recovery experiment, not a previously successful project procedure. Its CLI SDK
warning does not explain the preceding ARM-template failures. No deployment-tool
change has been established as the cause or as a remedy.

The review found a preparation omission: the inherited canary and unchanged
policy/incarnation proved identity, not current data-plane health. A fresh owned
health check should have preceded the eight uploads. It could have detected the
known E17 failure earlier; it does not establish the cause of that failure.
The worker/model checks were preserved, but this preparation also lacked M1's
independent reread of the mounted Microsoft workflow package and parameters.

M1 finished its live preservation checks more than five hours before this run;
index versioning performed no cloud operations. That rules out overlap from those
two activities, not unrelated tenant activity. The ranking experiment remains on
the pinned foundation. Future versioning integration must reconcile duplicate
E67 documentation identifiers and adapt the direct-capture instrumentation to
request-scoped searchers; its changed source hashes intentionally fail this
experiment's frozen controls. These are prospective integration issues, not
explanations for the current service failures.

## Controlled comparison

Both arms read one newly owned immutable library using the retained
`microsoft-indexer-layout-v1` profile, current physical policies and stored vectors.
Both use compact evidence and the accepted selector handles. Extraction, source
bytes, metadata, chunk boundaries, source generations, embedding model/revision,
filters, result limits and neighbor expansion remain equal. The original I1
18.8542783% input benefit remains accepted and is not retested as a selection gate.

Only search mode and its required working route differ. Vector search uses the
library partition prefix; hybrid omits the explicit partition option while
retaining the same single-library equality and TOP bound. Both retain the current
float16 query conversion, no embedding projection and default RRF/index settings.

The existing client lexical extractor lowercases the question, extracts ASCII
alphanumeric runs, removes duplicate terms in first-occurrence order and keeps
at most sixteen. It does not remove stopwords, preserve phrases or punctuation in
identifiers, or perform client-side stemming. Full-text policy is en-US; the
provider performs its own text analysis. RRF weights are omitted/default. The
16-term cap can omit later terms. These behaviors are frozen; they cannot be
changed after examining confirmation outcomes. No preparatory query correction
was identified in the initial audit.

## Measurement order and interpretation

Direct retrieval precedes answering. The primary direct comparison uses top 8
with neighbors enabled, identical cached question embeddings and alternating
arm-first order by question. The application's candidate window remains 16,
followed by active-generation visibility checks and the requested top 8. Initial
hits and neighboring source evidence are scored separately. Required original
region/fact recall, complete task evidence and useful evidence are separate
measures; ANN agreement with exact vectors is not semantic source recall.
Unlabeled material is unjudged, not automatically irrelevant. Unanswerable tasks
have no required-answer-region recall denominator; appropriate abstention and
absence-context usefulness are reviewed separately.

Answering uses frozen Sol `2026-09-29`, Responses, medium reasoning, 8,000 output
tokens, three attempts and a 200,000-token allowance per independent turn, with
storage and automatic truncation disabled. Two repeats interleave both arms per
question and reverse order in repeat two. Actual tool choices may differ in
response to retrieved evidence; they are preserved, not normalized away. Report
full-read recovery of a search miss separately from initial search recall.

Source/answer judgments cover completion, factual support, citation accuracy,
required-source coverage and appropriate abstention. Arm labels are hidden in
review packets where feasible; report the actual blinding and independent review
limitations. Each question is the paired unit; repeats are not new independent
questions. Aggregate and family-level wins/losses/ties retain their denominators.
The final recommendation follows source recall and answer quality, with measured
usage and incomplete cost/latency counters alongside it. No 20% efficiency veto
applies. Conditional recommendations must name the observed evidence and remain
explicit about any need for prospective confirmation.

## Cloud and safety boundaries

The coordinator owns cloud reservations and serial measurement windows. Existing
isolated validation services may host the new library; no frozen R0/P1/I1 scope,
ongoing scaling resource, container policy or existing worker configuration is
modified. New containers require a new canary. Reused containers require matching
incarnation/policy receipts. Actual-corpus readiness and meaningful hybrid ranking
must be inspected before measurement; missing readiness evidence forbids
steady-state performance claims rather than becoming an invented passing result.

The subsequent M1 report at `46ae45a` found scheduled orphan discovery targeting
a literal Blob container instead of its configured container. M1 changed no
application code and did not close that gate. This comparison does not repair or
waive it and makes no production lifecycle acceptance claim. Preparation waits
for completed source publication; owned-scope cleanup must be independently verified,
including late-result checks, without changing shared scheduled jobs or claiming
manual cleanup proves scheduled discovery. Ordinary retention remains in force.

Raw sources, gold facts, provider messages, identifiers and credentials stay in
private runtime directories. Public artifacts contain reviewed hashes, benchmark
identifiers, counters, judgments, commands and limitations only. Frozen manifests,
actual runtime/deployment bindings and a single-use execution ledger prevent
selective reruns or silent control changes. All failed attempts remain evidence.

## Frozen preparation and ownership

The inherited container canary still matches actual policies and incarnation.
The deployed ingestion worker configuration is unchanged. Read-only snapshots of
all three R0 libraries (15 documents), the P1 library (five documents), and the
I1 library (four documents) match exactly. The [prospective plan](testing/hybrid-quality/plan.json) binds all eight original
source hashes, 48 questions, 104 original regions, 62 requested facts, the private
gold hash, and the query/answering controls. The source pack and both independent
original-source reviews precede all confirmation retrieval and answering.
Meaningful original filenames and supported title tags are retained as normal
metadata. The corpus is a purposeful procurement-contract pilot, not a broad
enterprise accuracy estimate.

Two distinct preparation queries are frozen separately. Actual publication must
yield more than 16 chunks, with at least six of eight documents having multiple
chunks. These gates prevent top eight from trivially covering the entire library.
The local estimate of 148,863 source tokens and 32 five-thousand-token units is
not a provider chunk count. Missing index-execution evidence prevents a
steady-state performance claim.

The coordinator created one new evaluation library. It will create one
exact-revision temporary Sol deployment only when answering is ready to run;
none was created during this preparation. An external ownership ledger precedes
creation/uploads. Cleanup uses ordinary deletion and verifies scoped records,
chunks and live Blob prefix absence twice after ingestion has completed; soft
delete retention remains. Existing libraries, deployments, worker and scheduled
jobs remain unchanged. Raw sources and failed attempts remain private.

The historical Phase 5 suggestion to add a stop list is deliberately deferred:
this experiment compares the existing implementation under the user-requested
frozen controls. No stop list, weights, physical index, representation or
application-default change is included. The service failures above occurred before those execution windows. The fixed
questions, gold, controls and unused execution ledgers remain ready for a healthy
replacement environment.

## Executable restart handoff

Keep this branch's source pack and plan byte-for-byte. Keep raw runtime material
outside every checkout. Local setup remains free of cloud provisioning and
credentials:

```sh
sh scripts/dev setup
sh scripts/dev check
HQ_PRIVATE=/private/tmp/fli-hybrid-quality-restart
.venv/bin/python -m scripts.hybrid_quality_corpus fetch \
  --source-dir "$HQ_PRIVATE/sources" --gold-output "$HQ_PRIVATE/gold.json"
.venv/bin/python -m scripts.hybrid_quality_direct audit
```

The operational dependency is an isolated account that accepts the required
vector capability and serves the unchanged float16 container policies. The
private ARM/data-plane receipts retain activity IDs for investigation. The
owned staged replacement was empty when deletion was submitted, and final HTTP
and exact-target checks verified account absence. The failed preparation library
remains fenced. Check those retained private receipts before further provisioning;
do not infer absence from a failed command or create additional targets to bypass
this error. Microsoft recommends contacting support when resource-management
delays continue more than 30 minutes after reactivation. A private, unsent support
draft retains the relevant request IDs and exact reproduction details.

Once that dependency is resolved, follow these committed command contracts in
order:

0. Before any full-corpus upload, require fresh data-plane health in an owned
   smoke scope, including actual real-chunk writes/reads and working scoped,
   vector and hybrid queries. Reused containers need this too; a historical
   canary, policy readback or missing-item 404 is insufficient. Rebind the mounted
   workflow package/parameters and predeclare cleanup for partial ingestion.
   The [restart preflight](hybrid-quality-restart.md) now enforces these checks
   before the ingest command can create a library or upload a source. A historical
   canary alone cannot authorize new ingestion. A new container still requires
   the full canary.
1. Run `scripts/canary.py` on each new container with the separate real-text canary
   corpus; require all checks, exact actual policy/incarnation binding and cleanup.
   Capture actual embedding deployment/version before and after. The fresh path
   records `newContainerCreated=true`, `freshCanaryPassed=true` and
   `inheritedCanaryPassed=false`. Readback alone is insufficient health evidence.
2. Use the [owned-scope handoff](hybrid-quality-scope.md) to create a new library and
   ingest all eight exact originals. Accept only all-ready generation 1, retained
   metadata/provenance, more than 16 chunks and at least six multi-chunk documents.
   Verify the cloned worker, model bindings and protected scope snapshots.
3. Follow [direct measurement](hybrid-quality-direct.md): run only the two frozen
   preparation queries first, establish meaningful hybrid ranking and source
   boundaries, cache each confirmation question's embedding once, then run the
   one 96-search paired schedule. Private preflight includes `canaryReceiptPath`,
   its exact hash and actual target fingerprint. Review original evidence and
   usefulness independently before accepting the immutable direct receipt. A
   quality miss is a result, not permission to tune or repeat a case.
4. Follow [answer replay](hybrid-quality-replay.md) in an exclusive window. Verify
   the exact Sol revision and actual embedding deployment, launch both checked
   APIs, bind their loaded code/settings/stored rows to direct evidence, and run
   the fixed 192-turn schedule once. Recheck both deployments, both processes and
   source rows after the last turn. Keep the two repeats correlated by question.
5. Export opaque review packets outside the finalized run, judge all requested
   quality dimensions against original sources, independently review failures and
   disagreements, report initial versus expanded and full-read recovery evidence,
   then decide recall first. Preserve raw failures and unavailable counters.
6. Remove only ledger-owned data/resources and verify protected snapshots and
   late artifacts. A manually recovered failed prefix does not validate ordinary
   worker cleanup, broker redelivery or the open M1 scheduled-reconciliation gate.

Acceptance requires the complete source/query/answer matrices and fixed controls,
not a preselected winner or a 20% efficiency threshold. Until these gates run,
required-region recall, useful-hit precision, answer quality, paired outcomes and
comparative cost/latency remain **untested**.
