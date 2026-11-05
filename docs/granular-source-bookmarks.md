# Conversation-owned source bookmarks

This candidate continues from `90e0d90` on
`codex/granular-reference-presentation`. The earlier
[reference-contract repair and forced-expiry comparison](granular-reference-contract-repair.md),
[presentation measurements](granular-reference-presentation.md), and their
archives remain unchanged. The historical compact regression still executes
the adapter from its original Git commit. CI now fetches that history rather
than replacing or skipping the regression. The live run uses the current normal
adapter, with historical baseline equivalence checked locally.

**Recommendation: ready for integration as an opt-in candidate.** The navigation
arm completed the initial source task and delayed bookmark reread with correct
source, scope and citations, without internal-ID display. Its complete interaction
used 13.52% less measured provider input than compact in this run. Compact also
answered both source tasks correctly, but its follow-up exposed the full internal
selector and failed that presentation criterion. These are not two fully passing
arms under the strict rubric, nor evidence of a general efficiency or quality
advantage. Keep the compact default and the earlier failed results unchanged.
The [qualification receipt](../records/granular-source-bookmarks-20261105.json)
contains per-turn counters, correctness judgments and evidence hashes.

## Lifetime and authorization

The navigation presentation can opt into an application-owned source bookmark
store. A bookmark identifies an already-delivered, complete single-document
source selection, retaining its original scope, document generation, content
revision and source digest. It carries a useful label and citation, while exact
selectors remain in application state. A bookmark reread calls the ordinary
source API and revalidates permissions and publication. It never supplies cached
text, grants access, widens the selection or follows a replacement automatically.

Three invocation forms remain distinct:

- Explicit selectors bootstrap discovery and source navigation.
- Returned action references support navigation within one user turn and expire
  when that turn ends.
- Returned source bookmarks support the same exact selection in later turns of
  the same application conversation.

Bookmark ownership is bound to the conversation, actor, library and API target.
The store has an absolute 24-hour lifetime and holds at most 32 selections;
restoring it does not renew its expiry, and reaching the cap does not evict or
reuse existing bindings. Multi-document selections and incomplete/preview-only
responses do not create source bookmarks in this slice.

The demo uses its existing trusted Streamlit session state. A metadata-only
checkpoint is authenticated with a separate, session-held random authority key.
Shortened prose history does not discard the application state. Restoration
requires that same trusted authority and matching ownership; conversation text
alone cannot reconstruct it. A new conversation, actor/API-target change, or
loss of the trusted session ends that ownership. This is not persistent chat
storage across browser-session or server loss. Expired, missing or invalid
protected state requires the demo's explicit **New chat** action; it does not
silently create a replacement owner under the existing conversation.

The feature is opt-in through `DEMO_SOURCE_BOOKMARKS=1` when starting the demo.
Existing compact behavior,
ordinary navigation without a bookmark store, raw API compatibility, ingestion,
embeddings, ranking and context accounting are unchanged. Full diagnostic
selectors and provenance remain available.

## Bounded comparison

The new comparison retains the preceding pinned answering model, verified local
source publication, questions, context settings, output reservation and pacing
policy. Its fixed order is compact initial navigation, acknowledgment and delayed
follow-up, then those same three navigation stages. Both arms retain the actual
user prompts and delivered assistant prose. The follow-up copies only a source
bookmark or compact selector from a tool response actually delivered to the
model. No hidden reference dictionary, expected selector or corrected model call
is inserted.

Navigation retains trusted bookmark state across these turns, while each action
reference session closes normally. The new follow-up tests surviving bookmarks;
the earlier forced-expiry run and negative expiry regressions remain separate.
If an initial source task fails or delivers no bookmark, that failure is retained.

The unchanged cumulative bounds are 60 logical calls, 80 physical attempts,
4 million input tokens and 400,000 output tokens including unknown-attempt
reservations, with a 1,800-second execution-admission deadline. Each source turn
allows 12 rounds; the acknowledgment allows three. The same 200k context allowance,
8k output reservation and 45k/60-second pacing policy apply. No limits are raised
during execution. Source access uses the verified local API snapshot; no cloud
resources, cloud data or embeddings are created.

Actual source delivery, scope, completeness, citations and unsolicited internal
IDs determine completion. Accounting includes registration, current bookmark
labels, tool schemas, instructions, wrappers, repeated history and any recovery
calls. Provider input/output and their cached/reasoning subsets remain separate
from local serialization estimates. This is one consumed-source development
check, not a broader quality or retrieval study.

## Validation and recommendation

CI repair is separately committed at `d868af6`; bookmark implementation and demo
integration at `f97f618`; the executed protocol is
`7902a26455df3df08e0d216b356df9e6406746d8`. No runtime or runner change followed
execution. The final report commit is the reviewable branch candidate; its exact
pushed SHA is checked by GitHub Actions, separately from the frozen model run.

