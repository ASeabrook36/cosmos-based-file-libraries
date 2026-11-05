# Granular navigation and pacing refinement

This candidate continues from `9fcc9bc` on `codex/granular-source-navigation`.
The previous [outline qualification](granular-outline-contract.md), including its
failed seventh request, remains unchanged. The original six-turn results and the
successful complete-cell/identifier checks are retained. Pacing and presentation
are separate changes; context-accounting repair, index versioning, provider choice,
retrieval tuning and the larger study remain outside this candidate.

## Retained-request audit

The normal Responses adapter can reproduce the first six wire requests exactly
from the retained inputs and responses, preserving all encrypted reasoning items.
Replaying those responses locally also reconstructs the undispatched seventh
request. No new model call is required for this audit.

| Request | Provider input tokens | Old serialized estimate | New guarded pacing estimate |
| --- | ---: | ---: | ---: |
| 1 | 2,909 | 3,196 | 4,220 |
| 2 | 5,284 | 7,306 | 6,752 |
| 3 | 8,361 | 13,222 | 10,054 |
| 4 | 11,304 | 17,847 | 13,359 |
| 5 | 14,223 | 22,331 | 16,769 |
| 6 | 20,393 | 31,033 | 24,066 |
| 7 | Unavailable: never dispatched | 42,073, reconstructed | 31,653, reconstructed |

The old estimator tokenized opaque encrypted-reasoning strings as ordinary JSON
text. That transport serialization is not the model's reasoning-token count.
Request seven has 28,071 local tokens after those opaque fields are excluded from
an **accounting copy**; 704 prior provider-reported reasoning tokens remain to
reserve separately. No field is removed from the actual request. A different,
exact-prefix estimate anchors to the last measured input, reserves the preceding
output and adds newly appended content. It is 27,192 for request seven. Neither
estimate is provider-measured usage for that undispatched request.

OpenAI's [conversation-state guidance](https://developers.openai.com/api/docs/guides/conversation-state)
requires preserving complete response output for stateless reasoning continuity.
The existing adapter remains responsible for replaying those items unchanged.
The pacing policy does not decode or discard encrypted reasoning.

## Separate limits and pacing policy

