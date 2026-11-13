# Granular navigation presentation candidate

This bounded candidate starts at main `e8058d7cc07bf8ede9ca93a0a6bafc5f0079feb3`
on `codex/granular-reference-presentation`. It preserves the
[previous qualification](granular-navigation-refinement.md) and its historical
captures. It changes only the opt-in agent-facing presentation of supported
granular reads. Ingestion, chunk sizes, source artifacts, embeddings, query
ranking, application defaults, answering model and context accounting are unchanged.
The separate context-budget repair is not included.

**Recommendation: hold integration of this presentation candidate.** The bounded
implementation reduces local serialization substantially and passes regression
tests, but the one matched real-model pair exposed an invocation/recovery contract
problem: compact completed the task; navigation never obtained its source text.
No selective rerun or post-run product fix was made. The existing integrated
granular-navigation baseline and all defaults remain unchanged. The
[reviewed receipt](../records/granular-reference-presentation-20261105.json)
separates these outcomes from the local estimates below.

## Representation and reference contract

Call the existing `run_turn` with `presentation="navigation"` and a normal
`IndexClient` using compact evidence and selector handles. Omitting that option
retains current behavior. No REST API or storage format changes. Existing compact
and lossless `references` formats remain supported comparison baselines.

The application holds exact, deep-copied read requests under opaque short
references. Each binding retains the library, document generation/revision and
the original selected scope. The model copies a returned `actions.read`,
`actions.outline`, `actions.section`, `actions.subtree`, `actions.parent` or
`actions.full` value into `reference`. Compatible selected references can be
combined through `references`; this forms an exact union, not a broader container
read. Paragraph membership does not establish complete-cell coverage. Pagination
references advance only the server's returned boundary of the same pinned outline.

Every reference resolves in application code. It is not a model-decoded dictionary
and never grants permission or serves cached source text. Each use calls the
ordinary API, preserving personal-library checks, platform library binding,
source integrity, active-version checks and replacement/deletion fences. Unknown,
expired, foreign, conflicting or scope-overriding references fail explicitly.
Neither a stale reference nor a changed document is silently rebound to new content.

| Information | Presentation |
| --- | --- |
| Required at use | Exact source text, filename/citation, meaningful labels and authored context, selected scope, completeness and applicable source limitations |
| Stable instructions | One retained system contract and a concise read-tool description |
| Document information | Introduced on the first retained occurrence and refreshed after its fingerprint changes or earlier context is removed |
| Machine metadata | Original pins/identifiers and selectors remain in canonical evidence and application bindings; `details=true` performs the same authorized read with canonical diagnostic fields |
| Conditional details | Errors, refusal/recovery, pagination, list/table relationships and unsupported locator information remain when applicable |

Readable JSON keeps ordinary `documents`, `outline` and `sections` lists. It
does not encode source text in a positional matrix. Original markup, Unicode,
whitespace, list identity/depth, cells, qualifiers and citations stay intact.
Known duplicate machine identifiers are omitted only from the new model view;
unknown fields are retained, while unsupported payload/selector shapes and
mixed/media replies fall back to canonical compact evidence before changing
registry state. Diagnostic evidence and the raw
API remain available. Source previews still do not establish source grounding.

## Lifetime and restoration

References are **turn-local** in this candidate. They have a fresh namespace,
an agent/library/API-target binding and an explicit closed state. They remain
valid during a delayed operation within that turn only for their exact snapshot;
normal API checks still run. They expire on return or failure. No durable chat
store or application-wide reference cache is introduced.

An earlier reference dictionary need not remain in model context. The application
retains the registry, and every newly presented result contains useful labels and
source context. Re-rendering shortened retained context emits document information
at its first remaining occurrence again. This refresh has a measured context cost;
it is not assumed free. The existing agent does not automatically shorten its
history; the shortened-history check exercises the renderer/core-loop boundary,
without adding a context-budget or compaction policy.

The demo restores user/assistant prose rather than full tool transcripts. A new
or restored turn gets new references: an old short handle fails, then the agent
must reacquire the file/scope using authorized tools. Passing an old full tool
transcript as a navigation turn's history is explicitly refused, rather than
implicitly reconstructing trusted registry authority from transcript content.
Tests and measurements include both a conservative expired-handle recovery path
and fair prose-only restoration across formats. A retained full pinned selector
can avoid some reacquisition that an expired short reference cannot; report that
tradeoff, not only fresh-turn savings.

## Budgets and compatibility

