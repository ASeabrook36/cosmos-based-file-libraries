# R1, R2 and P1 coordination

Subsequent result: [selector-only I1 confirmation](retrieval-i1.md) completed the
reserved held-out evaluation on 2026-11-02 (E66). This stage-one report preserves
the decisions and remaining gates as they stood before I1.

The three experiments descend from frozen R0 commit
`a449dc01e5acde6391bfc1aa50ca07670d54097d`. They have separate worktrees,
branches and private runtime directories. The coordinator owns shared documentation,
cloud provisioning, quota, measurement windows and cleanup. R3 and held-out
evaluation are not part of this stage.

## R0 audit before comparison

The [artifact audit](../records/retrieval-stage1-r0-audit-20261101.json)
reopens all six original cells: 36 executed questions, frozen case definitions,
before/after source snapshots and cell budgets match. Re-scoring with the stricter
checks preserves every reported R0 quality count. Original R0 receipts remain
unchanged.

The audit nevertheless found assumptions that could invalidate successor results:

| Finding | Correction and scope |
| --- | --- |
| Complete/unchanged status flags were trusted without reopening source artifacts; executed questions were not compared with the frozen task text. | The scorer now verifies source artifacts and executed questions. The shared control helper also verifies exact pack bytes, fixed answering controls and instructions. |
| A receipt for the right model revision could describe a different deployment or endpoint. | Fresh private management-plane envelopes bind the invoked endpoint, deployment, revision, upgrade policy and SKU before/after a measured window. These are consistency checks on operator receipts, not signed attestations. |
| The R0 scale harness enforced repeat spread for RU but only the 20% rule for retrieved rows. | R1 version 3 enforces both written spread rules and retains explicit component grades. Historical E59 is not relabeled as passing the stricter rule. |
| Retry tallies discarded earlier attempts; only the first index report was retained; query ordinals/reference completeness were weakly checked. | R1 retains visible attempts, known charges, waits, response/range/index diagnostics and exact query matrices. Unobservable total charges remain unavailable. |
| Prefix exact and ANN queries could share an SDK merge defect. | R1 builds its independent reference from full document-partition exact queries and merges distances locally. Library placement and cross-range winners need separate proof. |
| Existing structure-lab node coalescing, summary creation and document-ID scoring did not isolate passage representation. | R2 adds a DOCX source-coordinate bridge, matched zero-overlap passage variants and original-region delivery scoring. Source coordinates are verified against original bytes, not inferred from numerically similar offsets. |
| Equal coverage counts could conceal loss of different required regions. | R2's premeasurement gate compares delivered-region sets, with no loss of a baseline region allowed. Large evidence payloads are reported separately from retrieval quality. |
| Saved source replies alone do not prove that the answering model received them. | P1 binds each task's decoded actual wire tool sequence to its canonical captured replies before source scoring. |
| The first P1 implementation added opt-in selector hints after raw-response budget counting. | Peer review reproduced an over-budget complete response. P1 now decorates before the raw all-or-nothing gate; default behavior and the planned compact replay cells remain separate. |
| A navigation comparison against passages alone could attribute whole-document expansion gains to summary vectors. | Peer review identified explicitly named-file tasks. The navigation comparison must include a metadata-based named-file control with the same expansion before claiming incremental discovery-vector value. |

The answering model remains GPT-6.1 Sol `2026-09-29`, Responses, medium reasoning,
8,000 output tokens, three provider attempts, `store=false`, truncation disabled,
and 200,000 conversation tokens per independent task/member. Implementation
model choice does not change these controls. Source hashes, original metadata,
generation/revision bindings and prompts stay frozen. All task selection in these
experiments is development-only.

## Attribution and resource controls

| Track | Branch | Changed variable | Unchanged boundary |
| --- | --- | --- | --- |
| R1 | `codex/retrieval-r1` | Diagnostic routing/query path and measurement correctness | Source text/vectors, container policies, application representation and permissions |
| R2 | `codex/retrieval-r2` | Passage granularity, then a separately frozen discovery-level comparison | Canonical extraction, source coordinates, zero overlap, embedding revision, query vectors and direct relevance oracle |
| P1 | `codex/retrieval-p1` | Format-only and selector-only opt-in cells | One frozen new Microsoft library, source snapshots, physical storage, model and context controls |

The automatic local setup remains dependency/test/lint setup only. Authentication,
environment bindings and provisioning are explicitly invoked private steps.

Cloud resources are coordinator-owned: new temporary embedding and answering
deployments; a new provisioned R1 Cosmos account with unchanged container
definitions; and one new five-file P1 library in the isolated validation services.
R1 has 11,000 provisioned RU/s on chunks and 400 on each metadata container. This
is a test allocation, not a production tuning recommendation. Existing deployments,
frozen R0 libraries and unidentified scale resources are reserved.

