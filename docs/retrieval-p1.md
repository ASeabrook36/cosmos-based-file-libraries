# P1 — evidence references and usable selectors

P1 starts at R0 `a449dc01e5acde6391bfc1aa50ca07670d54097d`. It adds two
independent, compatible opt-ins to the existing API and consuming adapter:
`view=references` changes evidence serialization; `selectors=handles` adds usable
snapshot-bound request templates and explicit expansion capabilities. Omitted
parameters preserve raw REST and the existing compact adapter behavior. There is
no ingestion, representation, index, query-routing, embedding or provider change.

The [predeclared plan](testing/retrieval-p1-controls.json) holds source bytes,
initial metadata, source generation/revision, answering model and read/query
controls fixed. Development tasks `r0-01`, `r0-11` and `r0-17` exercise selective
expansion, table evidence and a complete section. Baseline, format-only and
selector-only cells run serially, twice, reversing cell order on the second
repeat. The held-out pack remains reserved for I1. A 20% targeted efficiency
improvement with no observed source-quality regression is the provisional gate;
no threshold is relaxed after seeing results.

## Evidence contract

All four retrieval operations accept `?view=references`, with the same body and
platform-bound library/agent. The envelope is `fli.references.v1`; attachments and
operational metadata retain their existing platform-only separation. Serialize
only `evidence` for the answering model. Its inner contract is
`fli.evidence.references.v1`:

- `dictionary` contains repeated metadata values. `{"$ref": n}` means exactly
  `dictionary[n]`, without inheriting or modifying another source's identity.
- `{"$columns": [fields...], "$rows": [[values...], ...]}` factors repeated
  object keys in homogeneous record lists. Reconstruct each object by pairing
  its columns and row values, preserving row order. This is wire encoding; it
  does not assert that an original document had a table.
- `$literal` escapes source objects containing reserved keys. Body/caption
  strings are neither normalized nor abbreviated. Explicit nulls, empty cells,
  source locations, section order, coverage and revision pins remain exact.

`fli.reference_format.unpack` strictly validates references and row dimensions.
It reconstructs the complete current evidence object, including non-body fields.
Malformed or ambiguous encodings fail closed. All 95 saved R0 replies reconstruct
exactly, including existing table markup and omissions. Factoring cannot restore
structure lost during extraction, improve source locator precision or turn an
outline/selected read into a complete document.

Evidence budgets count the entire encoded evidence JSON with the existing named
tokenizer. An oversized selected batch returns no bodies or attachments and
explicitly false completeness. Native images remain separate. The reference
adapter keeps canonical expanded evidence for its source/progress ledger, while
sending the encoded messages to the model and using that same wire form for
context accounting. The frozen 200k allowance and 8k answer reserve still apply.

## Selector contract

`?selectors=handles` is independent of the evidence view. The same switch on
`GET …/tools` supplies matching tool guidance. Authorized rows expose
`documentSelector` with document ID, generation and content revision. Returned
section handles offer a complete `readSelector` request template; discovery and
search rows also offer a pinned `outlineSelector`. These are locators, not
permission tokens or Blob paths. Existing authorization and source-version checks
run on every subsequent operation.

`readCapabilities.evidenceExpansion` is `selected_handle` only when the returned
section handle or indexed passage/source location provides a supported route.
An extracted provider passage with an empty source scope advertises
`outline_required`. It does not manufacture a source-coordinate mapping. R0's two
Microsoft `source_scope_unavailable` attempts followed exactly this empty-scope
case; outline handles already provide a valid recovery route.

The selector adapter requires an object rather than silently converting a bare
ID into an unpinned document selector. Opt-in errors expose allowlisted categories
and recovery guidance without echoing invalid selector values. Invalid, foreign,
unsupported and stale selectors still fail; no retry drops snapshot pins or
widens the selected source. Existing metadata, replacement, deletion, personal
binding and all-or-nothing behavior remain authoritative.

## Measurements and interpretation

[Saved-response measurement](../records/retrieval-p1-saved-responses-20261101.json)
compares exactly the same R0 evidence before and after factoring. These are
`cl100k_base` estimates for serialized tool JSON, not measured provider inputs,
latency, RU or money.