Canonical evidence remains the source of grounding, completeness, progress and
diagnostic records. Only the model-facing messages/tool descriptions are changed.
The existing context formula, provider usage anchoring, output reservations and
limits remain intact; it counts the messages actually sent. Ordinary append-only
renders must keep earlier wire messages byte-for-byte stable so the Responses
adapter retains encrypted-reasoning continuity. Deliberate history shortening
does not claim that missing reasoning prefixes can be restored.

Backend canonical response budgets remain conservative and unchanged. Therefore
a response that is too large canonically can still be refused even if this new
presentation would fit. No larger backend budget or text clipping is used to
manufacture a presentation gain. The roughly 5,000-source-token case is a
measurement workload, not a new default read limit.

## Bounded measurement and model controls

Reproduce the historical five replies and all six recorded model requests before
comparison. The final paragraph was 1,324 model-evidence JSON tokens, 1,447 with
its message wrapper. Its unchanged marked-up source was 11 local tokens; the
plain sentence was 7. Existing response-local reference packing yields 1,246 and
1,383 respectively. Count instructions, schemas, reference setup, wrappers,
repeated history and additional navigation/refresh calls in complete interactions.
Local `cl100k_base` counts are estimates, not provider usage or billing allocations.

Use actual local API/normal-agent responses for a small paragraph, multiple
paragraphs, complete cell and table, section-own content, authored subtree,
approximately 5,000 source tokens and the full historical navigation route.
Include delayed access, shortened context and restored conversation costs. Keep
per-response, per-document and per-source-unit overhead separate, and verify
exact source text, relationships, citations, pins and completeness across formats.

After local regression and review, execute one compact/navigation model pair with
the unchanged historical outline question and exact retained DOCX publication.
The normal API runs against hash-verified local stores. This tests presentation
and reference resolution; it does not requalify cloud ingestion or lifecycle.
Search is explicitly unavailable in this source-only fixture rather than simulated
with invented rankings. Any attempted search is retained as a limitation/detour.

Both cells use the same pinned GPT-5-mini `2025-08-07`, Responses transport,
medium reasoning, 200k context allowance, 8k output reservation, 12 rounds and
source/question. The reviewed 45k pacing window spans the pair. Shared limits:
26 logical calls, 36 provider attempts, 2M input/200k output including unknown-usage
reservations, and a 20-minute admission deadline. Fixed compact-then-navigation
order is disclosed. Fresh read-only deployment/capacity checks precede execution
and follow it. All attempts are retained; no selective rerun. Only answering-model
calls and management reads use Azure; no Cosmos/Blob/queue/embedding writes or
new cloud resources are needed. Closing the provider, local API contexts and
reference session is verified. Announce the cloud window before executing.

Review actual delivered outlines, returned actions, resolved API selectors,
complete focused source and final answer/citation. Record internal-ID display,
detours, repeated reads, refusals, measured usage and pacing separately. Cell/list,
replacement/deletion and history-refresh behavior are locally tested, not claims
from this one live outline task. This consumed fixture is a development check,
not a new quality benchmark, broad recall study or default-change justification.

## Local comparison results

Product commit `31137cb1372ba3a24c24c150f82fa038be6a1090` and comparison/runner
commit `f88f4f22423b421c8f2b7b19bd48571b536d826d` were frozen before execution.
The runtime and runner were not changed afterward. All measurements below are
local `cl100k_base` JSON serialization counts, **not provider token usage**.

The exact original-source reconstruction reproduces every compact model callback
and all five replies from the retained qualification. The scripted comparison
uses the same source, choices and six callback boundaries, changing presentation
and its associated instructions/schema only:

| Exact original route | Compact | Existing references | Navigation candidate |
| --- | ---: | ---: | ---: |
| Final paragraph reply | 1,324 | 1,246 | 620 |
| Five unique reply payloads | 8,743 | 7,931 | 4,685 |
| Cumulative input messages plus tools | 50,592 | 48,108 | 36,010 |

The candidate reduces the paragraph reply by **53.17%**, the five unique payloads
by **46.41%**, and cumulative input serialization by **28.82%**. The cumulative
measure includes instructions, tool schemas, wrappers, reference setup and each
repeated exposure of retained history. These are matched scripted choices, not
evidence that a model takes the same route. Source markup remains 11 tokens;
the plain sentence remains 7. The earlier illustrative 615-token candidate
capture is preserved; random application-generated reference namespaces tokenize
slightly differently. The final comparison uses its sealed 620-token capture.
An earlier comparison that reserialized assistant argument whitespace is also
retained; the final version preserves original argument strings exactly.

