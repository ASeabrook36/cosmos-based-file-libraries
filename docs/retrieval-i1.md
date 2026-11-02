# I1 — selector confirmation on the retained foundation

I1 starts from reviewed stage-one commit `d65a225` and the user's authorization
to run live lifecycle checks before the reserved held-out evaluation. The
[frozen plan](testing/retrieval-i1-controls.json) was committed before cloud
measurement or answering. Its SHA-256 is
`d87f983214e8f8ae2b241dfe08db12be831839eb35c4b22c2b7056a64772e643`.

**Completed, 2026-11-02 (E66):** all 13 live lifecycle gates passed before the
64 held-out answering turns. Source/answer quality and selector usability passed
their frozen gates. Provider input fell **18.8542783%**, below the required 20%;
the efficiency gate failed. Retain selector handles as an experimental opt-in
with bounded usability evidence. Keep current compact defaults, representation
and physical policies. No R3 comparison or default promotion follows from I1.

## Comparison and acceptance

The only comparison variable is `selectors=handles` versus the existing selector
behavior. Both cells use the same compact response format, current physical
foundation, vector search, Microsoft layout profile, four original held-out
DOCX sources (`d09`–`d12`) and frozen metadata. All sixteen held-out questions
(`r0-33`–`r0-48`) run twice per cell: baseline then selectors, followed by
selectors then baseline. That is 64 independent answering turns. R0/P1 libraries
are preserved; disposable lifecycle sources are separate development originals.

The answering model stays GPT-6.1 Sol `2026-09-29`, Azure Responses, medium
reasoning, 8,000 output tokens, three attempts, 200,000 conversation tokens,
`store=false` and no automatic truncation. Embeddings remain
`text-embedding-3-large` revision 1, 3,072 dimensions, with the existing float16
query conversion. Default/max search counts remain 8/20. Actual runtime and
management-plane before/after receipts bind these controls to invoked services.
Implementation model choice does not change evaluation controls.

The provisional efficiency target remains 20% fewer aggregate provider input
tokens, with no loss of baseline-delivered original region IDs or applicable
source/answer quality per task and repeat. Selector error counts must not regress;
strictly fewer unsupported expansions are a separately reported usability benefit.
Unknown applicable judgments make acceptance inconclusive. No gate automatically
changes defaults or promotes an architecture. Cache, tool choices and provider
rate waits preclude a steady-state latency interpretation.

## Live lifecycle prerequisite

The harness creates owned disposable shared/personal libraries in the existing
isolated, canary-validated services. Two loopback API instances have independent
Cosmos/Blob clients and session caches. Actual Microsoft ingestion uses the
existing managed-identity worker and workflow. Only owned library scopes may be
mutated; no receiver consumes unrelated messages and no worker configuration changes.

The thirteen planned gates cover caller authentication, personal binding on every
retrieval endpoint, foreign selectors, pinned selector round trips, metadata
propagation and an in-flight read, stale generations/revisions, replacement and
deletion during reads, failed enqueue/retry paths, complete purge, and observed
freshness across the two clients. Explicit one-shot barriers pause after actual
Blob reads, and scope-bound queue faults fail before sending. These are controlled
live races/faults, not naturally occurring outage or performance measurements.
Private HTTP and storage observations permit independent review of each assertion.

The current API supports localhost dev authentication. Production Entra platform
authentication, broker-level worker redelivery, global immediate revocation under
Session consistency and sustained concurrency remain separate gates. Passing
bounded lifecycle observations cannot close those broader claims.

## Execution and review

Each implementation track has its own worktree, environment and private runtime.
The coordinator owns resource bindings, model reservations, exclusive cloud
windows, independent review and cleanup. Setup performs no provisioning or
credential loading. Use `sh scripts/dev setup` and `sh scripts/dev check -q`.

**Historical execution sequence.** These commands document the completed run;
do not reuse them to execute the consumed held-out set. After explicitly loading
private isolated settings, the lifecycle harness ran with original development
sources and a fresh private output directory:

```sh
.venv/bin/python -m scripts.retrieval_i1_lifecycle \
  --plan docs/testing/retrieval-i1-controls.json \
  --preflight "$I1_PRIVATE/bound-preflight-private.json" \
  --old-docx "$R0_CORPUS/source-1.docx" \
  --new-docx "$R0_CORPUS/source-2.docx" \
  --out "$I1_PRIVATE/lifecycle"
```

