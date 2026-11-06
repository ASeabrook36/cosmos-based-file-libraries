# Reference invocation and delayed-follow-up qualification

This follow-up starts at `13986b6d58b2fe5112edebb00ade21109bea8c6d` on
`codex/granular-reference-presentation`. The previous
[failed comparison](granular-reference-presentation.md) and its committed receipt,
private wire captures and archive remain unchanged. This candidate repairs the
opt-in model-facing contract; it does not change ingestion, source publication,
embeddings, ranking, raw REST behavior, defaults or context accounting.

**Recommendation: hold general integration of the registry-backed presentation.**
The repaired navigation format passes both source tasks, including actual
expired-reference recovery, with correct citations and no unnecessary IDs.
However, the complete declared interaction uses 60.5% more provider input than
compact because the delayed follow-up reacquires its source through additional
calls. The interface repair is successful; cross-turn efficiency remains the
integration issue. This is a product recommendation from a bounded development
check, not a newly invented quality threshold or a general cost estimate.
The [qualification receipt](../records/granular-reference-contract-repair-20261105.json)
records controls, all six turns, counters and private evidence hashes.

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

## Implemented contract and local validation

Product commit `20e461f10dc2189b6e50f41efee0bcbe3bbe609d` repairs the opt-in
schemas, descriptions, nested field guidance, argument validation and recovery
together. Closed alternatives prohibit combining references with explicit
selectors or mode/paging overrides. Reference-looking values in node fields and
documentRef inside document selectors are rejected. Explicit bootstrap reads
require their full returned snapshot pin. Search distinguishes its documentRef
from read actions and rejects document plus documentRef.

Descriptions provide concise bootstrap and reference examples. For a malformed
known action, recovery may provide an exact executable request and its bound mode,
explicitly stating that conflicting arguments were not applied. The model must
choose and issue that request; the application does not execute a correction.
Unknown/expired references receive an executable discovery route and guidance to
copy the discovery result's actual pinned outlineSelector. Genuine unavailable
source scopes do not suggest repeating an unchanged failed action. Permission,
missing-source and version errors retain their meanings.

Ten new contract regressions validate the delivered JSON schema and execute
recovery examples through the normal agent and API. Sixteen new runner checks
cover actual six-turn local execution, bookmark expiry/reacquisition, continuation
after semantic failure, frozen controls, bounded accounting and closure. A check
compares compact A's actual callback messages/tools with the frozen `13986b6`
adapter. Its baseline remains unchanged. Jsonschema, already pinned at 4.26.0 in
the local lock, is now an explicit development dependency; no installed SDK or
model dependency version changed.

The full repository checks passed **2,091 tests** (45 warnings,
219.32 seconds), lint, formatting and privacy. An earlier sandboxed attempt passed
2,087 tests but could not bind local sockets in four lifecycle tests. Both logs
are retained; the full rerun with loopback access passed without product changes.
Independent focused runs passed 85 combined checks and 96 product/guidance/
measurement checks; these overlap the full suite and are not additional coverage
counts. Existing source, list/cell, completeness, permission, stale-reference,
replacement/deletion, history-restoration and opaque-reasoning checks remain.

Runner/plan commit `4bb1fb5d4b84530e4a0b8dc2fb23a7a10b363344` was executed once.
There were no post-execution product/runner changes or model reruns.

## Observed source and presentation outcomes

Both formats initially delivered four authored outlines and then the complete
target paragraph. Both answered the rule correctly with the correct human source
location. Navigation used returned top-level action references correctly, with
no recurrence of the malformed calls from the preserved failed comparison.

Both delayed tasks read the same paragraph from the same published snapshot in
their new turn. Compact's retained full selector remained usable and needed one
read. Navigation's actual earlier action reference expired; the first attempt
was refused before API read dispatch. The model then chose filename-scoped discovery,
a fresh pinned root outline, three child outlines, and a new paragraph action.
That path used six successful API operations after the local expiry refusal.
No registry or prior tool/source payload was restored beyond the declared
user/assistant prose and visible bookmark. That prose includes A's quoted rule;
B still had to obtain fresh source evidence rather than answer from memory.
The ordinary retained A user prompt still included a valid document pin in both
arms, so skipping catalogue discovery was available; this model chose discovery.

| Criterion | Compact A | Compact B | Navigation A | Navigation B |
| --- | --- | --- | --- | --- |
| Correct focused source freshly delivered | Pass | Pass | Pass | Pass |
| Scope and completeness truthful | Pass | Pass | Pass | Pass |
| Correct rule and human citation | Pass | Pass | Pass | Pass |
| No unnecessary internal IDs | **Fail** | **Fail** | Pass | Pass |
| Full frozen source-task criteria | **Fail** | **Fail** | **Pass** | **Pass** |

