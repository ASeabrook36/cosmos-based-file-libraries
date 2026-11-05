# Reference invocation and delayed-follow-up qualification

This follow-up starts at `13986b6d58b2fe5112edebb00ade21109bea8c6d` on
`codex/granular-reference-presentation`. The previous
[failed comparison](granular-reference-presentation.md) and its committed receipt,
private wire captures and archive remain unchanged. This candidate repairs the
opt-in model-facing contract; it does not change ingestion, source publication,
embeddings, ranking, raw REST behavior, defaults or context accounting.

## Required interface

Bootstrap and discovery use explicit document selectors returned by the normal
API. Subsequent granular reads use a returned action reference in the top-level
`reference` argument, or a compatible exact union in `references`. An action
already fixes its mode, document snapshot and scope: mixing it with explicit
mode/documents/paging is rejected. A document reference identifies only a pinned
document for the supported search argument; it is not a read action or a nested
document selector.

The model-facing schema, nested field descriptions, system/tool instructions,
runtime validation and error guidance must describe these same alternatives.
Invalid calls receive a valid next-call shape and an available recovery route;
the application never silently edits the call, widens scope or changes snapshots.
Existing access checks and turn-local registry lifetime remain intact. Unknown
or expired references require fresh authorized acquisition.

Local checks must execute advertised valid examples through the normal adapter
and API, and reject the observed reference-plus-mode, action-in-node-field and
documentRef-in-document-selector patterns. Source wording, list/cell membership,
citations, completeness, permissions, stale versions, replacement/deletion and
history-restoration regressions remain required. Full repository checks precede
model execution on the final frozen product/runner baseline.

## Declared bounded comparison

Use the same pinned GPT-5-mini `2025-08-07`, Responses transport, medium reasoning,
200k context allowance, 8k output reservation, verified local source publication
and initial question as the previous comparison. Compact remains the unchanged
baseline. Use the normal local API with retained source and no cloud data writes,
new resources, embeddings or simulated search rankings. The source-only fixture
has no search backend; a search detour must be reported, not called a ranking or
Azure retrieval failure.

The fixed order is compact initial navigation, compact acknowledgment, compact
follow-up, navigation initial navigation, navigation acknowledgment, navigation
follow-up. All six are real-model normal-agent turns. The source turns retain the
12-round limit; the brief acknowledgment is bounded to three rounds. One shared
meter/pacer spans the entire run: 60 logical calls, 80 physical attempts, 4 million
input/400,000 output tokens including unknown-usage reservations, the existing
45k reservation window, and a 30-minute admission deadline. Limits are not raised
after execution begins. Semantic failure is retained and does not selectively
skip a later declared cell; a hard operational limit stops the run safely.

Both formats retain exact prior user prompts and delivered assistant prose,
without tool transcripts or registry state. The follow-up visibly includes one
bookmark copied from an actual successful model-bound initial-turn response:
the first source row of the last delivered source reply, or, if no source was
delivered, an explicitly labeled outline fallback. Compact retains that row's
exact read selector; navigation retains its exact action reference. Missing
bookmarks are disclosed rather than synthesized. No hidden reference dictionary,
expected source selector or manually corrected tool call is supplied.

This is the same retention policy applied to different representations, and
deliberately tests their information-lifetime tradeoff. A full selector may survive
the turn boundary; a short reference must expire. The follow-up requests one
bookmark attempt, then recovery through available tools and a fresh source read.
It is a conservative expired-reference test, not a prose-only comparison or a
claim that applications must always attempt an expired handle before rediscovery.
Earlier acquisition, acknowledgment, rejected calls, rediscovery, refreshed labels,
source reads and repeated conversation history all belong in the measurements.

The initial task requires delivered nonempty outlines, a valid returned action or
selector, complete focused paragraph/immediate-section evidence, and a correct
answer with a human citation. Whole-document fallback does not pass. The delayed
task must revisit the correct original source in a fresh turn, respect expired
references, recover when needed, and answer from freshly delivered evidence with
truthful completeness and a correct citation. Neither task may unnecessarily
display internal IDs. Inspect both raw model drafts and actual delivered answers;
a grounding-guard refusal is a safety result, not task completion.

Exact question strings, acknowledgment text, bookmark rules, source/control/code
hashes and limits are frozen in private preparation records before the announced
model-call window. All attempts and the earlier failed comparison are retained.
Report measured provider input/output separately from local serialization,
cached/reasoning subsets, pacing waits and unavailable cloud retrieval charges.
No merge, deployment, default change or larger study is authorized by this check.

Implementation, validation and execution results follow after review.
