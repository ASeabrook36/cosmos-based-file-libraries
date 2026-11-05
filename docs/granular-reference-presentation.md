# Granular navigation presentation candidate

This bounded candidate starts at main `e8058d7cc07bf8ede9ca93a0a6bafc5f0079feb3`
on `codex/granular-reference-presentation`. It preserves the
[previous qualification](granular-navigation-refinement.md) and its historical
captures. It changes only the opt-in agent-facing presentation of supported
granular reads. Ingestion, chunk sizes, source artifacts, embeddings, query
ranking, application defaults, answering model and context accounting are unchanged.
The separate context-budget repair is not included.

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
unknown features and unsupported/mixed/media replies fall back to canonical
compact evidence before changing registry state. Diagnostic evidence and the raw
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

## Bounded measurement and model plan

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

Results and the integration recommendation are recorded after the bounded checks.
No merge, application deployment or larger study is part of this candidate.