Compact A printed an internal section identifier and a full pinned selector;
compact B appended an internal section identifier to an otherwise correct human
citation. These appeared in both raw drafts and delivered answers. They are
baseline presentation failures in this sample. Navigation printed no such IDs,
and its final answers were delivered without grounding-guard replacement. Both
acknowledgments were brief and used no source tools. No source turn used a whole
document, search detour or repeated accepted scope. The expected expired-reference
refusal in navigation B was followed by successful recovery and a fresh answer.

The candidate's two quality passes demonstrate this repaired path on one consumed
fixture. They do not establish general answer quality, a ranking winner or a
statistical improvement over compact.

## Complete-interaction measurements

All six declared turns, including acknowledgments, executed. The provider reported
complete usage for **24 physical requests**, with no retries, HTTP 429s, unknown
usage or pacing refusals. Counts below include all attempts and recovery:

| Stage | Compact calls | Compact input / output | Navigation calls | Navigation input / output |
| --- | ---: | ---: | ---: | ---: |
| Initial navigation A | 6 | 48,677 / 1,733 | 6 | 36,564 / 1,012 |
| Intervening acknowledgment | 1 | 3,170 / 67 | 1 | 3,283 / 245 |
| Delayed follow-up B | 2 | 8,302 / 1,022 | 8 | 56,674 / 1,996 |
| **Complete interaction** | **9** | **60,149 / 2,822** | **15** | **96,521 / 3,253** |

Navigation's initial input is **24.88% lower**. Its complete-interaction input is
**60.47% higher**, because six additional delayed-turn model calls outweigh the
initial saving. The total run measured **156,670 input / 6,075 output tokens**.
Cached input is a subset: compact 39,552, navigation 69,632, total 109,184.
Reasoning output is also a subset: compact 1,856, navigation 2,112, total 3,968.
Do not add these subsets to the input/output totals.

Local `cl100k_base` estimates of the complete adapter messages plus tool schemas
give the following independent accounting. They include instructions, schemas,
response wrappers, reference setup/refresh, visible bookmarks, refusal and
discovery replies, and repeated conversation history at every model callback:

| Stage | Compact local input serialization | Navigation local input serialization |
| --- | ---: | ---: |
| Initial navigation A | 50,628 | 38,898 |
| Acknowledgment | 3,469 | 3,646 |
| Delayed follow-up B | 8,916 | 56,925 |
| **Complete interaction** | **63,013** | **99,469** |

The visible bookmark's removal delta across B's inputs is 134 local tokens for
compact versus 192 for navigation: the short reference is smaller per occurrence
but appears in eight requests instead of two. These deletion-based estimates are
non-additive and are not provider billing allocations. Raw provider wire replay
matches all 24 requests, including encrypted-reasoning continuity; opaque fields
were not dropped or treated as source text to improve these figures.

Measured local pacing waits total 159.684 seconds for compact and 318.470 seconds
for navigation. The same conservative window spans all six turns, so order and
window carryover affect those waits. They do not establish production latency or
model speed. Retrieval RU is unavailable because source APIs used local stores;
fake-store zero counters are excluded. There were no embedding calls or cloud
data writes. Provider input/output are measured; dollar cost is not.

This deliberately forced expired-bookmark attempt measures one persistence route,
not minimum possible recovery or a matched prose-only restoration. Retained human
answers differ naturally between arms; compact's unsolicited selector also remains
visible in its retained answer. Fixed order, caching and stochastic output further
limit causal cost/quality claims. Neither the 60.47% increase nor the initial saving
should be generalized to every conversation or reference-registry design.

## Preservation and next action

Fresh read-only checks verified the exact model/capacity before and after the run.
All source snapshots and frozen code remained unchanged, all six API contexts and
three navigation sessions closed, and the provider closed. No cloud data resources were
created or require deletion. Execution evidence and final reviewed evidence are
archived privately with verified hashes. All 292 files in the preceding comparison
root remain unchanged; its failed result and archive are preserved.

Keep this reviewed candidate on its branch and **hold general integration**. The
remaining issue is the complete workflow's cross-turn acquisition cost. A next
focused design should assess an explicit, snapshot-bound bookmark that supports
a direct normal authorized follow-up read while short action references still
expire. It must preserve truthful labels, exact pins and access checks; it must
not revive a stale handle or silently translate it to new content. That design
and any further model calls are outside this completed comparison. No merge,
deployment, default change, re-ingestion, provider switch, context-accounting
change or larger study was performed.