Measurement windows were exclusive: R2 passage embeddings, R1 canary/direct
database probes, then P1 paired replay. The R2 navigation bound was evaluated
locally and stopped summary generation before any model call.
Management-plane reads and local tests are not performance comparisons. Cache,
provider backend placement and external tenant activity remain uncontrolled;
serialization avoids known contention between these experiments, not all possible
service variability.

## Reviewed local and passage results

R2's exact-vector development comparison uses 30 body-evidence tasks over all
eight development sources. Its granular baseline is a controlled zero-overlap
lab representation, not a replacement for the shipped 50-token-overlap recipe.
Coalescing reduces passage rows from **8,435 to 3,368 (60.07%)**. Complete labeled
region delivery rises from **14 to 19 tasks**, but required baseline regions are
lost in `r0-01`, `r0-02`, `r0-09` and `r0-10` (one in each). The no-loss selection
gate therefore rejects promotion. Retain the granular experimental baseline and
leave existing application recipes unchanged.

The frozen embedding artifact contains 8,717 unique texts/vectors. Provider
counters report 642,547 embedding tokens, 589 requests and no reported retry or
rate-limit wait. Maximum expanded source JSON is an estimated 23,134 tokens;
there is no evidence-only 200k refusal in this comparison. These are direct exact
source-delivery results. They do not establish Cosmos ANN behavior, answering
quality, ingestion/write cost or a latency winner. Vectors, originals and complete
source captures remain private and hash-bound.

P1's saved-response experiment reconstructs all **95 R0 tool replies** exactly,
including text, HTML, blank strings, source order and snapshot fields. Estimated
serialized-token savings are **4.39% current, 13.73% custom and 1.06% Microsoft**.
Every strategy misses the predeclared 20% target. Keep the existing compact
default; the reference format remains an opt-in experimental contract.

## Reviewed database and replay results

[R1](retrieval-r1.md) ran a passing fresh-account canary, then 220 direct queries
over the same 1,500 frozen vectors: 40 full-document-key exact references and
three repeats of 20 queries through each of prefix vector, no-prefix vector and
no-prefix hybrid paths. The container had two verified physical ranges; the
library occupied only one. Both vector paths achieved exact top-ten recall 1.0
on every repeat, with no source-binding, order or cardinality failure.

The first grader incorrectly interpreted the returned cosine similarity as an
ascending distance. Its failing receipt is preserved. Read-only reanalysis
corrected the ordering after independent local cosine calculation reproduced
all 400 exact scores within 2.22e-16. The coordinator separately reproduced those
scores and all 120 vector-query top-ten sets. Neither raw calls nor the recall
floor or score tolerance changed. This correction is not a database failure.

All 60 hybrid calls produced successful responses from both physical ranges,
despite the library census placing its documents in one. Positive fan-out is
established even though the diagnostic hook cannot prove a complete inventory
of every SDK/query-plan request. Known median RU was 15.34 prefix, 15.09 no-prefix
vector and 185.155 hybrid; total RU is unavailable. These narrow, warmed diagnostics
do not establish application latency, exact hybrid quality, graph engagement or
multi-range-library merge correctness. Keep prefix vector search and the current
physical policies; hybrid remains nondefault.

The [R2 navigation bound](../records/retrieval-r2-navigation-bound-20261101.json)
compares proposed document-summary selection against both passage retrieval and
literal named-file selection with identical document expansion. All five selected
tasks name files. Enumerating all three document choices for each task proves
zero strictly improving choices without loss relative to the filename control.
The coordinator independently reproduced all 15 choices. Stop this particular
summary experiment; summary creation, refresh and model quality are unmeasured.
This says nothing about vague-file tasks or section navigation, which need a new
development task selection and broader distractors before any new model spend.

[P1](retrieval-p1.md) completed 18 development turns: three tasks, three independently
attributable cells, two reversed-order repeats. Each cell used the same new
five-file Microsoft library, model, prompt and 200k allowance. Actual provider
wire captures decode to the scored evidence. Independent track reviews and
coordinator spot reviews found completion, support and citations passing all
six turns per cell; original-region delivery was 2/2, 56/56 (including 32 blank
table cells) and 71/71 in order, in both repeats. Reviews are source-based assistant
judgments, not blinded human evaluation or a general accuracy estimate.

| Six turns per cell | Baseline | Format only | Selectors only |
| --- | ---: | ---: | ---: |
| Actual model input tokens | 690,926 | 675,202 | 585,251 |
| Output tokens | 4,338 | 4,259 | 4,233 |
| Model calls | 22 | 22 | 22 |
| Recovered source-scope errors | 2 | 2 | 0 |
| Read-budget refusals | 1 | 2 | 0 |
| Provider rate-limit retries | 8 | 12 | 11 |

Format input savings were 2.28%; selector savings were 15.29% overall. The targeted
selector case saved 19.962%, still below 20%. Unsupported expansion attempts were
avoided in both selector repeats, but this is a small usability result, not an
efficiency-gate pass. Different tool paths, cached inputs and rate waits prevent
a latency/cost winner. Maximum actual input was 96,652 tokens; a 200k allowance
does not establish operation at 200k input. All refusals recovered, and no context
refusal occurred. Keep both new contracts opt-in and the default unchanged.