The preflight is the private settings-bound receipt, not the sanitized public
infrastructure summary. The coordinator must reopen the live receipt and its
private evidence before accepting it. The execution authorization binds the exact
lifecycle bytes, frozen plan, actual API settings, new evaluation library, output path and an external
single-execution ledger. It records this review, not a new user permission request.
Only after that acceptance were the four held-out originals ingested/frozen and
the answering matrix run:

```sh
.venv/bin/python -m scripts.retrieval_i1 replay \
  --config "$I1_PRIVATE/config.json" \
  --deployment "$I1_PRIVATE/answering-before.json" \
  --runtime "$I1_PRIVATE/api-before.json" \
  --lifecycle "$I1_PRIVATE/lifecycle/receipt-private.json" \
  --authorization "$I1_PRIVATE/authorization.json" \
  --output "$I1_PRIVATE/replay"
.venv/bin/python -m scripts.retrieval_i1 finalize \
  --deployment "$I1_PRIVATE/answering-after.json" \
  --runtime "$I1_PRIVATE/api-after.json" \
  --output "$I1_PRIVATE/replay"
.venv/bin/python -m scripts.retrieval_i1 score \
  --source "$I1_PRIVATE/replay" --gold "$R0_PRIVATE/gold.json" \
  --reviews "$I1_PRIVATE/reviews.json" --output "$I1_PRIVATE/scorecard.json"
```

The original development-only drivers retain their guards. The new driver claims
the external ledger before any answering call; failures remain partial and never
silently restart. An infrastructure-only continuation requires a separate recorded
coordinator decision; answers cannot be used to tune prompts, selectors or exclusions.

Reviews compare original DOCX regions with the actual final provider evidence,
then independently assess task completion, factual support, citations and source
relationships. Preserve exact region sets, merged table relationships and ordered
sections. Two labels with empty frozen direct-paragraph text require positional
verification of original nested placeholders; neither empty-string matching nor
a complete-file flag proves them delivered. The pack
contains 102 distinct regions and 116 task-region incidences, and has no catalogue
enumeration task. Human-blinded accuracy and metadata catalogue completeness cannot
be inferred from this answering matrix.

Source bytes, credentials, environment identifiers, provider messages and reviewer
notes remain outside Git. Publish only reviewed counts, hashes, judgments and
limitations, and scan using a private identifier deny file before committing.

## Results and independent acceptance

The [live lifecycle report](retrieval-i1-lifecycle.md) passed all 13 gates on the
first live attempt. The coordinator reopened 94 HTTP receipts and 13 direct SDK
observations, verified that every request stayed in the three owned scopes and
that errors returned no source body, and independently checked metadata, failed
enqueue/replacement generations and deletion inventories. A separate reviewer
also rechecked the raw lifecycle evidence. See the
[independent audit](../records/retrieval-i1-lifecycle-independent-20261102.json).

Four hash-verified held-out originals were then ingested through the existing
Microsoft worker into one fresh evaluation library. They published five chunks,
all generation 1/revision 2. These small-library ingestion counters establish no
ANN readiness, steady-state cost or large-library behavior. The
[ingestion receipt](../records/retrieval-i1-ingestion-20261102.json) keeps missing
provider/connector counters unknown.

The [held-out report](retrieval-i1-replay.md),
[scorecard](../records/retrieval-i1-scorecard-20261102.json) and
[runtime receipt](../records/retrieval-i1-runtime-20261102.json) cover all 64 turns
and 208 provider calls. No case was excluded or rerun; neither cell retried a
provider call or hit a read-budget refusal.

| Both repeats combined | Baseline | Selector handles |
| --- | ---: | ---: |
| Completion / support / citations, each | 32/32 | 32/32 |
| Applicable source relationships | 10/10 | 10/10 |
| Required source-region occurrences | 232/232 | 232/232 |
| Provider input tokens | 709,160 | 575,453 |
| Provider output tokens | 12,383 | 12,764 |
| Unsupported expansion errors | 4 | 0 |
| Model calls | 104 | 104 |

The four baseline scope errors occurred on `r0-38` and `r0-39`, once per repeat;
the baseline recovered and answered successfully. Selectors avoided that friction
without observed source or answer loss. The 18.8542783% provider-input reduction
is measured separately from serialized evidence estimates and must not be rounded
into a passing 20% result. Summed observed times were 352.83 seconds baseline and
381.84 seconds selectors. Baseline RU is incomplete (known subtotal 397.72), while
selector retrieval RU was 379.80. Cache state and backend placement were not
controlled, so no latency or billed-cost benefit is established.