The complete local check passed **2,155 tests** on Python 3.14.3, with 63
Starlette timeout deprecation warnings, plus lint, formatting and privacy
checks. Focused tests include unchanged historical compact callbacks, actual
source/API responses, complete-cell/list/qualifier preservation, shortened and
restored conversation history, tampered/foreign state, absolute expiry, replacement,
revision changes, deletion and changed access. Pending capacity is checked before
advertising a bookmark. A malformed known bookmark call receives an executable
same-selection example, without performing a read or applying conflicting fields.
Ordinary action-reference expiry remains covered separately. Local test-development
failures and their corrections are retained privately; they did not alter the
source/grounding guards or consume extra real-model attempts.

The new single-use run completed all six turns and **18 provider requests**, with
no retry, rate-limit error, unknown-usage attempt, pacing refusal, detour, repeated
read within a turn or whole-document fallback. Both initial turns received four nonempty
authored outlines, copied a returned paragraph action/selector and received the
same complete source paragraph. Both follow-ups made one ordinary source read and
finished in two model calls. The navigation follow-up first used the bookmark
actually delivered in A, after the normal application helper restored its signed
checkpoint. The checkpoint retained the same exact selection and absolute expiry;
no source body was cached or carried forward as evidence.

All four source answers preserved the rule and its condition and used the correct
human-readable citation. The delivered selection was complete; the document was
not. Navigation A/B met every applicable criterion. Compact B alone printed its
internal selector in both raw and delivered answers. That existing baseline
failure is preserved, and source correctness is reported separately from the
strict presentation result. The prior forced-expiry follow-up remains intact;
this new comparison did not substitute an expired action or correct model calls.

### Complete-interaction measurements

| Stage | Compact input / output | Navigation input / output | Requests per arm |
| --- | ---: | ---: | ---: |
| Initial navigation | 48,467 / 1,527 | 39,084 / 1,220 | 6 |
| Brief acknowledgment | 3,083 / 92 | 3,707 / 107 | 1 |
| Delayed fresh reread | 8,095 / 759 | 8,789 / 458 | 2 |
| **Complete interaction** | **59,645 / 2,378** | **51,580 / 1,785** | **9** |

These are provider-reported tokens for all completed stages. Cached input was
36,480 compact and 34,944 navigation; reasoning output was 1,472 and 1,216.
Those counters are subsets, not extra usage. The 13.52% complete-interaction input
reduction includes all instructions, schemas, wrappers, repeated history,
bookmark setup and refresh. The direct navigation follow-up itself cost **694
more input tokens (8.57%)** than direct compact. Surviving bookmarks removed the
earlier reacquisition penalty; they did not make every turn cheaper.

Local serialization totals were 62,679 compact and 54,585 navigation tokens.
An ordered removal calculation attributes 2,122 navigation input tokens to the
bookmark presentation: 238 for its source-response annotations, 345 for repeated
saved-catalogue contents, and 1,539 for repeated bookmark instructions and the
empty-catalogue wrapper. Exactly **one** bookmark was created; its annotation
appeared in the initial response and again on reread. These components add only
under that removal order and are not provider billing allocations. Schemas and
other navigation instructions remain included in the complete totals.

The actual compact paragraph reply remains 1,324 local JSON tokens. Navigation's
focused reply was 717 tokens after the earlier outlines, and 950 on the fresh
follow-up when document information was introduced again. The five initial
replies totalled 8,743 compact versus 4,696 navigation local JSON tokens. All four
source deliveries retained the identical 11-token marked-up body; its plain
sentence is seven tokens. Historical captures were not rewritten. These small
response counts do not replace the complete-interaction/provider comparison.

All 18 provider wire requests, including encrypted-reasoning items, replayed
exactly offline. The shared
60-second pacing reservation peaked at 44,625 under the unchanged 45,000 ceiling,
retaining the 8,000-token output allowance. Local waits totalled 162.75 seconds
compact and 212.67 navigation. The fixed order and shared window prevent a
latency/throughput conclusion. Cloud retrieval RU and monetary cost are
unavailable: source reads used verified local stores, and fake-store zero charges
were excluded. No embedding calls ran.

### Evidence, closure and limits

Fresh deployment binding/capacity checks before and after execution matched the
pinned model and policy. All source snapshots and frozen code matched. The
290-file execution archive was independently verified, and all 377 files in the
preceding evidence root remained byte-identical. Private reviewed evidence keeps
raw requests/responses, source and checkpoint details outside Git; the authority
key was never archived. All six local API contexts, three action sessions and
three transient bookmark stores closed; application authority state was cleared
and the provider client closed. No cloud resources or cloud data needed cleanup.

This qualifies the repaired invocation, exact bookmark lifetime and focused
paragraph navigation on one consumed source with one pinned model. Replacement,
deletion, permission changes and cell fidelity have local regression coverage and
retained earlier evidence; this run does not freshly qualify cloud lifecycle or
broader answer quality. A full 32-bookmark catalogue, longer real-model histories
and persistent restoration after server/session loss remain outside this slice.
Multi-document reads still work but do not create a bookmark. No re-ingestion is
required for supported existing granular artifacts.

The next action is review and integration of this opt-in candidate. No merge,
application deployment, default change or larger study was performed. The
separate context-budget repair and frozen retrieval/provider experiments remain
separately attributable.
