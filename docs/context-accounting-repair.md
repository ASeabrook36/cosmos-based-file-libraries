# Opt-in enclosing-context accounting

`envelope-v2` repairs a local accounting mismatch found in the completed matched
model pilot. It admits a tool reply only if the **complete serialized transcript**,
remaining tool replies and output reserve fit the same estimator used before the
next provider call. `legacy-v1` remains the default. No application setting,
answering model, prompt, client, source representation or retrieval ranking changed.
The completed experiment branch, old scorecards and archived evidence stay frozen.

This is a separately qualified consuming-agent change. It is not part of the
source-structure repair, does not establish a new answer-quality score and does
not launch the larger confirmation study.

## What changed

The previous check measured a tool payload before adding progress and native-image
delivery metadata. The next guard measured that JSON string inside another JSON
message, including escaping and the surrounding tools/transcript. A fixed 512-token
margin did not bound that difference. The known Sol/hybrid hq-38 continuation
therefore passed its individual payload checks and then stopped at 64,843 against
a 64,000 allowance.

The opt-in mode now:

- Checks the complete final tool message after progress/image metadata and the
  compact or reference projection, reserving exact no-body replies for all other
  pending calls in the same batch. The final tool round also reserves the synthesis
  instruction. When even these replies cannot fit, no further source is fetched.
- Refuses an oversized new payload as a whole. Its model-visible reply says no
  content was delivered; full/selected source text is never silently shortened.
  Previous evidence remains intact, attempted retrieval charges remain recorded,
  and withheld bodies do not enter source-reference memory or delivery credit.
- Keeps a bounded local set of withheld document identities for the existing
  source-grounding checks. A generic model-visible refusal cannot erase the fact
  that no body, or only one of two requested sources, was received. These local
  facts are never added to model input, source credit or the reference ledger.
- Checks native-image input, its explanatory reminder and the resulting tool
  delivery metadata together. Duplicate-image status changes are checked too.
- Checks answer-repair messages before submitting them, and retains protocol-valid
  no-body tool replies when a local stop prevents a provider call.
- Calibrates local growth against actual reported input tokens. New image and
  opaque-reasoning estimates are reservations, not usage. Missing subsequent input
  meters cannot erase accumulated reasoning reservations. Actual counters are
  never inflated or rewritten.

The output reservation remains 8,000 tokens. The estimator is the local agent's
serialized-envelope estimate, not the provider's native tokenizer or opaque
Responses representation. A successful local check is not proof of actual model
capacity or sufficient context for the task. An unusually large model tool-call
response can still exhaust the allowance before any next source reply fits.

Full-tool-history resumption has an explicit conservative limit: a fixed-size
withholding reply does not retain all document identities. If that history is
reused for an explicit both/each/every-file substantive comparison, the final
grounding gate refuses to assert complete source scope and requires a fresh
retrieval session. It does not invent missing identities or let the smaller reply
silently weaken the existing comparison guard. Ordinary replay cases retain their
existing independent-turn/history policy. The unknown-scope restriction can remain
even after new reads in that resumed transcript; an independent fresh turn is
supported. Preserving bounded scope state across such histories is separate future
work. This is not general same-chat recovery or default-ready promotion.

## Frozen-trace qualification

[The content-free receipt](../records/context-accounting-qualification-20261104.json)
reconstructs all 18 prior context stops from hash-bound provider requests/responses,
wire transcripts and local tool candidates. It verifies the historical guards
exactly, then applies the new admission policy at each final continuation. The
recorded candidates and prior received prefix are held fixed; this is an offline
counterfactual, not a replay of changed model decisions.

| Measurement | Result |
| --- | ---: |
| Historical stop guards reproduced exactly | 18 |
| Cases with room for bounded no-body continuation | 6 |
| Cases still unable to fit the minimum replies | 12 |
| Recorded candidates accepted across the six cases | 1 |
| Recorded candidates wholly refused across the six cases | 6 |
| New provider/cloud calls | 0 |

For hq-38, the outline candidate is retained. The source-bearing search candidate's
29,358-token serialized body adds **31,215 tokens** inside its enclosing message.
That candidate is refused, giving a resulting guard of **33,703/64,000**, including
the output reserve. The previously received source prefix is preserved. This
allows a future model continuation to receive an explicit refusal; whether it
then retrieves smaller evidence or answers correctly remains unknown. The original
answer and its failed judgment are unchanged.

The other 17 cases are not claimed fixed. Five would permit a bounded no-body
continuation; twelve still stop before fetching more source. Neither these counts
nor hq-38 establish adequate 64k context, model recovery or improved answer quality.

Synthetic regressions cover escaped Unicode/quoted content, complete-payload
refusal, batched replies, compact/reference modes, final metadata, positive and
withheld native images, repeated image delivery, output reserve, final synthesis,
answer repair, resumable stopped histories, missing/invalid provider meters and
hash-bound offline evidence. All-withheld and explicit two-document partial-source
cases also reject unqualified invented answers, without treating retained scope as
delivered evidence, including after full-tool-history resumption. These tests do
not call Azure or an answering model.

The [validation receipt](../records/context-accounting-validation-20261104.json)
records 1,434 passing repository tests, including 35 new synthetic cases, plus
lint/format, privacy and diff checks. Independent review reproduced 72 archived
artifact bindings and all 18 historical guards, checked six default-transcript /
image / escaping cases and ten grounding cases, and verified the exact final
implementation hashes. Synthetic replay plumbing also checks that the explicit
mode and reservations are recorded and applied to both cells of a paired run.

## Run locally

The normal repository setup provides a worktree-local environment and never loads
cloud credentials:

```sh
sh scripts/dev setup
sh scripts/dev test -q tests/test_context_budget.py tests/test_context_qualification.py
sh scripts/dev lint
sh scripts/dev privacy
```

To reproduce the private frozen-trace check, prepare an input JSON outside Git:

```json
{
  "schema": "fli.context-envelope-qualification.inputs.v1",
  "runRoot": "PRIVATE_ARCHIVE/scored-run",
  "frozenControlsSha256": "71c0b8bb851bac58c39d248b3a0794cbd870e8ec1beee1081afba1b06fe7c378",
  "diagnostic": {
    "path": "PRIVATE_ARCHIVE/analysis-v4-final-diagnostic.json",
    "sha256": "d038e1cae92a00df407443fa8969e57db89eb43ed13055bd1cbc6ddfd4df9a0d"
  }
}
```

Use a new output file; existing evidence is never overwritten:

```sh
.venv/bin/python -m scripts.qualify_context_envelope \
  --inputs "$PRIVATE_RUNTIME/inputs.json" \
  --output "$PRIVATE_RUNTIME/new-qualification.json"
```

For a separately authorized future consuming-agent run, select the mode explicitly:

```python
turn = run_turn(chat, index, agent, history, question, context_token_budget=64000, context_accounting="envelope-v2")
```

The existing replay CLI also accepts `--context-accounting envelope-v2`; it records
the opt-in mode, context allowance and 8,000-token output reservation. Paired runs
apply the same mode to both cells. Match the actual outbound model output limit to
that reservation. These options are executable handoffs, not authorization to
provision resources or rerun the consumed benchmark.

Before larger confirmation, separately establish actual provider compatibility,
context adequacy and recovery behavior under newly frozen common controls. Keep
that check independent of source-structure changes. No live model continuation,
new latency/RU benefit or new answer-quality result was measured here.