Two reviewers, independent of the replay implementation owner, assessed all
answers against original sources and actual provider evidence. The coordinator
reopened the code and control receipts, spot-reviewed relationship/form cases
`r0-34`, `r0-41` and `r0-47`, and independently reproduced the complete scorecard
exactly from both review files. This is unblinded assistant review of four DOCX
sources, not a human accuracy estimate, statistical equivalence result or a
catalogue-enumeration benchmark. The
[review receipt](../records/retrieval-i1-review-20261102.json) binds the private
reviews and records those limits.

**Frozen annotation limitation:** `d12:p38` and `d12:p43` have empty direct
paragraph text/runs in the frozen parser output, but the original DOCX contains
underscore placeholders inside nested Word content controls. Original XML,
field positions and actual delivered placeholder lines were checked explicitly;
the four placeholder occurrences per cell were not credited from empty-string
matching. Unticked controls were likewise checked against original checkbox
states. The initial private review checklist's blank-paragraph interpretation
was corrected before answer review; frozen tasks, gold, prompts and selectors
were unchanged. A future benchmark version should annotate nested controls
explicitly. The consumed held-out set cannot serve as fresh confirmation after
tuning to these results.

## Preservation, cleanup and local reproduction

The [preservation check](../records/retrieval-i1-preservation-20261102.json)
exactly matches the before/after catalogue, tools, metadata, source hashes and
generation/revision snapshots for the three R0 libraries (15 documents) and the
P1 library (five documents). Original model deployments and the existing worker
configuration remained unchanged. Ongoing scale resources were not accessed.
The [cleanup receipt](../records/retrieval-i1-cleanup-20261102.json) confirms
removal of the owned temporary answering deployment and all three disposable
lifecycle scopes. Local API processes stopped. The new four-source I1 evaluation
library remains frozen for reproduction; it is the only retained I1 data scope.
Purge checks cover live records, chunks and Blob prefixes; service-retained
soft-deleted backups are not evidence of permanent erasure.

The merged checkout passed **1,089 tests**, with 14 existing warnings, plus lint,
formatting and privacy checks. The
[validation receipt](../records/retrieval-i1-validation-20261102.json) records
ancestry, protected paths and the 60-entry private environment-identifier scan.
No application, infrastructure, query policy, automatic setup, lockfile or frozen
R0 benchmark file changed in I1.

For offline scoring, use the exact measurement implementation, not the merged
coordinator checkout: the scorer hashes every top-level Python measurement
script, so adding the separate lifecycle harness intentionally changes that
set. The coordinator's first re-score from the combined checkout refused that
mismatch; the re-score from the measured checkout matched exactly. Do not bypass
the guard or alter the receipts. With private captures available, this requires
no model deployment, credentials or cloud calls:

```sh
git worktree add --detach "$I1_RECHECK_WORKTREE" 0c7e6a2e9e2034a33939121627ed3684e44362a7
cd "$I1_RECHECK_WORKTREE"
sh scripts/dev setup
.venv/bin/python -m scripts.retrieval_i1 score \
  --source "$I1_REPLAY_PRIVATE/replay" --gold "$R0_PRIVATE/gold.json" \
  --reviews "$I1_REPLAY_PRIVATE/reviews.json" \
  --output "$I1_RECHECK_PRIVATE/scorecard.json"
```

Private paths must already be defined outside the repository; automatic setup
loads no cloud credentials and provisions no resources. The external execution
ledger remains consumed. The replay commands above document the completed run,
not authorization to reset that ledger or run the held-out tasks again.

## Next integration decision and open gates

Retain selector handles as an independently selectable usability improvement;
keep the production/default behavior unchanged because the declared efficiency
target was not met. Existing evidence does not justify a new representation or
container comparison, so R3 remains unstarted. A later foundation or evidence-
format candidate still needs its own justified comparison and the roadmap's
full four-cell integration confirmation.

Before a production rollout, validate hosted Entra authentication, real broker
redelivery, replacement/deletion under sustained concurrent clients and the
required cross-instance revocation policy. These were not established by local
dev authentication, scoped pre-send faults or two-client barrier observations.
Large libraries spanning physical ranges, global top-k merging, many-range load,
metadata/catalogue completeness, non-DOCX/image workloads, complete billed costs
and controlled latency remain untested here. Any further efficiency or general
quality claim needs a newly frozen representative confirmation set; keep the
current held-out record intact and do not tune and retest it as unseen data.