| R0 source strategy | Saved replies | Current tokens | Reference tokens | Reduction |
| --- | ---: | ---: | ---: | ---: |
| Current | 26 | 139,112 | 133,002 | 4.39% |
| Custom structured | 42 | 164,427 | 141,851 | 13.73% |
| Microsoft layout | 27 | 394,285 | 390,123 | 1.06% |

None meets the predeclared 20% efficiency target. Preserve current compact as the
default; reference encoding is an explicit experiment, not a selected integration
candidate. Provider replay, independently scored source delivery and selector
recovery are separate gates. Model output randomness, cache state and rate waits
must remain visible; a paired pilot cannot establish a latency or general accuracy
winner.

## Reproduce and hand off

Use a separate worktree with `sh scripts/dev setup`. Runtime inputs, original
source content, captures, credentials and environment identities belong outside
all Git checkouts. The local setup provisions no cloud resources.

```sh
sh scripts/dev test -q
sh scripts/dev lint
sh scripts/dev privacy --deny-file "$P1_PRIVATE/private-deny.json"
.venv/bin/python -m scripts.retrieval_p1 measure \
  --source "$R0_PRIVATE/replay" --output "$P1_PRIVATE/saved-responses"
```

Cloud work must be scheduled centrally and announced. Use a new isolated
Microsoft-profile library with the five baseline development files; freeze it
before starting all three cells. Do not mutate R0 libraries or borrow scale
resources. No new queried container is needed for this presentation experiment.
The inherited container canary establishes only its original measured scope,
not actual-corpus ANN readiness or scale performance.

Run this checkout's API on localhost with private settings and vector search.
Private config fields are exactly `model`, `agent`, `libraryId`, `indexUrl` and
`searchMode`. The agent instructions must exactly match
`scripts.retrieval_controls.AGENT_INSTRUCTIONS`. The model is frozen GPT-6.1 Sol
`2026-09-29`, Responses, medium reasoning, 8,000 output tokens, three attempts,
no automatic truncation and 200,000 conversation tokens per task/cell.

A fresh management envelope contains `capturedAt`, actual ARM `account` and actual
ARM `deployment`. The shared control helper binds its endpoint/deployment identity,
model version, no-auto-upgrade setting and SKU to the invoked model; the envelope
must be no older than 15 minutes. Capture it again after replay. Keep the API and
experiment code unchanged throughout the measured window.

```sh
.venv/bin/python -m scripts.retrieval_p1 replay \
  --config "$P1_PRIVATE/config.json" \
  --deployment "$P1_PRIVATE/model-before.json" --output "$P1_PRIVATE/replay"
.venv/bin/python -m scripts.retrieval_p1 finalize \
  --deployment "$P1_PRIVATE/model-after.json" --output "$P1_PRIVATE/replay"
.venv/bin/python -m scripts.retrieval_p1 score \
  --source "$P1_PRIVATE/replay" --gold "$R0_PRIVATE/gold.json" \
  --reviews "$P1_PRIVATE/reviews.json" --output "$P1_PRIVATE/scorecard.json"
```

The driver uses existing replay and Responses transports. Each cell has a fresh
model client; tasks have independent histories. Actual wire messages are captured
privately with attachment credentials removed, and token estimates are recorded
separately from provider counters. Reopened cases, questions and before/after
source snapshots must match. A run remains pending until post-run deployment
reconciliation passes. Interrupted/failed runs are preserved; never overwrite a
partial directory or call a partial matrix complete.

Reviews name `repeat`, `cell`, `case`, `turn`, all four R0 quality fields,
`relevantSourceCoverage` and allowlisted `failureCategories`. Verify original source
regions, table relationships and section order separately from answering quality.
Only reviewed allowlist receipts belong in `records/`.

P1 may inform I1 only after paired replay and lifecycle gates. Retain current
compact if no useful gain appears; selector hints can be selected independently
if their recovery benefit survives confirmation. I1 must use the planned current/new
foundation × current/compact held-out comparison with frozen model/context. P1
provides no reason to start R3 or alter physical storage. Broader provider/format
coverage, natural remote races, multi-instance freshness, lifecycle under load,
ANN readiness and production authentication remain outside this package.