An ordered removal decomposition distinguishes selected source fields, per-unit
metadata, per-document metadata and the response envelope. For that paragraph:

| Allocation | Compact | Existing references | Navigation |
| --- | ---: | ---: | ---: |
| Source-field contribution | 15 | 15 | 15 |
| Per-source-unit fields | 241 | 209 | 112 |
| Per-document fields | 774 | 712 | 379 |
| Response envelope | 294 | 310 | 114 |

These allocations sum to each response but depend on JSON removal order and BPE
boundaries. The 15-token field contribution is not the 11-token standalone source
body. Non-body fields include useful scope, citation and limitations, not only
removable overhead. The first retained navigation reply introduces documentInfo
(235 standalone tokens; 238 removal-delta tokens); later replies retain labels,
scope, citation and applicable limitations without that stable block. The private
decomposition includes 42 responses and 30 first/last metadata comparisons.

The expanded local fixture covers the requested source shapes. Each row includes
the complete scripted interaction, not just the final response:

| Workload | Compact | Existing references | Navigation |
| --- | ---: | ---: | ---: |
| Small paragraph | 15,093 | 14,668 | 12,160 |
| Multiple paragraphs | 17,091 | 16,006 | 13,209 |
| Complete cell | 18,214 | 16,877 | 13,668 |
| Complete table | 17,950 | 17,183 | 13,766 |
| Section own content | 15,483 | 14,955 | 12,261 |
| Authored subtree | 19,167 | 18,223 | 14,207 |
| Large selection | 33,485 | 28,891 | 23,030 |
| Five-response route on expanded fixture | 51,401 | 47,656 | 35,335 |
| Delayed reference after intervening navigation | 35,944 | 33,858 | 25,652 |

The large selection contains 5,022 plain-source tokens, 5,215 rendered-source
tokens and 47 source atoms. The expanded fixture's paragraph reply is 1,330
compact tokens; it is a different capture from the historical 1,324-token target.
Exact source bodies, citations, pins, relationships and selected scopes match
across formats in all 27 workload captures.

History checks count refresh and recovery. The shortened-history row includes
initial acquisition; restored-history rows start at the resumed turn with
preexisting context, as detailed below:

| History condition | Compact | Existing references | Navigation |
| --- | ---: | ---: | ---: |
| Shortened history, same application session | 26,203 | 25,350 | 20,659 |
| Restored with exact full selector retained | 8,017 | 7,911 | 6,993 |
| Restored with prose only | 62,043 | 57,309 | 44,654 |
| Restored with expired short handle | — | — | 49,108 |

Shortening refreshes documentInfo at its first retained use, adding 228 local
JSON tokens already included above. The prose-only comparison uses the same six
authorized API calls in each format. The expired-reference route includes one
local refusal and six successful reacquisition calls. Comparing it directly with
the retained-full-selector route would compare different retained information;
the full selector needs only one API read. Routes are specified conservative
fixtures, not proven minimum-cost recovery paths. All ten history captures retain
the same selected source/citation. Shortening is a tested renderer boundary;
automatic conversation compaction is not introduced by this candidate.

The expired-handle acquisition turn is separately captured at 12,220 local input
tokens, making its recorded acquisition-plus-recovery total **61,328**, rather
than 49,108. Compatible compact/reference acquisition legs advertising the later
full selector were separately captured at 15,240/14,703; composing those with
restoration gives 23,257/22,614. These are composed local scenarios, not continuous
captured conversations. Navigation's retained raw selector was supplied beforehand;
its acquisition cost is unavailable, not zero. Prose handoffs are authored fixture
summaries; model-generated summarization would add unmeasured cost. Acquisition
fixtures start from a supplied subsection selector, so earlier file discovery is
outside this measured boundary.

## Real-model result and remaining defect

One fixed-order compact/navigation pair ran against the same verified local
publication, with the pinned model and controls above. It made 14 physical model
calls with complete usage reporting, no HTTP 429s, provider retries or unknown
usage. It was not rerun. Independent review followed actual provider-bound
messages, successful responses, tool calls and application-resolved selectors.
Canonical diagnostic transcripts were not mistaken for delivered model evidence.

