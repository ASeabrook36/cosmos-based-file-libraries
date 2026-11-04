# Granular outline inspection contract

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
rule and cite the delivered source correctly. Any whole-document fallback fails
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