| Limit | Meaning and retained control |
| --- | --- |
| Model context capacity | The [pinned model's documented context](https://developers.openai.com/api/docs/models/gpt-5-mini) is 400k; the experiment uses a smaller 200k allowance. Input, output and reasoning share context. |
| Deployment rate capacity | Fresh read-only ARM receipts report 50,000 tokens and 50 requests per 60 seconds for the unchanged pinned deployment. This is throughput capacity, not model context size. |
| Local request/window reservation | Retain 45,000 tokens per 60 seconds, including the entire 8,000-token output allowance, with request-spacing enforcement. The rate budget is not raised to fit the old failed request. |
| Cumulative experiment bounds | One attempted turn, at most 13 logical calls/18 physical attempts, 1M input/100k output including reservations for unknown attempts. Waiting never resets these totals. |
| Source and lifecycle bounds | One fresh owned library/document/generation, at most 16 chunks; 20-minute execution-admission and 10-minute postcheck/cleanup deadlines. |

The new policy uses the larger of (a) non-opaque serialized content plus reasoning
reservations bound to exact prior successful response items and (b) an applicable
exact-prefix provider-input anchor plus prior output and newly appended content.
It then adds an uncertainty reserve of the larger of 1,024 tokens or 10%, and
retains the full 8,000-token output reservation. Unknown or modified opaque items
refuse instead of being counted as zero. Missing reasoning detail reserves the
entire corresponding output count. These are conservative policy estimates;
reported input/output remain separate measured counters.

For the old seventh request, this policy estimates 31,653 input tokens after its
uncertainty reserve, giving a 39,653-token total reservation under the unchanged
45k window. This is a local counterfactual admission result, not proof that Azure
would accept the request or that the model would navigate correctly afterward.
The 10%/1,024 reserve is an explicit operational margin, not a statistical guarantee
from six observations. Observed usage can increase reservations; failed attempts
retain theirs. No output allowance is reclaimed merely because one answer is short.

Microsoft's [current quota documentation](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/quota)
distinguishes deployment rate limits from model context and documents rate-limit
headers and 429 handling. Documentation and cached earlier descriptions differ on
service token-rate estimation. The client therefore does not claim to reproduce
Azure's exact admission formula: explicit capacity receipts, conservative local
reservations, bounded attempts and observed service responses remain distinct.

Pacing lives in qualification tooling. The normal agent's existing context guard
and the separate context-accounting repair are unchanged. Requests carry full
reasoning continuity, `store=false`, disabled truncation, medium effort and the
same 8k output cap. The local RPM ceiling is 45, with a minimum 1.333-second
spacing; failed/unknown attempts retain a full local window reservation in
addition to the unchanged cumulative unknown-usage allowance. Predispatch diagnostics record estimates and refusals before
any provider attempt; an undispatched logical call is not invented provider usage.

## Navigation diagnosis and targeted contract

The retained target outline exposed paragraph numbers and a table label without
content hints. It also repeated duplicate selectors on each node and source atom.
The selected table reported complete selection, but the semantics of implicit
versus explicit subtree scope and unselected document content were not easy to
see together. These observations justify clearer navigation; one trace cannot
establish why the model chose the table or repeated it.

The refined granular view exposes bounded exact source previews as navigation
hints, authored heading ancestry and explicit scope semantics. A preview is not a
complete source read, even when a short atom happens to fit. Agents must use the
returned pinned selector for complete source evidence and its citation. Source
bodies, list membership, qualifiers, citations, unavailable locators, snapshot
identity and completeness stay authoritative. Redundant selectors and audit-only
metadata may be omitted from the compact model view; full provenance remains in
storage and the raw representation. Unsupported locations are not invented.

Local tests cover actual API/tool responses, source/pin equality, inspection
content removal, serialized budgets, the earlier cell and identifier contracts,
and permissions/lifecycle refusals. An offline replay loaded the exact retained
source snapshot into in-memory services and sent the same six read requests
through the real local API and normal client. It performed no worker, embedding,
model or Azure calls. Source bodies, locations, citations, IDs and pins matched.

| Retained scope | Old API evidence JSON tokens | Refined API evidence JSON tokens |
| --- | ---: | ---: |
| Root outline | 2,144 | 1,766 |
| First authored section outline | 2,687 | 1,941 |
| Nested section outline | 2,688 | 1,950 |
| Target subsection outline | 2,678 | 1,887 |
| Table read, each of two requests | 5,749 | 3,940 |

These are local serialization estimates for identical API scopes. They are not
model usage or a prediction of model efficiency; the prior duplicate table reply
never reached a successful model call. The target subsection now exposes the
rule paragraph's original prefix without making that preview complete source evidence.
Presentation commit `9648473` and pacing commit `b9176a3` are separately attributable.

The final local regression passed **1,965 tests** (23 deprecation warnings),
with lint, formatting and reference-privacy checks passing. An earlier run exposed
four sandbox-only localhost binding failures and an outdated qualification helper
that expected a per-paragraph full-document selector. Five helper accesses now
copy the authorized root document selectors; 62 affected tests passed before the
full rerun. All local attempts are retained privately.

## Frozen bounded qualification

After local validation and independent review, freeze a new `outline-v2` protocol
with the same original DOCX, pinned GPT-5-mini `2025-08-07`, embedding binding,
vector search, compact evidence, handles and legacy context accounting. Keep the
same question and source-based acceptance criteria. The fresh turn must receive
nonempty outline entries, copy a returned selector for the relevant paragraph or
its immediate authored section, receive the complete focused source, and produce
the correct answer and human citation. Preview-only answers and whole-document
fallback do not pass. Record detours and repeats even if the turn eventually passes.

Those configured deadlines are not a hard total wall-clock guarantee: joining
in-flight work, quiescence and archival can overrun to preserve safety. Report
actual elapsed time separately.

Bind the exact prior failed receipt/archive and fresh capacity evidence, then use
one new ownership ledger and meter. No resume or selective model rerun is allowed.
Read-only postchecks must retain source/binding/code evidence even on failure,
without masking the original failure or bypassing cleanup. Quiesce writers,
archive, clean only owned resources and verify independent absence twice at least
60 seconds apart. Rehash all three earlier private evidence roots unchanged.

The combined live check qualifies the revised candidate; it is not a controlled
causal comparison isolating presentation from pacing, or a fresh quality benchmark.
No merge, application deployment, resource provisioning or larger study is authorized
by this protocol.


## Executed result and integration recommendation

**Ready for integration of the opt-in granular-navigation candidate.** The single
frozen `outline-v2` turn passed independent source-based review on executed runner
`eea454df8c773a544b74c19475ca4c3fcf67d776`, with runtime bytes identical to
`96484734957382b25214077fc10aea30c125e6fe` and pacing from `b9176a3`. No product or
runner changes followed the live run; the final candidate adds reviewed evidence.
See the [qualification receipt](../records/granular-refinement-qualification-20261105.json).

The actual model received four nonempty authored outlines, copied the returned
selector for the relevant paragraph, and then received that complete source atom
in a successful sixth provider request. The final answer accurately reproduced
the rule, including its condition, and used the exact human-readable paragraph
citation. Selection completeness was true while document completeness remained
false. There were **no table detours, repeated reads, whole-document fallbacks,
size-only inspections, retries, rate-limit errors or pacing refusals**. Navigation
previews did not substitute for the required focused source read.

This closes the outstanding outline-to-focused-read check. It does not establish
general answer quality, retrieval recall or a causal explanation for the earlier
model choice. Presentation and pacing changed together in this live check; their
commits and offline measurements remain separate. The earlier failed attempt and
successful cell/identifier checks were preserved, not replaced or rerun. The new
turn does not repeat every earlier permission/lifecycle or cell/identifier case;
local regressions and the earlier live evidence supply that separate coverage.

### Measured usage and pacing

| Request | Provider input | Guarded local input estimate | Provider output |
| --- | ---: | ---: | ---: |
| 1 | 3,019 | 4,330 | 200 |
| 2 | 4,989 | 6,504 | 266 |
| 3 | 7,203 | 8,850 | 141 |
| 4 | 9,301 | 11,156 | 120 |
| 5 | 11,320 | 13,555 | 168 |
| 6 | 12,844 | 15,417 | 788 |

Six physical requests reported **48,676 input and 1,683 output tokens**. The
30,976 cached input tokens and 1,024 reasoning output tokens are subsets of those
totals. No attempt has unknown usage. Exact offline replay matched all six live
wire requests, including the encrypted reasoning. The full 8k output reservation
was retained for every request; the largest total reservation was 23,417 tokens.
The unchanged 45k window imposed 108.159 seconds of measured local waiting within
a 129.157-second model turn. The conservative policy worked for this sequence;
service rate-limit headers were not captured, and this is not proof of Azure's
internal admission formula or a throughput benchmark. The new route's largest raw
serialized request was 23,395 local tokens, already below the old 37k input
boundary. This live result therefore does not prove acceptance of the old seventh
request or isolate the pacing change; that request remains an offline
counterfactual with unavailable provider usage.

The single embedding request reported 98 input tokens for 15 source atoms, with
no retry; raw embedding provider bodies are not separately retained. Five source
read operations reported **23.80 RU**, and 15 chunk writes reported **1,007.45 RU**.
Their **1,031.25 RU** sum is only an instrumented subtotal. Metadata, publication,
preflight, source-snapshot and cleanup operations are outside that subtotal, so
total qualification RU and monetary cost remain unavailable. This navigation
turn performed no vector search; the fixed vector setting and earlier search
qualification are separate evidence.

### Representation overhead still to improve

The five unique delivered tool replies contained 8,743 locally estimated evidence
JSON tokens (9,425 with tool-message wrappers). An ordered JSON-removal analysis
attributes 2,693 to selector fields, 144 to navigation-preview fields, 15 to source
body fields, and leaves 5,891 in navigation/protocol metadata. Those four numbers
sum only under that specified removal order; BPE boundaries make them unsuitable
as provider billing allocations. The remaining metadata includes useful hierarchy,
identity, citations and completeness, not simply waste.

Standalone preview text was 27 local tokens, separate from the 11-token focused
source body. The final focused reply alone was 1,324 evidence JSON tokens, with a
301-token selector-removal delta and 1,008 tokens after source and selectors were
removed. These are local serialization estimates. Replayed conversation history
exposed tool messages 15 times across the six model requests; cached provider
usage is reported separately above.

The final answer also unnecessarily echoed its internal selector despite the tool
guidance. That did not invalidate the frozen source-read/citation rubric, but it
is a concrete remaining presentation issue. Reduce repeated protocol/selector
material and discourage internal selector display in a separately evaluated
presentation-efficiency change. Do not infer a general efficiency improvement
from this one shorter route.

### Safety, evidence and scope

Fresh authenticated preflight and before-execution checks verified the same pinned
answering and embedding deployments. Answering binding/capacity was checked again
immediately before the model; both deployment bindings were checked after the run. Source
snapshots, post-run bindings/capacity and code hashes matched. The runner created
one owned library, document and generation, 15 chunks and seven blobs, then
quiesced writers and cleaned its owned scope. Two independent checks 64.120
seconds apart found the owned library/document/chunks absent and the blob prefix
empty, with the protected 14 libraries and 48 documents unchanged. The ownership
ledger ended with zero unresolved operations and unfinished messages.

All 1,621 files in the three earlier private evidence roots were rehashed unchanged.
Private before-cleanup, after-cleanup and reviewed-final archives retain all new
attempts, actual tool/model evidence, local failures, reviews and cleanup receipts.
The public receipt contains hashes and aggregate measurements, with environment
identifiers and source text withheld. Two-time current absence does not prove
backup erasure or indefinite future absence.

No further re-ingestion is required for this presentation/pacing refinement when
the existing granular source representation is present. Older documents without
that representation still require the already documented granular ingestion path.
The separate context-accounting repair, index versioning and Microsoft provider
decision remain excluded. Main is unchanged; no merge, deployment or larger study
was performed.