| Observed result | Compact | Navigation |
| --- | --- | --- |
| Model calls | 6 | 8 |
| Delivered nonempty outlines | 4 | 1 |
| Focused source body received | Yes | No |
| Correct rule and human citation | Yes | No; grounded refusal |
| Provider input tokens | 48,558 | 42,062 |
| Provider cached-input subset | 31,616 | 31,360 |
| Provider output tokens | 1,461 | 3,882 |
| Provider reasoning-output subset | 832 | 2,816 |
| Actual cumulative local messages plus tools | 50,634 | 38,985 |
| Unique local tool-reply JSON tokens | 8,743 | 1,864 |
| Repeated tool-reply exposures | 15 | 28 |
| Local pacing wait, seconds | 111.306 | 156.285 |

Navigation made five malformed read attempts and one search detour after its
initial outline. It put action references into outline/node fields, mixed a
reference with forbidden mode overrides, and once placed documentRef inside a
document selector. No follow-up reference resolved successfully. The reference
mixed with mode=selected named the document-root outline, not a full-document
read; exact action-map review corrected an interim interpretation. No whole-file
request or source fallback was delivered. Search is explicitly unavailable in this local source-only
fixture; that detour is not an Azure retrieval failure or a ranking result.

The model's raw final draft honestly declined to state the rule, but displayed an
unrequested internal document identifier/revision and requested user direction.
The unchanged grounding guard replaced it with a short, accurate no-source
refusal. That safety behavior does not make navigation successful. Compact read
the relevant paragraph, preserved its wording and gave the correct human citation.

Review found concrete inconsistencies in the delivered interface:

- The schema's alternative required fields still permit reference plus mode,
  although the resolver rejects that combination. The provider uses non-strict
  tools; schema exclusivity alone would not establish reliable behavior.
- Nested selector documentation still says to copy outlineSelector, while the
  candidate presents actions instead.
- Some invalid-request recovery replies recommend documentSelector/readSelector,
  which the new presentation has replaced. A usable recovery path must name the
  actual accepted argument shape and a valid returned action.

This single trace does not establish which inconsistency caused each model
choice. The next focused change should align reference invocation, retained
explicit-selector documentation and error recovery, then receive a newly frozen
bounded check. Do not weaken scope checks, permit silent rebinding, or claim that
the local token reduction has already improved model usability.

The pair totals **90,620 input / 5,343 output tokens**. Cached input and reasoning
are subsets, not additional tokens. The candidate's lower input usage is not a
successful-task efficiency gain: it failed to retrieve the answer and used more
calls/output. Fixed order, caching and stochastic choices prevent a causal cost
or latency conclusion. There is no measured Cosmos retrieval charge in this
local-store check; RU is unavailable, not a claimed zero-cost retrieval result.
There were no embedding calls or cloud data writes.

The actual local navigation totals include all six error replies, extra model
calls and repeated history; they contain zero focused-source tokens. They cannot
be compared as equivalent completed work. Offline Responses reconstruction
matched all 14 actual requests, including encrypted-reasoning continuity. The
unchanged pacing policy admitted every request without a refusal, retained its
8,000-token output reservation and estimated input conservatively above reported
input by 1,311–2,536 tokens. Its measured waits are operational delays, not model
context usage, provider usage or production latency.

## Validation, preservation and disposition

The full regression suite passed **2,065 tests** (27 warnings), followed by Ruff
lint/format and repository privacy checks. Ninety focused checks also passed;
they overlap the full suite and must not be added to it. Local checks cover exact
snapshot/library/actor/API-target binding, changed permissions, replacement,
deletion, expired/cross-session references, closed sessions, compatible unions,
source text/list/cell semantics, diagnostic reads, fallback, pagination, source
grounding and unchanged encrypted-reasoning continuity. They did not predict the
real-model invocation failure. Live cell/list and delayed/restored-history
usability were not tested; the live pair exercised outline navigation only.

Fresh read-only deployment/capacity checks passed before and after execution;
code hashes and both local source snapshots remained unchanged. The provider,
local API contexts and navigation session closed. No cloud data resources were
created, so there is no owned cloud data scope to delete. Private execution and
reviewed evidence archives are hash-verified. The automatic execution archive
captured the command-output file before final stdout was written; its original
bytes remain unchanged, and the completed output is separately frozen in the
reviewed archive. All 386 files in the previous
refinement evidence root remain unchanged. Prior successful cell/identifier
checks and the failed outline attempt remain historical results, not new passes.

This is a reviewable opt-in implementation and comparison with a **hold**
recommendation. It needs no re-ingestion or embedding rebuild. Do not merge it or
change defaults until the invocation/recovery contract is repaired and focused
model navigation succeeds. No main merge, deployment, context-accounting change,
retrieval tuning, provider switch or larger confirmation study occurred.
