# Conversation-owned source bookmarks

This candidate continues from `90e0d90` on
`codex/granular-reference-presentation`. The earlier
[reference-contract repair and forced-expiry comparison](granular-reference-contract-repair.md),
[presentation measurements](granular-reference-presentation.md), and their
archives remain unchanged. The frozen compact baseline is still executed from
its original Git commit. CI now fetches that history rather than replacing or
skipping the regression.

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

Local checks, frozen execution results and the final integration recommendation
will be recorded here after the candidate is tested. No merge, deployment or
default change is included.
