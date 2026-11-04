# Granular outline inspection contract

## Result and integration recommendation

**The contract repair is implemented and locally tested; the one-turn live
navigation qualification did not pass. Keep integration sign-off on hold.**
Actual nonempty authored outlines reached the model, which copied returned
selectors. It then read a table instead of the required rule paragraph, repeated
that table read, and hit the unchanged local pacing ceiling before another model
request could be dispatched. No final answer or citation was produced.

Product commit `752d420` contains the description, parameter documentation and
inspection-response fix. Runner `e8326a7` executed exactly one fresh attempted
turn, with zero completed turns and no rerun. Its product runtime matches that
reviewed fix. The [public receipt](../records/granular-outline-qualification-20261104.json)
retains the failed qualification independently of the two earlier successful core
checks and all preceding experiments. No post-live product changes were made.

| Required observation | Actual evidence | Assessment |
| --- | --- | --- |
| Nonempty authored outlines delivered | Four outline responses were present in subsequent successful model requests; each used `includeText=true`. | Passed |
| Use of a returned selector | The model copied exposed selectors through the hierarchy, then copied a table read selector. | Proven for that table; not the required rule scope |
| Focused source for the requested rule | A complete table was delivered; the rule paragraph or its immediate section was never read. The table was requested again with explicit subtree scope and a larger response allowance. | Failed |
| Correct answer and citation | No final answer was produced. | Not demonstrated |
| No whole-document content fallback | No full-document request occurred. | Passed |

Independent review pairs model-input files with successful provider results;
writing an input file alone does not establish delivery. The second table reply
was returned by the API, but its subsequent model request was refused locally.
The first table reply already reported complete selected scope. The record does
not establish why the model repeated it. Inspection recovery guidance is covered
by local API/adapter tests; this live turn used true directly and did not exercise
that recovery branch.

The concrete remaining problem is an incomplete model navigation route plus a
qualification pacing limit that stops the growing conversation. The **200k
context allowance was not exhausted**. The harness reserves 8k output within a
45k local serialized-token pacing allowance, leaving a 37k input ceiling per
request. Logical call seven exceeded that ceiling before provider dispatch; this
was neither an Azure rejection nor a retrieval API refusal. Waiting alone cannot
fit a single request larger than this harness ceiling.

An offline replay of the normal Responses adapter matched all six archived live
requests, then reconstructed the undispatched seventh request: **42,073 local
serialized input tokens**, 5,073 above the input ceiling. Removing the final
duplicate tool output in a diagnostic copy reduced that estimate to 35,764;
removing opaque encrypted-reasoning fields reduced it to 28,071. Those are
counterfactual local serialization counts, not provider token usage or predictions
that edited requests would work. Nothing was removed from the live conversation.

Next, review the captured table detour/repeat and the qualification pacer's
counting of opaque encrypted-reasoning strings before defining a separately
authorized qualification. This diagnosis is separate from the context-accounting
repair and from source-response compression. If pacing is changed,
freeze and disclose that changed control and check deployment capacity first;
do not silently raise it or count this consumed attempt as a pass. Any change to
selector/envelope presentation remains separate response-efficiency work. This
record does not establish that a higher limit alone would produce correct
navigation or an answer. The bounded allowance is consumed; no additional model
turn is included in this candidate.

### Validation, usage and preservation

The product regression run passed **1,850 tests**, lint, formatting and reference
privacy checks. The final runner/case suites separately passed **103 tests**;
these overlap the regression suite and are not an additive unique-test total.
Local coverage includes actual raw, compact and references responses, both
selector modes, true/default outlines, content-free inspections with usable
recovery selectors, scope/snapshot preservation and unchanged budget refusals.

Six physical model calls measured **62,474 input / 1,391 output tokens**. Logical
call seven stopped before a physical attempt. One embedding request reported
98 prompt tokens for ingestion; there was no search/query embedding. There were
no provider failures, 429s, retries or unknown provider usage. Local serialization
and partial Cosmos counters are recorded separately in the receipt; they are not
provider tokens or a complete Azure bill.

The six API reads measured 28.56 RU; 15 chunk writes measured 1,007.45 RU, excluding
other Cosmos and storage operations. Five unique replies actually delivered to
successful model calls totaled 16,221 local evidence-JSON tokens versus 287 source
text tokens, with an 8,834-token selector-removal delta and 7,058 tokens remaining
after source/selectors were removed. These figures are non-additive estimates.
The undelivered duplicate reply and repeated conversation exposures are recorded
separately. Response overhead remains substantial and outside this change.

