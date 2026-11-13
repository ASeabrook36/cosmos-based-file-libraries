# Bounded granular navigation qualification

Subsequent [two-turn follow-up](granular-navigation-followup.md): the original
cell-completeness and identifier-reporting gaps are corrected in those samples.
The full frozen follow-up still fails outline acquisition and omission of
unrequested numbering metadata; its specific remaining gate is documented there.
The six-turn results and decisions below are preserved as the earlier checkpoint.

## Decision and tested revisions

**Bounded cloud qualification is complete; hold integration for two focused
real-model checks of the post-run guidance mitigation.** All ten live mechanics
gates passed. Six actual model turns returned correct core source facts and
human-readable citations, but one did not establish complete-cell selection and
another incorrectly reported an internal section identifier. These are distinct
from successful ingestion, source fidelity and lifecycle mechanics. They are not
resolved merely by passing local tests.

The sanitized [result receipt](../records/granular-navigation-live-qualification-20261104.json)
binds attempts, measurements, independent reviews and cleanup evidence. The
[first-attempt receipt](../records/granular-navigation-live-attempt-1-20261104.json)
is retained unchanged; its then-pending cleanup was resolved by the continuation.

| Revision | Qualification |
| --- | --- |
| `01a934c` | Original source-navigation candidate; 1,613 local tests |
| `e12f857` | First live runner baseline; 1,674 local tests; stopped on a harness guard defect after first publication, before navigation/model turns |
| `821ea25` | Corrected runner and exact-scope continuation; 1,730 local tests, all ten live mechanics gates and all six model turns |
| `0b12072` | Granular read-tool description only; 59 focused local tests passed; no further model or cloud calls |

The source runtime is identical between the first and successful live baselines.
The subsequent guidance change leaves schemas, selectors, storage, passages,
embeddings and other profiles' tool bundles unchanged. It explicitly requires a
cell selector to establish cell completeness, distinguishes evidence-unit IDs from
authored-section/root IDs, and directs answers to use returned human citations.
Its effect on model behavior remains unverified.

The branch remains `codex/granular-source-navigation`, incorporating main
`1ddc438` and source-only repair `33402d3` through reviewed integration `7e9a31b`.
Context-accounting repair `4470804` and index versioning `1cf7e93` remain separate.
No main merge, application deployment, provider decision, retrieval tuning or
larger confirmation study occurred. Existing experiments and their receipts are
unchanged. The granular recipe requires new ingestion/embeddings; the post-run
tool-description mitigation itself requires no re-ingestion.

## Live execution and recovery

Read-only preflight on 2026-11-04 verified authenticated management and data access,
container policies/incarnation and the prior canary, scoped reads, Blob inventory,
protected scopes, deployment versions/SKUs and capacity. No new cloud resources or
model deployments were created. The announced window used existing isolated
resources. Answering used pinned GPT-5-mini `2025-08-07` (GlobalStandard capacity
50, automatic upgrades disabled); embedding used text-embedding-3-large version 1
(capacity 150). Fresh deployment bindings agreed before and after execution.
Raw provider responses did not expose a usable model name; deployment bindings
are the model-identity evidence.
These capacity observations are not a sustained throughput guarantee.

Attempt 1 published the first DOCX source successfully. Cosmos normalized two
processing timing fields from JSON `0.0` to `0`; the harness's byte-oriented body
hash classified the outcome as unknown. Cleanup correctly stopped before any
deletion. Independent readback reconstructed the exact original proposal hash
with only those numeric spellings restored. No source, identity, profile or
generation difference was found. The runner now compares finite integral floats
semantically while retaining strict field, value, type and ownership checks, and
durably captures proposed/observed records. Reconciliation required the original
proposal hash, independent evidence and an append-only ledger event.

The continuation reused all three original libraries/documents, preserved the
original 106-event ledger head and all embedding/usage reservations, and deducted
181 seconds from the original execution allowance. It did not reset limits or
create replacement test scopes. Final cumulative use was three libraries, three
documents, four ingestion generations, 49 chunks and 32 recorded Blob paths.
The 698-event final ownership chain has no unresolved mutations or unfinished
messages. All attempts and the reconciliation evidence are privately archived.

The ten passing live gates cover:

1. Real worker ingestion and immutable source publication, including original,
   artifact, job/profile/generation bindings and all chunk-to-source mappings.
2. Personal-library binding and local development caller authentication: five
   wrong-agent 403s and five invalid-caller 401s. Production Entra API auth was
   not exercised by the local runner.
3. Paragraph, cell, section-own-content, subtree, containing-section and whole
   document reads, with truthful completeness. Scripted receipts contain 1, 4,
   2, 13, 9 and 15 source atoms respectively.
4. Markdown hierarchy and exact end-exclusive character spans.
5. Actual vector search followed by verified passage-to-source expansion.
6. Unsupported-page and foreign-scope refusals, plus atomic multi-document budget
   refusal with no partial text.
