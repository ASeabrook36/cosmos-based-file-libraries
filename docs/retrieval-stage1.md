# R1, R2 and P1 coordination

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

Measurement windows are exclusive: R2 passage embeddings, R1 canary/direct
database probes, the bounded R2 navigation follow-on, then P1 paired replay.
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
default; the reference format remains an opt-in experimental contract. Actual
answering-model input and selector behavior require the separate paired replay.