Source publication passed on the exact original DOCX bytes. Normal owned cleanup
and two independent absence checks **65.85 seconds apart** each verified one
library 404, one document 404, 15 chunk 404s, two empty scoped queries and one
empty Blob prefix. All 14 protected libraries and 48 documents remained unchanged;
the final 179-event ownership ledger has no unresolved operations or messages.
Because the exception skipped normal post-turn binding checks, a separately
recorded read-only check after cleanup verified the exact prior model/embedding
bindings and all executed source hashes. It made no model call or data mutation.

Main remains `1ddc438`; context repair, index versioning and the unfinished
Microsoft provider decision remain separate. Earlier complete-cell and exact-ID
successes are retained, not rerun or regraded. No merge, application deployment,
new cloud infrastructure or larger study occurred. This small failed attempt says
nothing about general answer quality, recall or production/scale readiness.

## Preserved preparation contract

This change closes the specific contract gap found in the
[two-turn follow-up](granular-navigation-followup.md). It retains the successful
complete-cell and exact-identifier results and all earlier evidence. It does not
change ingestion, stored sources, embeddings, query policy, permissions, lifecycle,
context accounting or the provider decision. Response-overhead work stays separate.

## Model-facing contract

| Request | Returned content |
| --- | --- |
| `includeText=false`, any mode | Size/coverage inspection only; no source bodies or outline entries |
| `mode=outline`, `includeText=true` or omitted | Authored navigation entries, without source body text |
| `mode=selected` or `full`, `includeText=true` or omitted | Selected or full source content if it fits the unchanged budget |

The granular tool description and the `includeText` parameter explain this
distinction. A granular inspection response supplies a usable `readSelector` with
`includeText=true`, preserving the inspected mode, document scope, snapshot pins,
outline paging and effective `maxTokens`. Its `nextAction` explains that copying
the selector obtains the inspected scope; if content does not fit, the caller must
narrow or paginate. Inspection never establishes source/outline delivery or
completeness. Budget refusals retain their existing recovery behavior.

Only the opt-in granular profile receives this guidance. Other profiles' tool
bundles and response behavior remain unchanged. The four product files involved
are `tool.py`, `selector_hints.py`, `reading.py` and `presentation.py`; a reviewed
runtime commit freezes their exact hashes before cloud work. No re-ingestion is
needed for this contract clarification.

## One-turn qualification contract

Use the same original DOCX bytes and the same pinned GPT-5-mini `2025-08-07` model,
embedding binding, vector search, compact evidence, handles and legacy context
accounting as the completed runs. Per-turn controls stay at 200k context, 8k output,
medium reasoning and 12 tool rounds. The existing local pacing reservation remains
45k per minute, including 8k output, with a 37k serialized-input ceiling.

Exactly one normal-agent turn must obtain nonempty authored outline entries
**delivered in a successful model request**, then use a selector returned in those
entries for a focused read of the damaged-vessel rule. The target paragraph or its
immediate authored section is acceptable; a broader ancestor is not. Selection
identity/scope/pins must be copied, not reconstructed; transport budget/content
fields may be supplied without widening scope. The final answer must preserve the
rule and cite the delivered source correctly. Any whole-document content fallback fails
this navigation check. Merely issuing an outline request or inspecting its size
does not pass. Manual review examines actual calls, delivered evidence and answer;
automated observations are supporting checks, not an answer-quality grade.

One fresh shared library, one document/generation and at most 16 chunks are
permitted. Source publication is verified before answering. Cumulative model
limits are 13 logical calls, 18 physical attempts, 1M input and 100k output including
unknown-attempt reservations. Embedding retains the previous 20-call, three-attempt,
50k-local-input ceilings shared across ingestion/search. Execution and cleanup
retain separate 20/10-minute limits. No retry of the completed model turn, raised
ceiling, changed prompt or selective rerun is allowed.

The existing coordinator's allowlisted `outline-v1` protocol reuses the reviewed
ownership ledger, private queue, worker, model meter and cleanup. Fresh health,
capacity, container-incarnation and exact prior deployment-binding checks precede
mutations. Quiesce writers, archive evidence, then run normal owned cleanup and
independent absence checks at least 60 seconds apart. Protected snapshots and all
earlier evidence remain unchanged. Total service billing and uninstrumented charges
remain unavailable; measured usage, partial RU and local token estimates stay separate.

Entry point: `scripts/granular_followup.py --protocol outline-v1`, with `prepare`,
`preflight` and `execute` actions. Preparation requires the exact reviewed runtime
commit, the previous follow-up's private state and its hash-bound archive manifest.
The original two-turn protocol remains the default and retains its existing limits.
No main merge, application deployment, resource provisioning or larger study is included.