7. Idempotent retry through the actual worker, preserving published identity.
8. Metadata-race fencing and source identity through independent Cosmos clients.
9. Pending replacement, in-flight publication and stale-selector refusal.
10. Deletion fencing and repeated idempotent purge.

The three independent-client race observations returned `409 document_changed`.
These are bounded observed races, not a universal Session-consistency guarantee.
The recipe targets 500 tokens with 50-token overlap **within source atoms**;
these tiny DOCX chunks were only 3–14 tokens and Markdown chunks 8–13. This run
does not establish ANN engagement, recall, comparative search cost or scale.

## Actual model behavior

An independent source-based review compared final answers, actual model-input
tool payloads and operation receipts with original sources and published
artifacts. It was neither blinded nor a controlled accuracy benchmark. Six core
fact/citation checks passed; five answers had no observed incorrect assertion,
and five demonstrated the requested navigation route. Those are separate counts.

| Case | Observed behavior | Qualification |
| --- | --- | --- |
| Paragraph → containing section | Selected one paragraph, then expanded its authored containing subtree; correct fact, scope and citation | Passed |
| Cell and qualifier | Selected four paragraph handles and preserved all action text, list membership and separate qualifier; correct table/paragraph citations | Content correct; never selected the cell node or obtained a cell-completeness receipt |
| Section → subtree | Distinguished a section's own content from a rule in its subsection, with correct citations | Passed |
| Region → whole document | Read a small region, then all 15 atoms with `complete=true`; correct facts and citations | Passed |
| Markdown block | Traversed the authored outline and selected the exact source block; correct end-exclusive span and no false full-document claim | Passed |
| Unsupported locators | Disclosed unavailable rendered pages/chapters and unverifiable old mapping; correct fact, hierarchy and paragraph citation | Route worked, but twice substituted a versioned `sectionId` evidence handle for the returned `containingSectionId` authored-node ID |

The last case selected a broad 13-atom subtree. Uniformly minimal evidence-region
selection is therefore not demonstrated. Its missing historical mapping was a
hypothetical prompt condition, not an actual malformed live index entry. Fixed
source pins and explicit navigation tasks make this controlled navigation
qualification, not free-form discovery or source-recall evidence.

## Measured usage and representation overhead

| Counter | Observation | Scope |
| --- | ---: | --- |
| Model calls / physical responses | 25 / 25 | Six turns; no failures, 429s or unknown attempts |
| Provider input / output tokens | 251,828 / 12,403 | Provider-reported, complete across the 25 calls |
| Cached input / reasoning output | 114,048 / 8,192 | Included in the input/output totals, not additional tokens |
| Embedding requests / prompt tokens | 8 / 374 | Both attempts combined, including the first 98 tokens; no retries, throttles or unknown attempts |
| Published chunk write charge | 3,291.67 RU | Four verified publication metrics; excludes record writes and cleanup |
| Agent retrieval charge | 136.97 RU | 19 instrumented tool operations; all these trace charges known |

Embedding publication metrics account for 338 of the 374 measured tokens; the
remaining 36 were query embedding inputs. No monetary cost is inferred. Retrieval
RU excludes preflight, tool-bundle/snapshot checks and other non-instrumented
Cosmos operations; Blob, management, storage and the complete service bill are
unavailable. Operation counters must not be presented as total Azure charges.

Across the 19 unique tool replies, local `cl100k_base` serialization measured:

| Local measurement | Tokens |
| --- | ---: |
| Evidence JSON | 84,949 |
| Standalone rendered source text, including structural markup | 2,086 |
| Selector-removal serialization delta | 36,216 |
| Payload with source text and selectors removed | 46,167 |
| Complete tool-message JSON | 91,132 |

These counterfactual components are **non-additive**, are not provider billing
counters and do not include repeated tool history across model calls. An actual
one-paragraph model response contained 1,567 local evidence-JSON tokens for 11
rendered-source tokens; removing selectors changed serialization by 549 tokens.
The measured overhead is a clear follow-up opportunity. This run does not show
how much a compatible reduction would save or whether it would preserve quality.

No context or pacing refusal occurred. The context allowance was 200k, but a
separate quota guard allowed at most 37k locally serialized input per request
(45k rate budget minus 8k output reservation). Maximum observed local request
input was 36,145 tokens, versus 23,723 provider input tokens. Local serialization
and provider accounting differ, including serialized reasoning data. This is
not a large-context-fit qualification or a context-accounting comparison.

## Cleanup, tests and next action

The loopback listener stopped and active requests/model work joined before
cleanup. Evidence was hash-archived privately, normal fenced lifecycle cleanup
ran, and independent-client absence checks passed twice, 67.77 seconds apart.
Each check observed 3 library 404s, 3 document 404s, 49 chunk 404s, 6 empty scoped
queries and 3 empty owned Blob prefixes. All 14 protected libraries and 48
protected documents remained unchanged. No model deployment was changed or
deleted. Existing failed experimental scopes were not adopted or cleaned up.
Soft-delete retention/backups and indefinitely late external writers are outside
this current-item absence proof.

