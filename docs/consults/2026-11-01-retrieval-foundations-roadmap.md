# Retrieval foundations and evaluation roadmap

*2026-11-01. Proposed next work on the isolated modular-ingestion branch. This
orders experiments and integration; it does not record new measurements or select
a production architecture.*

Resolve the choices that could force a rebuild first, using small controlled
experiments and an agreed workload. In parallel, improve the model-facing evidence
format where that work is independent of storage. Then test the combined result
through the existing demo and agent replay. Smaller-model and tight-context tuning
come after we establish that the system can supply and use the required evidence.

Priority depends on uncertainty, consequence and the cost of reversing a decision.
A partition key deserves an early check because changing it is expensive. It does
not automatically deserve a redesign: the workload may show that vector granularity
or source navigation has a much larger effect.

## Starting point

The demo already has current, custom structured and Microsoft profiles, matched
custom/Microsoft creation, isolated cloud ingestion and a shared publication
boundary. The next milestone is a defensible foundation choice and an integrated
improvement, not another demonstration application.

| Evidence already available | Consequence for the next work |
| --- | --- |
| [E63](../evidence.md#e63-controlled-answering-model-and-larger-context-comparison--measured) records 100 replay turns. Larger context recovered the tested Microsoft tasks; the stronger model improved attribution. Larger-context answering was not matched across every profile. | Freeze the answering model and give every strategy adequate context before judging ingestion or retrieval. |
| The same record estimates about 92,900 tokens for the custom complete-read JSON versus 20,086 for Microsoft, with roughly similar body-text volume. These are serialized-response estimates, not billed model tokens. | Test deterministic compaction promptly. The result does not establish a provider cost winner. |
| The [structure lab](../structure-lab.md) has working shared/split layouts, real-text canaries and small synthetic examples. It has selected no layout winner. | Extend its freeze/load/evaluate path instead of building a new harness. |
| [E59](../../records/scale-probe-20261029.json) measured up to 1,013 libraries and 243,273 vectors on one physical partition. Further scale work is ongoing. | Reconcile the latest results before launching more scale tests. One-partition results do not resolve multi-partition ranking or cost. |

The [current chunk container](../../cosmos/chunks.container.json) uses hierarchical
partition keys `/libraryId`, `/documentId`; a library-sharded DiskANN index; and
3,072-dimensional float16 vectors. The authored hierarchy and complete extracted
source are generation-bound Blob artifacts. These are separate design choices.
**Fine source structure does not require one embedding per fine source block.**

## Sequence and decision gates

| Stage | Question to resolve | Deliverable and gate |
| --- | --- | --- |
| 0. Freeze the benchmark | Which real tasks and operating sizes must the design serve? | A reproducible baseline, source-based labels, workload ranges and an inventory of completed/open scale tests. |
| 1. Test foundations | Is routing/ranking correct at the intended scale? What should be embedded, and how should those records be indexed? | A small set of controlled results; retain or change each foundation explicitly. |
| Parallel work | Can we send the same evidence more compactly and expose more reliable source selectors? What drives total cost? | A reversible evidence-format candidate and a cost/ownership worksheet. |
| 2. Integrate the finalists | Do the individual gains survive together and help real agent tasks? | A versioned candidate in the existing demo, passing held-out replay and lifecycle checks. |
| 3. Decide provider ownership | Can Microsoft supply the chosen capabilities with less total code, operations and cost? | Adopt specific components or retain the custom worker, with a documented reason and a preserved provider boundary. |
| 4. Tune and harden | What is the economical model/context policy, and what remains before production? | Separate affordability results and a scoped release checklist. |

### Stage 0 — Freeze a useful benchmark

Start with a pilot of roughly 12–20 representative documents and 40–60 tasks, using
real source content. Cover precise facts, exceptions buried in long documents,
complete-section reads, cross-file comparison, tables/forms, and metadata-filtered
discovery. Include files and task families withheld from development. Freeze the
development/held-out split and scoring rules before tuning; use held-out results
to confirm finalists rather than repeatedly selecting candidates on them.

This pilot is a practical starting size, not a statistically sufficient benchmark
for every library. Reuse the existing four-DOCX cases as regressions. Start matched
strategy comparisons with formats all members support. Report PDF, visual and
other format gaps separately; capability omissions must not disappear from the
overall product decision.

Gold labels identify relevant original-source regions and relationships, never
implementation-specific chunk IDs. Keep expected answers and source labels out of
the answering model's input. Preserve source hashes, profile versions, generations,
model revisions, prompts, tools, query settings and repeated-run order in a frozen
manifest. Record actual chunk-size distributions, units and overlap, not just a
configured target.

Declare workload ranges for small personal libraries, larger shared libraries,
library count, update rate and query concurrency. Until usage data exists, label
them as scenarios rather than forecasts. Ingest enough real material for physical
index tests; the task-labelled pilot alone may be too small to exercise ANN.

Reconcile the ongoing scale work with [the capacity questions](../capacity/untested-at-scale.md).
Record which results are complete, which are running and who owns the resources.
Share sanitized receipts, not mutable containers or private runtime directories.

Use the tested GPT-6.1 Sol Responses configuration as the fixed diagnostic answering
model, subject to deployment availability. Start with a **200k per-run conversation
allowance, identically configured for each comparison member**. It is not a pooled
budget across libraries or runs. Inspect actual input and reserve requirements; if
a task cannot fit, increase the allowance for every member where supported or
declare that task a separate context-limited case. Do not count such a refusal as
an index-quality result. This allowance is a testing control, not a product default.

### Stage 1 — Resolve the foundations with controlled experiments

**First, close the consequential correctness questions.** Consume the existing
scale results before scheduling new work. For any unresolved intended workload,
verify vector ranking across physical partitions, library isolation, hybrid ranking
with the required routing, index readiness and cost as library/container sizes
grow. A correctness failure takes precedence over performance optimization.
Investigate the query/SDK path before inferring that the partition key is wrong.

Run these checks directly against retrieval, without an answering agent. That
prevents the known selector mistakes or citation behavior from masking database
results. Unavailable large-scale resources need not block local work or a bounded
small-library result; keep the larger-scale claim explicitly open.

**Next, decide what gets a vector.** On identical extraction and source structure,
compare the existing granular representation with section-aware coalescing around
a 500-token target. Preserve small blocks, headings, table relationships and exact
source locations in both cases. Hold overlap policy, embeddings, ranking and read
expansion fixed, and measure the actual distribution of chunk lengths. Chunk text
and therefore vectors change in this experiment; the physical index does not.

Then test whether adding section/document discovery vectors improves the tasks
that need broader navigation. Keep the selected passage vectors unchanged for
this comparison. Reuse the structure lab; generated summaries are discovery aids,
and evidence still comes from original source. Report summary creation, refresh
and omission costs. Stop if the extra level provides no useful benefit on the
declared tasks. This is two small comparisons, not a sweep through every chunk size.

**Finally, test the physical layout with frozen vectors and query shapes.**
Choose the applicable comparisons from the following table after the workload and
representation are known. Do not run all combinations by default.

| Choice | Controlled comparison | When it deserves a change |
| --- | --- | --- |
| Shared versus separate passage/discovery indexes | Copy identical frozen rows and vectors to the lab's shared and split layouts; compare the same typed searches, filters and source expansion. | Discovery vectors have demonstrated value, and separation materially improves total query/ingestion cost or quality after accounting for an extra container. |
| DiskANN versus `quantizedFlat` | Use identical vectors and workloads at representative small and larger search scopes. Record the index behavior actually observed, not only its configured type. | Existing scale results do not settle which index suits the intended scope. Establish where a choice stops being appropriate. |
| Indexed section membership and read order | Compare the current artifact reader with an explicitly indexed lookup or section-addressable source artifact, returning the same source selection. | Selected reads are paying material whole-artifact download/parse cost, or query shapes need reliable subtree/neighbor access. Include new writes, storage and consistency work. |
| Partition hierarchy or vector shard key | Change one candidate at a time, only in a disposable container, preserving library authorization and source semantics. | Measured fan-out, hot partitions or dominant query scopes justify the rebuild. Adding a section level is not inherently an improvement. |
| Dimensions, precision and index tuning | Use a separate experiment after representation/layout are stable. | Measured database storage/RU or recall is still a limiting cost. Preserve the existing defaults until that evidence exists. |

Microsoft's guidance makes index selection dependent on scoped dataset size and
query shape; its approximate 50k-vector guidance is a candidate-selection aid, not
a measured crossover for this application. ([Vector performance guidance](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/vector-search-performance-tips))
Hierarchical partitioning governs routing and data distribution, while the vector
shard key groups vector indexes and may use a different property. Keep those
decisions distinct. ([Hierarchical partition keys](https://learn.microsoft.com/en-us/azure/cosmos-db/hierarchical-partition-keys),
[sharded DiskANN](https://learn.microsoft.com/en-us/azure/cosmos-db/gen-ai/sharded-diskann))

Every new queried container requires the repository's real-text canary. Check
readiness again on the actual experiment corpus. A passing canary does not prove
the experiment's small shards use DiskANN, and ANN recall against exact vector
search does not measure semantic source relevance. Retain the required library
equality, `TOP`, float16 conversion, projection and hybrid-routing invariants.
Use new containers for vector-policy changes; do not rebuild existing scale data.

### Parallel work — Compact evidence, source selectors and cost

Build a provider-neutral response variant that factors repeated document metadata,
heading paths and citation details into compact references. Preserve exact text,
order, coverage, source locations and generation binding. A section read must
remain distinguishable from a complete document read. Handle tables explicitly;
do not claim to restore relationships already lost during extraction.

Begin with saved responses and deterministic equivalence checks. Then use a small
paired replay with the same model and adequate context. Report estimated serialized
tokens separately from measured provider input tokens. This work can proceed while
index tests run because the evidence format need not dictate physical storage.
Keep the existing response available for an A/B comparison and compatibility.

Address the remaining `source_scope_unavailable` and invalid document selectors in
the same workstream, as a separately testable change: return usable handles and
explicit capabilities, bind them to the correct document/revision, and retain
sanitized error categories sufficient to diagnose misuse. Test stale generations,
unsupported scopes and recovery. Preserve personal-library checks and existing
all-or-nothing read behavior. Record the effects of selector recovery separately
from compaction so a combined improvement has an intelligible cause.

Start cost accounting now, using matched workload assumptions. Include extraction,
embeddings, Cosmos reads/writes/storage, source storage, worker/workflow hosting,
connectors, retries, reprocessing and answer-model usage. Show allocated standing
cost and incremental use of existing capacity separately. Fewer vectors do not
automatically mean proportionally fewer embedding tokens. Record maintenance and
operational responsibilities alongside currency costs. Label counters as measured,
estimated or unavailable; keep known subtotals when a total is unavailable.

### Stage 2 — Integrate and confirm

Select the simplest candidate supported by the pilot. Retaining the current
foundation is a valid decision. For a changed foundation, compare four cells on
the held-out tasks: current/new foundation crossed with current/compact evidence.
Hold the answering model and context allowance fixed. This checks whether isolated
gains survive together and whether a cheaper response changes source use.

Use direct source-delivery checks and existing agent replay. Score relevant-source
coverage, task completion, factual support and citation accuracy separately. Include
selective retrieval as well as full-file synthesis: complete reads bypass search
chunk boundaries and cannot settle chunking quality by themselves.

Promote a candidate only after replacement/deletion safety, retries, metadata,
permissions, provenance and stale-generation checks pass. Add a versioned opt-in
recipe or contract through the existing API/demo, keeping the baseline reproducible.
Do not mutate already-frozen profiles or invent a second ingestion application.

### Stage 3 — Make the custom/Microsoft ownership decision

Provisionally keep the Python worker as the primary orchestration path while
retaining the provider/enrichment boundary. Compare Microsoft against the selected
capability contract, including source structure and read behavior. A bounded
adapter experiment should establish whether Microsoft can meet it with ordinary
configuration and thin mapping, and identify code or operational work we could
actually retire.

Run two explicitly different evaluations: complete strategies using their best
chosen recipes, and controlled components with matched extraction, chunk targets,
overlap units, embeddings and retrieval. Use identical source files and adequate
context. Disclose remaining algorithm/runtime differences. The current complete
Microsoft recipe's 5,000-token windows and controlled recipe's 500-token target
must remain visible in the result.

Combine quality, supported formats, lifecycle behavior and the cost worksheet.
Adopt Microsoft components if they reduce total ownership or deliver needed
capabilities at an acceptable cost. If reproducing the chosen contract requires
maintaining most of our processing inside a customized workflow, retain the custom
path and the replaceable adapter. Neither previous effort nor a Microsoft label
settles this choice. No broad workflow rewrite is needed to reach a decision.

### Stage 4 — Affordability and production readiness

Once evidence delivery is sound, repeat a bounded subset with mini and smaller
context allowances. Find the cost/quality frontier and an explicit fallback policy.
Large-document cases should exercise actual large inputs; merely allowing 200k or
500k tokens does not test that workload. Keep source loss, retrieval failure,
context refusal and answering-model error distinct.

Production work remains separately scoped: hosted API authentication, remote
completion/cancellation races, multi-instance operation, replacement/deletion under
load, broader format/visual coverage, and the unresolved scale/support gates.
Neither a winning retrieval experiment nor passing local tests closes these items.

## Session-sized work packages

Each package gets a separate worktree/branch and a private runtime directory.
Future branches start from the frozen baseline or an explicitly named predecessor.
Assign one owner for shared spec/build-plan updates during integration; experiment
sessions write their own sanitized records and handoffs.

| Package | Start after | Bounded assignment | Completion artifact |
| --- | --- | --- | --- |
| R0 — Benchmark and scale handoff | Now | Freeze corpus/tasks/controls; inventory existing scale results; define scenarios and measurement gaps. Extend existing replay only where necessary. | Manifest, baseline receipt, scorecard and an exact list of remaining foundation questions. |
| R1 — Physical correctness and scale | R0; coordinate with existing scale work | Reuse results; close the highest-risk missing routing/ranking/readiness checks for the intended workload. No representation change. | Keep/change decision for the current routing and partition assumptions, with measured limits. |
| R2 — Vector granularity and navigation | R0 | Compare granular/coalesced passage vectors, then the value of an added discovery level. Same extracted source and physical layout. | Frozen candidate vectors, source-relevance results, ingestion/write/read costs and a recommendation. |
| R3 — Container and query layout | R1 findings and R2 candidate | Run only the justified layout/index/source-read comparisons. Same vectors for each physical-layout comparison. | Container/query decision, canary/readiness receipts, rebuild implications or an explicit no-change result. |
| P1 — Evidence format and selectors | R0 | Compact the existing output; test source-preserving equivalence; repair selector usability as a separately measured change. | Compatible opt-in contract, focused tests and paired replay. |
| C1 — Cost and ownership | R0; update with later receipts | Attribute available usage and standing resources; compare workload scenarios and responsibilities. | Assumption-labelled cost worksheet, missing counters and actual retirement opportunities. |
| I1 — Integrate and evaluate | R3 or a documented decision to retain current layout; P1 | Combine finalists in the existing demo and run the four-cell held-out evaluation plus lifecycle checks. | Versioned candidate, evidence record, spec/build-plan update and rollout/rollback recommendation. |
| M1 — Provider decision | I1 and C1 | Test a thin Microsoft mapping to the chosen contract; run matched complete/component comparisons. | Adopt/retain decision with quality, capability, operational and cost reasons. |

Start R0 first. Then R1 and R2 can run independently; P1 can run alongside them
when another session is available. C1 can begin as a short read-only audit. Schedule
cloud performance runs to avoid shared quota/throughput contention and record any
unavoidable contention. Parallel coding does not require simultaneous cloud load.

Every handoff includes the exact commit, frozen inputs, changed variables, tests,
sanitized measurement record, confounders, remaining failures and next decision.
Keep environment identifiers and raw content outside the repository. Announce cloud
steps before executing them, use isolated resources, and do not alter ongoing scale
resources. A cloud blocker should leave a concrete unrun check while local fixtures,
contracts, analysis and tests continue.

## Acceptance and effort

Permission, provenance, coverage disclosure and lifecycle failures block promotion.
For database correctness, retain the canary's vector recall requirement and inspect
ranking behavior separately from semantic relevance. For product quality, report
paired task counts and failure categories; small pilot percentages are not a
general accuracy claim. Include failed attempts and retries in cost/latency results.

Before running each performance comparison, record its minimum useful improvement
and tolerated quality variation. A reasonable provisional efficiency threshold is
about 20% in the targeted cost or latency measure with no observed source-quality
regression, followed by held-out confirmation; it is not statistical proof of
equivalence. A clear source-coverage gain can justify some added cost. If evidence
is inconclusive, retain the simpler baseline and name the specific follow-up that
could change the decision rather than expanding every experimental axis.

Budget roughly **8–15 engineering days** for R0 through the first integrated
foundation/evidence decision, including focused testing and records. Parallel
sessions can reduce elapsed time; corpus preparation, index readiness, quota and
multi-partition provisioning can extend it. Allow another **2–4 engineering days**
for a bounded provider decision if the thin adapter is feasible. Broader extraction
coverage and production hardening need their own estimate after these gates. These
are incremental planning ranges, not a delivery promise or cloud-spending estimate.