## Next integration decision and exact open gates

Retain the current foundation and compact format. The next useful I1 step is a
versioned **selector-only** candidate on that foundation, first confirming live
lifecycle behavior on a disposable copy, then a predeclared held-out baseline/
selector comparison with the frozen Sol/200k controls. This is a recommendation,
not a promotion or a launched held-out run. Since no changed foundation or format
won its selection gate, do not manufacture a four-cell architecture comparison.
If either changes later, restore the roadmap's current/new foundation ×
current/new format four-cell held-out matrix before promotion.

The next owner must freeze an I1 plan and extend the development-only replay
driver explicitly for integration; do not bypass its held-out guard. Preserve
the existing benchmark labels and original sources. Keep canonical and actual-wire
captures, paired order and before/after model/source bindings, and score source
coverage, relationships, completion, factual support and citations separately.
Require no loss of baseline-delivered regions or quality, successful stale/foreign
selector rejection without dropping pins, and correct metadata, permissions,
retry, replacement and deletion behavior. A usability-only adoption must be
explicitly justified as such; it cannot be called a 20% efficiency win.

| Gate still untested | Required next evidence |
| --- | --- |
| A library spanning physical ranges; SDK global top-k merging | New isolated R1 continuation, verified document placement and queries with winners in different ranges; independent full-document-key reference. Investigate merging before any partition rebuild. |
| Large-library DiskANN engagement/crossover, growth, concurrency and split transients | Purpose-built isolated load and readiness proof with exclusive performance windows. Current QuantizedFlat/small-scope results do not settle these. |
| Coalesced production representation or summary navigation | A new development candidate satisfying no-loss source delivery; vague-file/section tasks before summary generation. Actual-corpus ANN, write/read RU and answering quality remain separate gates. |
| P1 live replacement/deletion/retries/metadata/permissions/stale races under load | Disposable library lifecycle run; existing focused/local tests do not substitute for deployed concurrency and freshness evidence. |
| Broader providers/formats, long-document context, held-out integration | Frozen I1 design and replay extension; held-out tasks remain untouched in this stage. |
| Complete workload cost | Measured ingestion, maintenance, storage/hosting/connector allocations and billing; known request counters are not a complete monetary total. |

No R3 layout/index sweep is started or justified by these bounded findings.
The documented decision to retain current layout satisfies I1's layout dependency
for a limited current-foundation candidate, not production scale acceptance.

## Reproduce and audit

All three branches descend from R0 and include the shared control correction
`73ee68c`. R2 `a2ccf85`, R1 `5dfb7db` and P1 `eef7e1b` were reviewed and merged
separately; P1's measured implementation is `ddc5f2d`. Each track's runbook provides exact
private-input commands, receipts, dependencies and limits. Authentication and
cloud provisioning stay outside automatic setup.

```sh
sh scripts/dev setup
sh scripts/dev check -q
sh scripts/dev privacy --deny-file "$STAGE1_PRIVATE/private-deny.json"
# Local-only reproduction after resource cleanup:
.venv/bin/python -m scripts.retrieval_r1 --help
.venv/bin/python -m scripts.retrieval_r2_offline --help
.venv/bin/python -m scripts.retrieval_r2_navigation_bound --help
.venv/bin/python -m scripts.retrieval_p1 --help
```

Use the named read-only reanalysis/offline recheck commands in the track runbooks;
execution code hashes intentionally prevent rerunning a frozen measurement under
changed code. Full original source captures, vectors, credentials and environment
bindings remain outside every checkout. Sanitized coordinator checks are indexed
in [records](../records/README.md).

The combined checkout passed **1,050 tests**, with 14 existing warnings, in
142.56 seconds, plus lint, formatting, diff and a 47-entry private-identifier
scan. [The validation receipt](../records/retrieval-stage1-validation-20261101.json)
binds those checks to the merged code and verifies ancestry from both R0 and
`9738236`. The frozen benchmark, container/infra definitions, automatic setup,
permissions, query construction and ingestion/worker files remain unchanged.
Local tests do not close the live gates above.

The final [R0 preservation check](../records/retrieval-stage1-r0-preservation-20261101.json)
reads catalogue metadata, tools, original-source hashes and active generation/
revision bindings for all three libraries and 15 documents. Its snapshot exactly
matches R0. It does not reread raw source bodies or run a performance probe.

[Cleanup is complete](../records/retrieval-stage1-cleanup-20261101.json): both
owned temporary model deployments were removed, original deployment settings
were verified unchanged, and the isolated R1 resource group's deletion was
verified after Azure's asynchronous operation completed. Ownership and the group
inventory were checked before deletion. Temporary API processes are stopped.
The new five-file P1 library is retained for follow-on confirmation; frozen R0
libraries and unrelated/ongoing scale resources are preserved. Private source,
vector, model-wire and management receipts remain available for local reanalysis.