The live baseline passed **1,730 tests** (16 existing warnings), lint, formatting
and reference privacy; the corrected runner's focused safety/continuation suite
passed **117 tests**. The guidance revision's focused checks and exact source hash
are recorded separately in the result receipt: **59 tests passed**, with lint,
formatting and privacy checks. Local tests exercise actual
`IndexClient.tools` descriptions, distinct IDs and complete-cell API responses;
other profiles/tool schemas retain their frozen bundles. They do not prove that
the model follows the new guidance. Private archives retain actual payloads,
provider receipts, immutable originals, ownership history, independent reviews
and both cleanup checks; only sanitized summaries/hashes are committed.

**Next action:** in a fresh, separately bounded two-turn window on the guidance
revision, keep model/search/context controls fixed. First require a complete cell
read and check the actual node selector, cell-scoped completeness, list membership,
separate qualifier and citations. Second request the named identifier fields and
check verbatim `sectionId` versus `containingSectionId` values, correct human
citation and unavailable-locator disclosure. Stop and diagnose if either fails;
do not infer success from wording tests or consume the larger confirmation pack.
The original six-turn allowance is exhausted. Hosted execution, broker redelivery,
production authentication, long documents, sustained concurrency/scale and the
known scheduled orphan-container issue remain separate gates.

## Runner contract and frozen limits

This is the live follow-through to source-navigation candidate `01a934c`.
The candidate remains separate from context-accounting repair `4470804`, index
versioning and completed retrieval experiments. No application deployment,
provider switch, main merge or larger confirmation study is part of this run.

The reviewed coordinator composes the normal API, worker, stores, IndexClient and
agent locally. Cosmos, Blob and model calls use the existing isolated Azure
resources and Entra credentials. Every API service is explicitly injected;
there is no Service Bus sender or receiver. An exclusive private ledger records
exact ownership before writes. Unknown outcomes prohibit automatic cleanup.
Deletes verify source/generation/body hashes and use conditional ETag operations.

The prepared limits remain three libraries, four documents, five generations,
64 cumulative chunks, 50,000 local embedding input tokens, 20 logical embedding
requests with at most three attempts each, six answering turns, 36 logical model
calls and 48 physical attempts. Provider token reservations remain capped at
2M input and 200k output. Execution and cleanup have separate 30/10-minute windows.
The embedding guard conservatively includes search inputs in its token ceiling.

The six sealed cases exercise paragraph/containing section, table cell and list
qualification, section/subtree distinction, selected region/full document,
Markdown source block and unavailable locator disclosure. Source pins are supplied
in their task context. This tests controlled navigation; it is not an unbiased
test of free-form document discovery or retrieval recall. The missing old passage
mapping in the sixth task is hypothetical, rather than an actual malformed live
index entry.

Answering uses the existing pinned deployment observed at preflight, with vector
search, compact responses, handles and legacy context accounting. The 200k context
allowance and 8k maximum output are unchanged. A separate conservative 45k-per-minute
request pacing guard leaves at most 37k locally estimated serialized input per
request; its refusal is distinct from a context or retrieval failure. Provider
usage and missing-attempt reservations are reported separately. Local source,
selector and envelope token measurements are non-additive counterfactual estimates.
Reported retrieval RU covers observed instrumented responses; it is not a complete
Azure bill. Tiny fixtures cannot establish ANN index engagement or production cost.

Before mutations, the coordinator verifies actual container policies/incarnation,
the prior canary, current scoped reads, protected inventories and fresh pinned
embedding/answering deployment identity and SKU. A new resource would require a
new canary. Protected inventory changes fail isolation; the runner never repairs
or deletes a protected scope.

Before cleanup, it stops the loopback listener and joins active requests/model
work. It archives private evidence, inventories every owned row and blob, invokes
the normal fenced lifecycle through a separate in-process client, and independently
verifies point-read 404s, scoped TOP 1 emptiness and exact Blob-prefix absence.
Absence and protected preservation are checked again after 60 seconds. Private
archives are hash-verified outside Git. Soft delete/backups and indefinitely late
external writers are outside this proof.

The entry points are `scripts/granular_live_preflight.py` (read-only preflight) and
`scripts/granular_live.py` (single-use execution/cleanup). Runtime configuration,
sources, ownership ledgers, actual responses and provider receipts stay private.
The runner refuses a dirty checkout and freezes its source hashes. Public result
receipts contain sanitized measurements and hashes only.

Hosted authentication/execution, broker redelivery, broad concurrency, long-source
fit and larger answer-quality confirmation remain separate gates. Local runner
tests prove harness mechanics, not live Azure behavior or model judgment.
